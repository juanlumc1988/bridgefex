# SPDX-License-Identifier: Apache-2.0
"""The JVM backend: the files it writes, its names, and what it refuses.

These tests only generate (the ones that say so need a JDK of Java 22 or later to compile what
is generated); the code that the backend writes is run by test_jvm_roundtrip.py and the
conformance suite.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import bridgefex
from bridgefex.backends import reservations
from bridgefex.backends.jvm import naming
from bridgefex.errors import ConfigurationError, GenerationError
from bridgefex.model import Module
from bridgefex.plan import build_plan
from tests.conformance import runner_jvm

# (the package re-exports the function ``check``, which hides the module)
jvm_check = importlib.import_module("bridgefex.backends.jvm.check")

pytestmark = pytest.mark.usefixtures("libclang_loaded")


@pytest.fixture
def java() -> None:
    runner_jvm.skip_without_java()


PRELUDE = (
    "#pragma once\n#include <cstddef>\n#include <cstdint>\n#include <string>\n#include <memory>\n"
)


def generate(
    tmp_path: Path,
    code: str,
    *,
    module: str = "mod",
    languages: tuple[str, ...] = ("jvm",),
    verify: bool = False,
    std: str = "c++17",
    name: str = "api.h",
) -> dict[str, str]:
    header = tmp_path / name
    header.write_text(PRELUDE + code + "\n", encoding="utf-8")
    options = bridgefex.Options(module, std=std, languages=languages, verify=verify)
    return bridgefex.generate([header], options).files


def error_of(tmp_path: Path, code: str, **options: object) -> str:
    with pytest.raises((GenerationError, ConfigurationError)) as raised:
        generate(tmp_path, code, **options)  # type: ignore[arg-type]
    return str(raised.value)


SMALL = """
enum class Color { Red, Green };
struct Point { int x; int y; };
class Counter {
public:
    Counter();
    explicit Counter(int start);
    ~Counter();
    int value() const;
    void add(int delta);
    void add(double delta);
    static int live();
};
int twice(int value);
"""

JAVA = "jvm/mod/src/main/java/mod"


def test_a_project_per_module_and_a_package_per_header(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL)
    assert sorted(path for path in files if path.startswith("jvm/")) == [
        "jvm/mod/pom.xml",
        f"{JAVA}/BridgefexException.java",
        f"{JAVA}/BridgefexFunction.java",
        f"{JAVA}/BridgefexHandle.java",
        f"{JAVA}/BridgefexKeep.java",
        f"{JAVA}/BridgefexLease.java",
        f"{JAVA}/BridgefexNative.java",
        f"{JAVA}/BridgefexObject.java",
        f"{JAVA}/BridgefexRuntime.java",
        f"{JAVA}/BridgefexUnsigned.java",
        f"{JAVA}/api/Color.java",
        f"{JAVA}/api/Counter.java",
        f"{JAVA}/api/Functions.java",
        f"{JAVA}/api/Point.java",
    ]
    assert not any(path.startswith("python/") for path in files)
    assert any(path.startswith("c/") for path in files)
    assert "package mod.api;" in files[f"{JAVA}/api/Counter.java"]
    pom = files["jvm/mod/pom.xml"]
    assert "<maven.compiler.release>22</maven.compiler.release>" in pom


def test_jvm_is_not_generated_unless_it_is_asked_for(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL, languages=bridgefex.DEFAULT_LANGUAGES)
    assert not any(path.startswith("jvm/") for path in files)
    both = generate(tmp_path, SMALL, languages=("python", "jvm"))
    assert any(path.startswith("jvm/") for path in both)
    assert any(path.startswith("python/") for path in both)


def test_the_c_layer_does_not_depend_on_the_languages(tmp_path: Path) -> None:
    only_python = generate(tmp_path, SMALL, languages=("python",))
    only_jvm = generate(tmp_path, SMALL, languages=("jvm",))
    c_python = {path: text for path, text in only_python.items() if path.startswith("c/")}
    c_jvm = {path: text for path, text in only_jvm.items() if path.startswith("c/")}
    assert c_python == c_jvm


def test_the_output_is_deterministic(tmp_path: Path) -> None:
    assert generate(tmp_path, SMALL) == generate(tmp_path, SMALL)


def test_no_generated_file_has_carriage_returns_or_trailing_blank_lines(tmp_path: Path) -> None:
    for path, text in generate(tmp_path, SMALL).items():
        if path.startswith("jvm/"):
            assert "\r" not in text, path
            assert text.endswith("\n"), path
            assert not text.endswith("\n\n"), path


# -- Names ----------------------------------------------------------------------------------------


def test_names_follow_java_and_overloads_keep_the_suffix_of_the_c_api(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL)
    counter = files[f"{JAVA}/api/Counter.java"]
    assert "public void addInt(int delta)" in counter
    assert "public void addDouble(double delta)" in counter
    assert "public static int live()" in counter
    assert "public int value()" in counter
    assert "public static final Color RED" in files[f"{JAVA}/api/Color.java"]
    assert "public static final Color GREEN" in files[f"{JAVA}/api/Color.java"]
    assert "public int x;" in files[f"{JAVA}/api/Point.java"]


@pytest.mark.parametrize(
    ("name", "pascal", "camel", "upper"),
    [
        ("echo_int8", "EchoInt8", "echoInt8", "ECHO_INT8"),
        ("liveInstances", "LiveInstances", "liveInstances", "LIVE_INSTANCES"),
        ("IOError", "IOError", "ioError", "IO_ERROR"),
        ("LOW_LEVEL", "LowLevel", "lowLevel", "LOW_LEVEL"),
        ("x", "X", "x", "X"),
        ("_1st", "_1st", "_1st", "_1ST"),
        ("", "_x", "_x", "_X"),
    ],
)
def test_the_case_conversions(name: str, pascal: str, camel: str, upper: str) -> None:
    assert naming.pascal(name) == pascal
    assert naming.camel(name) == camel
    assert naming.upper_snake(name) == upper


def test_keywords_get_an_underscore() -> None:
    assert naming.identifier("package") == "package_"
    assert naming.identifier("native") == "native_"
    assert naming.identifier("value") == "value"
    assert naming.parameter("java") == "java_"
    assert naming.parameter("__arena") == "arena"
    assert naming.parameter("class") == "class_"


def test_keywords_of_java_that_c_plus_plus_has_as_names_are_escaped(tmp_path: Path) -> None:
    code = "int package(int native, int synchronized);\nstruct S { int import; int extends; };"
    files = generate(tmp_path, code)
    functions = files[f"{JAVA}/api/Functions.java"]
    assert "package_(int native_, int synchronized_)" in functions
    struct = files[f"{JAVA}/api/S.java"]
    assert "public int import_;" in struct
    assert "public int extends_;" in struct


def test_a_module_cannot_hide_the_packages_of_the_platform(tmp_path: Path) -> None:
    for module in ("java", "javax", "jdk"):
        assert "Java:" in error_of(tmp_path, SMALL, module=module)
    # The other languages do not care.
    assert generate(tmp_path, SMALL, module="java", languages=("python",))


@pytest.mark.parametrize(
    "name",
    ["BridgefexObject", "BridgefexRuntime", "BridgefexNative", "BridgefexHandle", "BridgefexLease"],
)
def test_a_class_cannot_hide_a_type_of_the_runtime(tmp_path: Path, name: str) -> None:
    message = error_of(tmp_path, f"class {name} {{ public: {name}(); ~{name}(); }};")
    assert name in message
    assert "runtime" in message


def test_two_classes_whose_files_differ_only_by_case_are_refused(tmp_path: Path) -> None:
    code = "class Foo { public: Foo(); ~Foo(); };\nclass FOO { public: FOO(); ~FOO(); };"
    assert "differ only by case" in error_of(tmp_path, code)


def test_a_class_cannot_have_the_name_of_the_functions_of_its_header(tmp_path: Path) -> None:
    assert "Functions" in error_of(
        tmp_path,
        "class Functions { public: Functions(); ~Functions(); };\nint f(int x);",
    )


def test_two_methods_that_get_the_same_java_name_are_refused(tmp_path: Path) -> None:
    code = "class Name { public: Name(); ~Name(); int Name_(); int name(); };"
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    [diagnostic] = raised.value.diagnostics
    assert "gets the Java method 'name'" in diagnostic.message
    assert "Name_" in diagnostic.message
    assert diagnostic.location is not None


# -- Types ----------------------------------------------------------------------------------------


def test_integers_that_java_has_no_unsigned_type_for_are_widened_or_annotated(
    tmp_path: Path,
) -> None:
    code = (
        "std::uint8_t a(std::uint8_t x);\n"
        "std::uint16_t b(std::uint16_t x);\n"
        "std::uint32_t c(std::uint32_t x);\n"
        "std::uint64_t d(std::uint64_t x);\n"
        "std::size_t e(std::size_t x);\n"
        "std::int64_t f(std::int64_t x);\n"
    )
    text = generate(tmp_path, code)[f"{JAVA}/api/Functions.java"]
    assert "public static short a(short x)" in text
    assert "public static int b(int x)" in text
    assert "public static long c(long x)" in text
    assert "@BridgefexUnsigned\n    public static long d(@BridgefexUnsigned long x)" in text
    assert "@BridgefexUnsigned\n    public static long e(@BridgefexUnsigned long x)" in text
    assert "public static long f(long x)" in text
    assert "@BridgefexUnsigned" not in text.split("public static long f")[1]


def test_unsigned_integers_of_8_and_16_bits_go_to_a_downcall_as_32_bit_integers(
    tmp_path: Path,
) -> None:
    """A callee may rely on the caller having extended an unsigned argument (clang does): the
    layout of one byte or two would be extended as a signed one (200 would be -56)."""
    files = generate(tmp_path, "void f(std::uint8_t a, std::uint16_t b, std::uint32_t c, short d);")
    native = files[f"{JAVA}/BridgefexNative.java"]
    assert (
        "FunctionDescriptor.of(BridgefexRuntime.C_INT, BridgefexRuntime.C_INT, "
        "BridgefexRuntime.C_INT, BridgefexRuntime.C_INT, BridgefexRuntime.C_SHORT)" in native
    )
    assert "int p_a, int p_b, int p_c, short p_d" in native
    functions = files[f"{JAVA}/api/Functions.java"]
    assert 'BridgefexRuntime.argU8("a", a)' in functions
    assert 'BridgefexRuntime.argU16("b", b)' in functions
    # The members of a struct are in memory: they keep the layout of their width.
    struct = generate(tmp_path, "struct S { std::uint8_t a; std::uint16_t b; };")[
        f"{JAVA}/api/S.java"
    ]
    assert "BridgefexRuntime.C_BYTE" in struct
    assert "BridgefexRuntime.C_SHORT" in struct
    assert "argU8" not in struct


def test_the_handle_constructor_is_not_public_so_that_null_is_not_ambiguous(
    tmp_path: Path,
) -> None:
    code = (
        "class Node { public: Node(); ~Node(); };\n"
        "class Holder { public: explicit Holder(Node* node); ~Holder(); };"
    )
    text = generate(tmp_path, code)[f"{JAVA}/api/Holder.java"]
    assert "protected Holder(BridgefexHandle handle)" in text
    assert "public static Holder wrap(BridgefexHandle handle)" in text
    assert "public Holder(BridgefexHandle" not in text


def test_a_constructor_that_java_cannot_tell_apart_is_a_static_method(tmp_path: Path) -> None:
    code = "class C { public: explicit C(const std::string& s); explicit C(const char* s); ~C(); };"
    text = generate(tmp_path, code)[f"{JAVA}/api/C.java"]
    assert "public static C createString(java.lang.String s)" in text
    assert "public static C createCstr(java.lang.String s)" in text
    assert "public C(" not in text


def test_a_class_has_one_superclass_and_converts_to_its_other_bases(tmp_path: Path) -> None:
    code = (
        "class A { public: A(); virtual ~A(); int a(); };\n"
        "class B { public: B(); virtual ~B(); int b(); };\n"
        "class C : public A, public B { public: C(); };"
    )
    text = generate(tmp_path, code)[f"{JAVA}/api/C.java"]
    assert "public class C extends mod.api.A" in text
    assert "public mod.api.B asB()" in text
    assert "public int b()" in text  # the methods of the second base are copied
    assert "public int a()" not in text  # and those of the first one are inherited


def test_the_accessor_of_a_base_does_not_take_the_name_of_a_method(tmp_path: Path) -> None:
    code = (
        "class A { public: A(); virtual ~A(); };\n"
        "class B { public: B(); virtual ~B(); };\n"
        "class C : public A, public B { public: C(); int asB(); };"
    )
    text = generate(tmp_path, code)[f"{JAVA}/api/C.java"]
    assert "public int asB()" in text
    assert "public mod.api.B asB_()" in text


# -- Inheritance: Java cannot hide a method -------------------------------------------------------

HIDING = {
    "a non-virtual method with the same signature": (
        "class A { public: A(); ~A(); int f(int x); };\n"
        "class B : public A { public: B(); int f(int x); };"
    ),
    "a method with another result": (
        "class A { public: A(); ~A(); int f(int x); };\n"
        "class B : public A { public: B(); std::string f(int x); };"
    ),
    "a static method that hides an instance one": (
        "class A { public: A(); ~A(); int n(); };\n"
        "class B : public A { public: B(); static int n(); };"
    ),
    # C++ calls A::f through an A: Java would run B.f.
    "a virtual method over one that is not virtual": (
        "class A { public: A(); ~A(); int f(); };\n"
        "class B : public A { public: B(); virtual ~B(); virtual int f(); };"
    ),
    # A std::string and a const char* are one String in Java, two overloads in C++.
    "an overload that Java cannot tell from the virtual method": (
        "class A { public: A(); virtual ~A(); virtual int f(const std::string& s); };\n"
        "class B : public A { public: B(); virtual int f(const char* s); };"
    ),
    "a long and a long long": (
        "class A { public: A(); virtual ~A(); virtual int f(long x); };\n"
        "class B : public A { public: B(); virtual int f(long long x); };"
    ),
    "a const method over one that is not": (
        "class A { public: A(); virtual ~A(); virtual int f(); };\n"
        "class B : public A { public: B(); virtual int f() const; };"
    ),
}


@pytest.mark.parametrize("code", HIDING.values(), ids=HIDING.keys())
def test_a_method_that_would_hide_one_of_its_base_is_refused_where_it_is_declared(
    tmp_path: Path, code: str
) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    [diagnostic] = raised.value.diagnostics
    assert "Java cannot hide a method" in diagnostic.message
    assert "'A' has too" in diagnostic.message
    assert diagnostic.location is not None
    assert diagnostic.location.line == PRELUDE.count("\n") + 2  # the second class
    # The Python bindings of the same header are generated all the same.
    assert generate(tmp_path, code, languages=("python",))


OVERRIDES = {
    "a virtual method": (
        "class A { public: A(); virtual ~A(); virtual int f(int x); };\n"
        "class B : public A { public: B(); int f(int x) override; };"
    ),
    "a static method": (
        "class A { public: A(); ~A(); static int n(); };\n"
        "class B : public A { public: B(); static int n(); };"
    ),
    "another parameter (an overload)": (
        "class A { public: A(); ~A(); int f(int x); };\n"
        "class B : public A { public: B(); int f(double x); };"
    ),
    "a covariant result": (
        "class A { public: A(); virtual ~A(); virtual A& self(); };\n"
        "class B : public A { public: B(); B& self() override; };"
    ),
    "the same name in the second base": (
        "class A { public: A(); virtual ~A(); int f(); };\n"
        "class B { public: B(); virtual ~B(); int f(); };\n"
        "class C : public A, public B { public: C(); };"
    ),
    "the same name in the class and its second base": (
        "class A { public: A(); virtual ~A(); };\n"
        "class B { public: B(); virtual ~B(); int f(); };\n"
        "class C : public A, public B { public: C(); int f(int x); };"
    ),
}


SECOND_BASE = (
    "class X { public: X(); virtual ~X(); {x}; };\n"
    "class A { public: A(); virtual ~A(); };\n"
    "class B : public A, public X { public: B(); };\n"
    "class C : public B { public: C(); {c}; };"
)


def test_a_method_that_hides_one_that_a_class_has_of_its_second_base_is_refused(
    tmp_path: Path,
) -> None:
    """B has the methods of X (it is not its first base): C::f does not override X::f, and in
    Java it would replace the copy that B has, so that a call through B runs it."""
    code = SECOND_BASE.replace("{x}", "int f()").replace("{c}", "int f()")
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    [diagnostic] = raised.value.diagnostics
    assert "Java cannot hide a method" in diagnostic.message
    assert "'X' has too" in diagnostic.message
    assert generate(tmp_path, code, languages=("python",))


def test_a_method_that_overrides_one_of_a_second_base_is_accepted(
    tmp_path: Path, java: None
) -> None:
    code = SECOND_BASE.replace("{x}", "virtual int f()").replace("{c}", "int f() override")
    assert generate(tmp_path, code, verify=True)


@pytest.mark.parametrize("code", OVERRIDES.values(), ids=OVERRIDES.keys())
def test_what_java_can_override_or_overload_is_accepted_and_compiles(
    tmp_path: Path, java: None, code: str
) -> None:
    assert generate(tmp_path, code, verify=True)


# -- Hostile names compile ------------------------------------------------------------------------

COMPILES = {
    "classes named like the types of java.lang": """
