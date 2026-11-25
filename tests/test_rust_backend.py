# SPDX-License-Identifier: Apache-2.0
"""The Rust backend: the names it gives, what it refuses, and the crate that it writes (checked
by cargo when the machine has it). What the code does when it runs is the business of
tests/test_rust_roundtrip.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import bridgefex
from bridgefex.backends.rust import lower, mapping, naming
from bridgefex.backends.rust.mapping import Unsupported
from bridgefex.errors import GenerationError
from bridgefex.plan import CParam, Ffi, FfiKind, Role
from tests.conformance import runner_rust

pytestmark = pytest.mark.usefixtures("libclang_loaded")

PRELUDE = "#pragma once\n#include <cstddef>\n#include <cstdint>\n#include <string>\n"


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


def refuse_closures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rust has no type for a callback that the plan can give today (an argument that is a class or
    a container is only for the overrides of a director, which Rust does not have yet): the
    refusal that a type of the future would get is made by a type that cannot be written."""

    def closure_type(*_: object) -> str:
        raise Unsupported("a callback that takes 'Widget'")

    monkeypatch.setattr(lower, "closure_type", closure_type)


def test_what_the_bindings_do_not_have_yet_is_refused_with_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refuse_closures(monkeypatch)
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, "#include <functional>\nint f(std::function<int ()> a);\n")
    assert "not supported" not in str(raised.value)
    assert "Rust:" in str(raised.value)


def test_what_is_not_supported_yet_says_what_it_is_and_where(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refuse_closures(monkeypatch)
    code = "#include <functional>\nvoid f(std::function<void ()> done);\n"
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    assert "api.h:" in str(raised.value)
    assert "a callback that takes 'Widget'" in str(raised.value)


def test_a_c_type_that_the_bindings_do_not_know_is_refused() -> None:
    with pytest.raises(Unsupported, match="'weird_t' of a callback"):
        mapping.function_types(
            CParam("f", "void", Role.INPUT, Ffi(FfiKind.CALLBACK, "k"), function_params="weird_t"),
            {},
            {},
        )
    with pytest.raises(Unsupported, match="of kind 'status'"):
        mapping.native_type(CParam("x", "int", Role.INPUT, Ffi(FfiKind.STATUS)), {}, {})


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


@pytest.mark.parametrize(
    "name",
    [
        *["std", "core", "alloc", "test", "crate", "self", "super", "type"],
        # The libraries that every program links: the module's would be found in their place.
        *["c", "m", "dl", "rt", "pthread", "gcc_s"],
    ],
)
def test_a_module_that_a_crate_cannot_have_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, "int f(int a);\n", module=name)
    assert "Rust: cannot name the module" in str(raised.value)


@pytest.mark.parametrize("stem", ["native", "runtime", "lib", "main"])
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


# Enums, structs and strings ---------------------------------------------------------------------


def test_an_enum_is_a_type_that_holds_the_integer_with_a_constant_for_each_enumerator(
    tmp_path: Path,
) -> None:
    code = "enum class Color : std::uint8_t { Red, Green = 200, kLowLevel = 255, Alias = 200 };\n"
    api = source(generate(tmp_path, code))
    assert "#[repr(transparent)]" in api
    assert "pub struct Color(pub u8);" in api
    assert "pub const RED: Color = Color(0);" in api
    assert "pub const K_LOW_LEVEL: Color = Color(255);" in api
    # An alias has a constant, and the first enumerator names the value.
    assert "pub const ALIAS: Color = Color(200);" in api
    assert api.count("200 => f.write_str") == 1
    assert '200 => f.write_str("Color::GREEN")' in api


def test_an_enum_without_a_fixed_type_checks_its_argument(tmp_path: Path) -> None:
    code = "enum Mode { Off, On, Auto = 5 };\nint f(Mode m);\n"
    api = source(generate(tmp_path, code))
    assert 'crate::runtime::check_enum(m.0 as i128, 0, 7, "m")?;' in api


def test_two_enumerators_with_the_same_rust_name_are_refused(tmp_path: Path) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, "enum Mode { Low_Level, LowLevel };\n")
    assert "get the Rust name 'LOW_LEVEL'" in str(raised.value)


