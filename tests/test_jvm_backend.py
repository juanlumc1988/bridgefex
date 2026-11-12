# SPDX-License-Identifier: Apache-2.0
"""The JVM backend: the files it writes, its names, and what it refuses.

These tests only generate (the ones that say so need a JDK of Java 22 or later to compile what
is generated); the code that the backend writes is run by test_jvm_roundtrip.py and the
conformance suite.
"""

from __future__ import annotations

import importlib
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
}


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

UNSUPPORTED = {
    "a vector": "#include <vector>\nint f(const std::vector<int>& v);",
    "a vector result": "#include <vector>\nstd::vector<int> f();",
    "a callback": "#include <functional>\nint f(std::function<int(int)> g);",
    "an array": "#include <array>\nint f(const std::array<int, 3>& v);",
    "a method with a vector": (
        "#include <vector>\nclass K { public: K(); ~K(); void m(const std::vector<int>& v); };"
    ),
}


@pytest.mark.parametrize("code", UNSUPPORTED.values(), ids=UNSUPPORTED.keys())
def test_what_the_jvm_bindings_do_not_have_is_refused_saying_which_declaration_and_where(
    tmp_path: Path, code: str
) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    [diagnostic] = raised.value.diagnostics
    assert diagnostic.message.startswith("Java:")
    assert "not supported in Java yet" in diagnostic.message
    assert diagnostic.location is not None
    assert diagnostic.location.line >= 1
    assert "api.h" in str(raised.value)
    # The Python bindings of the same header are generated all the same.
    assert generate(tmp_path, code, languages=("python",))


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
    monkeypatch.setattr(jvm_check, "javac", lambda: None)
    assert jvm_check.check(_plan(), files, tmp_path / "scratch") == []
