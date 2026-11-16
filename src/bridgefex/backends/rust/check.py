# SPDX-License-Identifier: Apache-2.0
"""The check of the generated Rust crate before it is written: the compiler checks it."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from ...errors import Diagnostic
from ...plan import ModulePlan
from ...rendering import write_files

ENV_SKIP = "BRIDGEFEX_SKIP_RUST_CHECK"
"""Set to 1 to skip the check."""


@functools.cache
def cargo() -> str | None:
    """The ``cargo`` command, if the machine has one that works."""
    command = shutil.which("cargo") or shutil.which(
        "cargo", path=str(Path.home() / ".cargo" / "bin")
    )
    if command is None:
        return None
    try:
        completed = subprocess.run(
            [command, "--version"], capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return command if completed.returncode == 0 else None


def check(plan: ModulePlan, files: Mapping[str, str], root: Path) -> list[Diagnostic]:
    """Check the generated crate with ``cargo check`` (warnings are errors) in a scratch
    directory. Without cargo the generated code is not checked."""
    command = cargo()
    if command is None or os.environ.get(ENV_SKIP) == "1":
        return []
    rust_files = {path: text for path, text in files.items() if path.startswith("rust/")}
    write_files(rust_files, root)
    manifest = next((root / path for path in rust_files if path.endswith("Cargo.toml")), None)
    if manifest is None:
        return []
    environment = {
        **os.environ,
        "RUSTFLAGS": "-D warnings",
        "CARGO_TARGET_DIR": str(root / "target"),
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TERM_COLOR": "never",
    }
    try:
        completed = subprocess.run(
            [command, "check", "--quiet", "--offline", "--manifest-path", str(manifest)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [
            Diagnostic(
                f"cannot run cargo to check the generated Rust code ({error}); "
                "use --no-verify to skip the verification"
            )
        ]
    if completed.returncode == 0:
        return []
    lines = [
        line.strip()
        for line in (completed.stdout + completed.stderr).splitlines()
        if line.startswith("error")
    ]
    unique = list(dict.fromkeys(lines))[:10]
    if unique:
        detail = "; ".join(unique)
    else:
        tail = " ".join((completed.stdout + completed.stderr).split())[-300:]
        detail = f"exit status {completed.returncode}: {tail}"
    return [Diagnostic(f"the generated Rust code does not compile: {detail}")]
