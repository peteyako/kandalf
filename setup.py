"""The build step that stamps a built kandalf with where it was built from (kandalf/stamp.py):
the release tag on its commit, or the commit, and when. pyproject.toml holds everything else."""

import importlib.util
import json
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


class StampedBuild(build_py):
    def run(self):
        super().run()
        spec = importlib.util.spec_from_file_location("kandalf_stamp", ROOT / "kandalf" / "stamp.py")
        stamp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stamp)
        facts = stamp.describe(ROOT)
        if facts:
            (Path(self.build_lib) / "kandalf" / stamp.BUILD_FILE).write_text(json.dumps(facts), encoding="utf-8")


setup(cmdclass={"build_py": StampedBuild})
