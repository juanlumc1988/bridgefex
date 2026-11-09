# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated C# bindings.

For each golden case with a check_csharp.cs, the library of the case is built (the
generated C layer and the C++ implementation), the bindings are generated and compiled with
the check, and the check is run in its own process. The tests of the conformance suite play
the same scenarios in every language; these ones check what is particular of C#.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conformance import runner_csharp
from tests.support.build import build
from tests.support.cases import Case, all_cases
from tests.support.toolchain import Toolchain

CASES = [case for case in all_cases() if case.check_csharp.exists()]

pytestmark = [
    pytest.mark.usefixtures("libclang_loaded"),
    pytest.mark.skipif(not runner_csharp.available(), reason="needs the .NET SDK"),
]

Built = tuple[Case, Path, Path]


def test_there_are_checks() -> None:
    assert {case.name for case in CASES} >= {"demo", "edge", "inherit", "ownership", "strings"}


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def built(
    request: pytest.FixtureRequest,
    toolchain: Toolchain,
    tmp_path_factory: pytest.TempPathFactory,
) -> Built:
    case: Case = request.param
    directory = tmp_path_factory.mktemp(f"csharp_{case.name}")
    library, output = build(case, toolchain, directory)
    return case, library, output


def test_csharp_round_trip(built: Built) -> None:
    case, library, output = built
    dll = runner_csharp.build_program(case, output, case.check_csharp, "Check")
    completed = runner_csharp.execute(case, dll, library)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


@pytest.fixture(scope="module")
def demo(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Case, Path, Path, Path]:
    """The demo case, built, with its check compiled: (case, library, output, check.dll)."""
    case = next(case for case in CASES if case.name == "demo")
    directory = tmp_path_factory.mktemp("csharp_demo_extra")
    library, output = build(case, toolchain, directory)
    dll = runner_csharp.build_program(case, output, case.check_csharp, "Check")
    return case, library, output, dll


def test_the_library_next_to_the_program_is_used(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """Without the environment variable, the default name of the library is looked up next
    to the program."""
    case, library, _output, dll = demo
    program = tmp_path / "program"
    shutil.copytree(dll.parent, program)
    default = f"lib{case.module}.so"
    if library.suffix != ".so":
        pytest.skip("the default name is checked on Linux")
    shutil.copy(library, program / default)
    environment = {**runner_csharp.environment()}
    environment.pop(f"{case.module.upper()}_LIBRARY", None)
    completed = subprocess.run(
        ["dotnet", str(program / dll.name)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_a_missing_library_is_reported(demo: tuple[Case, Path, Path, Path], tmp_path: Path) -> None:
    case, _library, _output, dll = demo
    completed = runner_csharp.execute(case, dll, tmp_path / "nothing.so")
    assert completed.returncode != 0
    assert "nothing.so" in completed.stdout + completed.stderr


def test_a_stale_library_is_refused(demo: tuple[Case, Path, Path, Path], tmp_path: Path) -> None:
    """Bindings whose fingerprint is not the one of the library refuse to run."""
    case, library, _output, _dll = demo

    def stale(files: dict[str, str]) -> dict[str, str]:
        marker = 'Fingerprint = "'
        return {
            path: content.replace(marker, marker + "0", 1)
            if path.endswith("Runtime.cs")
            else content
            for path, content in files.items()
        }

    dll = runner_csharp.build_program(case, tmp_path, case.check_csharp, "Check", edit=stale)
    completed = runner_csharp.execute(case, dll, library)
    assert completed.returncode != 0
    assert "does not match these bindings" in completed.stdout + completed.stderr