class String { public: String(); ~String(); int length() const; };
class Object { public: Object(); ~Object(); int hashCode() const; };
class Override { public: Override(); ~Override(); };
class Integer { public: Integer(); ~Integer(); };
class Exception { public: Exception(); ~Exception(); };
class Class { public: Class(); ~Class(); };
class Throwable { public: Throwable(); ~Throwable(); void f(String* s); };
class Runnable { public: Runnable(); ~Runnable(); };
struct Math { int x; };
struct System { int out; };
int use(const Math& m, System s, std::string str);
""",
    "the methods of every object": """
class Thing { public: Thing(); ~Thing();
  int equals() const; int hashCode() const; int toString() const; int getClass() const;
  void wait(int x); void notify(); void notifyAll(); int clone() const; void finalize();
  void close(); bool isClosed() const; void hold(); void retain(); void borrow(); void wrap();
  void cppType();
  void handle(); void isReadOnly(); };
""",
    "parameters named like what the code uses": """
int f(int java, int javax, int jdk, int mod, int handle, int pointer, int owners, int readOnly,
      int give, int lease, int view, int error);
int g(const std::string& __arena, const std::string& __result, std::string __self);
class Thing { public: Thing(); ~Thing();
  int m(int java, int mod, const Thing& other, Thing* handle) const; };
