#!/usr/bin/env python3
"""
onedrive_name_cleanup.py - fix OneDrive-illegal file/folder names on macOS.

macOS allows characters in names that OneDrive/SharePoint reject (most often a
colon: typing "/" in Finder is stored as ":" on disk). Those items fail to sync,
and repeated conflicts across Macs spawn piles of empty duplicate folders such as

    Visuals : renders 2-OC-MBK-10001-OC-MBK-10002 3

What this script does, per directory, deepest first:

  1. Finds entries whose names contain OneDrive-illegal characters.
  2. Groups conflict copies into families by stripping machine-name suffixes
     ("-OC-MBK-10001") and Finder copy numbers (" 2", " 3").
  3. Deletes members that are empty (contain nothing but folders and OS junk
     such as .DS_Store).
  4. Renames members with content to a clean name ("Visuals - renders").
     If several members have content, the first keeps the clean name and the
     rest get " (2)", " (3)"... and are flagged for a human to merge.

It never deletes a real file and never merges folders. Dry run by default;
pass --apply to make changes. Every action is written to a CSV log.

Python 3.9+ stdlib only (ships with macOS Command Line Tools).
"""

import argparse
import csv
import datetime as dt
import os
import re
import sys
import unicodedata
from pathlib import Path

__version__ = "1.0.0"

# Characters OneDrive / SharePoint refuse in names.
ILLEGAL_CHARS = set('\\:*?"<>|')

# OS clutter that does not count as "content" when deciding a folder is empty.
JUNK_FILES = {".DS_Store", "Icon\r", "desktop.ini", "Thumbs.db", ".localized"}

# Machine-name suffix OneDrive appends on conflict, e.g. "-OC-MBK-10001".
DEFAULT_MACHINE_PATTERN = r"OC-[A-Z]{2,4}-\d+"

# Timestamp OneDrive inserts on some conflict copies: " (2026-11-08 20:22:40 +0000)".
STAMP_RE = r"\s?\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}\)"

DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "onedrive-name-cleanup"


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def has_illegal(name):
    return any(c in ILLEGAL_CHARS for c in name)


