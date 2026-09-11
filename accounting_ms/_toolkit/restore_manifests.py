#!/usr/bin/env python3
"""Restore pristine manifests from the earliest Phase 1 backup."""
import ast
import glob
import os
import shutil
import sys

base = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".backup")
backups = sorted(os.listdir(base))
if not backups:
    sys.exit("no backup found")
bk = backups[0]
src = os.path.join(base, bk)
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

count = 0
for path in glob.glob(os.path.join(src, "**", "__manifest__.py"), recursive=True):
    rel = os.path.relpath(path, src)
    target = os.path.join(root, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(path, target)
    count += 1

print(f"restored {count} manifests from {bk}")
sample = os.path.join(root, "account_avatax_sale", "__manifest__.py")
print("account_avatax_sale depends ->",
      ast.literal_eval(open(sample, encoding="utf-8").read())["depends"])
