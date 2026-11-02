# SPDX-License-Identifier: Apache-2.0
"""The check of the generated Python package before it is written."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from ...errors import Diagnostic
from ...plan import ModulePlan
from ...rendering import write_files

_IMPORT_ALL = """\
import importlib, sys
sys.path.insert(0, sys.argv[1])
for name in sys.argv[2:]:
    importlib.import_module(name)
"""


def check(plan: ModulePlan, files: Mapping[str, str], root: Path) -> list[Diagnostic]:
    """Compile the generated Python files, then import them in a new interpreter
    (importing does not load the library). Writes them below ``root``."""
    python_files = dict(files)
    errors: list[Diagnostic] = []
    for path, content in python_files.items():
        try:
            compile(content, path, "exec", dont_inherit=True)
        except SyntaxError as error:
            errors.append(
                Diagnostic(
                    f"the generated Python code does not compile: {path}:{error.lineno}: "
                    f"{error.msg}"
                )
            )
    # Without an interpreter to run (bridgefex embedded, or frozen into an
    # executable), the import is not checked.
    if errors or not sys.executable or getattr(sys, "frozen", False):
        return errors
    write_files(python_files, root)
    modules = [
        plan.name,
        f"{plan.name}._types",
        *(f"{plan.name}.{header.stem}" for header in plan.headers),
    ]
    command = [sys.executable, "-I", "-B", "-X", "dev", "-W", "error", "-c", _IMPORT_ALL]
    try:
        completed = subprocess.run(
            [*command, str(root / "python"), *modules],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [
            Diagnostic(
                f"cannot run {sys.executable} to import the generated Python code ({error}); "
                "use --no-verify to skip the verification"
            )
        ]
    if completed.returncode != 0:
        lines = completed.stderr.strip().splitlines()
        errors.append(
            Diagnostic(
                "the generated Python code cannot be imported: "
                + (lines[-1] if lines else f"exit status {completed.returncode}")
            )
        )
    return errors
