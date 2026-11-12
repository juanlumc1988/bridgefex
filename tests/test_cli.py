# SPDX-License-Identifier: Apache-2.0
"""Tests of the command-line interface."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import bridgefex
from bridgefex.cli import main
from tests.support.cases import GOLDEN_DIR, load_case, read_tree

pytestmark = pytest.mark.usefixtures("libclang_loaded")

COUNTER = GOLDEN_DIR / "demo" / "input" / "counter.h"
GEOMETRY = GOLDEN_DIR / "demo" / "input" / "geometry.h"
SLEEPER = GOLDEN_DIR / "demo" / "input" / "sleeper.h"


def test_generates_the_golden_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], update_goldens: bool
) -> None:
    if update_goldens:
        pytest.skip("the golden files are being rewritten by test_golden.py")
    case = load_case(GOLDEN_DIR / "demo")
    languages = [argument for language in case.languages for argument in ("--lang", language)]
    code = main(
        ["-m", "demo", "-o", str(tmp_path), *languages, str(COUNTER), str(GEOMETRY), str(SLEEPER)]
    )
    assert code == 0
    expected = read_tree(case.expected_dir)
    assert read_tree(tmp_path) == expected
    assert f"wrote {len(expected)} files" in capsys.readouterr().out


def test_the_default_language_is_python(tmp_path: Path) -> None:
    assert main(["-m", "demo", "-o", str(tmp_path), str(COUNTER)]) == 0
    assert {path.split("/")[0] for path in read_tree(tmp_path)} == {"c", "python"}


def test_lang_can_be_repeated_and_repeated_languages_are_generated_once(tmp_path: Path) -> None:
    arguments = ["--lang", "csharp", "--lang", "python", "--lang", "csharp"]
    assert main(["-m", "demo", "-o", str(tmp_path), "--no-verify", *arguments, str(COUNTER)]) == 0
    assert {path.split("/")[0] for path in read_tree(tmp_path)} == {"c", "python", "csharp"}


@pytest.mark.parametrize("name", ["java", "Kotlin", "jvm"])
def test_java_and_kotlin_are_the_jvm_bindings(tmp_path: Path, name: str) -> None:
    arguments = ["--lang", name, "--no-verify"]
    assert main(["-m", "demo", "-o", str(tmp_path), *arguments, str(COUNTER)]) == 0
    assert {path.split("/")[0] for path in read_tree(tmp_path)} == {"c", "jvm"}


def test_the_help_names_the_languages_and_what_the_module_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "Python package, the C# namespace and the Java package" in text
    assert "the Java sources with javac" in text
    assert "ctypes" not in text


def test_lang_none_cannot_be_combined_with_a_language(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = ["--lang", "none", "--lang", "python"]
    with pytest.raises(SystemExit) as raised:
        main(["-m", "demo", "-o", str(tmp_path), *arguments, str(COUNTER)])
    assert raised.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert not tmp_path.exists() or not read_tree(tmp_path)


def test_lang_none_generates_only_the_c_layer(tmp_path: Path) -> None:
    assert main(["-m", "demo", "-o", str(tmp_path), "--lang", "none", str(COUNTER)]) == 0
    assert sorted(read_tree(tmp_path)) == [
        "c/counter_c.cpp",
        "c/counter_c.h",
        "c/demo_runtime.cpp",
        "c/demo_runtime.h",
        "c/demo_runtime_internal.hpp",
        "c/demo_types.h",
    ]


def test_include_root(tmp_path: Path) -> None:
    root = tmp_path / "include"
    (root / "lib").mkdir(parents=True)
    header = root / "lib" / "api.h"
    header.write_text("#pragma once\nnamespace lib { int answer(); }\n", encoding="utf-8")
    output = tmp_path / "out"
    assert main(["-m", "mylib", "-o", str(output), "--include-root", str(root), str(header)]) == 0
    source = (output / "c" / "api_c.cpp").read_text(encoding="utf-8")
    assert '#include "lib/api.h"' in source


def test_unsupported_input_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    header = tmp_path / "bad.h"
    header.write_text("#pragma once\nvoid f(char c);\nvoid g(int* p);\n", encoding="utf-8")
    output = tmp_path / "out"
    assert main(["-m", "bad", "-o", str(output), str(header)]) == 1
    errors = capsys.readouterr().err.splitlines()
    assert errors == [
        f"bridgefex: error: {header}:2:13: parameter 'c' of 'f': plain 'char' is not "
        "supported: its signedness depends on the platform; use 'signed char', "
        "'unsigned char', 'int8_t' or 'uint8_t'",
        f"bridgefex: error: {header}:3:13: parameter 'p' of 'g': pointers are not supported yet",
    ]
    assert not output.exists(), "nothing is written when generation fails"


def test_errors_of_several_headers_are_all_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.h"
    second = tmp_path / "second.h"
    first.write_text("void f(char c);\n", encoding="utf-8")
    second.write_text("void g(wchar_t c);\n", encoding="utf-8")
    assert main(["-m", "bad", "-o", str(tmp_path / "out"), str(first), str(second)]) == 1
    errors = capsys.readouterr().err
    assert "first.h" in errors
    assert "second.h" in errors


def test_parse_options_are_passed_to_libclang(tmp_path: Path) -> None:
    (tmp_path / "dep").mkdir()
    (tmp_path / "dep" / "dep.h").write_text("#pragma once\n#define DEP_OK 1\n", encoding="utf-8")
    header = tmp_path / "api.h"
    header.write_text(
        '#include "dep.h"\n'
        "#if !DEP_OK || !defined(EXTRA) || EXTRA != 3\n#error options missing\n#endif\n"
        "int answer();\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    arguments = ["-m", "api", "-o", str(output), "-I", str(tmp_path / "dep"), "-D", "EXTRA=3"]
    assert main([*arguments, "--std", "c++20", "--clang-arg=-Wall", str(header)]) == 0


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["-m", "Bad-Name"], "invalid module name"),
        (["-m", "json"], "standard library"),
        (["-m", "ok", "--std", "c17"], "invalid C++ standard"),
        (["-m", "ok", "--include-root", "/nonexistent/root"], "not inside the include root"),
    ],
)
def test_invalid_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str], message: str
) -> None:
    assert main([*arguments, "-o", str(tmp_path), str(COUNTER)]) == 1
    assert message in capsys.readouterr().err


def test_missing_header(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-m", "ok", "-o", str(tmp_path), str(tmp_path / "missing.h")]) == 1
    assert "input header not found" in capsys.readouterr().err


def test_warnings_are_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    header = tmp_path / "api.h"
    header.write_text("#warning careful\nint answer();\n", encoding="utf-8")
    assert main(["-m", "api", "-o", str(tmp_path / "out"), str(header)]) == 0
    assert "bridgefex: warning:" in capsys.readouterr().err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.strip() == f"bridgefex {bridgefex.__version__}"


def test_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as file:
        assert tomllib.load(file)["project"]["version"] == bridgefex.__version__


def test_missing_libclang_in_a_fresh_process(tmp_path: Path) -> None:
    """In this process libclang is already loaded, so the check runs in a new one."""
    command = [
        sys.executable,
        "-m",
        "bridgefex",
        "-m",
        "ok",
        "-o",
        str(tmp_path),
        "--libclang",
        str(tmp_path / "nonexistent" / "libclang.so"),
        str(COUNTER),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 1
    assert "bridgefex: error: libclang not found at" in completed.stderr


def test_annotations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    header = tmp_path / "pool.h"
    header.write_text(
        "#pragma once\nnamespace p { class Item { public: Item(); };\n"
        "class Pool { public: Pool(); Item* find(int id); void keep(Item* item); }; }\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    command = ["-m", "pools", "-o", str(output), str(header)]
    # Without them, the owner of a raw pointer is unknown.
    assert main(command) == 1
    error = capsys.readouterr().err
    assert 'say who owns it with [p_Pool_find] return = "owned"' in error
    # Two files, each with its part.
    first = tmp_path / "first.toml"
    first.write_text('[p_Pool_find]\nreturn = "borrowed"\n', encoding="utf-8")
    second = tmp_path / "second.toml"
    second.write_text('[p_Pool_keep]\nitem = "retain"\n', encoding="utf-8")
    code = main([*command, "--annotations", str(first), "--annotations", str(second)])
    assert code == 0, capsys.readouterr().err
    declarations = (output / "c" / "pool_c.h").read_text(encoding="utf-8")
    assert "borrowed from self, may be NULL" in declarations
    assert "item: self keeps a pointer to it" in declarations


def test_annotation_errors_are_reported_with_their_position(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    header = tmp_path / "api.h"
    header.write_text("#pragma once\nint answer();\n", encoding="utf-8")
    notes = tmp_path / "notes.toml"

    def run(path: Path) -> str:
        command = ["-m", "mod", "-o", str(tmp_path / "out"), "--annotations", str(path)]
        assert main([*command, str(header)]) == 1
        return capsys.readouterr().err

    notes.write_text('[mod_answer]\nreturn = "owned"\n[mod_answr]\n', encoding="utf-8")
    error = run(notes)
    assert f"{notes}:3: [mod_answr] is not a generated C function" in error
    assert "did you mean mod_answer" in error
    assert f"({notes}:2)" in error  # the annotation that does not apply
    assert "cannot read the annotations file" in run(tmp_path / "missing.toml")
    notes.write_text("[mod_answer\n", encoding="utf-8")
    assert f"{notes}:1:" in run(notes)