def test_a_struct_is_the_mirror_of_the_c_struct(tmp_path: Path) -> None:
    code = "struct P { double x; std::uint8_t tag[3]; };\nP twice(P p);\n"
    api = source(generate(tmp_path, code))
    assert "#[repr(C)]" in api
    assert "#[derive(Clone, Copy, Debug, PartialEq)]" in api
    assert "pub x: f64," in api
    assert "pub tag: [u8; 3]," in api
    assert "pub fn defaults() -> crate::Result<Self>" in api
    assert "pub fn twice(p: crate::api::P) -> crate::Result<crate::api::P>" in api


def test_an_enum_nested_in_a_class_is_in_a_module_with_the_name_of_the_class(
    tmp_path: Path,
) -> None:
    code = (
        "class Lamp { public: enum class State { Off, On }; Lamp(); ~Lamp(); "
        "State state() const; };\n"
    )
    api = source(generate(tmp_path, code))
    assert "pub mod lamp {" in api
    assert "-> crate::Result<crate::api::lamp::State>" in api


def test_a_string_is_a_str_in_and_a_string_out(tmp_path: Path) -> None:
    code = "std::string echo(const std::string& s);\nconst char* maybe(const char* s);\n"
    api = source(generate(tmp_path, code))
    assert "pub fn echo(s: &str) -> crate::Result<::std::string::String>" in api
    # A const char* may be NULL, and C++ reads up to a NUL: the text cannot have one.
    assert "-> crate::Result<::core::option::Option<::std::string::String>>" in api
    assert 'crate::runtime::c_string(s, "s")?' in api


def test_a_nullable_const_char_pointer_is_an_option(tmp_path: Path) -> None:
    annotations = tmp_path / "ann.toml"
    annotations.write_text('[bind_maybe]\ns = "nullable"\n', encoding="utf-8")
    path = tmp_path / "api.h"
    path.write_text(PRELUDE + "int maybe(const char* s);\n", encoding="utf-8")
    options = bridgefex.Options(
        "bind", std="c++17", languages=("rust",), verify=False, annotations=(annotations,)
    )
    api = source(bridgefex.generate([path], options).files)
    assert "s: ::core::option::Option<&str>" in api


def test_enums_structs_and_strings_compile(tmp_path: Path, cargo: None) -> None:
    code = """
enum class Color : std::uint8_t { Red, Green = 200 };
enum Mode { Off, On };
struct Point { double x; double y; };
struct Line { Point from; Point to; Color color[2]; };
class Lamp { public: enum class State { Off, On }; Lamp(); ~Lamp(); State state() const; };
Line make(Point a, Point type, Mode m, Color c);
std::string echo(const std::string& s);
const char* maybe(const char* s);
"""
    generate(tmp_path, code, verify=True)


# Borrowing: what a borrowed result may be ------------------------------------------------------

BORROWING = """
class Node { public: Node(); ~Node(); int value() const; void set(int value); };
class Holder {
public:
    Holder();
    ~Holder();
    Node& slot() const;
    const Node& cslot() const;
    Node& pick(Node& first, const Node& second);
    const Node& cpick(const Node& first) const;
};
Node& choose(const Node& first, Node& second);
const Node& cchoose(const Node& first, const Node& second);
Node& fresh(Node copy);
"""


def flat(text: str) -> str:
    """The code on one line, as rustfmt-like wrapping does not matter to what is tested."""
    return " ".join(text.split()).replace("( ", "(").replace(", )", ")")


def test_a_const_method_that_gives_a_mutable_reference_takes_mut_self(tmp_path: Path) -> None:
    """Two results of ``&self`` would be two ``&mut`` to one object."""
    api = flat(source(generate(tmp_path, BORROWING)))
    assert "pub fn slot(&mut self) -> crate::Result<&mut crate::api::Node>" in api
    assert "Takes `&mut self` although C++ declares it `const`" in api
    # A const result is shared, as the method is.
    assert "pub fn cslot(&self) -> crate::Result<&crate::api::Node>" in api