""",
    "enumerators named like the code of the enum class": """
enum class Kind { class_ = 1, int_, values, of, value, name, ordinal, BRIDGEFEX_VALUES, equals,
                  hashCode, toString };
Kind roundtrip(Kind k);
""",
    "members of a struct named like the package of the module": """
struct Inner { int value; int LAYOUT; int write; int read; };
struct Outer { Inner mod; Inner java; int handle; int name; };
Outer make(Inner mod);
""",
    "a member named like the package of the module": """
struct S { int mod; };
struct T { S s; int mod; };
class C { public: C(); ~C(); int mod(const T& t) const; };
""",
    "enums nested in a class": """
class Outer { public: enum class Kind { A, B }; enum Plain { X, Y }; Outer(); ~Outer();
  Kind kind() const; void set(Kind k); static Plain plain(); };
""",
    "overloads": """
void f(int a); void f(long a); void f(const std::string& s); void f(double d);
class C { public: C(); explicit C(int a); explicit C(long a); explicit C(const std::string& s);
  explicit C(const char* s); ~C(); int get(int) const; int get(long) const; };
""",
    "a module without classes": "int twice(int x);",
    # The constructor assigns the handle that the base class has: the parameter hides the field.
    "a constructor with a parameter named handle": """
class N { public: N(); ~N(); };
class K { public: explicit K(int handle); explicit K(N* handle); ~K(); };
""",
    "parameters that get the same Java name": """
