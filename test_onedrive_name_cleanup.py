"""Run with:  python3 -m unittest -v"""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

import onedrive_name_cleanup as m


def touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class CleanNameTests(unittest.TestCase):
    def test_spaced_colon(self):
        self.assertEqual(m.clean_name("Visuals : renders"), "Visuals - renders")

    def test_tight_colon(self):
        self.assertEqual(m.clean_name("Ecotourism:other EPC"), "Ecotourism-other EPC")

    def test_trailing_space_colon(self):
        self.assertEqual(m.clean_name("Phase: 2"), "Phase - 2")

    def test_other_chars(self):
        self.assertEqual(m.clean_name('a*b?c"d<e>f|g'), "abc'def-g")
        self.assertEqual(m.clean_name("WaterVole?.AVI"), "WaterVole.AVI")
        self.assertEqual(m.clean_name("**OLD** Tracker.xlsx"), "OLD Tracker.xlsx")


class FamilyTests(unittest.TestCase):
    def check(self, name, expected_stripped):
        stripped, core = m.family_parts(name, m.DEFAULT_MACHINE_PATTERN)
        self.assertEqual(stripped, expected_stripped)

    def test_machine_suffixes(self):
        self.check("Visuals : renders-OC-MBK-10003 4", "Visuals : renders")
        self.check("Visuals : renders 2-OC-MBK-10001-OC-MBK-10002 4", "Visuals : renders")
        self.check("Visuals : renders-OC-MBK-10001-OC-MBK-10002-OC-MBK-10004 3", "Visuals : renders")

    def test_timestamp_conflict_marker(self):
        stripped, core = m.family_parts("Phase One - 25:26 2 (2026-11-08 20:21:58 +0000) 2", m.DEFAULT_MACHINE_PATTERN)
        self.assertEqual(core, "Phase One - 25:26")

    def test_plain_number_kept_in_stripped(self):
        self.check("Visuals : renders 5", "Visuals : renders 5")


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Docs"
        mp = self.root / "Property" / "MASTER PLANNING"

        # Real-world shape from the incident: many empty conflict copies,
        # one copy holding the actual renders.
        for n in ["Visuals : renders 7", "Visuals : renders 8",
                  "Visuals : renders-OC-MBK-10003 4",
                  "Visuals : renders 2-OC-MBK-10001-OC-MBK-10002 4",
                  "Visuals : renders-OC-MBK-10001-OC-MBK-10002-OC-MBK-10004 3"]:
            (mp / n).mkdir(parents=True)
        touch(mp / "Visuals : renders 8" / ".DS_Store")              # junk only
        (mp / "Visuals : renders 7" / "sub" / "deeper").mkdir(parents=True)  # empty tree
        touch(mp / "Visuals : renders 5" / "Site A" / "Cabin.png")
        touch(mp / "Visuals : renders 5" / "Site A" / "obr" / "site-1.jpg")

        # Nested illegal names inside a folder that itself needs renaming.
        touch(mp / "Data Room" / "Visuals:Renders 5" / "Barn: plan.pdf")
        (mp / "Data Room" / "Visuals:Renders-OC-MBK-10004 3").mkdir(parents=True)

        # Two copies with content -> second is flagged for review.
        touch(mp / "EPCs" / "Ecotourism:other EPC 5" / "a.pdf")
        touch(mp / "EPCs" / "Ecotourism:other EPC 6" / "b.pdf")
        touch(mp / "EPCs" / "Ecotourism:other EPC 6" / "c.pdf")

        # A lone name with a meaningful trailing number keeps it.
        touch(mp / "Phase: 2" / "notes.txt")

        # A clean sibling already exists -> no overwrite.
        touch(mp / "Clash" / "Report - final" / "keep.txt")
        touch(mp / "Clash" / "Report : final" / "other.txt")

        # File conflict copy: machine name goes after the extension.
        touch(mp / "Photos" / "deer larder:steading.jpg", b"abc")
        touch(mp / "Photos" / "deer larder:steading.jpg-OC-MBK-10005", b"abc")

        # Different files that merely differ by a number are not copies.
        touch(mp / "Cam" / "23:11:25 Otter 2.AVI", b"aaaa")
        touch(mp / "Cam" / "23:11:25 Otter.AVI", b"bb")

        # Timestamped conflict copy with content.
        touch(mp / "Hedges" / "Phase One - 25:26 2 (2026-11-08 20:21:58 +0000) 2" / "map.png")
        (mp / "Hedges" / "Phase One - 25:26 3").mkdir(parents=True)

        # Normal content must be untouched.
        touch(mp / "Normal folder" / "file.txt")

        self.logdir = Path(self.tmp.name) / "logs"

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main([str(self.root), "--log-dir", str(self.logdir)] + list(extra))
        return rc, buf.getvalue()

    def snapshot(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))

    def test_dry_run_changes_nothing(self):
        before = self.snapshot()
        rc, out = self.run_tool()
        self.assertEqual(rc, 0)
        self.assertEqual(before, self.snapshot())
        self.assertIn("Re-run with --apply", out)

    def test_apply(self):
        rc, out = self.run_tool("--apply")
        self.assertEqual(rc, 0, out)
        mp = self.root / "Property" / "MASTER PLANNING"
        names = sorted(p.name for p in mp.iterdir())

        # No illegal names left anywhere.
        for p in self.root.rglob("*"):
            self.assertFalse(m.has_illegal(p.name), p)

        # Empty conflict copies gone; content survives under the clean name.
        self.assertIn("Visuals - renders", names)
        self.assertEqual([n for n in names if n.startswith("Visuals")], ["Visuals - renders"])
        self.assertTrue((mp / "Visuals - renders" / "Site A" / "Cabin.png").exists())
        self.assertTrue((mp / "Visuals - renders" / "Site A" / "obr" / "site-1.jpg").exists())

        # Nested renames.
        self.assertTrue((mp / "Data Room" / "Visuals-Renders" / "Barn - plan.pdf").exists())
        self.assertEqual(sorted(p.name for p in (mp / "Data Room").iterdir()), ["Visuals-Renders"])

        # Two copies with content: fullest gets the clean name, other flagged.
        epc = sorted(p.name for p in (mp / "EPCs").iterdir())
        self.assertEqual(epc, ["Ecotourism-other EPC", "Ecotourism-other EPC (2)"])
        self.assertTrue((mp / "EPCs" / "Ecotourism-other EPC" / "c.pdf").exists())
        self.assertIn("REVIEW", out)

        self.assertTrue((mp / "Phase - 2" / "notes.txt").exists())

        clash = sorted(p.name for p in (mp / "Clash").iterdir())
        self.assertEqual(clash, ["Report - final", "Report - final (2)"])
        self.assertTrue((mp / "Clash" / "Report - final" / "keep.txt").exists())

        photos = sorted(p.name for p in (mp / "Photos").iterdir())
        self.assertEqual(photos, ["deer larder-steading (2).jpg", "deer larder-steading.jpg"])
        self.assertIn("same size, likely duplicates", out)

        self.assertEqual(sorted(p.name for p in (mp / "Cam").iterdir()),
                         ["23-11-25 Otter 2.AVI", "23-11-25 Otter.AVI"])
        self.assertEqual([p.name for p in (mp / "Hedges").iterdir()], ["Phase One - 25-26"])

        self.assertTrue((mp / "Normal folder" / "file.txt").exists())

        # Log written.
        logs = list(self.logdir.glob("*-apply.csv"))
        self.assertEqual(len(logs), 1)

    def test_idempotent(self):
        self.run_tool("--apply")
        before = self.snapshot()
        rc, out = self.run_tool("--apply")
        self.assertEqual(rc, 0)
        self.assertEqual(before, self.snapshot())
        self.assertIn("0 empty folders deleted, 0 items renamed", out)


if __name__ == "__main__":
    unittest.main()
