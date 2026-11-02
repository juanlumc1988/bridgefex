# SPDX-License-Identifier: Apache-2.0
"""The names of the Python backend, and what it asks the C API to leave alone."""

from __future__ import annotations

from pathlib import Path

import pytest

import bridgefex
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


@pytest.mark.parametrize("stem", ["lambda", "class", "None", "from"])
def test_a_header_called_like_a_python_keyword_cannot_be_a_module(stem: str) -> None:
    assert naming.check_header_stem(stem) == stem  # for C and C++ it is just a file name
    with pytest.raises(ConfigurationError, match="keyword of Python"):
        naming.check_header_stem(stem, reservations().header_problem)


@pytest.mark.parametrize("stem", ["register", "union", "export", "new", "float", "default"])
def test_a_header_called_like_a_c_keyword_is_fine_for_every_language(stem: str) -> None:
    assert naming.check_header_stem(stem, reservations().header_problem) == stem


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


# The wiring: what the Python backend reserves reaches the names of the C API (and so the
# bindings), where each kind of name avoids what it must. Without the wiring the bindings
# compile and import, and then fail when called (a parameter hides ``ctypes``).
def _generate(tmp_path: Path, text: str, **options: object) -> dict[str, str]:
    header = tmp_path / "names.h"
    header.write_text("#pragma once\n" + text, encoding="utf-8")
    return bridgefex.generate([header], bridgefex.Options(module="mm", **options)).files  # type: ignore[arg-type]


@pytest.mark.usefixtures("libclang_loaded")
def test_a_parameter_named_like_a_name_of_the_bindings_is_renamed(tmp_path: Path) -> None:
    reserved = reservations()
    words = sorted((reserved.words | reserved.parameter_words) - naming.KEYWORDS)
    assert {"lambda", "ctypes", "_runtime", "_handle", "cls", "None"} <= set(words)
    files = _generate(tmp_path, "".join(f"void f{i}(int {w});\n" for i, w in enumerate(words)))
    for number, word in enumerate(words):
        assert f"mm_f{number}(int {word}_)" in files["c/names_c.h"], word
        assert f"def f{number}({word}_):" in files["python/mm/names.py"], word


@pytest.mark.usefixtures("libclang_loaded")
def test_a_struct_member_avoids_the_keywords_but_not_the_locals_of_the_bindings(
    tmp_path: Path,
) -> None:
    reserved = reservations()
    keywords = sorted(reserved.words - naming.KEYWORDS)
    locals_only = sorted(reserved.parameter_words - reserved.words - naming.KEYWORDS)
    members = "".join(f"int {name};" for name in [*keywords, *locals_only])
    files = _generate(tmp_path, f"struct S {{ {members} }};")
    declarations = "".join(text for path, text in files.items() if path.endswith(".h"))
    for word in keywords:
        assert f"int {word}_;" in declarations, word
    for word in locals_only:
        assert f"int {word};" in declarations, word


@pytest.mark.usefixtures("libclang_loaded")
def test_a_parameter_avoids_the_locals_of_the_function_and_the_names_of_the_module(
    tmp_path: Path,
) -> None:
    files = _generate(
        tmp_path,
        "class Counter { public: int get() const; };\nint f(int _c, int _h2, int Counter);\n",
    )
    assert "def f(_c_, _h2_, Counter_):" in files["python/mm/names.py"]


@pytest.mark.usefixtures("libclang_loaded")
def test_the_names_of_the_c_api_do_not_depend_on_the_languages(tmp_path: Path) -> None:
    text = "int f(int lambda, int ctypes, int cls);\nstruct S { int from; int self; };\n"
    every = _generate(tmp_path, text)
    none = _generate(tmp_path, text, languages=())
    assert {path: every[path] for path in every if path.startswith("c/")} == none
    assert "mm_f(int lambda_, int ctypes_, int cls_" in none["c/names_c.h"]
