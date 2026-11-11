# SPDX-License-Identifier: Apache-2.0
"""scripts/build_libraries.py, which the Libraries workflow runs for every target: it
builds the shared and the static library of the demo case with the compilers of the host,
links and runs the C program of the case with both, and packages them."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

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


def test_other_cases_are_built_and_run_but_not_packaged(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """The director examples, which need an annotations file and a C++11 parse on Windows."""
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--target",
            "host",
            "--output",
            str(tmp_path / "dist"),
            "--work",
            str(tmp_path / "work"),
            "--cc",
            toolchain.cc,
            "--cxx",
            toolchain.cxx,
            "--check-case",
            "directors11",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    # The shared and the static library of each case, linked into its C program and run.
    assert completed.stdout.count("round trip OK") == 4, completed.stdout  # (and the demo's two)
    assert [path.name.split(".")[0] for path in (tmp_path / "dist").glob("*")] == [
        "bridgefex-demo-host",
        "bridgefex-demo-host",
    ]


def _host_runtime() -> str | None:
    """The .NET runtime identifier of the machine, if it is one that the CI has."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win-x64" if machine in ("amd64", "x86_64") else None
    if sys.platform.startswith("linux"):
        return {"x86_64": "linux-x64", "amd64": "linux-x64", "aarch64": "linux-arm64"}.get(machine)
    return None


@pytest.mark.usefixtures("dotnet")
def test_the_csharp_bindings_are_compiled_for_a_runtime(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """What the armhf job does with ``linux-arm``: the bindings are built for a runtime that
    cannot run them there."""
    runtime = _host_runtime()
    if runtime is None:
        pytest.skip("not a runtime of the CI")
    command = [
        sys.executable,
        str(SCRIPT),
        "--target",
        "host",
        "--output",
        str(tmp_path / "dist"),
        "--work",
        str(tmp_path / "work"),
        "--cc",
        toolchain.cc,
        "--cxx",
        toolchain.cxx,
        "--csharp-runtime",
        runtime,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"-r {runtime}" in completed.stdout
    assert (tmp_path / "work" / "demo" / "csharp-check" / "bin" / "Demo.Bindings.dll").exists()


@pytest.mark.usefixtures("dotnet")
@pytest.mark.parametrize("trim", [False, True], ids=["whole", "trimmed"])
def test_the_csharp_check_of_a_case_is_published_and_run(
    toolchain: Toolchain, tmp_path: Path, trim: bool
) -> None:
    """What the armhf job does with ``--csharp-check`` (there under QEMU, trimmed): the check of
    the case is published self-contained for the runtime and run against the library of the
    case."""
    runtime = _host_runtime()
    if runtime is None:
        pytest.skip("not a runtime of the CI")
    command = [
        sys.executable,
        str(SCRIPT),
        "--target",
        "host",
        "--output",
        str(tmp_path / "dist"),
        "--work",
        str(tmp_path / "work"),
        "--cc",
        toolchain.cc,
        "--cxx",
        toolchain.cxx,
        "--csharp-runtime",
        runtime,
        "--csharp-check",
        "containers",
        *(["--csharp-trim"] if trim else []),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "containers: C# round trip OK" in completed.stdout


def test_a_csharp_check_needs_a_runtime(toolchain: Toolchain, tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "--target",
        "host",
        "--output",
        str(tmp_path / "dist"),
        "--cc",
        toolchain.cc,
        "--cxx",
        toolchain.cxx,
        "--csharp-check",
        "containers",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode != 0
    assert "--csharp-check needs --csharp-runtime" in completed.stdout + completed.stderr


def test_a_trimmed_csharp_check_needs_a_check(toolchain: Toolchain, tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "--target",
        "host",
        "--output",
        str(tmp_path / "dist"),
        "--cc",
        toolchain.cc,
        "--cxx",
        toolchain.cxx,
        "--csharp-runtime",
        "linux-x64",
        "--csharp-trim",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode != 0
    assert "--csharp-trim needs --csharp-check" in completed.stdout + completed.stderr


def test_a_case_that_is_checked_and_run_in_csharp_is_built_once(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """The armhf job asks for ``directors`` as a case to check and as a C# check."""
    runtime = _host_runtime()
    if runtime is None:
        pytest.skip("not a runtime of the CI")
    command = [
        sys.executable,
        str(SCRIPT),
        "--target",
        "host",
        "--output",
        str(tmp_path / "dist"),
        "--work",
        str(tmp_path / "work"),
        "--cc",
        toolchain.cc,
        "--cxx",
        toolchain.cxx,
        "--check-case",
        "containers",
        "--csharp-runtime",
        runtime,
        "--csharp-check",
        "containers",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "containers: C# round trip OK" in completed.stdout
