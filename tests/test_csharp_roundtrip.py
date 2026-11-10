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

import bridgefex
from tests.conformance import runner_csharp
from tests.support.build import build
from tests.support.cases import Case, all_cases, load_case
from tests.support.toolchain import Toolchain

# The golden cases that have a check, and the cases of the C# bindings alone (tests/csharp_cases).
EXTRA_DIR = Path(__file__).parent / "csharp_cases"
CASES = [
    case
    for case in [
        *all_cases(),
        *(load_case(path.parent) for path in sorted(EXTRA_DIR.glob("*/case.toml"))),
    ]
    if case.check_csharp.exists()
]

pytestmark = pytest.mark.usefixtures("libclang_loaded", "dotnet")

Built = tuple[Case, Path, Path]


def test_there_are_checks() -> None:
    assert {case.name for case in CASES} >= {
        "demo",
        "edge",
        "inherit",
        "ownership",
        "strings",
        "lifetime",
    }


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
    """Without the environment variable, the default name of the library (``lib<module>.so``,
    ``<module>.dll`` or ``lib<module>.dylib``, which is how the toolchain names it) is looked
    up next to the program."""
    case, library, _output, dll = demo
    program = tmp_path / "program"
    shutil.copytree(dll.parent, program)
    shutil.copy(library, program / library.name)
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
    output = completed.stdout + completed.stderr
    assert "DllNotFoundException" in output
    assert "nothing.so" in output
    assert "DEMO_LIBRARY" in output


