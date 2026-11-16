# SPDX-License-Identifier: Apache-2.0
"""The Rust backend: the names it gives, what it refuses, and the crate that it writes (checked
by cargo when the machine has it). What the code does when it runs is the business of
tests/test_rust_roundtrip.py."""

from __future__ import annotations

from pathlib import Path

import pytest

import bridgefex
from bridgefex.backends.rust import naming
from bridgefex.errors import GenerationError
from tests.conformance import runner_rust

pytestmark = pytest.mark.usefixtures("libclang_loaded")

PRELUDE = "#pragma once\n#include <cstddef>\n#include <cstdint>\n"


def generate(
    tmp_path: Path, code: str, *, module: str = "bind", verify: bool = False, header: str = "api"
) -> dict[str, str]:
    path = tmp_path / f"{header}.h"
    path.write_text(PRELUDE + code, encoding="utf-8")
    options = bridgefex.Options(module, std="c++17", languages=("rust",), verify=verify)
    return bridgefex.generate([path], options).files


def source(files: dict[str, str], name: str = "api", module: str = "bind") -> str:
    return files[f"rust/{module}/src/{name}.rs"]


@pytest.fixture
def cargo() -> None:
    runner_rust.skip_without_rust()


# The names ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "snake"),
    [
        ("isZero", "is_zero"),
        ("liveInstances", "live_instances"),
        ("HTTPServer", "http_server"),
        ("getHTTPResponse", "get_http_response"),
        ("echo_int8", "echo_int8"),
        ("Vec3", "vec3"),
        ("getX2Y", "get_x2_y"),
        ("already_snake", "already_snake"),
        ("_private", "_private"),
        ("__x__", "__x"),
        ("a", "a"),
        ("A", "a"),
        ("X11", "x11"),
        ("", "_x"),
        ("_", "_x"),
        ("2fast", "_2_fast"),
    ],
)
def test_the_names_of_functions_are_in_snake_case(name: str, snake: str) -> None:
    assert naming.snake(name) == snake


@pytest.mark.parametrize(
    ("name", "pascal"),
    [
        ("Counter", "Counter"),
        ("counter", "Counter"),
        ("my_class", "MyClass"),
        ("HTTPServer", "HTTPServer"),
        ("liveInstances", "LiveInstances"),
        ("_x", "X"),
        ("", "X"),
        ("2d", "X2d"),
    ],
)
def test_the_names_of_types_are_in_pascal_case(name: str, pascal: str) -> None:
    assert naming.pascal(name) == pascal


def test_a_keyword_gets_a_trailing_underscore() -> None:
    for word in ("type", "match", "self", "Self", "crate", "fn", "loop", "async", "yield"):
        assert naming.identifier(word) == f"{word}_"
    assert naming.identifier("value") == "value"


def test_constants_are_in_upper_case() -> None:
    assert naming.upper_snake("Red") == "RED"
    assert naming.upper_snake("kLowLevel") == "K_LOW_LEVEL"
    assert naming.upper_snake("RED") == "RED"


# What the crate is -----------------------------------------------------------------------------


def test_the_crate_has_a_module_for_each_header_and_no_dependency(tmp_path: Path) -> None:
    files = generate(tmp_path, "int twice(int value);\n")
    assert set(files) >= {
        "rust/bind/Cargo.toml",
        "rust/bind/build.rs",
        "rust/bind/src/lib.rs",
        "rust/bind/src/native.rs",
        "rust/bind/src/runtime.rs",
        "rust/bind/src/api.rs",
    }
    manifest = files["rust/bind/Cargo.toml"]
    assert 'name = "bind"' in manifest
    assert "[dependencies]" not in manifest
    assert "pub mod api;" in files["rust/bind/src/lib.rs"]


def test_a_method_takes_self_by_the_constness_of_the_c_plus_plus_method(tmp_path: Path) -> None:
    code = """
class Box { public: Box(); ~Box(); int get() const; void set(int v); static int made(); };
"""
    api = source(generate(tmp_path, code))
    assert "pub fn get(&self) -> crate::Result<::core::ffi::c_int>" in api
    assert "pub fn set(&mut self, v: ::core::ffi::c_int) -> crate::Result<()>" in api
    assert "pub fn made() -> crate::Result<::core::ffi::c_int>" in api
    assert "pub fn new() -> crate::Result<crate::Owned<Self>>" in api


def test_constructors_are_told_apart_by_the_suffix_of_the_c_api(tmp_path: Path) -> None:
    code = "class Box { public: Box(); explicit Box(int v); ~Box(); };\n"
    api = source(generate(tmp_path, code))
    assert "pub fn new_void()" in api
    assert "pub fn new_int32(" in api or "pub fn new_int(" in api
    assert "pub fn new()" not in api


