# SPDX-License-Identifier: Apache-2.0
"""The generated crates build with the oldest Rust that they declare (``rust-version`` of their
``Cargo.toml``): what a newer compiler accepts, and the crate silently needs, is found here.

It needs that toolchain (``rustup toolchain install 1.70.0``): without it the test is skipped, and
with ``BRIDGEFEX_REQUIRE_RUST_MSRV=1`` (the CI) it fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bridgefex.backends.rust import RUST_VERSION
from bridgefex.backends.rust.check import cargo
from tests.support.cases import all_cases

ENV_REQUIRED = "BRIDGEFEX_REQUIRE_RUST_MSRV"
TOOLCHAIN = f"{RUST_VERSION}.0"
CRATES = sorted(
    (case.expected_dir / "rust" / case.module) for case in all_cases() if "rust" in case.languages
)


def _toolchain_installed() -> bool:
    rustup = shutil.which("rustup")
    if rustup is None or cargo() is None:
        return False
    listed = subprocess.run(
        [rustup, "toolchain", "list"], capture_output=True, text=True, check=False
    )
    return any(line.startswith(TOOLCHAIN + "-") for line in listed.stdout.splitlines())


@pytest.fixture(autouse=True)
def _msrv() -> None:
    if _toolchain_installed():
        return
    if os.environ.get(ENV_REQUIRED) == "1":
        pytest.fail(f"the toolchain {TOOLCHAIN} is required ({ENV_REQUIRED} is set)")
    pytest.skip(f"needs the toolchain {TOOLCHAIN}")


def test_the_crates_are_there() -> None:
    assert len(CRATES) >= 7


@pytest.mark.parametrize("crate", CRATES, ids=lambda path: path.name)
def test_the_crate_builds_with_the_oldest_rust(crate: Path, tmp_path: Path) -> None:
    assert f'rust-version = "{RUST_VERSION}"' in (crate / "Cargo.toml").read_text(encoding="utf-8")
    work = tmp_path / crate.name
    shutil.copytree(crate, work)
    with (work / "Cargo.toml").open("a", encoding="utf-8") as manifest:
        manifest.write("\n[workspace]\n")
    command = cargo()
    assert command is not None
    completed = subprocess.run(
        [command, f"+{TOOLCHAIN}", "check", "--offline", "--quiet"],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "CARGO_TARGET_DIR": str(tmp_path / "target"),
            "RUSTFLAGS": "-D warnings",
            "CARGO_TERM_COLOR": "never",
        },
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
