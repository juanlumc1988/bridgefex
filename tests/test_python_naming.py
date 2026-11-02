# SPDX-License-Identifier: Apache-2.0
"""The names of the Python backend, and what it asks the C API to leave alone."""

from __future__ import annotations

import pytest

from bridgefex import naming
from bridgefex.backends import reservations
from bridgefex.backends.python import naming as py_naming
from bridgefex.errors import ConfigurationError
from bridgefex.model import Module
from bridgefex.plan import build_plan


def test_python_and_member_names() -> None:
    assert py_naming.python_name("value") == "value"
    assert py_naming.python_name("from") == "from_"
    assert py_naming.python_name("match") == "match"  # soft keywords are valid names
    assert py_naming.member_name("close") == "close_"
    # Methods that the constructors and close() of every class call.
    assert py_naming.member_name("_watch") == "_watch_"
    assert py_naming.member_name("_collected") == "_collected_"
    assert py_naming.member_name("create_int32", reserved={"create_int32"}) == "create_int32_"
    assert py_naming.member_name("value") == "value"


def test_module_level_names() -> None:
    assert py_naming.module_level_name("ctypes") == "ctypes_"
    assert py_naming.module_level_name("threading") == "threading_"
    assert py_naming.module_level_name("lambda") == "lambda_"
    assert py_naming.module_level_name("Box", reserved={"Box"}) == "Box_"
    assert py_naming.module_level_name("getattr") == "getattr"


def test_module_aliases_do_not_hide_the_globals_of_the_modules() -> None:
    assert py_naming.module_alias("counter") == "_counter"
    assert py_naming.module_alias("types") == "_types_"


@pytest.mark.parametrize(
    ("name", "reason"),
    [("lambda", "keyword"), ("json", "standard library"), ("ctypes", "standard library")],
)
def test_module_names_the_package_cannot_have(name: str, reason: str) -> None:
    with pytest.raises(ConfigurationError, match=reason):
        build_plan(Module(name, ()), None, reservations())
    assert py_naming.module_problem("demo") is None


@pytest.mark.parametrize("stem", sorted(py_naming.PACKAGE_NAMES))
def test_header_stems_that_replace_package_names(stem: str) -> None:
    with pytest.raises(ConfigurationError, match="would replace"):
        naming.check_header_stem(stem, reservations().header_problem)


def test_a_header_called_like_a_python_keyword_cannot_be_a_module() -> None:
    assert naming.check_header_stem("lambda") == "lambda"  # C++ has no such keyword
    with pytest.raises(ConfigurationError, match="keyword"):
        naming.check_header_stem("lambda", reservations().header_problem)


def test_the_c_api_leaves_alone_the_keywords_and_the_locals_of_the_bindings() -> None:
    words = reservations()
    assert {"lambda", "from"} <= words.words
    assert {"ctypes", "_types", "_out_size", "_scope", "_status"} <= words.parameter_words
    assert {"ERROR_INVALID_ARGUMENT", "load"} <= py_naming.PACKAGE_NAMES
    assert {"_types", "ctypes"} <= py_naming.MODULE_GLOBALS
    # Without the backends the C API takes the names (the plan reads the reservations).
    assert naming.parameter_names(["lambda", "ctypes"]) == ("lambda", "ctypes")
    assert naming.parameter_names(["lambda", "ctypes"], words.words | words.parameter_words) == (
        "lambda_",
        "ctypes_",
    )


@pytest.mark.parametrize("name", ["_types", "_out_size", "_out_data", "_scope", "_status", "cls"])
def test_names_of_generated_locals_are_reserved_for_parameters(name: str) -> None:
    taken = reservations().parameter_words
    assert naming.parameter_names([name], taken) == (f"{name}_",)
