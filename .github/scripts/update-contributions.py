#!/usr/bin/env python3
"""Refresh the per-project "N merged" tally in the profile README.

Option B: your hand-written descriptions are never touched. This only rewrites the
small marker span (`<!--m:owner/repo-->· N merged<!--/m-->`) that sits right after
each project's star badge, and flips "in review" -> "N merged" once a PR lands.

The tally is "shipped" = merged + landed (cherry-picked to main by a maintainer,
closed rather than GitHub-merged but real credited work -- e.g. openclaw/openclaw#91536),
sourced from the live PR/activity dashboard so this number never disagrees with it.
Dashboard: https://harjoth-oss-contribution-workflow.pages.dev (unlisted Cloudflare
Pages site backed by the private repo harjothkhara/Harjoth-OSS-Contribution-Workflow;
data.js there is refreshed hourly during active hours and is the source of truth).
This includes golang/go, whose Gerrit CLs (across go/x/tools/x/crypto) the dashboard
already folds into a single "golang/go" bucket -- same mechanism as GitHub PRs here.

Which repos the dashboard tracks is read from its data.js `repos: [...]` array, not
hardcoded -- the dashboard adds repos over time, and a hand-maintained copy drifts.
Repos outside that array (e.g. garrytan/gbrain) use a direct merged-PR-count query
against the GitHub search API.

If the dashboard is unreachable or its repos[] can't be parsed, EVERY repo is SKIPPED
(left exactly as it was) rather than guessed at. With the tracked set unknown there's no
safe way to route a repo: a GitHub PR search for golang/go would wrongly read 0 (Go
doesn't use GitHub PRs), so falling back there would actively regress a repo that clearly
has shipped work. Skipping keeps the last good tallies until the next run.

Run in CI with GH_TOKEN set (built-in GITHUB_TOKEN is enough -- all data is public).
"""
import subprocess, re, sys, pathlib, urllib.request

README = pathlib.Path(__file__).resolve().parents[2] / "README.md"
AUTHOR = "harjothkhara"
DASHBOARD_DATA_URL = "https://harjoth-oss-contribution-workflow.pages.dev/data.js"

# (owner/repo, manual_tally). manual_tally=None -> auto tally (dashboard, else GitHub search fallback).
REPOS = [
    ("openclaw/openclaw", None),
    ("python/cpython", None),
    ("golang/go", None),
    ("NousResearch/hermes-agent", None),
    ("vllm-project/vllm", None),
    ("NVIDIA/NemoClaw", None),
    ("steipete/CodexBar", None),
    ("facebook/astryx", None),
    ("nodejs/node", None),
    ("kubernetes/website", None),
    ("garrytan/gbrain", None),  # not tracked by the dashboard -> GitHub search fallback
]