int f(int x, int x_);
int g(int foo_bar, int fooBar);
int h(int Name, int name);
int i(int java, int java_);
int j(int mod, int mod_);
int k(int, int arg1, int);
class K { public: K(int x, int x_); ~K(); int m(int Name, int name); };
""",
    "members of a struct that differ only by case": """
struct S { int fooBar; int foobar; int FOOBAR; };
S make(S s);
""",
    "members of a struct named like the variables of its methods": """
struct S { int that; int other; double that_; };
struct T { S that[2]; int other; };
T make(T t);
""",
    "types named like the annotations of java.lang": """
struct SuppressWarnings { int x; };
enum class Deprecated { A, B };
class FunctionalInterface { public: FunctionalInterface(); ~FunctionalInterface(); };
class K { public: K(); ~K(); SuppressWarnings get() const; };
""",
    "a class with several bases, in a module named lease": """
class A { public: A(); virtual ~A(); int a(); };
class B { public: B(); virtual ~B(); int b(); };
class C : public A, public B { public: C(); A& first(); B& second(); };
""",
}


@pytest.mark.parametrize("module", ["lease", "view", "owners", "origin", "error"])
def test_a_module_named_like_a_local_of_the_generated_code_compiles(
    tmp_path: Path, java: None, module: str
) -> None:
    code = (
        "class A { public: A(); virtual ~A(); int a(); };\n"
        "class B { public: B(); virtual ~B(); int b(); };\n"
        "class C : public A, public B { public: C(); A& first(); B& second(); };\n"
        "class D { public: D(); ~D(); C& make(); };"
    )
    assert generate(tmp_path, code, module=module, verify=True)


def test_a_module_named_like_the_field_of_the_classes_is_refused(tmp_path: Path) -> None:
    assert "'handle'" in error_of(tmp_path, SMALL, module="handle")


@pytest.mark.parametrize("code", COMPILES.values(), ids=COMPILES.keys())
def test_what_hides_the_names_of_the_generated_code_compiles(
    tmp_path: Path, java: None, code: str
) -> None:
    assert generate(tmp_path, code, verify=True)


@pytest.mark.parametrize("module", ["object", "exception", "system", "integer", "a"])
def test_a_module_named_like_a_type_of_java_compiles(
    tmp_path: Path, java: None, module: str
) -> None:
    assert generate(tmp_path, SMALL, module=module, verify=True)


@pytest.mark.parametrize(
    "stem",
    [
        "string",
        "object",
        "functions",
        "api",
        "mod",
        "java",
        "lang",
        "bridgefex_object",
        "BridgefexRuntime",
    ],
)
def test_a_header_named_like_a_type_of_java_compiles(tmp_path: Path, java: None, stem: str) -> None:
    assert generate(tmp_path, SMALL, name=f"{stem}.h", verify=True)


# -- What the Java bindings do not have yet -------------------------------------------------------


def refused_directors(
    tmp_path: Path, classes: tuple[str, ...] = ("Widget",), languages: tuple[str, ...] = ("jvm",)
) -> dict[str, str]:
    """Generates a header whose classes ask for a director, which Java cannot do yet."""
    annotations = tmp_path / "directors.toml"
    annotations.write_text(
        "directors = [" + ", ".join(f'"mod_{name}"' for name in classes) + "]\n", encoding="utf-8"
    )
    header = tmp_path / "api.h"
    header.write_text(
        PRELUDE
        + "".join(
            f"class {name} {{ public: {name}(); virtual ~{name}(); virtual int draw(); }};\n"
            for name in classes
        )
        + "int other(int);\n",
        encoding="utf-8",
    )
    options = bridgefex.Options(
        "mod", std="c++17", languages=languages, verify=False, annotations=(annotations,)
    )
    return bridgefex.generate([header], options).files


def test_what_the_jvm_bindings_do_not_have_is_refused_saying_which_declaration_and_where(
    tmp_path: Path,
) -> None:
    with pytest.raises(GenerationError) as raised:
        refused_directors(tmp_path)
    [diagnostic, advice] = raised.value.diagnostics
    assert diagnostic.message.startswith("Java:")
    assert "not supported in Java yet" in diagnostic.message
    assert diagnostic.location is not None
    assert diagnostic.location.line >= 1
    assert "api.h" in str(raised.value)
    # What to do about it.
    assert "leave 'jvm' out" in advice.message
    # The Python bindings of the same header are generated all the same.
    assert refused_directors(tmp_path, languages=("python",))


def test_every_declaration_that_the_jvm_bindings_cannot_do_is_reported(tmp_path: Path) -> None:
    with pytest.raises(GenerationError) as raised:
        refused_directors(tmp_path, ("Widget", "Gadget", "Gizmo"))
    refused = [d for d in raised.value.diagnostics if d.location is not None]
    lines = sorted(d.location.line for d in refused if d.location is not None)
    base = PRELUDE.count("\n")
    assert lines == [base + 1, base + 2, base + 3]  # the three classes
    assert len(raised.value.diagnostics) == 4  # and what to do


def test_a_class_that_asks_for_a_director_is_refused(tmp_path: Path) -> None:
    """Without a director C++ would never call what a subclass in Java overrides."""
    annotations = tmp_path / "directors.toml"
    annotations.write_text('directors = ["mod_Widget"]\n', encoding="utf-8")
    header = tmp_path / "api.h"
    header.write_text(
        PRELUDE + "class Widget { public: Widget(); virtual ~Widget(); virtual int draw(); };\n",
        encoding="utf-8",
    )
    options = bridgefex.Options(
        "mod", std="c++17", languages=("jvm",), verify=False, annotations=(annotations,)
    )
    with pytest.raises(GenerationError) as raised:
        bridgefex.generate([header], options)
    message = str(raised.value)
    assert "'Widget' asks for a director" in message
    assert "api.h" in message
    # The other languages have it.
    python = bridgefex.Options(
        "mod", std="c++17", languages=("python",), verify=False, annotations=(annotations,)
    )
    assert bridgefex.generate([header], python).files


# -- The check with the compiler of Java -----------------------------------------------------------


def _plan():  # type: ignore[no-untyped-def]
    return build_plan(Module("mod", ()), None, reservations())


def test_the_check_reports_code_that_does_not_compile(tmp_path: Path, java: None) -> None:
    files = generate(tmp_path, SMALL)
    files[f"{JAVA}/api/Counter.java"] += '\nclass Broken { int x = "text"; }\n'
    diagnostics = jvm_check.check(_plan(), files, tmp_path / "scratch")
    assert diagnostics
    assert "does not compile" in diagnostics[0].message
    assert "incompatible types" in diagnostics[0].message


def test_the_check_passes_generated_code(tmp_path: Path, java: None) -> None:
    files = generate(tmp_path, SMALL)
    assert jvm_check.check(_plan(), files, tmp_path / "scratch") == []


def test_the_check_treats_warnings_as_errors(tmp_path: Path, java: None) -> None:
    files = generate(tmp_path, SMALL)
    files[f"{JAVA}/api/Counter.java"] += "\nclass Lax { java.util.List raw; }\n"
    diagnostics = jvm_check.check(_plan(), files, tmp_path / "scratch")
    assert diagnostics
    assert "does not compile" in diagnostics[0].message or "warnings" in diagnostics[0].message


def test_the_check_can_be_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = generate(tmp_path, SMALL)
    files[f"{JAVA}/api/Counter.java"] += "\nthis is not Java\n"
    monkeypatch.setenv(jvm_check.ENV_SKIP, "1")
    assert jvm_check.check(_plan(), files, tmp_path / "scratch") == []


def test_without_a_jdk_the_code_is_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = generate(tmp_path, SMALL)
    files[f"{JAVA}/api/Counter.java"] += "\nthis is not Java\n"
    monkeypatch.setattr(jvm_check, "javac", lambda *_: None)
    assert jvm_check.check(_plan(), files, tmp_path / "scratch") == []


# -- Limits of the Java class file and of the file system -----------------------------------------


def test_a_library_with_many_functions_compiles(tmp_path: Path, java: None) -> None:
    """The handles are not initialised by one class initialiser, which Java limits to 64 KB: 1000
    functions with three parameters did not compile."""
    code = "\n".join(f"int f{n}(int a, double b, const std::string& c);" for n in range(1200))
    files = generate(tmp_path, code, verify=True)
    native = files[f"{JAVA}/BridgefexNative.java"]
    assert "static final class Handles" not in native
    assert native.count("BridgefexRuntime.bind(") == 1200 + 3


def test_a_function_binds_when_it_is_first_called(tmp_path: Path) -> None:
    """A function that the library lacks fails alone, and the others work."""
    native = generate(tmp_path, SMALL)[f"{JAVA}/BridgefexNative.java"]
    assert re.search(r"private static final class Handle\d+ ", native)
    assert "class Handles" not in native
    assert "BridgefexRuntime.ensureLoaded();" in native


def test_an_enum_with_too_many_enumerators_is_refused_where_it_is_declared(
    tmp_path: Path,
) -> None:
    code = "enum class Big { " + ", ".join(f"E{n}" for n in range(2001)) + " };"
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    diagnostic = raised.value.diagnostics[0]
    assert "'Big' has 2001 enumerators" in diagnostic.message
    assert diagnostic.location is not None
    assert diagnostic.location.line == PRELUDE.count("\n") + 1
    assert generate(tmp_path, code, languages=("python",))


def test_an_enum_with_many_enumerators_compiles(tmp_path: Path, java: None) -> None:
    code = "enum class Big { " + ", ".join(f"E{n}" for n in range(2000)) + " };\nBig same(Big b);"
    assert generate(tmp_path, code, verify=True)


def test_a_struct_with_many_members_and_a_long_array_of_structs_compiles(
    tmp_path: Path, java: None
) -> None:
    """The constructor with a parameter for each member would have more than the 255 slots that a
    method has, and an initialiser with an element for each place of a long array is too big."""
    members = "\n".join(f"int m{n};" for n in range(300))
    code = (
        "struct Item { int a; };\n"
        f"struct Wide {{ {members} }};\n"
        "struct Long { Item items[6000]; };\n"
        "Wide same(Wide w);\nLong again(Long l);"
    )
    files = generate(tmp_path, code, verify=True)
    wide = files[f"{JAVA}/api/Wide.java"]
    assert "public Wide()" in wide
    assert "public Wide(int m0" not in wide  # too many parameters for the constructor of all
    assert (
        "public Wide(int m0"
        in generate(tmp_path, "struct Wide { int m0; int m1; };", verify=False)[
            f"{JAVA}/api/Wide.java"
        ]
    )


def test_a_function_with_too_many_parameters_is_refused_where_it_is_declared(
    tmp_path: Path,
) -> None:
    parameters = ", ".join(f"const std::string& s{n}" for n in range(90))
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, f"int many({parameters});")
    diagnostic = raised.value.diagnostics[0]
    assert "too many parameters" in diagnostic.message
    assert "many" in diagnostic.message


def test_a_very_long_name_is_refused_not_a_crash(tmp_path: Path) -> None:
    long_name = "L" * 300
    message = error_of(tmp_path, f"class {long_name} {{ public: {long_name}(); ~{long_name}(); }};")
    assert "longer than 100 characters" in message


@pytest.mark.parametrize("name", ["Aux", "con", "NUL", "Com1", "lpt9"])
def test_a_class_named_like_a_file_of_windows_is_refused(tmp_path: Path, name: str) -> None:
    message = error_of(tmp_path, f"struct {name} {{ int x; }};")
    assert "Windows" in message


def test_a_header_named_like_a_file_of_windows_is_refused(tmp_path: Path) -> None:
    assert "Windows" in error_of(tmp_path, SMALL, name="aux.h")


# -- Names inside a class -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["BridgefexHandle", "BridgefexObject", "BridgefexRuntime"])
def test_an_enum_inside_a_class_cannot_hide_a_type_of_the_runtime(
    tmp_path: Path, name: str
) -> None:
    code = f"class K {{ public: enum class {name} {{ A }}; K(); ~K(); }};"
    message = error_of(tmp_path, code)
    assert f"'K::{name}'" in message
    assert "runtime" in message
    inside_a_struct = f"struct S {{ enum class {name} {{ A }}; int x; }};"
    assert "runtime" in error_of(tmp_path, inside_a_struct)


def test_an_enum_inside_a_class_cannot_have_the_name_of_the_class(tmp_path: Path) -> None:
    code = "class foo_bar { public: enum class FooBar { A }; foo_bar(); ~foo_bar(); };"
    message = error_of(tmp_path, code)
    assert "'foo_bar::FooBar'" in message
    assert "own name" in message


def test_two_enums_inside_a_class_that_get_the_same_name_are_refused(tmp_path: Path) -> None:
    code = "class K { public: enum class foo_bar { A }; enum class FooBar { B }; K(); ~K(); };"
    message = error_of(tmp_path, code)
    assert "'K::foo_bar'" in message
    assert "'K::FooBar'" in message


# -- Containers -----------------------------------------------------------------------------------

CONTAINERS = """
#include <array>
#include <vector>
enum class Color : std::uint8_t { Red, Green };
struct Point { double x; double y; };
std::vector<int> ints(const std::vector<int>& v);
std::vector<std::uint8_t> bytes(const std::vector<std::uint8_t>& v);
std::vector<std::uint64_t> wide(const std::vector<std::uint64_t>& v);
std::vector<bool> flags(const std::vector<bool>& v);
std::vector<std::string> words(const std::vector<std::string>& v);
std::vector<Color> colors(const std::vector<Color>& v);
std::vector<Point> points(const std::vector<Point>& v);
std::array<float, 4> four(const std::array<float, 4>& v);
class Bag { public: Bag(const std::vector<std::string>& items); ~Bag();
            std::vector<int> counts() const; void add(const std::vector<int>& c); };
