#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Push accounting_ms in size-bounded chunks.

GitHub answers HTTP 408 when a single push request takes too long. On a slow
uplink a ~95 MiB pack blows past that limit, so this script:

  1. undoes the single large commit (soft reset, files stay on disk),
  2. packs the files into chunks of at most MAX_CHUNK_MB,
  3. commits and pushes each chunk, retrying a few times.

Usage:  python3 push_in_chunks.py [--max-mb 18] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

REPO = "/home/sameh/odoo_source/odoo19/msolutions_custom"
PREFIX = "accounting_ms"
COMMIT_SUBJECT = "feat(accounting_ms): port the Enterprise Accounting suite to Odoo 19 Community"


def run(args, **kw):
    return subprocess.run(args, cwd=REPO, text=True, capture_output=True, **kw)


def git(*args, **kw):
    res = run(["git", *args], **kw)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{res.stdout}\n{res.stderr}")
    return res.stdout


def staged_files():
    out = run(["git", "diff", "--cached", "--name-only", "-z"])
    return [p for p in out.stdout.split("\0") if p]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-mb", type=float, default=18.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start-at", type=int, default=1, help="resume at chunk N")
    args = ap.parse_args()

    os.chdir(REPO)
    max_bytes = int(args.max_mb * 1024 * 1024)

    # ---------------------------------------------------------------- reset
    head_subject = git("log", "-1", "--pretty=%s").strip()
    if head_subject.startswith("feat(accounting_ms)"):
        print(f"[reset] undoing commit: {head_subject[:60]}...")
        git("reset", "--soft", "HEAD~1")
    git("reset", "-q")

    # ---------------------------------------------------------------- files
    out = run(["git", "ls-files", "--others", "--exclude-standard", "-z", PREFIX])
    files = [p for p in out.stdout.split("\0") if p]
    if not files:
        print("nothing to commit - already pushed?")
        return 0
    files.sort(key=lambda p: os.path.getsize(os.path.join(REPO, p)), reverse=True)
    total = sum(os.path.getsize(os.path.join(REPO, p)) for p in files)
    print(f"[plan ] {len(files)} files, {total / 1048576:.1f} MiB, chunk cap {args.max_mb} MiB")

    # ------------------------------------------------------- greedy chunks
    chunks: list[tuple[list[str], int]] = []
    current: list[str] = []
    current_size = 0
    for path in files:
        size = os.path.getsize(os.path.join(REPO, path))
        if current and current_size + size > max_bytes:
            chunks.append((current, current_size))
            current, current_size = [], 0
        current.append(path)
        current_size += size
    if current:
        chunks.append((current, current_size))

    print(f"[plan ] {len(chunks)} chunks")
    for i, (paths, size) in enumerate(chunks, 1):
        top = sorted({"/".join(p.split("/")[1:3]) for p in paths})[:3]
        print(f"        {i}/{len(chunks)}  {size / 1048576:6.1f} MiB  {len(paths):5d} files  {', '.join(top)}")
    if args.dry_run:
        return 0

    # ------------------------------------------------------ commit + push
    for index, (paths, size) in enumerate(chunks, 1):
        if index < args.start_at:
            continue
        top = sorted({"/".join(p.split("/")[1:3]) for p in paths})[:4]
        subject = f"{COMMIT_SUBJECT} [{index}/{len(chunks)}]"
        body = (
            f"Chunk {index} of {len(chunks)} ({size / 1048576:.1f} MiB, {len(paths)} files).\n"
            f"Pushed separately because a single ~95 MiB push exceeds GitHub's\n"
            f"request timeout on this uplink.\n\n"
            f"Contents: {', '.join(top)}"
        )
        print(f"\n[chunk {index}/{len(chunks)}] staging {len(paths)} files ({size / 1048576:.1f} MiB)")
        payload = "\0".join(paths) + "\0"
        res = run(["git", "add", "--pathspec-from-file=-", "--pathspec-file-nul"], input=payload)
        if res.returncode != 0:
            print("  git add failed:", res.stderr[:400])
            return 1
        staged = staged_files()
        print(f"  staged {len(staged)} files")

        msg = f"{subject}\n\n{body}\n"
        res = run(["git", "commit", "-F", "-"], input=msg)
        if res.returncode != 0:
            print("  git commit failed:", res.stderr[:400])
            return 1
        print("  committed:", git("log", "-1", "--pretty=%h %s").strip())

        pushed = False
        for attempt in range(1, 4):
            r = run(["git", "-c", "http.lowSpeedLimit=0", "-c", "http.lowSpeedTime=999999",
                     "push", "origin", "main"])
            if r.returncode == 0:
                print(f"  pushed (attempt {attempt})")
                pushed = True
                break
            print(f"  push attempt {attempt} failed: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '?'}")
            time.sleep(5)
        if not pushed:
            print(f"  !! chunk {index} could not be pushed - stopping here.")
            print(f"  !! resume with: python3 {os.path.basename(__file__)} --start-at {index}")
            return 2

    print("\n[done ] all chunks pushed")
    print(git("log", "--oneline", "-12"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