def test_a_borrowed_result_may_come_from_any_object_that_the_call_takes(tmp_path: Path) -> None:
    api = flat(source(generate(tmp_path, BORROWING)))
    # C++ does not say which: the result lives as long as all of them, and a result that is not
    # const needs all of them exclusively.
    assert (
        "pub fn pick<'a>(&'a mut self, first: &'a mut crate::api::Node, "
        "second: &'a mut crate::api::Node) -> crate::Result<&'a mut crate::api::Node>"
    ) in api
    assert "`second` is `&mut` although C++ takes it `const`" in api
    assert (
        "pub fn cpick<'a>(&'a self, first: &'a crate::api::Node) "
        "-> crate::Result<&'a crate::api::Node>"
    ) in api
    assert (
        "pub fn choose<'a>(first: &'a mut crate::api::Node, second: &'a mut crate::api::Node) "
        "-> crate::Result<&'a mut crate::api::Node>"
    ) in api
    assert (
        "pub fn cchoose<'a>(first: &'a crate::api::Node, second: &'a crate::api::Node) "
        "-> crate::Result<&'a crate::api::Node>"
    ) in api


def test_a_copy_that_a_function_takes_is_not_what_the_result_borrows_from(tmp_path: Path) -> None:
    api = flat(source(generate(tmp_path, BORROWING)))
    # `fresh(Node copy)` gives a reference that nobody owns: it is `'static`, and unsafe because
    # it is not const; the copy does not take part.
    assert "pub unsafe fn fresh(copy: &crate::api::Node) -> crate::Result<&'static mut" in api


