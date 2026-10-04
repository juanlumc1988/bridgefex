# SPDX-License-Identifier: Apache-2.0
"""Tests of the command-line interface."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import bridgefex
from bridgefex.cli import main
from tests.support.cases import GOLDEN_DIR, read_tree

pytestmark = pytest.mark.usefixtures("libclang_loaded")

COUNTER = GOLDEN_DIR / "demo" / "input" / "counter.h"
GEOMETRY = GOLDEN_DIR / "demo" / "input" / "geometry.h"


def test_generates_the_golden_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-m", "demo", "-o", str(tmp_path), str(COUNTER), str(GEOMETRY)]) == 0
    assert read_tree(tmp_path) == read_tree(GOLDEN_DIR / "demo" / "expected")
    assert "wrote 11 files" in capsys.readouterr().out


def test_lang_none_generates_only_the_c_layer(tmp_path: Path) -> None:
    assert main(["-m", "demo", "-o", str(tmp_path), "--lang", "none", str(COUNTER)]) == 0
    assert sorted(read_tree(tmp_path)) == [
        "c/counter_c.cpp",
        "c/counter_c.h",
        "c/demo_runtime.cpp",
        "c/demo_runtime.h",
        "c/demo_runtime_internal.hpp",
    ]


def test_include_root(tmp_path: Path) -> None:
    root = tmp_path / "include"
    (root / "lib").mkdir(parents=True)
    header = root / "lib" / "api.h"
    header.write_text("#pragma once\nnamespace lib { int answer(); }\n")
    output = tmp_path / "out"
    assert main(["-m", "mylib", "-o", str(output), "--include-root", str(root), str(header)]) == 0
    source = (output / "c" / "api_c.cpp").read_text()
    assert '#include "lib/api.h"' in source


def test_unsupported_input_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    header = tmp_path / "bad.h"
    header.write_text("#pragma once\nvoid f(char c);\nvoid g(int* p);\n")
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
    first.write_text("void f(char c);\n")
    second.write_text("void g(wchar_t c);\n")
    assert main(["-m", "bad", "-o", str(tmp_path / "out"), str(first), str(second)]) == 1
    errors = capsys.readouterr().err
    assert "first.h" in errors
    assert "second.h" in errors


def test_parse_options_are_passed_to_libclang(tmp_path: Path) -> None:
    (tmp_path / "dep").mkdir()
    (tmp_path / "dep" / "dep.h").write_text("#pragma once\n#define DEP_OK 1\n")
    header = tmp_path / "api.h"
    header.write_text(
        '#include "dep.h"\n'
        "#if !DEP_OK || !defined(EXTRA) || EXTRA != 3\n#error options missing\n#endif\n"
        "int answer();\n"
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
        (["-m", "ok", "--libclang", "/nonexistent/libclang.so"], "libclang"),
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
    header.write_text("#warning careful\nint answer();\n")
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
