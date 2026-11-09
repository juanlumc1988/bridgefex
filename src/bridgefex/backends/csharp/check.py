# SPDX-License-Identifier: Apache-2.0
"""The check of the generated C# project before it is written: the compiler builds it."""

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

ENV_SKIP = "BRIDGEFEX_SKIP_DOTNET_CHECK"
"""Set to 1 to skip the check."""


@functools.cache
def dotnet_sdk() -> str | None:
    """The ``dotnet`` command, if the machine has an SDK (a machine with only the runtime has the
    command, but cannot build)."""
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        return None
    try:
        completed = subprocess.run(
            [dotnet, "--list-sdks"], capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return dotnet if completed.returncode == 0 and completed.stdout.strip() else None


def check(plan: ModulePlan, files: Mapping[str, str], root: Path) -> list[Diagnostic]:
    """Build the generated project with ``dotnet build`` (the compiler of .NET) in a
    scratch directory. Without the SDK the generated code is not checked."""
    dotnet = dotnet_sdk()
    if dotnet is None or os.environ.get(ENV_SKIP) == "1":
        return []
    csharp_files = {path: text for path, text in files.items() if path.startswith("csharp/")}
    write_files(csharp_files, root)
    project = next((root / path for path in csharp_files if path.endswith(".csproj")), None)
    if project is None:
        return []
    environment = {
        **os.environ,
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "MSBUILDDISABLENODEREUSE": "1",
    }
    command = [
        dotnet,
        "build",
        str(project),
        "-c",
        "Release",
        "-nologo",
        "-v",
        "q",
        "-p:TreatWarningsAsErrors=true",
        "-p:UseSharedCompilation=false",
        "-o",
        str(root / "bin"),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=600, check=False, env=environment
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [
            Diagnostic(
                f"cannot run dotnet to build the generated C# code ({error}); "
                "use --no-verify to skip the verification"
            )
        ]
    if completed.returncode == 0:
        return []
    lines = [
        line.strip()
        for line in (completed.stdout + completed.stderr).splitlines()
        if ": error " in line
    ]
    unique = list(dict.fromkeys(lines))[:10]
    if unique:
        detail = "; ".join(unique)
    else:
        tail = " ".join((completed.stdout + completed.stderr).split())[-300:]
        detail = f"exit status {completed.returncode}: {tail}"
    return [Diagnostic(f"the generated C# code does not compile: {detail}")]