def test_overloads_get_the_suffix_of_the_c_api(tmp_path: Path) -> None:
    api = source(generate(tmp_path, "int f(int a);\ndouble f(double a);\n"))
    assert "pub fn f_int(" in api or "pub fn f_int32(" in api
    assert "pub fn f_double(" in api


def test_the_library_is_checked_by_its_fingerprint_on_the_first_call(tmp_path: Path) -> None:
    files = generate(tmp_path, "int twice(int value);\n")
    runtime = files["rust/bind/src/runtime.rs"]
    assert "bind_api_fingerprint" in runtime
    assert "StaleLibrary" in runtime
    assert "crate::runtime::ensure()?;" in source(files)


def test_the_build_script_links_the_library_from_the_variables_of_the_module(
    tmp_path: Path,
) -> None:
    build = generate(tmp_path, "int twice(int value);\n")["rust/bind/build.rs"]
    assert "BIND_LIB_DIR" in build
    assert "BIND_LIB_KIND" in build
    assert "rustc-link-lib" in build


# What is refused ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "what"),
    [
        ("enum class Color { Red };\nint f(Color c);\n", "enum"),
        ("struct P { int x; };\nint f(P p);\n", "struct"),
    ],
)
def test_what_the_bindings_do_not_have_yet_is_refused_with_a_message(
    tmp_path: Path, code: str, what: str
) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    assert f"Rust: the {what}" in str(raised.value)
    assert "not supported in Rust yet" in str(raised.value)


def test_two_functions_with_the_same_rust_name_are_refused(tmp_path: Path) -> None:
    code = "int isZero(int a);\nint is_zero(int a);\n"
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    assert "gets the Rust name 'is_zero'" in str(raised.value)


@pytest.mark.parametrize("method", ["New", "new_"])
def test_a_constructor_and_a_method_cannot_both_be_new(tmp_path: Path, method: str) -> None:
    code = f"class Box {{ public: Box(); ~Box(); static int {method}(); }};\n"
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    assert "gets the Rust name 'new'" in str(raised.value)


@pytest.mark.parametrize("name", ["std", "core", "alloc", "test", "crate", "self", "super", "type"])
def test_a_module_that_a_crate_cannot_have_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, "int f(int a);\n", module=name)
    assert "Rust: cannot name the module" in str(raised.value)


@pytest.mark.parametrize("stem", ["native", "runtime"])
def test_a_header_that_would_be_a_module_of_the_runtime_is_refused(
    tmp_path: Path, stem: str
) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, "int f(int a);\n", header=stem)
    assert "Rust: cannot use header name" in str(raised.value)


def test_two_headers_with_the_same_rust_module_are_refused(tmp_path: Path) -> None:
    first = tmp_path / "my_api.h"
    second = tmp_path / "MyApi.h"
    first.write_text(PRELUDE + "int f(int a);\n", encoding="utf-8")
    second.write_text(PRELUDE + "int g(int a);\n", encoding="utf-8")
    options = bridgefex.Options("bind", std="c++17", languages=("rust",), verify=False)
    with pytest.raises(GenerationError) as raised:
        bridgefex.generate([first, second], options)
    assert "get the Rust module" in str(raised.value)


# The crate compiles (cargo check, warnings are errors) ------------------------------------------


def test_names_that_are_keywords_or_that_the_crate_uses_compile(
    tmp_path: Path, cargo: None
) -> None:
    code = """
class Result { public: Result(); ~Result(); int get() const; };
class Error { public: Error(); ~Error(); int type() const; };
class Option { public: Option(); ~Option(); int Self() const; };
class Owned { public: Owned(); ~Owned(); int crate() const; };
int match_(int type, int fn, int loop, int self, int Self, int crate, int super, int mod);
int async(int await, int dyn, int impl, int move, int ref, int unsafe, int use);
int result(int status, int object);
"""
    files = generate(tmp_path, code, verify=True)
    api = source(files)
    assert "pub fn match_(" in api
    assert "pub fn type_(&self)" in api


def test_a_name_in_camel_case_gets_a_name_in_snake_case_that_compiles(
    tmp_path: Path, cargo: None
) -> None:
    code = """
class HTTPServer { public: HTTPServer(); ~HTTPServer(); int getHTTPResponse2() const; };
int echoInt8Value(int Value);
"""
    api = source(generate(tmp_path, code, verify=True))
    assert "pub fn get_http_response2(&self)" in api
    assert "pub fn echo_int8_value(value:" in api


def test_unnamed_and_equal_parameters_get_different_names(tmp_path: Path, cargo: None) -> None:
    code = "int f(int, int, int x_, int x);\n"
    api = source(generate(tmp_path, code, verify=True))
    assert "arg1: " in api
    assert "x_: " in api