"""


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_a_container_is_an_array_of_the_java_type_of_its_elements(
    tmp_path: Path, language: str
) -> None:
    files = generate(tmp_path, CONTAINERS, languages=(language,))
    functions = files[f"{language}/mod/src/main/java/mod/api/Functions.java"]
    for signature in (
        "public static int[] ints(int[] v)",
        "public static short[] bytes(short[] v)",
        "public static boolean[] flags(boolean[] v)",
        "public static java.lang.String[] words(java.lang.String[] v)",
        "public static mod.api.Color[] colors(mod.api.Color[] v)",
        "public static mod.api.Point[] points(mod.api.Point[] v)",
        "public static float[] four(float[] v)",
        "@BridgefexUnsigned\n    public static long[] wide(@BridgefexUnsigned long[] v)",
    ):
        assert signature in functions
    bag = files[f"{language}/mod/src/main/java/mod/api/Bag.java"]
    assert "public Bag(java.lang.String[] items)" in bag
    assert "public int[] counts()" in bag
    assert "public void add(int[] c)" in bag


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_a_container_that_is_not_null_and_has_the_length_of_its_type(
    tmp_path: Path, language: str
) -> None:
    functions = generate(tmp_path, CONTAINERS, languages=(language,))[
        f"{language}/mod/src/main/java/mod/api/Functions.java"
    ]
    assert 'BridgefexRuntime.nonNull(v, "v");' in functions
    assert "must have 4 elements, got " in functions
    # What C++ allocated for the result is freed whatever happens.
    assert "mod_free(" in functions


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_the_messages_of_a_container_name_the_java_parameter(tmp_path: Path, language: str) -> None:
    """The C name of a parameter (``my_values``) is not the one that the Java code has
    (``myValues``): the message of an error names the one that the caller wrote."""
    code = (
        "#include <array>\n#include <cstdint>\n"
        "int total(const std::array<std::uint8_t, 2>& my_values);"
    )
    functions = generate(tmp_path, code, languages=(language,))[
        f"{language}/mod/src/main/java/mod/api/Functions.java"
    ]
    assert "argument 'myValues' must have 2 elements" in functions
    assert '"myValues[" + ' in functions
    assert "argument 'my_values'" not in functions
    assert '"my_values[' not in functions


@pytest.mark.parametrize("language", ["jvm", "jni"])
@pytest.mark.parametrize("length", ["2147483640ULL", "3000000000ULL"])
def test_a_std_array_longer_than_a_java_array_is_refused(
    tmp_path: Path, language: str, length: str
) -> None:
    """The array of Java and its length (a literal) are an int: javac refused the code."""
    code = (
        "#include <array>\n#include <cstdint>\n"
        f"std::array<std::uint8_t, {length}> big(const std::array<std::uint8_t, {length}>& v);"
    )
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code, languages=(language,))
    assert "more than a Java array holds" in raised.value.diagnostics[0].message


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_the_containers_compile(tmp_path: Path, java: None, language: str) -> None:
    assert generate(tmp_path, CONTAINERS, languages=(language,), verify=True)


def test_a_container_of_something_else_is_refused(tmp_path: Path) -> None:
    code = (
        "#include <vector>\nclass K { public: K(); ~K(); };\n"
        "std::vector<K*> pointers();\nstd::vector<std::vector<int>> rows();"
    )
    with pytest.raises((GenerationError, ConfigurationError)):
        generate(tmp_path, code)


# -- Callbacks ---------------------------------------------------------------------------------

CALLBACKS = """
#include <cstdint>
#include <functional>
#include <string>
enum class Level : std::uint8_t { Low, High };
struct Pt { double x; double y; };
void none(std::function<void()> f);
int supply(std::function<int()> f);
void one(std::function<void(int)> f);
bool two(std::function<bool(int, double)> f);
void three(std::function<void(int, const std::string&, Pt)> f);
Level four(std::function<Level(int, int, int, int)> f);
void same_a(std::function<int(int)> f);
void same_b(std::function<int(int)> g);
class Holder { public: Holder(std::function<void(std::uint8_t)> f); ~Holder(); };
"""


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_a_std_function_is_the_functional_interface_of_its_arity(
    tmp_path: Path, language: str
) -> None:
    files = generate(tmp_path, CALLBACKS, languages=(language,))
    functions = files[f"{language}/mod/src/main/java/mod/api/Functions.java"]
    for signature in (
        "none(java.lang.Runnable f)",
        "supply(java.util.function.Supplier<java.lang.Integer> f)",
        "one(java.util.function.Consumer<java.lang.Integer> f)",
        "two(java.util.function.BiFunction<java.lang.Integer, java.lang.Double, "
        "java.lang.Boolean> f)",
        "three(mod.BridgefexCallbacks.Consumer3<java.lang.Integer, java.lang.String, "
        "mod.api.Pt> f)",
        "four(mod.BridgefexCallbacks.Function4<java.lang.Integer, java.lang.Integer, "
        "java.lang.Integer, java.lang.Integer, mod.api.Level> f)",
    ):
        assert signature in functions
    # An unsigned integer is the next bigger type, as everywhere else.
    holder = files[f"{language}/mod/src/main/java/mod/api/Holder.java"]
    assert "Holder(java.util.function.Consumer<java.lang.Short> f)" in holder
    callbacks = files[f"{language}/mod/src/main/java/mod/BridgefexCallbacks.java"]
    assert "public interface Consumer3<T1, T2, T3>" in callbacks
    assert "public interface Function4<T1, T2, T3, T4, TResult>" in callbacks
    assert "Consumer1" not in callbacks  # java.util.function has it


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_the_callbacks_of_one_signature_share_a_thunk(tmp_path: Path, language: str) -> None:
    files = generate(tmp_path, CALLBACKS, languages=(language,))
    callbacks = files[f"{language}/mod/src/main/java/mod/BridgefexCallbacks.java"]
    thunks = re.findall(r"private static \w+ (thunk\d+)\(", callbacks)
    # none, supply, one, two, three, four, int(int) for same_a and same_b, and the holder's.
    assert len(thunks) == 8
    functions = files[f"{language}/mod/src/main/java/mod/api/Functions.java"]
    used = re.findall(r"BridgefexCallbacks\.(Thunk\d+)\.address\(\)", functions)
    assert len(used) == len(set(used)) + 1  # same_a and same_b use the same one


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_the_callbacks_compile(tmp_path: Path, java: None, language: str) -> None:
    assert generate(tmp_path, CALLBACKS, languages=(language,), verify=True)


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_a_call_that_gives_a_callback_raises_its_exception_and_drops_it_if_it_never_runs(
    tmp_path: Path, language: str
) -> None:
    functions = generate(tmp_path, CALLBACKS, languages=(language,))[
        f"{language}/mod/src/main/java/mod/api/Functions.java"
    ]
    assert "BridgefexCallbacks.Scope __scope = BridgefexCallbacks.begin()" in functions
    assert '__scope.register(f, "f", mod.BridgefexCallbacks.Thunk' in functions
    assert "__scope.returned();" in functions
    assert "__scope.check(__status);" in functions
    # A call that gives none is a call all the same (a callback that C++ stored may run in it).
    assert "BridgefexCallbacks.enter()" not in functions  # every function here gives one
    plain = generate(tmp_path, CALLBACKS + "\nint plain(int);\n", languages=(language,))[
        f"{language}/mod/src/main/java/mod/api/Functions.java"
    ]
    assert "BridgefexCallbacks.Call __call = BridgefexCallbacks.enter()" in plain


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_a_callback_with_more_arguments_than_the_bindings_have_interfaces_for_is_refused(
    tmp_path: Path, language: str
) -> None:
    arguments = ", ".join("int" for _ in range(65))
    with pytest.raises(GenerationError) as raised:
        generate(
            tmp_path,
            f"#include <functional>\nvoid wide(std::function<void({arguments})> f);",
            languages=(language,),
        )
    assert "a callback with 65 arguments" in str(raised.value)


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_a_module_without_callbacks_has_no_callbacks_class(tmp_path: Path, language: str) -> None:
    files = generate(tmp_path, SMALL, languages=(language,))
    assert not any(path.endswith("BridgefexCallbacks.java") for path in files)
