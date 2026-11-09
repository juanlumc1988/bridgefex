# SPDX-License-Identifier: Apache-2.0
"""Plays the conformance scenarios against the C# bindings.

The bindings are generated from the case, compiled together with the runner
(``runner_csharp/Program.cs``, which plays the scenarios by reflection) and the runner
is run with the library of the case. Scenarios are given as JSON: the runner has no
TOML parser, and each ``new`` and ``call`` says the C function it wraps, so that it finds
what to call without knowing how C# names things.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import bridgefex
from bridgefex.backends.csharp.module import build_names, namespace
from tests.conformance import scenarios
from tests.support.cases import Case
from tests.support.plans import plan_of

RUNNER = Path(__file__).parent / "runner_csharp"

_PROJECT = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <LangVersion>12.0</LangVersion>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
    <RootNamespace>BridgefexRunner</RootNamespace>
    <AssemblyName>{name}</AssemblyName>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <InvariantGlobalization>true</InvariantGlobalization>
  </PropertyGroup>
  <ItemGroup>
    <Compile Include="{program}" />
    <ProjectReference Include="{project}" />
  </ItemGroup>
</Project>
"""


def environment() -> dict[str, str]:
    return {
        **os.environ,
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "MSBUILDDISABLENODEREUSE": "1",
    }


def available() -> bool:
    return shutil.which("dotnet") is not None


def build_program(
    case: Case,
    output: Path,
    program: Path,
    name: str,
    *,
    edit: Callable[[dict[str, str]], dict[str, str]] | None = None,
) -> Path:
    """``program`` (a C# file) built with the bindings of ``case`` (``edit`` may change the
    generated files first): ``<output>/csharp-<name>/bin/<name>.dll``. Raises RuntimeError if
    it does not build."""
    work = output / f"csharp-{name}"
    dll = work / "bin" / f"{name}.dll"
    if dll.exists():
        return dll
    options = dataclasses.replace(case.options(), languages=("csharp",), verify=False)
    result = bridgefex.generate(case.headers, options)
    generated = work / "generated"
    files = edit(dict(result.files)) if edit else result.files
    bridgefex.write_files(files, generated)
    # The program is built as a user's would: against the project of the bindings, an
    # assembly of its own (so that what is internal to it is not visible to the program).
    project = next(generated / path for path in files if path.endswith(".csproj"))
    (work / f"{name}.csproj").write_text(
        _PROJECT.format(program=program.as_posix(), project=project.as_posix(), name=name),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "dotnet",
            "build",
            str(work / f"{name}.csproj"),
            "-c",
            "Release",
            "-nologo",
            "-v",
            "q",
            "-p:UseSharedCompilation=false",
            "-p:TreatWarningsAsErrors=true",
            "-o",
            str(work / "bin"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment(),
        timeout=900,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{name} does not build:\n" + completed.stdout + completed.stderr)
    return dll


def execute(
    case: Case, dll: Path, library: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run a built program with the library of ``case``."""
    return subprocess.run(
        ["dotnet", str(dll), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env={**environment(), f"{case.module.upper()}_LIBRARY": str(library)},
        timeout=600,
    )


def _json_safe(value: object) -> object:
    """JSON has no NaN nor infinities: they become ``{ "float": "nan" }``."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _map(case: Case) -> dict[str, object]:
    plan = plan_of(case)
    names = build_names(plan)
    root = namespace(plan)
    enums: dict[str, str] = {}
    for key, enum in scenarios.enum_types(plan).items():
        # The full name that reflection gives: a nested type is separated by '+'.
        full = names.enums[enum.c_type].removeprefix("global::")
        if key.count(".") > 1:
            head, _, last = full.rpartition(".")
            full = f"{head}+{last}"
        enums[key] = full
    structs = {
        key: names.structs[struct.c_type].removeprefix("global::")
        for key, struct in scenarios.struct_types(plan).items()
    }
    return {"enums": enums, "structs": structs, "root": root}


def run(case: Case, library: Path, output: Path, path: Path) -> subprocess.CompletedProcess[str]:
    """Play the scenarios of ``path`` against the C# bindings of ``case``."""
    try:
        dll = build_program(case, output, RUNNER / "Program.cs", "Runner")
    except RuntimeError as error:
        return subprocess.CompletedProcess([], 3, "", str(error))
    plan = plan_of(case)
    data = {
        "scenarios": scenarios.resolve(scenarios.load(path), plan),
        "map": _map(case),
    }
    work = dll.parent.parent
    # Each file of scenarios has its own data: the tests run in parallel with others.
    target = work / f"data-{path.parent.name}-{path.stem}.json"
    target.write_text(json.dumps(_json_safe(data)), encoding="utf-8")
    return execute(case, dll, library, str(target))
