"""Tests for stamp.py, which says where a kandalf was built from, and setup.py's build step that
writes it into the package."""

import datetime
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kandalf import stamp

ROOT = Path(__file__).resolve().parent
AT = datetime.datetime(2026, 11, 21, 12, 40, tzinfo=datetime.timezone(datetime.timedelta(hours=-8)))


def git(path, *args):
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


class Describe(unittest.TestCase):
    def setUp(self):
        self.src = Path(tempfile.mkdtemp()).resolve()
        git(self.src, "init", "-q", "-b", "dev")
        (self.src / "a.txt").write_text("a")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-qm", "init")

    def test_a_commit_with_no_tag_is_a_dev_build_stamped_with_when_in_utc(self):
        facts = stamp.describe(self.src, now=AT)
        self.assertEqual((facts["tag"], facts["dirty"], facts["built"]), (None, False, "2026-11-21T20:40:00+00:00"))
        self.assertRegex(facts["commit"], r"^[0-9a-f]{7,}$")

    def test_a_tagged_commit_names_its_tag_unless_files_are_not_committed(self):
        git(self.src, "tag", "-a", "v1.2.3", "-m", "kandalf 1.2.3")
        self.assertEqual(stamp.describe(self.src, now=AT)["tag"], "v1.2.3")
        (self.src / "a.txt").write_text("changed")
        facts = stamp.describe(self.src, now=AT)
        self.assertEqual((facts["tag"], facts["dirty"]), (None, True))
        (self.src / "a.txt").write_text("a")
        (self.src / "new.txt").write_text("not tracked")  # an untracked file isn't part of the build
        self.assertEqual(stamp.describe(self.src, now=AT)["tag"], "v1.2.3")

    def test_a_folder_that_is_not_a_checkout_has_nothing_to_say(self):
        self.assertIsNone(stamp.describe(Path(tempfile.mkdtemp())))


class Info(unittest.TestCase):
    def test_an_installed_copy_reads_what_its_build_wrote(self):
        package = Path(tempfile.mkdtemp()) / "kandalf"
        package.mkdir()
        (package / stamp.BUILD_FILE).write_text(json.dumps({"tag": "v1.2.3", "commit": "abc1234", "dirty": False, "built": "x"}))
        self.assertEqual(stamp.info(package, "1.2.3"), {"version": "1.2.3", "tag": "v1.2.3", "commit": "abc1234", "dirty": False, "built": "x"})

    def test_a_copy_with_no_build_file_outside_a_checkout_knows_only_its_version(self):
        package = Path(tempfile.mkdtemp()) / "kandalf"
        package.mkdir()
        self.assertEqual(stamp.info(package, "1.2.3"), {"version": "1.2.3"})

    def test_a_copy_run_from_its_checkout_asks_git(self):
        facts = stamp.info(ROOT / "kandalf", "1.2.3")
        self.assertEqual(facts["version"], "1.2.3")
        self.assertIn("commit", facts)


@unittest.skipUnless(importlib.util.find_spec("setuptools"), "setuptools isn't installed")
class Build(unittest.TestCase):
    def test_the_build_writes_where_it_was_built_from_into_the_package(self):
        out = Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, "setup.py", "-q", "build_py", "--build-lib", str(out)], cwd=ROOT, check=True, capture_output=True)
        facts = json.loads((out / "kandalf" / stamp.BUILD_FILE).read_text())
        self.assertEqual(set(facts), {"tag", "commit", "dirty", "built"})


if __name__ == "__main__":
    unittest.main()
