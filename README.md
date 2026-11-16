# onedrive-name-cleanup

Fixes OneDrive-illegal file and folder names on macOS, and removes the empty
conflict-copy folders they spawn.

## The problem

macOS allows characters in names that OneDrive/SharePoint reject, most often a
**colon**. In Finder users never type a colon: they type `/` (e.g. "Visuals /
renders"), which macOS stores on disk as `:`. That item cannot sync, and as
other Macs hit the same item, OneDrive generates conflict copies tagged with
each machine name, then copies of the copies:

```
Visuals : renders 2-OC-MBK-10001-OC-MBK-10002 4
Ecotourism:other EPC-OC-MBK-10001-OC-MBK-10002-OC-MBK-10004 3
deer larder:steading.jpg-OC-MBK-10005
```

Almost all of these are empty.

## What the script does

For every folder under the target, deepest first:

1. Finds names containing `\ : * ? " < > |`.
2. Groups conflict copies into a family by stripping machine suffixes
   (`-OC-MBK-12345`) and Finder copy numbers (` 2`, ` 3`).
3. **Deletes** members that are empty: only subfolders and OS junk such as
   `.DS_Store`.
4. **Renames** members with content to a clean name: ` : ` becomes ` - `, and a
   bare `:` becomes `-`. So `Visuals : renders 5` becomes `Visuals - renders`.
5. If more than one member has content, the fullest one gets the clean name and
   the others get ` (2)`, ` (3)`. These are flagged `REVIEW` for a person to
   merge. The same happens if the clean name already exists.

The script never deletes a file with content, never merges folders and never
overwrites anything. It reads only names and sizes, never file contents, so it
does not download online-only files. It is safe to run more than once.

## Usage

Requires Python 3.9+, which comes with the macOS Command Line Tools. If it is
missing, running `python3` once prompts macOS to install it.

```bash
git clone https://github.com/oxygenconservation/onedrive-name-cleanup.git
cd onedrive-name-cleanup
```

**1. Dry run (the default; changes nothing):**

```bash
python3 onedrive_name_cleanup.py "$HOME/Library/CloudStorage/OneDrive-SharedLibraries-OxygenConservation"
```

With no path, it scans every `~/Library/CloudStorage/OneDrive*` folder (including personal OneDrive).
Add `-q` to print only `REVIEW` items, errors and the summary. Every action, including the ones `-q` hides, is in the CSV log.

**2. Read the output.** Check the `REVIEW` lines and spot-check the renames.

**3. Apply:**

```bash
python3 onedrive_name_cleanup.py --apply "$HOME/Library/CloudStorage/OneDrive-SharedLibraries-OxygenConservation"
```

**4. Merge any `REVIEW` items by hand,** then let OneDrive finish syncing.

Every run writes a CSV of each action to `~/Library/Logs/onedrive-name-cleanup/`.

### Options

| Flag | Purpose |
|---|---|
| `--apply` | Make the changes (default is a dry run) |
| `-q`, `--quiet` | Print only review items, errors and the summary |
| `--machine-pattern REGEX` | Machine-name suffix pattern (default `OC-[A-Z]{2,4}-\d+`) |
| `--log-dir DIR` | Where to write the CSV log |

### Using this outside Oxygen Conservation

OneDrive tags conflict copies with the computer name. The default
`--machine-pattern` matches our naming (`OC-MBK-12345`). If your Macs are named
differently, pass a regex for your names, e.g. `--machine-pattern 'MacBook-[A-Za-z0-9-]+'`.
Without a match, the script still fixes illegal names and deletes empty folders,
but surviving conflict copies keep the machine name in their new name
rather than getting one clean name.

Exit codes: `0` success, `1` some actions failed (see the log), `2` bad arguments.

## Rollout notes

- **Fix it once, on one Mac, then let it sync.** The machine suffixes show the
  same folders are conflicting across several laptops. Run `--apply` on one
  machine and let OneDrive settle. Then dry-run on the other machines: they
  should show nothing or very little. Running it on every machine at once risks
  a new round of conflicts.
- Check the result in SharePoint on the web, not just in Finder.
- **Prevention:** tell people not to use `/` in Finder names (it becomes a
  colon on disk). A scheduled dry run with `-q` can catch new cases early.

## Tests

```bash
python3 -m unittest -v
```

The tests build a folder tree based on the real incident (empty conflict copies,
junk-only folders, nested illegal names, copies with content, name clashes,
file conflict copies) and check dry run, apply and repeat runs.

## Licence

MIT. See [LICENSE](LICENSE). Provided as is: run a dry run and read the output
before using `--apply`.