def refused(tmp_path: Path, code: str, body: str) -> str | None:
    """The error of the compiler for ``body`` as the ``main`` of a program that uses the crate
    of the header ``code``, or None if it compiles."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    files = generate(tmp_path, code)
    bridgefex.write_files(files, tmp_path / "out")
    app = tmp_path / "app"
    (app / "src").mkdir(parents=True)
    (app / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.0.0"\nedition = "2021"\n'
        f'[dependencies]\nbind = {{ path = "{(tmp_path / "out" / "rust" / "bind").as_posix()}" }}\n'
        "[workspace]\n",
        encoding="utf-8",
    )
    (app / "src" / "main.rs").write_text(
        "#![allow(unused)]\nuse bind::api::*;\nfn main() -> bind::Result<()> {\n"
        f"    {body}\n    Ok(())\n}}\n",
        encoding="utf-8",
    )
    command = runner_rust.cargo()
    assert command is not None
    environment = {
        **os.environ,
        "CARGO_TARGET_DIR": str(tmp_path / "target"),
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TERM_COLOR": "never",
    }
    completed = subprocess.run(
        [command, "check", "--offline", "--quiet", "--manifest-path", str(app / "Cargo.toml")],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=600,
    )
    return None if completed.returncode == 0 else completed.stdout + completed.stderr


@pytest.mark.parametrize(
    ("bad", "good", "code"),
    [
        pytest.param(
            "let mut h = Holder::new()?; let a = h.slot()?; let b = h.slot()?; "
            "a.set(1)?; b.set(2)?;",
            "let mut h = Holder::new()?; let a = h.slot()?; a.set(1)?; "
            "let b = h.slot()?; b.set(2)?;",
            "E0499",
            id="two-mutable-results-of-a-const-method",
        ),
        pytest.param(
            "let mut h = Holder::new()?; let r = { let n = Node::new()?; h.cpick(&n)? }; "
            "r.value()?;",
            "let mut h = Holder::new()?; let n = Node::new()?; let r = h.cpick(&n)?; r.value()?;",
            "E0597",
            id="a-result-cannot-outlive-an-object-parameter-of-a-method",
        ),
        pytest.param(
            "let a = Node::new()?; let r = { let b = Node::new()?; cchoose(&a, &b)? }; r.value()?;",
            "let a = Node::new()?; let b = Node::new()?; let r = cchoose(&a, &b)?; r.value()?;",
            "E0597",
            id="a-result-cannot-outlive-any-object-of-a-function",
        ),
        pytest.param(
            "let mut a = Node::new()?; let b = Node::new()?; let r = choose(&b, &mut a)?;",
            "let mut a = Node::new()?; let mut b = Node::new()?; let r = choose(&mut b, &mut a)?;",
            "E0308",
            id="a-mutable-result-needs-every-object-it-may-come-from-exclusively",
        ),
        pytest.param(
            "let mut a = Node::new()?; let mut b = Node::new()?; let r = choose(&mut b, &mut a)?; "
            "b.value()?; r.set(1)?;",
            "let mut a = Node::new()?; let mut b = Node::new()?; let r = choose(&mut b, &mut a)?; "
            "r.set(1)?; b.value()?;",
            "E0502",
            id="and-while-the-result-lives-nobody-else-uses-them",
        ),
    ],
)
def test_the_compiler_refuses_what_a_borrowed_result_forbids(
    tmp_path: Path, cargo: None, bad: str, good: str, code: str
) -> None:
    error = refused(tmp_path / "bad", BORROWING, bad)
    assert error is not None, "the program was accepted"
    assert f"error[{code}]" in error, error
    assert refused(tmp_path / "good", BORROWING, good) is None


# What the reviewers of the first phase found ------------------------------------------------------


def test_a_parameter_never_starts_with_the_prefix_of_the_locals() -> None:
    assert naming.parameter("__result") == "_result"
    assert naming.parameter("___x") == "_x"
    assert naming.parameter("__c_x") == "_c_x"
    assert naming.parameter("_x") == "_x"
    assert naming.parameter("x") == "x"


def test_two_c_string_parameters_get_two_locals(tmp_path: Path, cargo: None) -> None:
    """``x`` and ``_x`` would both have the local ``__c_x``: the second would hide the first."""
    api = source(generate(tmp_path, "int f(const char* x, const char* _x);\n", verify=True))
    assert "let __c_x = " in api
    assert "let __c_x_ = " in api


@pytest.mark.parametrize(
    ("name", "rust"),
    [
        ("Self", "Self_"),
        ("self", "Self_"),
        ("Ok", "Ok_"),
        ("Err", "Err_"),
        ("Some", "Some_"),
        ("None", "None_"),
        ("none", "None_"),
        ("Option", "Option"),
        ("Result", "Result"),
    ],
)
@pytest.mark.parametrize("kind", ["enum class {name} {{ A, B }};", "struct {name} {{ int x; }};"])
def test_a_type_named_like_self_or_a_constructor_of_the_prelude_compiles(
    tmp_path: Path, cargo: None, name: str, rust: str, kind: str
) -> None:
    """An enum is a tuple struct, and its name would hide ``Ok``, ``Some``... in the bodies
    that use them; ``Self`` cannot name a type."""
    code = kind.format(name=name) + "\nconst char* maybe(int a);\nint plain(int a);\n"
    api = source(generate(tmp_path, code, verify=True))
    assert f"pub struct {rust}" in api


@pytest.mark.parametrize(("name", "rust"), [("Self", "Self_"), ("None", "None_"), ("Ok", "Ok_")])
def test_a_class_named_like_self_or_a_constructor_of_the_prelude_compiles(
    tmp_path: Path, cargo: None, name: str, rust: str
) -> None:
    code = f"class {name} {{ public: {name}(); ~{name}(); int get() const; }};\n"
    api = source(generate(tmp_path, code, verify=True))
    assert f"pub struct {rust} " in api


@pytest.mark.parametrize("code", ["enum class Color { Red, Green };\n", ""])
def test_a_module_without_functions_compiles(tmp_path: Path, cargo: None, code: str) -> None:
    """Nothing calls the check of the library or the error of a status: they are not dead code."""
    generate(tmp_path, code, verify=True)


def test_a_method_named_like_a_conversion_of_the_bindings_is_renamed(
    tmp_path: Path, cargo: None
) -> None:
    """``as_<base>`` and ``into_base`` are what the bindings add for the base classes: a method
    of the header with one of these names would be a duplicate, or be hidden by the one of
    ``Owned``."""
    code = """