def test_a_library_that_is_not_found_says_how_to_give_it(
    demo: tuple[Case, Path, Path, Path],
) -> None:
    case, _library, _output, dll = demo
    environment = runner_csharp.environment()
    environment.pop(f"{case.module.upper()}_LIBRARY", None)
    completed = subprocess.run(
        ["dotnet", str(dll)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=600,
        cwd=dll.parent.parent,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "DllNotFoundException" in output
    assert "set the environment variable DEMO_LIBRARY" in output


def test_a_failure_to_load_the_library_is_not_for_ever(
    demo: tuple[Case, Path, Path, Path], tmp_path: Path
) -> None:
    """The next call tries again: the variable can be set after a first failure."""
    case, library, output, _dll = demo
    program = tmp_path / "Retry.cs"
    program.write_text(
        "try { Demo.Counter.Functions.MultiplyInt32Int32(1, 2); "
        'throw new Exception("no error"); }\n'
        "catch (DllNotFoundException) { }\n"
        'Environment.SetEnvironmentVariable("DEMO_LIBRARY", args[0]);\n'
        'if (Demo.Counter.Functions.MultiplyInt32Int32(3, 4) != 12) throw new Exception("wrong");\n'
        'Console.WriteLine("retry OK");\n',
        encoding="utf-8",
    )
    dll = runner_csharp.build_program(case, output, program, "Retry")
    environment = runner_csharp.environment()
    environment.pop("DEMO_LIBRARY", None)
    completed = subprocess.run(
        ["dotnet", str(dll), str(library)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "retry OK" in completed.stdout


def test_a_callback_given_before_the_library_is_found_is_dropped_and_the_next_call_works(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    """The first callback starts the callbacks of the module, which loads the library: if that
    fails, the call fails with the error of the library, nothing stays registered, and the next
    call tries again."""
    case = next(case for case in CASES if case.name == "callbacks_edge")
    directory = tmp_path_factory.mktemp("csharp_callbacks_retry")
    library, output = build(case, toolchain, directory)
    program = tmp_path / "CallbackRetry.cs"
    program.write_text(
        "bool called = false;\n"
        'try { Cbk.Cb.Functions.Run(() => called = true, "ok"); '
        'throw new Exception("no error"); }\n'
        "catch (DllNotFoundException) { }\n"
        "if (called || Cbk.BridgefexCallbacks.Registered != 0) "
        'throw new Exception("the callback stayed: " + Cbk.BridgefexCallbacks.Registered);\n'
        'Environment.SetEnvironmentVariable("CBK_LIBRARY", args[0]);\n'
        'Cbk.Cb.Functions.Run(() => called = true, "ok");\n'
        'if (!called || Cbk.BridgefexCallbacks.Registered != 0) throw new Exception("wrong");\n'
        'Console.WriteLine("retry OK");\n',
        encoding="utf-8",
    )
    dll = runner_csharp.build_program(case, output, program, "CallbackRetry")
    environment = runner_csharp.environment()
    environment.pop("CBK_LIBRARY", None)
    completed = subprocess.run(
        ["dotnet", str(dll), str(library)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "retry OK" in completed.stdout


def test_the_first_callback_given_while_the_process_exits_is_refused(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory, tmp_path: Path
) -> None:
    """The bindings disable the callbacks on ProcessExit, which they subscribe when the module
    starts: a handler of the user that runs later and gives the first callback (that C++ keeps in
    a global, and a static destructor calls) must find them disabled, not be accepted."""
    case = next(case for case in CASES if case.name == "callbacks")
    directory = tmp_path_factory.mktemp("csharp_callbacks_exit")
    library, output = build(case, toolchain, directory)
    program = tmp_path / "CallbackExit.cs"
    program.write_text(
        "using Cbx.Events;\nusing F = Cbx.Events.Functions;\n"
        'F.Log(Level.Info, "starts the module, without a callback");\n'
        "AppDomain.CurrentDomain.ProcessExit += (_, _) =>\n"
        "{\n"
        "    try\n"
        "    {\n"
        '        F.SetLogHandler((level, text) => { if (text == "exit") '
        'Environment.FailFast("a callback ran at exit"); });\n'
        "        F.LogAtExit(true);\n"
        '        Console.Error.WriteLine("FAILED: the callback was accepted");\n'
        "        Environment.ExitCode = 3;\n"
        "    }\n"
        "    catch (InvalidOperationException) { }\n"
        "};\n"
        'Console.WriteLine("exit OK");\n',
        encoding="utf-8",
    )
    dll = runner_csharp.build_program(case, output, program, "CallbackExit")
    completed = runner_csharp.execute(case, dll, library)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "exit OK" in completed.stdout


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
    output = completed.stdout + completed.stderr
    assert "System.InvalidOperationException" in output
    assert "does not match these bindings" in output


def test_two_modules_can_be_compiled_into_one_assembly(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """An assembly has one resolver of libraries: the second module (and a resolver of the user)
    must not break the first, nor itself."""
    cases: list[Case] = []
    libraries: dict[str, Path] = {}
    generated: list[Path] = []
    for number, module in enumerate(("modone", "modtwo"), start=1):
        root = tmp_path / module
        (root / "input").mkdir(parents=True)
        (root / "input" / f"{module}.h").write_text(
            f"#pragma once\nnamespace {module} {{ inline int value() {{ return {number}; }} }}\n",
            encoding="utf-8",
        )
        case = Case(
            name=module,
            directory=root,
            module=module,
            headers=(root / "input" / f"{module}.h",),
            std="c++17",
            min_cxx="c++11",
            languages=("csharp",),
        )
        cases.append(case)
        libraries[module], _ = build(case, toolchain, root / "build")
        files = bridgefex.generate(case.headers, case.options(("csharp",))).files
        bridgefex.write_files(files, root / "generated")
        generated.append(root / "generated")
    program = tmp_path / "Program.cs"
    line = (
        "Console.WriteLine(Modone.Modone.Functions.Value() * 10 + Modtwo.Modtwo.Functions.Value());"
    )
    program.write_text(f"{line}\n{line}\n", encoding="utf-8")
    project = tmp_path / "Both.csproj"
    sources = "\n".join(
        f'    <Compile Include="{path.as_posix()}/**/*.cs" />' for path in generated
    )
    project.write_text(
        '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
        "    <OutputType>Exe</OutputType>\n    <TargetFramework>net8.0</TargetFramework>\n"
        "    <Nullable>enable</Nullable>\n    <ImplicitUsings>enable</ImplicitUsings>\n"
        "    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>\n"
        "    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>\n"
        "  </PropertyGroup>\n  <ItemGroup>\n"
        f'    <Compile Include="{program.as_posix()}" />\n{sources}\n'
        "  </ItemGroup>\n</Project>\n",
        encoding="utf-8",
    )
    built_ = subprocess.run(
        [
            "dotnet",
            "build",
            str(project),
            "-c",
            "Release",
            "-nologo",
            "-v",
            "q",
            "-o",
            str(tmp_path / "bin"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=runner_csharp.environment(),
        timeout=900,
    )
    assert built_.returncode == 0, built_.stdout + built_.stderr
    environment = {
        **runner_csharp.environment(),
        **{f"{module.upper()}_LIBRARY": str(path) for module, path in libraries.items()},
    }
    completed = subprocess.run(
        ["dotnet", str(tmp_path / "bin" / "Both.dll")],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.split() == ["12", "12"]
