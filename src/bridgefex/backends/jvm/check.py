# SPDX-License-Identifier: Apache-2.0
"""The check of the generated Java project before it is written: the compiler builds it."""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from ...errors import Diagnostic
from ...plan import ModulePlan
from ...rendering import write_files

ENV_SKIP = "BRIDGEFEX_SKIP_JAVA_CHECK"
"""Set to 1 to skip the check."""

MIN_RELEASE = 22
"""The first Java with the final foreign function API."""


@functools.cache
def javac() -> str | None:
    """The ``javac`` command, if the machine has a JDK that can compile the bindings (Java 22
    or later)."""
    for candidate in _candidates():
        try:
            completed = subprocess.run(
                [candidate, "-version"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.search(r"javac (\d+)", completed.stdout + completed.stderr)
        if completed.returncode == 0 and match and int(match.group(1)) >= MIN_RELEASE:
            return candidate
    return None


def _candidates() -> list[str]:
    found: list[str] = []
    home = os.environ.get("JAVA_HOME")
    if home:
        found.append(str(Path(home) / "bin" / "javac"))
    found.extend(
        str(path / "bin" / "javac")
        for path in sorted(Path("/usr/lib/jvm").glob("*"), reverse=True)
        if (path / "bin" / "javac").exists()
    )
    which = shutil.which("javac")
    if which:
        found.append(which)
    return list(dict.fromkeys(found))


def java_runtime(javac_command: str) -> str:
    """The ``java`` command of the same JDK as ``javac_command``."""
    sibling = Path(javac_command).with_name("java")
    return str(sibling) if sibling.exists() else "java"


def check(plan: ModulePlan, files: Mapping[str, str], root: Path) -> list[Diagnostic]:
    """Compile the generated sources with ``javac`` (warnings are errors) in a scratch
    directory. Without a JDK of Java 22 or later the generated code is not checked."""
    command = javac()
    if command is None or os.environ.get(ENV_SKIP) == "1":
        return []
    java_files = {path: text for path, text in files.items() if path.startswith("jvm/")}
    write_files(java_files, root)
    sources = [str(root / path) for path in java_files if path.endswith(".java")]
    if not sources:
        return []
    out = root / "classes"
    out.mkdir(parents=True, exist_ok=True)
    arguments = [
        command,
        "--release",
        str(MIN_RELEASE),
        "-Xlint:all",
        "-Werror",
        "-encoding",
        "UTF-8",
        "-d",
        str(out),
        *sources,
    ]
    try:
        completed = subprocess.run(
            arguments, capture_output=True, text=True, timeout=600, check=False
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [
            Diagnostic(
                f"cannot run javac to compile the generated Java code ({error}); "
                "use --no-verify to skip the verification"
            )
        ]
    if completed.returncode == 0:
        return []
    lines = [
        line.strip()
        for line in (completed.stdout + completed.stderr).splitlines()
        if ": error:" in line
    ]
    unique = list(dict.fromkeys(lines))[:10]
    if unique:
        detail = "; ".join(unique)
    else:
        tail = " ".join((completed.stdout + completed.stderr).split())[-300:]
        detail = f"exit status {completed.returncode}: {tail}"
    return [Diagnostic(f"the generated Java code does not compile: {detail}")]