class A { public: A(); virtual ~A(); };
class B { public: B(); virtual ~B(); };
class D : public A, public B {
public:
    D();
    int as_b() const;
    int as_b_mut();
    int into_base();
    int into_b();
};
"""
    api = flat(source(generate(tmp_path, code, verify=True)))
    assert "pub fn as_b_(&self)" in api
    assert "pub fn as_b_mut_(&mut self)" in api
    assert "pub fn into_base_(&mut self)" in api
    assert "pub fn into_b_(&mut self)" in api
    # What the bindings add is still there, under its name.
    assert "pub fn as_b(&self) -> &crate::api::B" in api
    assert "pub fn into_base(self) -> crate::Owned<crate::api::A>" in api
    assert "pub fn into_b(self) -> crate::Owned<crate::api::B>" in api


def test_a_reference_that_borrows_from_nothing_is_unsafe_also_when_it_is_const(
    tmp_path: Path,
) -> None:
    """C++ may destroy the object that nobody in Rust owns (a registry that erases it), and a
    ``&'static`` would still be there."""
    code = """
class W { public: W(); ~W(); int v() const; };
class Widgets {
public:
    static void erase(int id);
    static const W& get(int id);
    static W& edit(int id);
};
const W& registered(int id);
"""
    api = flat(source(generate(tmp_path, code)))
    assert (
        "pub unsafe fn get(id: ::core::ffi::c_int) -> crate::Result<&'static crate::api::W>" in api
    )
    assert (
        "pub unsafe fn edit(id: ::core::ffi::c_int) -> crate::Result<&'static mut crate::api::W>"
    ) in api
    assert "pub unsafe fn registered(id: ::core::ffi::c_int)" in api
    assert "C++ may destroy it: the reference must not be used after that." in api
    assert "pub fn erase(id: ::core::ffi::c_int)" in api


def test_a_const_pointer_that_borrows_from_nothing_is_unsafe(tmp_path: Path) -> None:
    annotations = tmp_path / "ann.toml"
    annotations.write_text('[bind_find]\nreturn = "borrowed"\n', encoding="utf-8")
    path = tmp_path / "api.h"
    path.write_text(
        PRELUDE + "class W { public: W(); ~W(); };\nconst W* find(int id);\n", encoding="utf-8"
    )
    options = bridgefex.Options(
        "bind", std="c++17", languages=("rust",), verify=False, annotations=(annotations,)
    )
    api = flat(source(bridgefex.generate([path], options).files))
    assert (
        "pub unsafe fn find(id: ::core::ffi::c_int) -> "
        "crate::Result<::core::option::Option<&'static crate::api::W>>"
    ) in api


def test_a_borrowed_result_that_has_an_owner_stays_safe(tmp_path: Path) -> None:
    code = (
        "class W { public: W(); ~W(); };\nclass H { public: H(); ~H(); const W& get() const; };\n"
    )
    api = flat(source(generate(tmp_path, code)))
    assert "pub fn get(&self) -> crate::Result<&crate::api::W>" in api


def test_bytes_of_a_const_char_pointer_may_be_none(tmp_path: Path, cargo: None) -> None:
    """A ``const char*`` result may be NULL, bytes or not: ``Option<Vec<u8>>``."""
    annotations = tmp_path / "ann.toml"
    annotations.write_text('[bind_raw]\nreturn = "bytes"\n', encoding="utf-8")
    path = tmp_path / "api.h"
    path.write_text(PRELUDE + "const char* raw();\nstd::string text();\n", encoding="utf-8")
    options = bridgefex.Options(
        "bind", std="c++17", languages=("rust",), verify=True, annotations=(annotations,)
    )
    api = flat(source(bridgefex.generate([path], options).files))
    assert "pub fn raw() -> crate::Result<::core::option::Option<::std::vec::Vec<u8>>>" in api
    assert "pub fn text() -> crate::Result<::std::string::String>" in api


def test_what_a_static_function_that_returns_an_object_keeps_is_kept_by_that_object(
    tmp_path: Path,
) -> None:
    annotations = tmp_path / "ann.toml"
    annotations.write_text(
        '[bind_View_of]\nnode = "retain"\n[bind_keep]\nnode = "retain"\n', encoding="utf-8"
    )
    path = tmp_path / "api.h"
    path.write_text(
        PRELUDE + "#include <memory>\nclass Node { public: Node(); ~Node(); };\n"
        "class View { public: ~View(); static std::unique_ptr<View> of(Node& node); };\n"
        "void keep(Node& node);\n",
        encoding="utf-8",
    )
    options = bridgefex.Options(
        "bind", std="c++17", languages=("rust",), verify=False, annotations=(annotations,)
    )
    api = flat(source(bridgefex.generate([path], options).files))
    assert "for as long as the object that it returns uses it" in api
    assert "for as long as the program uses it" in api


