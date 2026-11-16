# SPDX-License-Identifier: Apache-2.0
"""The Rust bindings on 32-bit ARM, the target of embedded Linux: the library of a case is
cross-built with GCC for armhf, the check is built for ``armv7-unknown-linux-gnueabihf`` and run
under ``qemu-arm``. What is 32 bits there (``usize``, ``size_t``, the alignment of 64-bit values
in a struct) is what the bindings must get right and a 64-bit machine cannot show.

It needs the cross compiler (``g++-14-arm-linux-gnueabihf``), ``qemu-user`` and the Rust target
(``rustup target add armv7-unknown-linux-gnueabihf``): without them the tests are skipped, and
with ``BRIDGEFEX_REQUIRE_ARMHF=1`` (the CI) they fail.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bridgefex.backends.rust.check import cargo
from tests.conformance import runner_rust
from tests.support.build import build
from tests.support.cases import Case, all_cases
from tests.support.toolchain import Toolchain
from tests.test_conformance import supports

ENV_REQUIRED = "BRIDGEFEX_REQUIRE_ARMHF"
TARGET = "armv7-unknown-linux-gnueabihf"
SYSROOT = Path("/usr/arm-linux-gnueabihf")
CASES = [case for case in all_cases() if "rust" in case.languages]

pytestmark = pytest.mark.usefixtures("libclang_loaded")


def _missing() -> str | None:
    """What the machine lacks to run Rust on ARM, or None if it has everything."""
    if not sys.platform.startswith("linux"):
        return "needs Linux"
    for tool in ("arm-linux-gnueabihf-g++-14", "arm-linux-gnueabihf-gcc-14", "qemu-arm"):
        if shutil.which(tool) is None:
            return f"needs {tool}"
    if not SYSROOT.is_dir():
        return f"needs {SYSROOT}"
    command = cargo()
    if command is None:
        return "needs cargo"
    installed = subprocess.run(
        ["rustc", "--print", "target-libdir", "--target", TARGET],
        capture_output=True,
        text=True,
        check=False,
    )
    libraries = Path(installed.stdout.strip())
    if installed.returncode != 0 or not any(libraries.glob("libcore-*")):
        return f"needs the Rust target {TARGET}"
    return None


@pytest.fixture(autouse=True)
def _armhf() -> None:
    missing = _missing()
    if missing is None:
        return
    if os.environ.get(ENV_REQUIRED) == "1":
        pytest.fail(f"{missing} ({ENV_REQUIRED} is set)")
    pytest.skip(missing)


CROSS = runner_rust.Cross(
    target=TARGET,
    linker="arm-linux-gnueabihf-gcc-14",
    runner=("qemu-arm", "-L", str(SYSROOT)),
)
TOOLCHAIN = Toolchain("gnu", "arm-linux-gnueabihf-gcc-14", "arm-linux-gnueabihf-g++-14")


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def built(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Case, Path, Path]:
    case: Case = request.param
    directory = tmp_path_factory.mktemp(f"armhf_{case.name}")
    library, output = build(case, TOOLCHAIN, directory, glue=False)
    return case, library, output


def test_the_round_trip_on_armhf(built: tuple[Case, Path, Path]) -> None:
    case, library, output = built
    if not case.check_rust.exists():
        pytest.skip(f"the {case.name} case has no Rust check")
    try:
        executable = runner_rust.build_program(
            case, library, output, "check_armhf", case.check_rust, CROSS
        )
    except RuntimeError as error:
        pytest.fail(str(error))
    completed = subprocess.run(
        CROSS.command(executable, library),
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_the_scenarios_on_armhf(built: tuple[Case, Path, Path]) -> None:
    """The conformance scenarios of the case, which every language plays: the sizes of the
    integers, the layout of the structs and the strings, on a 32-bit machine."""
    case, library, output = built
    if not supports("rust", case) or not case.scenarios.exists():
        pytest.skip(f"the Rust backend does not play scenarios of the {case.name} case")
    completed = runner_rust.run(case, library, output, case.scenarios, CROSS)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "conformance OK (rust)" in completed.stdout
