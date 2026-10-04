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
    assert naming.overload_suffix([int32]) == "_int32"
    assert naming.overload_suffix([int32, double]) == "_int32_double"


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
    assert naming.parameter_names(["self", "self_"]) == ("self_", "self__")
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