def test_the_build_script_links_a_library_again_when_it_is_rebuilt(tmp_path: Path) -> None:
    build = generate(tmp_path, "int twice(int value);\n")["rust/bind/build.rs"]
    for name in ("libbind.so", "libbind.dylib", "libbind.a", "bind.dll", "bind.lib"):
        assert f'"{name}"' in build
    assert "cargo:rerun-if-changed=" in build
    assert "cargo:warning=no library of `bind` in" in build


def test_every_declaration_that_is_not_supported_yet_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refuse_closures(monkeypatch)
    code = """
#include <functional>
class Bag {
public:
    Bag();
    ~Bag();
    void each(const std::function<void (int)>& visit);
    int fine() const;
};
void later(std::function<void ()> done);
int sum(const std::function<int (int)>& add);
"""
    path = tmp_path / "api.h"
    path.write_text(PRELUDE + code, encoding="utf-8")
    options = bridgefex.Options("bind", std="c++20", languages=("rust",), verify=False)
    with pytest.raises(GenerationError) as raised:
        bridgefex.generate([path], options)
    lines = str(raised.value).splitlines()
    assert len(lines) == 3, lines
    for name in ("Bag::each", "later", "sum"):
        assert sum(name in line for line in lines) == 1, (name, lines)
    assert all("api.h:" in line and "Rust:" in line for line in lines)


def test_a_class_is_debug_so_that_what_holds_an_owned_object_can_be(
    tmp_path: Path, cargo: None
) -> None:
    code = "class Counter { public: Counter(); ~Counter(); int get() const; };\n"
    api = source(generate(tmp_path, code, verify=True))
    assert "impl ::core::fmt::Debug for Counter" in api
    assert 'f.debug_struct("Counter").finish_non_exhaustive()' in api


def test_a_borrowed_result_does_not_come_from_an_object_that_the_function_takes(
    tmp_path: Path,
) -> None:
    """An object that is given up (a ``std::unique_ptr``, a ``transfer`` pointer) is not what the
    result borrows from, and nothing else is: ``'static`` and ``unsafe``, not a lifetime that
    nothing bounds."""
    annotations = tmp_path / "ann.toml"
    annotations.write_text('[bind_keep]\nnode = "transfer"\n', encoding="utf-8")
    path = tmp_path / "api.h"
    path.write_text(
        PRELUDE + "#include <memory>\nclass Node { public: Node(); ~Node(); };\n"
        "Node& store(std::unique_ptr<Node> node);\nNode& keep(Node* node);\n",
        encoding="utf-8",
    )
    options = bridgefex.Options(
        "bind", std="c++17", languages=("rust",), verify=False, annotations=(annotations,)
    )
    api = flat(source(bridgefex.generate([path], options).files))
    assert (
        "pub unsafe fn store(node: crate::Owned<crate::api::Node>) "
        "-> crate::Result<&'static mut crate::api::Node>"
    ) in api
    assert (
        "pub unsafe fn keep(node: crate::Owned<crate::api::Node>) "
        "-> crate::Result<&'static mut crate::api::Node>"
    ) in api


# What C++ keeps: `this` and the strings (annotation `retain`) ----------------------------------

KEPT = """
#include <memory>
#include <string_view>
class Guard { public: ~Guard(); int level() const; };
class Mutex { public: Mutex(); ~Mutex(); std::unique_ptr<Guard> lock(); };
class Registry { public: Registry(); ~Registry(); void register_me(); int size() const; };
class Label {
public:
    Label();
    ~Label();
    void setRaw(const char* text);
    void setView(std::string_view text);
    void setMaybe(const char* text);
    void setCopy(const std::string& text);
    int length() const;
};
"""

KEPT_ANNOTATIONS = """
[bind_Mutex_lock]
this = "retain"
[bind_Registry_register_me]
this = "retain"
[bind_Label_setRaw]
text = "retain"
[bind_Label_setView]
text = "retain"
[bind_Label_setMaybe]
text = ["retain", "nullable"]
"""


def generate_annotated(
    tmp_path: Path, code: str, annotations: str, *, verify: bool = False
) -> dict[str, str]:
    table = tmp_path / "ann.toml"
    table.write_text(annotations, encoding="utf-8")
    path = tmp_path / "api.h"
    path.write_text(PRELUDE + code, encoding="utf-8")
    options = bridgefex.Options(
        "bind", std="c++17", languages=("rust",), verify=verify, annotations=(table,)
    )
    return bridgefex.generate([path], options).files


