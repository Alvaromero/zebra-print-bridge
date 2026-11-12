#!/usr/bin/env python3
"""Build dist/zebra_print_bridge-<version>-py3-none-any.whl with setuptools' PEP 517 backend.

No pip or `build` needed: it calls the backend in-process, in a temporary copy of the sources,
so build/ and *.egg-info never land in the repository.
Install it with:  pipx install dist/zebra_print_bridge-*.whl   (or pip install --user)
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def main():
    try:
        from setuptools import build_meta
    except ImportError:
        sys.exit("setuptools is required: sudo apt install python3-setuptools")
    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        shutil.copytree(
            ROOT / "zebra_print_bridge",
            src / "zebra_print_bridge",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, src / name)
        out = Path(tmp) / "out"
        out.mkdir()
        cwd = os.getcwd()
        os.chdir(src)
        try:
            name = build_meta.build_wheel(str(out))
        finally:
            os.chdir(cwd)
        target = DIST / name
        shutil.copy2(out / name, target)
    print(target)


if __name__ == "__main__":
    main()
