# SPDX-License-Identifier: Apache-2.0
"""The C# backend: the files it writes, its names, and what it refuses.

These tests only generate (the compiler of .NET is needed by the ones that say so); the code that
the backend writes is run by test_csharp_roundtrip.py and the conformance suite.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

import bridgefex
from bridgefex.backends import reservations
from bridgefex.backends.csharp import naming
from bridgefex.errors import ConfigurationError, GenerationError
from bridgefex.model import Module
from bridgefex.plan import build_plan

# (the package re-exports the function ``check``, which hides the module)
csharp_check = importlib.import_module("bridgefex.backends.csharp.check")

pytestmark = pytest.mark.usefixtures("libclang_loaded")

needs_dotnet = pytest.mark.usefixtures("dotnet")

PRELUDE = "#pragma once\n#include <cstddef>\n#include <cstdint>\n#include <string>\n"


def generate(
    tmp_path: Path,
    code: str,
    *,
    module: str = "mod",
    languages: tuple[str, ...] = ("csharp",),
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


# -- The files ----------------------------------------------------------------------------------


def test_a_project_per_module_and_a_file_per_header(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL)
    assert sorted(path for path in files if path.startswith("csharp/")) == [
        "csharp/Mod/Api.cs",
        "csharp/Mod/Mod.csproj",
        "csharp/Mod/Native.cs",
        "csharp/Mod/Objects.cs",
        "csharp/Mod/Runtime.cs",
    ]
    assert not any(path.startswith("python/") for path in files)
    assert any(path.startswith("c/") for path in files)
    assert "namespace Mod.Api;" in files["csharp/Mod/Api.cs"]
    assert "<TargetFramework>net8.0</TargetFramework>" in files["csharp/Mod/Mod.csproj"]
    assert "<AllowUnsafeBlocks>true</AllowUnsafeBlocks>" in files["csharp/Mod/Mod.csproj"]


def test_csharp_is_not_generated_unless_it_is_asked_for(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL, languages=bridgefex.DEFAULT_LANGUAGES)
    assert not any(path.startswith("csharp/") for path in files)
    both = generate(tmp_path, SMALL, languages=("python", "csharp"))
    assert any(path.startswith("csharp/") for path in both)
    assert any(path.startswith("python/") for path in both)


def test_the_c_layer_does_not_depend_on_the_languages(tmp_path: Path) -> None:
    only_python = generate(tmp_path, SMALL, languages=("python",))
    only_csharp = generate(tmp_path, SMALL, languages=("csharp",))
    c_python = {path: text for path, text in only_python.items() if path.startswith("c/")}
    c_csharp = {path: text for path, text in only_csharp.items() if path.startswith("c/")}
    assert c_python == c_csharp


def test_the_output_is_deterministic(tmp_path: Path) -> None:
    assert generate(tmp_path, SMALL) == generate(tmp_path, SMALL)


def test_no_generated_file_has_carriage_returns_or_trailing_blank_lines(tmp_path: Path) -> None:
    for path, text in generate(tmp_path, SMALL).items():
        if path.startswith("csharp/"):
            assert "\r" not in text, path
            assert text.endswith("\n"), path
            assert not text.endswith("\n\n"), path


# -- Names ----------------------------------------------------------------------------------------


def test_names_are_pascal_case_and_overloads_keep_the_suffix_of_the_c_api(tmp_path: Path) -> None:
    text = generate(tmp_path, SMALL)["csharp/Mod/Api.cs"]
    assert "public int Value()" in text
    assert "public static int Live()" in text
    assert "public static int Twice(int value)" in text
    assert "public void AddInt(int delta)" in text
    assert "public void AddDouble(double delta)" in text
    assert "public Counter()" in text
    assert "public Counter(int start)" in text
    assert "public enum Color : int" in text
    assert "public unsafe partial struct Point" in text
    # Each wrapper says the C function it wraps.
    assert '[BridgefexFunction("mod_Counter_add_int")]' in text


def test_pascal_case() -> None:
    assert naming.pascal("echo_int8") == "EchoInt8"
    assert naming.pascal("liveInstances") == "LiveInstances"
    assert naming.pascal("HTTPServer") == "HTTPServer"
    assert naming.pascal("LOW_LEVEL") == "LowLevel"
    assert naming.pascal("set_MAX_SIZE") == "SetMaxSize"
    assert naming.pascal("IO_ERROR") == "IOError"
    assert naming.pascal("ERROR_EXCEPTION") == "ErrorException"
    assert naming.pascal("kDefault") == "KDefault"
    assert naming.pascal("RED") == "RED"  # one part: it may be an acronym
    assert naming.pascal("__x__") == "X"
    assert naming.pascal("_") == "_"
    assert naming.pascal("8ball") == "_8ball"


def test_keywords_are_escaped_with_an_at_sign() -> None:
    assert naming.identifier("object") == "@object"
    assert naming.identifier("event") == "@event"
    assert naming.identifier("value") == "value"
    assert naming.identifier("from") == "from"  # contextual keywords are identifiers


def test_a_module_cannot_hide_the_namespaces_of_dotnet(tmp_path: Path) -> None:
    assert naming.module_problem("system") is not None
    assert naming.module_problem("demo") is None
    assert naming.header_problem("runtime") is not None
    assert naming.header_problem("shapes") is None
    assert "would hide" in error_of(tmp_path, "int f(int a);", module="system")


@pytest.mark.parametrize("stem", ["runtime", "native"])
def test_a_header_that_c_sharp_cannot_have_is_an_error_of_that_language_only(
    tmp_path: Path, stem: str
) -> None:
    code = "int f(int a);"
    message = error_of(tmp_path, code, name=f"{stem}.h")
    assert f"cannot use header name '{stem}'" in message
    assert "C#:" in message
    # The other languages generate it: nothing of C# is in the C layer.
    assert generate(tmp_path, code, name=f"{stem}.h", languages=("python",))


HOSTILE = """
enum class Kind { A, B };
struct Point { int x; int y; };
class Thing {
public:
    Thing();
    ~Thing();
    int dispose() const;
    int close() const;
    int equals() const;
    int getHashCode() const;
    int getType() const;
    int toString() const;
    int finalize() const;
    int isClosed() const;
    int handle() const;
    int hold() const;
    int owners() const;
    int readOnly() const;
    int retained() const;
    int origin() const;
    int cppType() const;
    int retain() const;
    int attach() const;
    int surrender() const;
    int keepOf() const;
    int claimToGive() const;
    int collectOwning() const;
    int borrow() const;
    int thing() const;
    int event(int object, int string, int __result, int __self, int params_) const;
};
int class_(int namespace_, int lock, int base);
std::string string_(std::string value);
"""


def test_the_names_of_the_objects_are_not_taken_by_the_wrappers(tmp_path: Path) -> None:
    text = generate(tmp_path, HOSTILE)["csharp/Mod/Api.cs"]
    for name in (
        "Dispose_",
        "Close_",
        "Equals_",
        "GetHashCode_",
        "GetType_",
        "ToString_",
        "Finalize_",
        "IsClosed_",
        "Handle_",
        "Hold_",
        "Owners_",
        "ReadOnly_",
        "Retained_",
        "Origin_",
        "CppType_",
        "Retain_",
        "Attach_",
        "Surrender_",
        "KeepOf_",
        "ClaimToGive_",
        "CollectOwning_",
        "Borrow_",
        "Thing_",  # a member cannot have the name of its class
    ):
        assert re.search(rf"public int {name}\(\)", text), name
    assert "int @object, int @string" in text
    assert "public static int Class(int namespace_, int @lock, int @base)" in text
    assert "public static string String(string value)" in text


@needs_dotnet
def test_hostile_names_compile(tmp_path: Path) -> None:
    generate(tmp_path, HOSTILE, verify=True)


@needs_dotnet
def test_a_module_without_classes_compiles(tmp_path: Path) -> None:
    """Functions, enums and structs only: the runtime of the objects is there all the same,
    and warnings are errors."""
    code = "enum class E { A, B };\nstruct P { int x; };\nint add(int a, int b);"
    generate(tmp_path, code, verify=True)


@needs_dotnet
def test_the_fields_of_a_struct_do_not_take_the_names_of_its_members(tmp_path: Path) -> None:
    code = "struct S { int Default; int toNative; int fromNative; int equals; int getType; };"
    text = generate(tmp_path, code, verify=True)["csharp/Mod/Api.cs"]
    for name in ("Default_", "ToNative_", "FromNative_", "Equals_", "GetType_"):
        assert re.search(rf"public int {name};", text), name


@needs_dotnet
def test_enums_with_an_underlying_type_of_c_long_compile(tmp_path: Path) -> None:
    code = (
        "enum class L : long { A = -2, B = 2 };\nenum class U : unsigned long { A = 0, B = 2 };\n"
        "struct H { L l; U u; L ls[2]; };\nL f(L l, U u);\nH g(const H& h);"
    )
    generate(tmp_path, code, verify=True)


@pytest.mark.parametrize(
    "name", ["Runtime", "Native", "Lease", "Block", "Keep", "Functions", "BridgefexObject"]
)
def test_a_class_cannot_hide_a_type_of_the_generated_code(tmp_path: Path, name: str) -> None:
    message = error_of(tmp_path, f"class {name} {{ public: {name}(); ~{name}(); }};")
    assert f"gets the C# name '{name}'" in message


# What an adversarial review found: names that hide what the generated code uses. Each header is
# valid C++; the generated C# must compile (the check builds it with warnings as errors).
COMPILES = {
    "a type of the user named like a type of .NET": (
        "enum class Type { A, B };\nstruct IntPtr { int a; };\n"
        "class Node { public: Node(); ~Node();"
        " Type type() const; IntPtr ptr() const; void set(Type t, IntPtr p); };"
    ),
    "classes named like the types they use": (
        "class Exception { public: Exception(); ~Exception(); };\n"
        "class ArgumentException { public: ArgumentException(); ~ArgumentException(); };\n"
        "class NativeMemory { public: NativeMemory(); ~NativeMemory(); };\n"
        "struct System { int a; };\nint use(const System& s, NativeMemory& m);"
    ),
    "parameters named like what the wrappers call": (
        "int f(int Runtime, int Native, int Handle, int Hold, int Retain, int System);\n"
        "class K { public: K(int Handle); ~K(); int m(int Hold, int Runtime, int Native) const; };"
    ),
    "members named like the generated types": (
        "class K { public: K(); ~K(); int runtime() const; int native() const; int block() const;"
        " int keep() const; int lease() const; int bridgefexObject() const; };\n"
        "int functions(int a);"
    ),
    "a function named like the class of the functions": "int Functions(int a);\nint other(int a);",
    "members of a struct named like the generated types": (
        "struct S { int Runtime; int Native; int Equals; int ToString; int GetHashCode;"
        " int GetType; int MemberwiseClone; int ReferenceEquals; };\nS f(S s);"
    ),
    "parameters named like the locals of the wrappers": (
        "#include <memory>\nclass K1 { public: K1(); ~K1(); };\n"
        "std::unique_ptr<K1> make(int __o);\nint take(std::unique_ptr<K1> x, int __c1, int __h1);"
    ),
    "constructors that are the same signature in C#": (
        "class G { public: explicit G(const std::string& s); explicit G(const char* s);"
        " explicit G(long a); explicit G(long long a); ~G(); };"
    ),
    "an accessor of a base named like a method": (
        "class P1 { public: P1(); virtual ~P1(); };\n"
        "class P2 { public: P2(); virtual ~P2(); };\n"
        "class Q : public P1, public P2 { public: Q(); int asP2(); };"
    ),
    "a method of a base named like the class": (
        "class B { public: B(); virtual ~B(); int b() const; };\n"
        "class D : public B { public: D(); };"
    ),
}


@needs_dotnet
@pytest.mark.parametrize("code", COMPILES.values(), ids=COMPILES.keys())
def test_what_hides_the_names_of_the_generated_code_compiles(tmp_path: Path, code: str) -> None:
    generate(tmp_path, code, verify=True)


@pytest.mark.parametrize(
    ("stem", "word"),
    [
        ("objects", "Objects"),
        ("system", "System"),
        ("microsoft", "Microsoft"),
        ("keep", "Keep"),
        ("lease", "Lease"),
        ("block", "Block"),
    ],
)
def test_headers_that_clash_with_the_generated_project_are_refused(
    tmp_path: Path, stem: str, word: str
) -> None:
    message = error_of(tmp_path, "int f(int a);", name=f"{stem}.h")
    assert f"cannot use header name '{stem}'" in message
    assert word in message


def test_headers_whose_files_differ_only_by_case_are_refused(tmp_path: Path) -> None:
    first = tmp_path / "a_b.h"
    second = tmp_path / "ab.h"
    for number, path in enumerate((first, second)):
        path.write_text(PRELUDE + f"int f{number}(int a);\n", encoding="utf-8")
    options = bridgefex.Options("mod", languages=("csharp",), verify=False)
    with pytest.raises(GenerationError, match="only differs by case"):
        bridgefex.generate([first, second], options)


def test_a_nested_enum_cannot_hide_a_type_of_the_generated_code(tmp_path: Path) -> None:
    message = error_of(tmp_path, "class NR { public: enum class Runtime { A }; NR(); ~NR(); };")
    assert "'Runtime'" in message
    assert "generated code" in message


def test_constructors_that_c_sharp_cannot_tell_apart_are_static_methods(tmp_path: Path) -> None:
    text = generate(tmp_path, COMPILES["constructors that are the same signature in C#"])[
        "csharp/Mod/Api.cs"
    ]
    assert "public static G CreateString(string s)" in text
    assert "public static G CreateCstr(string s)" in text
    assert "public static G CreateLong(long a)" in text
    assert "public static G CreateLlong(long a)" in text
    assert "public G(" not in text.split("class G")[1].split("Borrow")[0]


def test_the_accessor_of_a_base_does_not_take_the_name_of_a_method(tmp_path: Path) -> None:
    text = generate(tmp_path, COMPILES["an accessor of a base named like a method"])[
        "csharp/Mod/Api.cs"
    ]
    assert "public int AsP2()" in text
    assert " AsP2_()" in text


# -- What it refuses ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "std", "what"),
    [
        ("#include <vector>\nint f(const std::vector<int>& v);", "c++17", "std::vector"),
        ("#include <vector>\nstd::vector<std::string> f();", "c++17", "std::vector"),
        ("#include <functional>\nint f(const std::function<int(int)>& g);", "c++17", "callback"),
        ("#include <span>\nint f(std::span<const int> s);", "c++20", "std::span"),
    ],
)
def test_what_the_csharp_bindings_do_not_have_yet_is_refused_clearly(
    tmp_path: Path, code: str, std: str, what: str
) -> None:
    message = error_of(tmp_path, code, std=std)
    assert "C#:" in message
    assert "not supported in C# yet" in message
    assert what in message
    # The Python bindings of the same header are generated all the same.
    assert generate(tmp_path, code, languages=("python",), std=std)


def test_a_failure_of_the_csharp_backend_names_the_function(tmp_path: Path) -> None:
    code = "#include <functional>\nint add(int a, int b);\nint apply(std::function<int(int)> f);"
    assert "apply" in error_of(tmp_path, code)


@pytest.mark.parametrize(
    "code",
    [
        "#include <vector>\nint sum(const std::vector<int>& values);",
        "#include <vector>\nstd::vector<int> make();",
        "#include <vector>\nclass K { public: K(); ~K(); void put(std::vector<int> v); };",
    ],
)
def test_a_refusal_says_which_declaration_and_where(tmp_path: Path, code: str) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    [diagnostic] = raised.value.diagnostics
    assert "C#:" in diagnostic.message
    assert "std::vector" in diagnostic.message
    assert diagnostic.location is not None
    assert diagnostic.location.line >= 1
    assert str(raised.value).count("api.h") >= 1


# -- The check with the compiler of .NET ---------------------------------------------------------


def _plan():  # type: ignore[no-untyped-def]
    return build_plan(Module("mod", ()), None, reservations())


@needs_dotnet
def test_the_check_reports_code_that_does_not_compile(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL)
    files["csharp/Mod/Api.cs"] += '\nclass Broken { int x = "text"; }\n'
    diagnostics = csharp_check.check(_plan(), files, tmp_path / "scratch")
    assert diagnostics
    assert "does not compile" in diagnostics[0].message
    assert "CS0029" in diagnostics[0].message


@needs_dotnet
def test_the_check_passes_generated_code(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL)
    assert csharp_check.check(_plan(), files, tmp_path / "scratch") == []


def test_the_check_can_be_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = generate(tmp_path, SMALL)
    files["csharp/Mod/Api.cs"] += "\nthis is not C#\n"
    monkeypatch.setenv(csharp_check.ENV_SKIP, "1")
    assert csharp_check.check(_plan(), files, tmp_path / "scratch") == []


def test_without_the_sdk_the_code_is_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = generate(tmp_path, SMALL)
    files["csharp/Mod/Api.cs"] += "\nthis is not C#\n"
    monkeypatch.setattr(csharp_check, "dotnet_sdk", lambda: None)
    assert csharp_check.check(_plan(), files, tmp_path / "scratch") == []