def test_a_method_whose_result_keeps_its_object_is_unsafe(tmp_path: Path, cargo: None) -> None:
    api = flat(source(generate_annotated(tmp_path, KEPT, KEPT_ANNOTATIONS, verify=True)))
    assert (
        "pub unsafe fn lock(&mut self) -> crate::Result<::core::option::Option<"
        "crate::Owned<crate::api::Guard>>>"
    ) in api
    assert (
        "Unsafe: the object that it returns keeps a pointer to `self`, which must stay valid "
        "(not dropped or moved) for as long as that object is used."
    ) in api
    # A method that nothing annotates stays safe.
    assert "pub fn length(&self)" in api


def test_a_method_that_the_library_keeps_for_ever_says_how_to_give_it_up(tmp_path: Path) -> None:
    api = flat(source(generate_annotated(tmp_path, KEPT, KEPT_ANNOTATIONS)))
    assert "pub unsafe fn register_me(&mut self) -> crate::Result<()>" in api
    assert (
        "Unsafe: C++ keeps a pointer to `self` for as long as the program runs: it must not be "
        "dropped or moved, so give it up for good with `Owned::leak`."
    ) in api
    assert "pub fn size(&self)" in api


def test_a_string_that_cpp_keeps_is_unsafe_and_the_callers_c_string(
    tmp_path: Path, cargo: None
) -> None:
    api = flat(source(generate_annotated(tmp_path, KEPT, KEPT_ANNOTATIONS, verify=True)))
    # A const char* is the caller's C string (a text converted for the call would be gone).
    assert "pub unsafe fn set_raw(&mut self, text: &::core::ffi::CStr)" in api
    assert (
        "pub unsafe fn set_maybe(&mut self, text: ::core::option::Option<&::core::ffi::CStr>)"
    ) in api
    assert "text.map_or(::core::ptr::null(), |text| text.as_ptr())" in api
    # A string_view is the caller's str, which is passed as it is.
    assert "pub unsafe fn set_view(&mut self, text: &str)" in api
    assert (
        "Unsafe: C++ keeps a pointer to `text`, which must stay valid for as long as `self` "
        "uses it, and must not change."
    ) in api
    # A std::string is copied for the call: nothing to keep, nothing unsafe.
    assert "pub fn set_copy(&mut self, text: &str)" in api


def test_without_the_annotation_a_string_is_a_safe_str(tmp_path: Path) -> None:
    api = flat(source(generate(tmp_path, KEPT)))
    assert "pub fn set_raw(&mut self, text: &str)" in api
    assert "pub fn set_view(&mut self, text: &str)" in api
    assert "pub fn lock(" in api


def test_a_base_class_that_is_called_base_is_not_the_first_one(tmp_path: Path, cargo: None) -> None:
    """The first base is `into_base`, and a second one that is called `Base` cannot be too."""
    code = """
class Other { public: Other(); virtual ~Other(); };
class Base { public: Base(); virtual ~Base(); };
class Derived : public Other, public Base { public: Derived(); };
"""
    api = flat(source(generate(tmp_path, code, verify=True)))
    assert "pub fn into_base(self) -> crate::Owned<crate::api::Other>" in api
    assert "pub fn into_base_(self) -> crate::Owned<crate::api::Base>" in api
    assert "pub fn as_base(&self) -> &crate::api::Base" in api


# Containers and callbacks -----------------------------------------------------------------------


