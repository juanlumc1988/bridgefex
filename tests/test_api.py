# SPDX-License-Identifier: Apache-2.0
"""Tests of the library API, the generator helpers and libclang loading."""

from __future__ import annotations

from pathlib import Path

import pytest

import bridgefex
from bridgefex import libclang
from bridgefex.api import include_spelling
from bridgefex.errors import (
    ConfigurationError,
    Diagnostic,
    GenerationError,
    LibclangError,
    Location,
)
from bridgefex.generator import render, write_files
from bridgefex.model import Function, Header, Module
from bridgefex.plan import build_plan
from bridgefex.typemap import STANDARD_TYPEDEFS


def test_include_spelling(tmp_path: Path) -> None:
    header = tmp_path / "include" / "lib" / "api.h"
    assert include_spelling(header, None) == "api.h"
    assert include_spelling(header, tmp_path / "include") == "lib/api.h"
    with pytest.raises(ConfigurationError, match="not inside"):
        include_spelling(header, tmp_path / "other")


@pytest.mark.parametrize(
    ("directory", "name"), [("v2*", "api.h"), ("a\rb", "api.h"), ("lib", 'a"b.h')]
)
def test_include_spelling_rejects_what_an_include_cannot_hold(
    tmp_path: Path, directory: str, name: str
) -> None:
    """Checked without touching the file system: Windows forbids some of these names."""
    root = tmp_path / "include"
    with pytest.raises(ConfigurationError, match="cannot write an #include"):
        include_spelling(root / directory / name, root)


def test_options_are_validated() -> None:
    with pytest.raises(ConfigurationError, match="no input headers"):
        bridgefex.generate([], bridgefex.Options(module="ok"))
    with pytest.raises(ConfigurationError, match="unknown languages"):
        bridgefex.generate([Path("x.h")], bridgefex.Options(module="ok", languages=("rust",)))
    with pytest.raises(ConfigurationError, match="invalid C\\+\\+ standard"):
        bridgefex.generate([Path("x.h")], bridgefex.Options(module="ok", std="c11"))


def test_render_is_tidy_and_platform_independent() -> None:
    int32 = STANDARD_TYPEDEFS["int32_t"].type
    model = Function("answer", ("n",), (), int32, Location("a.h", 1, 1))
    plan = build_plan(Module("mod", (Header(Path("a.h"), "a.h", (model,)),)))
    files = render(plan)
    assert sorted(files) == [
        "c/a_c.cpp",
        "c/a_c.h",
        "c/mod_runtime.cpp",
        "c/mod_runtime.h",
        "c/mod_runtime_internal.hpp",
        "python/mod/__init__.py",
        "python/mod/_runtime.py",
        "python/mod/a.py",
    ]
    for path, content in files.items():
        assert "\r" not in content, path
        assert content.endswith("\n"), path
        assert not content.endswith("\n\n"), path
        assert all(line == line.rstrip() for line in content.splitlines()), path
    assert sorted(render(plan, languages=())) == sorted(p for p in files if p.startswith("c/"))
    with pytest.raises(ValueError, match="unknown languages"):
        render(plan, languages=("cobol",))


def test_write_files_uses_lf(tmp_path: Path) -> None:
    written = write_files({"c/a.h": "line 1\nline 2\n", "python/m/a.py": "x = 1\n"}, tmp_path)
    assert [path.relative_to(tmp_path).as_posix() for path in written] == ["c/a.h", "python/m/a.py"]
    assert (tmp_path / "c" / "a.h").read_bytes() == b"line 1\nline 2\n"


def test_generation_error_needs_diagnostics() -> None:
    with pytest.raises(ValueError, match="at least one diagnostic"):
        GenerationError([])
    error = GenerationError([Diagnostic("first"), Diagnostic("second", Location("a.h", 2, 3))])
    assert str(error) == "first\na.h:2:3: second"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ubuntu clang version 20.1.2 (0ubuntu1~24.04.3)", 20),
        ("clang version 20.1.8", 20),
        ("clang version 21.1.0 (https://github.com/llvm/llvm-project abc)", 21),
        ("something else", None),
    ],
)
def test_parse_major_version(text: str, expected: int | None) -> None:
    assert libclang.parse_major_version(text) == expected


def test_libclang_is_loaded_once(libclang_loaded: str) -> None:
    assert libclang.parse_major_version(libclang_loaded) in libclang.SUPPORTED_MAJOR_VERSIONS
    assert libclang.load() == libclang_loaded
    with pytest.raises(LibclangError, match="already loaded"):
        libclang.load("/some/other/libclang.so")


def test_default_candidates_are_absolute() -> None:
    candidates = libclang.default_candidates()
    assert candidates
    assert all(path.is_absolute() for path in candidates)


@pytest.fixture
def fresh_libclang_state(libclang_loaded: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A libclang module that has loaded nothing yet; the real state comes back afterwards."""
    loaded = libclang._state.path
    assert loaded is not None
    monkeypatch.setattr(libclang, "_state", libclang._State())
    return loaded


def test_unsupported_libclang_version_is_refused(
    fresh_libclang_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libclang, "_query_version", lambda library: "clang version 19.1.7")
    with pytest.raises(LibclangError, match="needs libclang 20"):
        libclang.load(fresh_libclang_state)


def test_bindings_of_another_version_are_refused(
    fresh_libclang_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libclang, "_bindings_major_version", lambda: 19)
    with pytest.raises(LibclangError, match="bindings are version 19"):
        libclang.load(fresh_libclang_state)


def test_missing_library_is_reported(fresh_libclang_state: Path, tmp_path: Path) -> None:
    with pytest.raises(LibclangError, match="libclang not found at"):
        libclang.load(tmp_path / "libclang.so")
