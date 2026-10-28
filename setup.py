"""Setuptools hook for the pinned, headless Core resources."""
from pathlib import Path
import sys

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
# PEP 517 runs setup.py through exec and need not put the source directory on
# sys.path. Resolve the local build helper explicitly in every backend mode.
sys.path.insert(0, str(ROOT))
from build_support import copy_resources, source_inventory, validate_release


class BuildHeadless(build_py):
    def run(self):
        super().run()
        copy_resources(ROOT, Path(self.build_lib) / "deploom_mcp")


class SourceHeadless(sdist):
    def run(self):
        validate_release(ROOT)
        super().run()

    def make_release_tree(self, base_dir, files):
        # A Git submodule is not a setuptools package. Add its explicit public
        # inventory, never a recursive glob that could capture local files.
        super().make_release_tree(base_dir, source_inventory(ROOT, files))


setup(cmdclass={"build_py": BuildHeadless, "sdist": SourceHeadless})