def test_a_container_is_a_slice_that_goes_in_and_a_vec_that_comes_back(
    tmp_path: Path, cargo: None
) -> None:
    code = """
#include <array>
#include <vector>
enum class Color : std::uint8_t { Red, Green };
enum Level { Low, High };
struct Point { double x; double y; };
std::vector<int> scale(const std::vector<int>& values);
std::vector<bool> invert(const std::vector<bool>& flags);
std::vector<std::string> upper(const std::vector<std::string>& words);
std::vector<Color> rotate(const std::vector<Color>& colors);
std::vector<Level> levels(const std::vector<Level>& values);
std::vector<Point> shift(std::vector<Point> points);
std::array<float, 4> twice(const std::array<float, 4>& m);
std::array<Color, 2> pair(std::array<Color, 2> colors);
"""
    api = source(generate(tmp_path, code, verify=True))
    flat = " ".join(api.split()).replace(", )", ")")
    assert "pub fn scale( values: &[::core::ffi::c_int]) -> crate::Result<" in flat
    assert "::std::vec::Vec<::core::ffi::c_int>>" in flat
    assert "invert(flags: &[bool]) -> crate::Result<::std::vec::Vec<bool>>" in flat
    assert "pub fn upper(words: &[&str])" in flat
    assert "::std::vec::Vec<::std::string::String>>" in flat
    assert "colors: &[crate::api::Color]" in flat
    assert "pub fn twice(m: &[f32; 4]) -> crate::Result<[f32; 4]>" in flat
    assert "pub fn pair(colors: &[crate::api::Color; 2])" in flat
    # An enum without a fixed type is checked, element by element, before the call.
    assert 'check_enums(values.iter().map(|item| item.0 as i128), 0, 1, "values")?;' in flat
    # An enum with a fixed type holds every value of its integer: nothing to check.
    assert api.count("check_enums") == 1
    # What the call allocated is released by the owner of the array, not by hand.
    assert "crate::runtime::Array::<" in api
    assert "native::bind_free" not in api


def test_a_callback_is_a_closure_that_c_plus_plus_may_call_from_any_thread(
    tmp_path: Path, cargo: None
) -> None:
    code = """
#include <functional>
enum Mode { Off, On };
struct Point { double x; double y; };
int sum(int n, std::function<int(int)> f);
void each(std::function<void(const Point&, const char*, std::string_view)> visit);
Mode pick(std::function<Mode()> chooser);
int text(const char* name, std::function<int(int)> f, std::function<void()> done);
"""
    annotations = tmp_path / "ann.toml"
    annotations.write_text('[bind_text]\ndone = "nullable"\n', encoding="utf-8")
    path = tmp_path / "api.h"
    path.write_text(PRELUDE + "#include <string_view>\n" + code, encoding="utf-8")
    options = bridgefex.Options(
        "bind", std="c++17", languages=("rust",), verify=True, annotations=(annotations,)
    )
    api = source(bridgefex.generate([path], options).files)
    bounds = "::core::marker::Send + ::core::marker::Sync + 'static"
    int_ = "::core::ffi::c_int"
    assert f"f: impl ::core::ops::Fn({int_}) -> {int_} + {bounds}," in api
    assert (
        "visit: impl ::core::ops::Fn(&crate::api::Point, ::core::option::Option<&str>, &str) + "
        in api
    )
    # A callback that annotations say may be empty is an Option of a closure; the others are not.
    assert f"done: ::core::option::Option<impl ::core::ops::Fn() + {bounds}>," in api
    assert api.count("::core::option::Option<impl") == 1
    # An enum without a fixed type that C++ cannot hold is the failure of the closure.
    assert "::core::panic!(" in api
    assert "0..1" in api
    # Nothing can fail between giving the closures away and the call, and a panic of a closure is
    # raised before the status is looked at.
    body = api[api.index("pub fn text(") :]
    body = body[: body.index("\n}\n")]
    order = [
        body.index("c_string(name"),
        body.index("Scope::new()"),
        body.index("__register_f(f"),
        body.index("native::bind_text("),
        body.index("resume_unwind"),
        body.index("runtime::check(__status)"),
    ]
    assert order == sorted(order), order
    assert body.count("Scope::new()") == 1


def test_a_callback_does_not_clash_with_the_names_that_the_bindings_use(
    tmp_path: Path, cargo: None
) -> None:
    code = """
#include <functional>
struct F { int a; };
class Registration { public: Registration(); ~Registration(); };
int call(std::function<int(int)> function, std::function<void()> scope, F data, int out, int value);
int registered(std::function<int(F)> registration);
int twin(std::function<int()> f, std::function<int()> _f, std::function<int()> __f);
"""
    api = source(generate(tmp_path, code, verify=True))
    assert "pub fn call(" in api
    assert "pub fn registered(" in api
    assert "pub fn twin(" in api