def clean_name(name):
    """Replace illegal characters with a dash, keeping things readable.

    "Visuals : renders"   -> "Visuals - renders"
    "Ecotourism:other"    -> "Ecotourism-other"
    "Phase: 2"            -> "Phase - 2"
    """
    # Colons/pipes with whitespace on either side become a spaced dash.
    out = re.sub(r"\s*[:|]\s*", lambda m: " - " if m.group(0).strip() != m.group(0) else "-", name)
    out = out.replace('"', "'")
    out = re.sub(r"[*?<>]", "", out)  # "WaterVole?.AVI" -> "WaterVole.AVI"
    out = re.sub(r"[\\:|]", "-", out)
    out = re.sub(r"-{2,}", "-", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = out.strip(" .")
    return out or "unnamed"


def split_ext(name, is_dir):
    if is_dir:
        return name, ""
    stem, ext = os.path.splitext(name)
    # Treat odd "extensions" (spaces, very long) as part of the name.
    if not ext or " " in ext or len(ext) > 8:
        return name, ""
    return stem, ext


def family_parts(stem, machine_re):
    """Return (stripped, core) for a name stem.

    stripped: machine-name suffixes (and the copy numbers around them) removed.
    core:     stripped with trailing Finder copy numbers also removed.
    """
    stem = re.sub(STAMP_RE, "", stem)
    stripped = re.sub(r"(?:\s\d+)?(?:-" + machine_re + r")+(?:\s\d+)?$", "", stem)
    core = re.sub(r"(?:\s\d+)+$", "", stripped)
    return stripped, core


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def is_junk(name):
    return name in JUNK_FILES or name.startswith("._")


def is_effectively_empty(path):
    """True if a directory tree holds nothing but folders and OS junk files.

    Only lists names, never reads file contents, so it will not trigger
    downloads of OneDrive online-only files.
    """
    for root, dirs, files in os.walk(path, followlinks=False):
        for f in files:
            if not is_junk(f):
                return False
        for d in dirs:
            if os.path.islink(os.path.join(root, d)):
                return False
    return True


def count_files(path):
    n = 0
    for _root, _dirs, files in os.walk(path, followlinks=False):
        n += sum(1 for f in files if not is_junk(f))
    return n


def remove_empty_tree(path):
    for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for f in files:
            os.remove(os.path.join(root, f))
        for d in dirs:
            os.rmdir(os.path.join(root, d))
    os.rmdir(path)


def norm(name):
    # APFS is normally case-insensitive and normalisation-insensitive.
    return unicodedata.normalize("NFC", name).casefold()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

class Action:
    __slots__ = ("kind", "path", "target", "note")

    def __init__(self, kind, path, target="", note=""):
        self.kind, self.path, self.target, self.note = kind, path, target, note


def plan_directory(dirpath, names, machine_re):
    """Plan actions for the illegal-named children of one directory."""
    bad = [n for n in names if has_illegal(n)]
    if not bad:
        return []

    existing = {norm(n) for n in names if not has_illegal(n)}
    entries = []
    for n in bad:
        full = os.path.join(dirpath, n)
        is_dir = os.path.isdir(full) and not os.path.islink(full)
        # For files OneDrive puts the machine name after the extension:
        # "plan:v2.jpg-OC-MBK-10005". Strip it before splitting the extension.
        pre = n if is_dir else re.sub(r"(?:-" + machine_re + r")+(?:\s\d+)?$", "", n)
        stem, ext = split_ext(pre, is_dir)
        stripped, core = family_parts(stem, machine_re)
        if not is_dir:
            # "Otter 2.AVI" and "Otter.AVI" are usually different files, not
            # copies, so only machine-name suffixes count as copy markers.
            core = stripped
        entries.append({"name": n, "path": full, "is_dir": is_dir, "ext": ext,
                        "stripped": stripped, "core": core})

    # A trailing number is a Finder copy number only if siblings share the core.
    core_counts = {}
    for e in entries:
        key = (e["core"], e["ext"].lower(), e["is_dir"])
        core_counts[key] = core_counts.get(key, 0) + 1
    families = {}
    for e in entries:
        key = (e["core"], e["ext"].lower(), e["is_dir"])
        base = e["core"] if core_counts[key] > 1 else e["stripped"]
        families.setdefault((base, e["ext"], e["is_dir"]), []).append(e)

    actions = []
    for (base, ext, is_dir), members in sorted(families.items()):
        keep = []
        for e in members:
            if e["is_dir"] and is_effectively_empty(e["path"]):
                actions.append(Action("delete-empty", e["path"],
                                      note="empty conflict copy" if len(members) > 1 else "empty folder"))
            else:
                keep.append(e)

        # Most content first, so the fullest copy gets the clean name.
        keep.sort(key=lambda e: (-(count_files(e["path"]) if e["is_dir"] else 1), e["name"]))
        clean_base = clean_name(base)
        for i, e in enumerate(keep):
            n = 1 if i == 0 else i + 1
            while True:
                candidate = clean_base + ("" if n == 1 else " (%d)" % n) + ext
                if norm(candidate) not in existing:
                    break
                n += 1
            existing.add(norm(candidate))
            note = ""
            if len(keep) > 1 and not is_dir:
                sizes = {os.path.getsize(k["path"]) for k in keep}
                note = "REVIEW: %d versions of this file - %s" % (
                    len(keep), "same size, likely duplicates" if len(sizes) == 1 else "sizes differ")
            elif len(keep) > 1:
                note = "REVIEW: %d copies with content - merge manually" % len(keep)
            elif n > 1:
                note = "REVIEW: '%s' already existed - merge manually" % (clean_base + ext)
            actions.append(Action("rename", e["path"], os.path.join(dirpath, candidate), note))
    return actions


def walk(root, machine_re):
    """Yield actions deepest-first so child paths stay valid."""
    for dirpath, dirs, files in os.walk(root, topdown=False, followlinks=False):
        names = dirs + files
        for a in plan_directory(dirpath, names, machine_re):
            yield a


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def default_roots():
    cloud = Path.home() / "Library" / "CloudStorage"
    if not cloud.is_dir():
        return []
    return sorted(p for p in cloud.iterdir() if p.is_dir() and p.name.startswith("OneDrive"))


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Fix OneDrive-illegal names (e.g. colons) and remove empty conflict-copy folders.")
    p.add_argument("paths", nargs="*",
                   help="Folders to scan. Default: every ~/Library/CloudStorage/OneDrive* folder.")
    p.add_argument("--apply", action="store_true",
                   help="Make the changes. Without this, only reports what would happen.")
    p.add_argument("--machine-pattern", default=DEFAULT_MACHINE_PATTERN,
                   help="Regex for machine-name suffixes (default: %(default)s).")
    p.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="Where to write the CSV log.")
    p.add_argument("-q", "--quiet", action="store_true", help="Only print the summary.")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)

    roots = [Path(x).expanduser() for x in args.paths] or default_roots()
    if not roots:
        print("No folders given and no ~/Library/CloudStorage/OneDrive* folder found.", file=sys.stderr)
        return 2
    for r in roots:
        if not r.is_dir():
            print("Not a folder: %s" % r, file=sys.stderr)
            return 2

    mode = "APPLY" if args.apply else "DRY RUN"
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = Path(args.log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("%s-%s.csv" % (stamp, "apply" if args.apply else "dryrun"))

    counts = {"delete-empty": 0, "rename": 0, "review": 0, "error": 0}
    with open(log_path, "w", newline="") as fh:
        log = csv.writer(fh)
        log.writerow(["mode", "action", "path", "target", "note", "result"])
        for root in roots:
            root = root.resolve()
            print("[%s] Scanning %s" % (mode, root))
            for a in walk(str(root), args.machine_pattern):
                result = "planned"
                if args.apply:
                    try:
                        if a.kind == "delete-empty":
                            remove_empty_tree(a.path)
                        else:
                            if os.path.exists(a.target) and norm(a.target) != norm(a.path):
                                raise FileExistsError("target exists: %s" % a.target)
                            os.rename(a.path, a.target)
                        result = "done"
                    except OSError as exc:
                        result = "error: %s" % exc
                        counts["error"] += 1
                if not result.startswith("error"):
                    counts[a.kind] += 1
                if a.note.startswith("REVIEW"):
                    counts["review"] += 1
                log.writerow([mode, a.kind, a.path, a.target, a.note, result])
                if not args.quiet or a.note.startswith("REVIEW") or result.startswith("error"):
                    rel = os.path.relpath(a.path, str(root))
                    if a.kind == "rename":
                        line = "RENAME  %s\n     -> %s" % (rel, os.path.basename(a.target))
                    else:
                        line = "DELETE  %s  (%s)" % (rel, a.note)
                    if a.note.startswith("REVIEW"):
                        line += "\n        " + a.note
                    if result.startswith("error"):
                        line += "\n        " + result
                    print(line)

    print()
    print("[%s] %d empty folders %s, %d items %s, %d need manual review, %d errors"
          % (mode,
             counts["delete-empty"], "deleted" if args.apply else "to delete",
             counts["rename"], "renamed" if args.apply else "to rename",
             counts["review"], counts["error"]))
    print("Log: %s" % log_path)
    if not args.apply and (counts["delete-empty"] or counts["rename"]):
        print("Nothing changed. Re-run with --apply to make these changes.")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