def fetch_dashboard():
    """(tracked_repos, {repo: shipped_count}) from the dashboard's data.js, or None if unusable.

    The set of tracked repos is read from data.js's own `repos: [...]` array rather than
    hardcoded here. It used to be a hand-maintained constant, which silently drifted as the
    dashboard picked up new repos (kubernetes/website sat in the wrong bucket that way), and
    a stale copy quietly routes a repo to the wrong counting path.

    Every tracked repo gets an explicit 0 entry up front, so a repo with zero shipped PRs is
    correctly distinguished from one the dashboard doesn't track at all (the latter falls
    through to the GitHub-search fallback instead of reading as "0").

    Returns None if the dashboard is unreachable OR its repos[] can't be parsed. Callers must
    treat None as "skip every repo": with the tracked set unknown, we cannot tell which repos
    are safe to GitHub-search, and searching a Gerrit-based repo like golang/go would wrongly
    read 0. An empty or unparsed repos[] must never be mistaken for "nothing is tracked".
    """
    try:
        # Cloudflare's WAF blocks urllib's default "Python-urllib/3.x" UA as a bot
        # signature (curl works fine) -- send a normal browser-ish UA to avoid the 403.
        req = urllib.request.Request(DASHBOARD_DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode()
    except Exception as e:
        print(f"warn: dashboard fetch failed ({e}); skipping all tallies this run", file=sys.stderr)
        return None
    # Anchored at line start so it can't match `repo_health:` or a repo name in the header comment.
    m = re.search(r'^\s*repos:\s*\[(.*?)\]', text, re.S | re.M)
    tracked = set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
    if not tracked:
        print("warn: could not read repos[] from dashboard payload; skipping all tallies this run", file=sys.stderr)
        return None
    counts = {repo: 0 for repo in tracked}
    for m in re.finditer(r'\{n:\d+, repo:"([^"]+)"[^}]*?state:"(merged|landed)"[^}]*\}', text):
        repo, state = m.group(1), m.group(2)
        if repo in counts:
            counts[repo] += 1
    return tracked, counts


def merged_count_via_github_search(repo):
    q = f"repo:{repo} author:{AUTHOR} is:pr is:merged"
    out = subprocess.run(
        ["gh", "api", "-X", "GET", "search/issues", "-f", f"q={q}", "--jq", ".total_count"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        # Fail hard: a transient query error must not blank out a real tally.
        raise SystemExit(f"query failed for {repo}: {out.stderr.strip()}")
    return int((out.stdout.strip() or "0"))


SKIP = object()  # dashboard unusable -- leave bullet exactly as it is


def tally(repo, manual, dashboard):
    if manual is not None:
        return manual
    if dashboard is None:
        # Tracked set unknown, so no repo can be safely routed -- don't guess via GitHub
        # search (e.g. golang/go has no GitHub PRs and would wrongly read 0).
        return SKIP
    tracked, counts = dashboard
    if repo in tracked:
        n = counts[repo]
    else:
        n = merged_count_via_github_search(repo)
    # "shipped" (not "merged") -- honest either way: some counts include cherry-picked
    # ("landed") contributions that were closed rather than GitHub-merged.
    return f"{n} shipped" if n > 0 else "in review"


def apply(text, repo, tally_str):
    span = re.compile(rf"<!--m:{re.escape(repo)}-->.*?<!--/m-->", re.S)
    new = f"<!--m:{repo}-->· {tally_str}<!--/m-->"
    if span.search(text):
        return span.sub(new, text)
    # Bootstrap: insert the marker right after this repo's star badge closing link.
    # Anchor on the shields badge image + repo link so it targets the *badge* link (not
    # the title link) regardless of whether the badge is the live or static form.
    m = re.search(rf"img\.shields\.io/[^)]*\)\]\(https://github\.com/{re.escape(repo)}\)", text)
    if not m:
        print(f"warn: star badge for {repo} not found; skipping", file=sys.stderr)
        return text
    ins = m.end()
    return text[:ins] + " " + new + text[ins:]


def humanize_stars(n):
    """Match shields' metric formatting: 73508 -> '74k', 381716 -> '382k', 1_250_000 -> '1.2M'."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{round(n / 1000)}k"
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def star_count(repo):
    """Current stargazer count via the GitHub API, or None on any transient failure."""
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}", "--jq", ".stargazers_count"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip())
    except ValueError:
        return None


def refresh_star_badges(text):
    """Rewrite each repo's star badge to a STATIC shields badge with the current count.

    The badges used to be live `github/stars/<repo>` lookups, which make shields call the
    GitHub API on every render. shields shares one GitHub token pool across all its users,
    so it periodically gets rate-limited and returns a badge that reads "invalid" -- and
    because GitHub's image proxy (Camo) caches whatever it got for the badge's cacheSeconds,
    a single unlucky fetch freezes "invalid" on the profile for hours. Serving a static
    `badge/stars-<count>-gold` badge (count refreshed here from the GitHub API, which this
    job already has a token for) means shields never calls GitHub live, so it can never read
    "invalid". On a fetch failure we leave the badge exactly as-is (keeps the last good count).
    """
    for repo, _ in REPOS:
        n = star_count(repo)
        if n is None:
            print(f"warn: star count fetch failed for {repo}; leaving badge unchanged", file=sys.stderr)
            continue
        badge = f"https://img.shields.io/badge/stars-{humanize_stars(n)}-gold?style=flat"
        pat = re.compile(rf"https://img\.shields\.io/[^)]*(\)\]\(https://github\.com/{re.escape(repo)}\))")
        text, k = pat.subn(lambda m: badge + m.group(1), text, count=1)
        if k == 0:
            print(f"warn: star badge for {repo} not found; skipping", file=sys.stderr)
    return text


def strip_dupe_inreview(text):
    # The marker now owns status, so drop any trailing "_(in review)_" on marked lines.
    lines = []
    for ln in text.split("\n"):
        if "<!--m:" in ln:
            ln = re.sub(r"\s*_\(in review\)_\s*$", "", ln)
        lines.append(ln)
    return "\n".join(lines)


def main():
    text = README.read_text()
    dashboard = fetch_dashboard()
    for repo, manual in REPOS:
        val = tally(repo, manual, dashboard)
        if val is SKIP:
            print(f"warn: skipping {repo} -- dashboard unusable", file=sys.stderr)
            continue
        text = apply(text, repo, val)
    text = strip_dupe_inreview(text)
    text = refresh_star_badges(text)
    README.write_text(text)


if __name__ == "__main__":
    main()
