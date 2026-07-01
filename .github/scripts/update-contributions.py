#!/usr/bin/env python3
"""Refresh the per-project "N merged" tally in the profile README.

Option B: your hand-written descriptions are never touched. This only rewrites the
small marker span (`<!--m:owner/repo-->· N merged<!--/m-->`) that sits right after
each project's star badge, and flips "in review" -> "N merged" once a PR lands.

Repos with a manual tally (e.g. Go, which lands via Gerrit, not GitHub PRs) are left
exactly as configured -- the script never queries or overwrites them.

Run in CI with GH_TOKEN set (built-in GITHUB_TOKEN is enough -- all data is public).
"""
import subprocess, re, sys, pathlib

README = pathlib.Path(__file__).resolve().parents[2] / "README.md"
AUTHOR = "harjothkhara"

# (owner/repo, manual_tally). manual_tally=None -> auto-count merged PRs by AUTHOR.
REPOS = [
    ("openclaw/openclaw", None),
    ("python/cpython", None),
    ("golang/go", "landed via Gerrit → x/tools"),  # Go lands via Gerrit, not GitHub PRs
    ("NousResearch/hermes-agent", None),
    ("vllm-project/vllm", None),
    ("NVIDIA/NemoClaw", None),
    ("garrytan/gbrain", None),
]


def merged_count(repo):
    q = f"repo:{repo} author:{AUTHOR} is:pr is:merged"
    out = subprocess.run(
        ["gh", "api", "-X", "GET", "search/issues", "-f", f"q={q}", "--jq", ".total_count"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        # Fail hard: a transient query error must not blank out a real tally.
        raise SystemExit(f"query failed for {repo}: {out.stderr.strip()}")
    return int((out.stdout.strip() or "0"))


def tally(repo, manual):
    if manual is not None:
        return manual
    n = merged_count(repo)
    return f"{n} merged" if n > 0 else "in review"


def apply(text, repo, tally_str):
    span = re.compile(rf"<!--m:{re.escape(repo)}-->.*?<!--/m-->", re.S)
    new = f"<!--m:{repo}-->· {tally_str}<!--/m-->"
    if span.search(text):
        return span.sub(new, text)
    # Bootstrap: insert the marker right after this repo's star badge closing link.
    si = text.find(f"img.shields.io/github/stars/{repo}")
    if si == -1:
        print(f"warn: star badge for {repo} not found; skipping", file=sys.stderr)
        return text
    link = f"](https://github.com/{repo})"
    li = text.find(link, si)
    if li == -1:
        print(f"warn: badge link for {repo} not found; skipping", file=sys.stderr)
        return text
    ins = li + len(link)
    return text[:ins] + " " + new + text[ins:]


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
    for repo, manual in REPOS:
        text = apply(text, repo, tally(repo, manual))
    text = strip_dupe_inreview(text)
    README.write_text(text)


if __name__ == "__main__":
    main()
