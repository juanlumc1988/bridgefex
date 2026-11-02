# SPDX-License-Identifier: Apache-2.0
"""The language backends: what they are and what each of them generates."""

from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

import bridgefex
from bridgefex import typemap
from bridgefex.backends import BACKENDS, registry
from bridgefex.backends.python.ffi import ctypes_scalar
from bridgefex.generator import render
from bridgefex.model import Module
from bridgefex.plan import build_plan


def test_every_backend_is_registered_by_its_language() -> None:
    assert [backend.name for backend in BACKENDS] == ["python"]
    assert tuple(registry()) == bridgefex.LANGUAGES == ("python",)


def test_the_c_layer_does_not_depend_on_the_languages() -> None:
    plan = build_plan(Module("m", ()))
    only_c = render(plan, ())
    with_python = render(plan)
    assert {path.split("/")[0] for path in only_c} == {"c"}
    assert {path for path in with_python if path.startswith("c/")} == set(only_c)
    assert all(with_python[path] == only_c[path] for path in only_c)


def test_a_backend_only_writes_below_its_language() -> None:
    plan = build_plan(Module("m", ()))
    backend = registry()["python"]
    files = backend.render(plan, "0" * 32)
    assert files
    assert all(path.startswith("python/m/") for path in files)


def test_an_unknown_language_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown languages: csharp"):
        render(build_plan(Module("m", ())), ("csharp",))


def _python_files() -> dict[str, str]:
    return registry()["python"].render(build_plan(Module("m", ())), "0" * 32)


def test_the_python_backend_checks_its_package(tmp_path: Path) -> None:
    check = registry()["python"].check
    assert check is not None
    assert check(build_plan(Module("m", ())), _python_files(), tmp_path) == []


def test_the_python_check_reports_code_that_does_not_compile(tmp_path: Path) -> None:
    check = registry()["python"].check
    assert check is not None
    files = {**_python_files(), "python/m/_types.py": "def broken(:\n"}
    (error,) = check(build_plan(Module("m", ())), files, tmp_path)
    assert "the generated Python code does not compile: python/m/_types.py:1:" in error.message


def test_the_python_check_reports_a_package_that_cannot_be_imported(tmp_path: Path) -> None:
    check = registry()["python"].check
    assert check is not None
    files = {**_python_files(), "python/m/_types.py": "raise RuntimeError('boom')\n"}
    (error,) = check(build_plan(Module("m", ())), files, tmp_path)
    assert error.message == "the generated Python code cannot be imported: RuntimeError: boom"


def test_the_python_backend_maps_every_scalar_type_to_a_ctypes_type() -> None:
    scalars = [
        *typemap.BUILTIN_TYPES.values(),
        *(typedef.type for typedef in typemap.STANDARD_TYPEDEFS.values()),
    ]
    for scalar in scalars:
        mapped = getattr(ctypes, ctypes_scalar(scalar.c_name))
        # The size of a ctypes type is the one of the C type on this platform.
        if scalar.c_name.endswith("_t") and scalar.c_name != "size_t":
            bits = int(scalar.c_name.removeprefix("u").removeprefix("int").removesuffix("_t"))
            assert ctypes.sizeof(mapped) * 8 == bits
        elif scalar.c_name == "size_t":
            assert mapped is ctypes.c_size_t
