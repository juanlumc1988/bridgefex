# SPDX-License-Identifier: Apache-2.0
"""scripts/build_libraries.py, which the Libraries workflow runs for every target: it
builds the shared and the static library of the demo case with the compilers of the host,
links and runs the C program of the case with both, and packages them."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from tests.support.toolchain import Toolchain

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_libraries.py"


def test_the_libraries_of_a_case_are_built_run_and_packaged(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "--target",
        "host",
        "--output",
        str(tmp_path / "dist"),
        "--work",
        str(tmp_path / "work"),
        # What the tests use, so that the library is built as the tests build it.
        "--cc",
        toolchain.cc,
        "--cxx",
        toolchain.cxx,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    # Both libraries were linked into the C program of the case and it ran.
    assert completed.stdout.count("demo: C round trip OK") == 2

    msvc = toolchain.kind == "msvc"
    archive = (
        tmp_path / "dist" / ("bridgefex-demo-host.zip" if msvc else "bridgefex-demo-host.tar.gz")
    )
    if msvc:
        with zipfile.ZipFile(archive) as zipped:
            names = set(zipped.namelist())
    else:
        with tarfile.open(archive) as tarred:
            names = set(tarred.getnames())
    root = "bridgefex-demo-host"
    libraries = {"demo.dll", "demo.lib", "demo_static.lib"} if msvc else {"libdemo.so", "libdemo.a"}
    assert names == {
        f"{root}/README.txt",
        *(
            f"{root}/include/{name}"
            for name in (
                "counter_c.h",
                "demo_runtime.h",
                "demo_types.h",
                "geometry_c.h",
                "sleeper_c.h",
            )
        ),
        *(f"{root}/lib/{name}" for name in libraries),
    }
    checksum = (tmp_path / "dist" / f"{archive.name}.sha256").read_text(encoding="utf-8")
    assert checksum == f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"


def test_a_case_that_needs_annotations_is_refused(tmp_path: Path) -> None:
    """The example of the libraries has to build on its own."""
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--target",
            "x",
            "--output",
            str(tmp_path),
            "--case",
            "directors",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "cannot be built as a library on its own" in completed.stderr
