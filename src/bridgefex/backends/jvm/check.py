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
from .flavor import FFM, Flavor

ENV_SKIP = "BRIDGEFEX_SKIP_JAVA_CHECK"
"""Set to 1 to skip the check."""

MIN_RELEASE = 22
"""The first Java with the final foreign function API."""


@functools.cache
def javac(minimum: int = MIN_RELEASE) -> str | None:
    """The ``javac`` command, if the machine has a JDK that can compile the bindings (Java 22
    or later for the FFM flavor; Java 9 or later, which has ``--release 8``, for the JNI one)."""
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
        if completed.returncode == 0 and match and int(match.group(1)) >= minimum:
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
    for name in ("java", "java.exe"):
        sibling = Path(javac_command).with_name(name)
        if sibling.exists():
            return str(sibling)
    return "java"


def check(
    plan: ModulePlan, files: Mapping[str, str], root: Path, flavor: Flavor = FFM
) -> list[Diagnostic]:
    """Compile the generated sources with ``javac`` (warnings are errors) in a scratch
    directory. Without a JDK that can (Java 22 or later for the FFM flavor, 9 for the JNI one)
    the generated code is not checked."""
    command = javac(MIN_RELEASE if flavor.modern else 9)
    if command is None or os.environ.get(ENV_SKIP) == "1":
        return []
    java_files = {
        path: text for path, text in files.items() if path.startswith(f"{flavor.directory}/")
    }
    write_files(java_files, root)
    sources = [str(root / path) for path in java_files if path.endswith(".java")]
    if not sources:
        return []
    out = root / "classes"
    out.mkdir(parents=True, exist_ok=True)
    # The sources go in a file of arguments: a big module has more of them than a command line of
    # Windows can hold.
    listing = root / "sources.txt"
    listing.write_text(
        "\n".join(f'"{Path(source).as_posix()}"' for source in sources) + "\n", encoding="utf-8"
    )
    arguments = [
        command,
        "--release",
        str(flavor.release),
        # The old releases are deprecated in the newest javac, which says so.
        "-Xlint:all" if flavor.modern else "-Xlint:all,-options",
        "-Werror",
        "-encoding",
        "UTF-8",
        "-d",
        str(out),
        f"@{listing}",
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
