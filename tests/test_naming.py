# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the naming rules (no libclang needed)."""

from __future__ import annotations

import pytest

from bridgefex import naming
from bridgefex.errors import ConfigurationError
from bridgefex.typemap import BUILTIN_TYPES, STANDARD_TYPEDEFS


@pytest.mark.parametrize("name", ["demo", "my_lib", "a1", "x"])
def test_valid_module_names(name: str) -> None:
    assert naming.check_module_name(name) == name


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Demo", "lowercase"),
        ("1demo", "lowercase"),
        ("_demo", "lowercase"),
        ("my-lib", "lowercase"),
        ("", "lowercase"),
        ("int", "keyword"),
        ("class", "keyword"),
        ("lambda", "keyword"),
        ("restrict", "keyword"),
        ("json", "standard library"),
        ("ctypes", "standard library"),
    ],
)
def test_invalid_module_names(name: str, reason: str) -> None:
    with pytest.raises(ConfigurationError, match=reason):
        naming.check_module_name(name)


@pytest.mark.parametrize("stem", ["counter", "Counter", "my_header2"])
def test_valid_header_stems(stem: str) -> None:
    assert naming.check_header_stem(stem) == stem


@pytest.mark.parametrize("stem", ["my-header", "2d", "class", "_private", "a.b"])
def test_invalid_header_stems(stem: str) -> None:
    with pytest.raises(ConfigurationError):
        naming.check_header_stem(stem)


def test_c_prefix() -> None:
    assert naming.c_prefix(("demo",), "mod") == "demo"
    assert naming.c_prefix(("a", "b"), "mod") == "a_b"
    assert naming.c_prefix((), "mod") == "mod"


def test_overload_suffix() -> None:
    int32 = STANDARD_TYPEDEFS["int32_t"].type
    double = BUILTIN_TYPES["DOUBLE"]
    assert naming.overload_suffix([]) == "_void"
    assert naming.overload_suffix([int32.token]) == "_int32"
    assert naming.overload_suffix([int32.token, double.token]) == "_int32_double"


def test_parameter_names() -> None:
    assert naming.parameter_names(["a", "b"]) == ("a", "b")
    assert naming.parameter_names(["", ""]) == ("arg1", "arg2")
    # Keywords of C, C++ and Python, macros and generated names get a '_'.
    assert naming.parameter_names(["lambda", "restrict", "errno", "self", "out_result"]) == (
        "lambda_",
        "restrict_",
        "errno_",
        "self_",
        "out_result_",
    )
    # Renaming never creates duplicates.
    # ... nor '__', which C and C++ reserve.
    assert naming.parameter_names(["self", "self_"]) == ("self2", "self_")
    assert naming.parameter_names(["x_", "x_"]) == ("x_", "x_2")
    assert naming.parameter_names(["", "arg1"]) == ("arg1", "arg1_")
    assert naming.parameter_names(["handle"], reserved={"handle"}) == ("handle_",)


def test_python_and_member_names() -> None:
    assert naming.python_name("value") == "value"
    assert naming.python_name("from") == "from_"
    assert naming.python_name("match") == "match"  # soft keywords are valid names
    assert naming.member_name("close") == "close_"
    assert naming.member_name("create_int32", reserved={"create_int32"}) == "create_int32_"
    assert naming.member_name("value") == "value"


def test_is_identifier() -> None:
    assert naming.is_identifier("abc_1")
    assert not naming.is_identifier("1abc")
    assert not naming.is_identifier("café")


@pytest.mark.parametrize("name", ["car_", "a__b", "x_"])
def test_module_names_that_would_make_reserved_identifiers(name: str) -> None:
    with pytest.raises(ConfigurationError, match="single '_'"):
        naming.check_module_name(name)


@pytest.mark.parametrize("stem", sorted(naming.PACKAGE_NAMES))
def test_header_stems_that_replace_package_names(stem: str) -> None:
    with pytest.raises(ConfigurationError, match="would replace"):
        naming.check_header_stem(stem)


def test_generated_macros() -> None:
    macros = naming.generated_macros("demo")
    assert {"DEMO_API", "DEMO_C_API_BUILD", "DEMO_C_API_STATIC", "DEMO_OK"} <= macros
    assert "DEMO_RUNTIME_H" in macros
    assert naming.header_guard("demo", "counter") == "DEMO_COUNTER_C_H"


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("co_yield", "keyword"),
        ("thread_local", "keyword"),
        ("impl__Engine", "reserved"),
        ("_Upper", "reserved"),
        ("demo_Counter", None),
        ("_lower", "reserved"),
    ],
)
def test_c_identifier_problem(name: str, problem: str | None) -> None:
    result = naming.c_identifier_problem(name)
    if problem is None:
        assert result is None
    else:
        assert result is not None
        assert problem in result


def test_reserved_parameter_names_include_types_and_platform_macros() -> None:
    assert naming.parameter_names(["int32_t", "size_t", "unix", "linux", "pascal"]) == (
        "int32_t_",
        "size_t_",
        "unix_",
        "linux_",
        "pascal_",
    )


def test_module_level_names() -> None:
    assert naming.module_level_name("ctypes") == "ctypes_"
    assert naming.module_level_name("threading") == "threading_"
    assert naming.module_level_name("lambda") == "lambda_"
    assert naming.module_level_name("Box", reserved={"Box"}) == "Box_"
    assert naming.module_level_name("getattr") == "getattr"


def test_derived_names() -> None:
    cpp = ["from", "x_", "errno", "", "s", "s_size", "out_result_size"]
    final = naming.parameter_names(cpp)
    assert final == ("from_", "x_", "errno_", "arg4", "s", "s_size", "out_result_size_")
    wants = [True, True, True, True, True, False, False]
    sizes = naming.derived_names(cpp, final, wants, "_size")
    assert sizes == ("from_size", "x_size", "errno_size", "arg4_size", "s_size_", None, None)
    for name in sizes:
        assert name is None or naming.c_identifier_problem(name) is None


def test_derived_names_avoid_reserved_names_and_each_other() -> None:
    # A macro of the header; then two parameters whose bases are the same.
    sizes = naming.derived_names(
        ["values", "a", "a_"], ["values", "a", "a_"], [True] * 3, "_size", {"values_size"}
    )
    assert sizes == ("values_size_", "a_size", "a_size_")
    # A chain of taken names never gives '__'.
    cpp = ["s", "s_size", "s_size_"]
    assert naming.derived_names(cpp, cpp, [True, False, False], "_size") == ("s_size2", None, None)
    # The C++ name, not the final one: '_' alone is the unnamed case.
    assert naming.derived_names(["_"], ["_"], [True], "_size") == ("arg1_size",)


@pytest.mark.parametrize(
    "name", ["_types", "_out_size", "_out_data", "out_result_size", "_item", "_scope", "_status"]
)
def test_names_of_generated_locals_are_reserved_for_parameters(name: str) -> None:
    assert naming.parameter_names([name]) == (f"{name}_",)


def test_runtime_names_of_step_two_are_reserved() -> None:
    assert {"DEMO_ERROR_INVALID_ARGUMENT", "DEMO_TYPES_H"} <= naming.generated_macros("demo")
    assert "ERROR_INVALID_ARGUMENT" in naming.PACKAGE_NAMES
    assert "_types" in naming.MODULE_GLOBALS
