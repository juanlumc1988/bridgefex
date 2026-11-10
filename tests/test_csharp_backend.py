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


def test_the_assembly_has_a_name_that_is_not_a_file_name_of_the_library(tmp_path: Path) -> None:
    """The native library is ``mod.dll`` and the assembly ``Mod.dll`` would be the same file
    where the file system ignores the case (Windows, macOS): the loader took one for the other."""
    project = generate(tmp_path, SMALL)["csharp/Mod/Mod.csproj"]
    assert "<AssemblyName>Mod.Bindings</AssemblyName>" in project
    assert "<RootNamespace>Mod</RootNamespace>" in project
    assembly = re.search(r"<AssemblyName>(.*)</AssemblyName>", project)
    assert assembly is not None
    libraries = {"mod.dll", "libmod.so", "libmod.dylib"}
    assert f"{assembly.group(1)}.dll".casefold() not in libraries


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
    "name",
    ["Runtime", "Native", "Lease", "Block", "StringBlock", "Keep", "Functions", "BridgefexObject"],
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
    "parameters named like what the callback wrappers use": (
        "#include <functional>\n"
        "int f(int Thunks, int CallScope, int CallbackBox, int BridgefexCallbacks, int __scope,"
        " int __call, std::function<int(int)> g, int __o1);\n"
        "class K { public: K(std::function<void(int)> Thunks); ~K();"
        " void m(std::function<void()> CallScope, int Registration) const; };"
    ),
    "types named like what the thunks use": (
        "#include <functional>\n"
        "struct Func { int a; };\nstruct Action { int a; };\nstruct Exception { int a; };\n"
        "enum class Level : unsigned char { Low };\n"
        "int f(std::function<Level(const Func&, const Exception&)> g);\n"
        "struct CallGuard { int a; };\nstruct Registration { int a; };\n"
        "void k(std::function<void(const CallGuard&, Registration)> g);\n"
        "void h(std::function<void(const Action&, const char*, const std::string&)> g);"
    ),
    "types named like the C long of .NET": (
        "#include <vector>\nstruct CLong { int a; };\nstruct CULong { int a; };\n"
        "enum class LongE : long { A, B };\n"
        "std::vector<long> vl(const std::vector<long>& v);\nunsigned long ul(unsigned long x);\n"
        "std::vector<LongE> ve(const std::vector<LongE>& v);"
    ),
    "a parameter named like the local of a factory": (
        "#include <functional>\nclass K { public:"
        " K(const char* __new, std::function<void()> g);"
        " K(const std::string& __new, std::function<void()> g); virtual ~K(); };"
    ),
    "a std::array parameter named like the operator that names it": (
        "#include <array>\nclass K { public: K(); ~K(); void m(std::array<int, 2> nameof); };"
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


# Names of types of .NET that the generated files of the root namespace use. The namespace of a
# header (`Mod.Math`) or the module itself (`Math`) would hide the type of that name there.
DOTNET_NAMES = """
Exception Delegate Console Array Math Type Environment Marshal Interlocked Volatile Attribute
AppDomain IntPtr IDisposable MemoryMarshal NativeMemory NativeLibrary LayoutKind StructLayout
MethodImplOptions UTF8Encoding CallConvCdecl DllImportSearchPath ExceptionDispatchInfo
AssemblyLoadContext
OperatingSystem EventArgs ArgumentException InvalidOperationException DllNotFoundException
SafeHandleZeroOrMinusOneIsInvalid ThreadStatic MethodImpl InlineArray LibraryImport
UnmanagedCallersOnly UnmanagedCallConv BadImageFormatException ObjectDisposedException
""".split()


@needs_dotnet
def test_a_header_named_like_a_type_of_dotnet_compiles(tmp_path: Path) -> None:
    """Every file of the project is in the namespace of the module, where the namespace of a header
    hides the type of its name: the runtime names the types of .NET in full."""
    headers = []
    for name in DOTNET_NAMES:
        header = tmp_path / f"{name}.h"
        header.write_text(
            "#pragma once\n#include <functional>\n#include <vector>\n"
            f"int f_{name.lower()}(std::function<int(int)> g, const std::vector<int>& v);\n",
            encoding="utf-8",
        )
        headers.append(header)
    options = bridgefex.Options("mm", std="c++17", languages=("csharp",), verify=True)
    assert bridgefex.generate(headers, options).files


@needs_dotnet
@pytest.mark.parametrize("module", ["exception", "console", "type", "environment", "delegate"])
def test_a_module_named_like_a_type_of_dotnet_compiles(tmp_path: Path, module: str) -> None:
    code = "#include <functional>\nint f(std::function<int(int)> g);"
    generate(tmp_path, code, module=module, verify=True)


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


# -- Callbacks ------------------------------------------------------------------------------------

CALLBACKS = (
    "#include <functional>\n"
    "int apply(int n, const std::function<int(int)>& g);\n"
    "void note(std::function<void()> h, int k);\n"
    "int plain(int a);\n"
    "class K { public: explicit K(std::function<void(int)> on); ~K();"
    " void set(std::function<void(int)> on); int count() const; };"
)


def test_a_callback_is_a_delegate_that_may_be_null(tmp_path: Path) -> None:
    text = generate(tmp_path, CALLBACKS)["csharp/Mod/Api.cs"]
    assert "public static int Apply(int n, global::System.Func<int, int>? g)" in text
    assert "public static void Note(global::System.Action? h, int k)" in text
    assert "public K(global::System.Action<int>? on)" in text
    # The C function gets the thunk, the key of the delegate and the release function.
    assert 'var __o2 = __scope.Register(g, "g", Thunks.Address_Thunk_int__int);' in text
    assert "Native.mod_apply(n, __o2.Callback, __o2.UserData, __o2.Release, &__result)" in text


def test_the_call_that_gives_callbacks_raises_their_exceptions(tmp_path: Path) -> None:
    text = generate(tmp_path, CALLBACKS)["csharp/Mod/Api.cs"]
    body = text.split("public static int Apply")[1].split("}")[0]
    lines = [line.strip() for line in body.splitlines()]
    # The scope is the first statement, and the check is the one of the scope, after the call
    # (C++ owns the callbacks from the moment the C function returns).
    assert lines[2] == "using var __scope = new CallScope();"
    call = next(i for i, line in enumerate(lines) if line.startswith("int __status = Native."))
    assert lines[call + 1] == "__scope.Returned();"
    assert lines[call + 2] == "__scope.Check(__status);"
    assert "Runtime.Check" not in body


def test_a_call_without_callbacks_of_a_module_that_has_them_is_a_call_of_the_bindings(
    tmp_path: Path,
) -> None:
    """A stored callback that it runs is not one of the callbacks of a call that is still running
    on another thread."""
    text = generate(tmp_path, CALLBACKS)["csharp/Mod/Api.cs"]
    plain = text.split("public static int Plain")[1].split("}")[0]
    assert "using var __call = CallScope.Enter();" in plain
    assert "__scope" not in plain
    method = text.split("public int Count")[1].split("}")[0]
    assert "using var __call = CallScope.Enter();" in method


def test_a_module_without_callbacks_has_no_runtime_for_them(tmp_path: Path) -> None:
    files = generate(tmp_path, SMALL)
    assert "csharp/Mod/BridgefexCallbacks.cs" not in files
    assert "CallScope" not in files["csharp/Mod/Api.cs"]
    assert "enable_callbacks" not in files["csharp/Mod/Native.cs"]


def test_a_module_with_callbacks_has_their_runtime_and_the_thunks_they_use(
    tmp_path: Path,
) -> None:
    files = generate(tmp_path, CALLBACKS)
    text = files["csharp/Mod/BridgefexCallbacks.cs"]
    # One thunk for each C signature that is used, and no other.
    assert sorted(re.findall(r"internal static void (Thunk_\w+)\(", text)) == [
        "Thunk_int__int",
        "Thunk_void",
        "Thunk_void__int",
    ]
    native = files["csharp/Mod/Native.cs"]
    assert "internal static partial void mod_enable_callbacks(nint release);" in native
    assert "internal static partial void mod_disable_callbacks(nint release);" in native
    # The callback, its user_data and its release function are addresses.
    assert (
        "internal static partial int mod_apply(int n, nint g, nint g_user_data, "
        "nint g_release, int* out_result);" in native
    )
    # No exception crosses to C++, and a callback is found by a key, never by an address.
    assert "catch (global::System.Exception error)" in text
    assert "CallbackBox.Of(userData)" in text


def test_callbacks_that_have_the_same_c_signature_share_a_thunk(tmp_path: Path) -> None:
    code = (
        "#include <functional>\n"
        "void a(std::function<int(int)> f);\nvoid b(const std::function<int(int)>& f);\n"
        "class C { public: C(); ~C(); void c(std::function<int(int)>&& f); };"
    )
    text = generate(tmp_path, code)["csharp/Mod/BridgefexCallbacks.cs"]
    assert text.count("internal static void Thunk_") == 1


def test_callbacks_of_different_types_have_thunks_of_their_own(tmp_path: Path) -> None:
    code = (
        "#include <functional>\n"
        "enum class A : int { X };\nenum class B : int { Y };\n"
        "struct P { int x; };\n"
        "void a(std::function<A()> f);\nvoid b(std::function<B()> f);\n"
        "void c(std::function<void(const P&, const char*, const std::string&)> f);"
    )
    files = generate(tmp_path, code)
    text = files["csharp/Mod/BridgefexCallbacks.cs"]
    assert "(void* userData, int* result)" in text
    assert text.count("internal static void Thunk_") == 3
    api = files["csharp/Mod/Api.cs"]
    assert "global::System.Func<global::Mod.Api.A>? f" in api
    assert "global::System.Func<global::Mod.Api.B>? f" in api
    assert "global::System.Action<global::Mod.Api.P, string?, string>? f" in api


def test_a_result_that_c_plus_plus_cannot_hold_is_checked_by_the_thunk(tmp_path: Path) -> None:
    code = "#include <functional>\nenum Mode { Off, On };\nint f(std::function<Mode()> g);"
    text = generate(tmp_path, code)["csharp/Mod/BridgefexCallbacks.cs"]
    assert "box.CheckResult((long)value, 0, 1);" in text


@pytest.mark.parametrize(
    "name", ["Thunks", "CallScope", "CallbackBox", "BridgefexCallbacks", "DirectorBox"]
)
def test_a_class_cannot_hide_a_type_of_the_callback_runtime(tmp_path: Path, name: str) -> None:
    code = f"#include <functional>\nvoid f(std::function<void()> g);\nstruct {name} {{ int a; }};"
    assert f"gets the C# name '{name}'" in error_of(tmp_path, code)


@pytest.mark.parametrize("stem", ["thunks", "call_scope", "callback_box", "bridgefex_callbacks"])
def test_a_header_cannot_have_the_name_of_a_file_of_the_callback_runtime(
    tmp_path: Path, stem: str
) -> None:
    message = error_of(tmp_path, "int f(int a);", name=f"{stem}.h")
    assert f"cannot use header name '{stem}'" in message


# -- Directors ------------------------------------------------------------------------------------

DIRECTOR_CODE = """
#include <vector>
class B {
public:
    B();
    explicit B(int start);
    virtual ~B();
    virtual int f(int a) const;
    virtual double g() const = 0;
    virtual std::string name() const;
    virtual int total(const std::vector<int>& v) const;
    int call() const;
protected:
    virtual void h(int reason);
};
class D : public B {
public:
    D();
    double g() const override;
};
"""


def generate_with_directors(
    tmp_path: Path, code: str, classes: list[str], *, verify: bool = False
) -> dict[str, str]:
    annotations = tmp_path / "directors.toml"
    annotations.write_text("directors = [" + ", ".join(f'"{c}"' for c in classes) + "]\n")
    header = tmp_path / "api.h"
    header.write_text(PRELUDE + code + "\n", encoding="utf-8")
    options = bridgefex.Options(
        "mod", std="c++17", languages=("csharp",), verify=verify, annotations=(annotations,)
    )
    return bridgefex.generate([header], options).files


def test_a_class_with_a_director_can_be_derived_from(tmp_path: Path) -> None:
    text = generate_with_directors(tmp_path, DIRECTOR_CODE, ["mod_B", "mod_D"])["csharp/Mod/Api.cs"]
    # A virtual method is virtual in the class that declares it, and an override below it.
    assert "public virtual int F(int a)" in text
    assert "public virtual double G()" in text
    assert "public override double G()" in text
    assert "protected virtual void H(int reason)" in text
    # The constructors that only a class of the user can call (the class is abstract).
    assert "protected B()" in text
    assert "protected B(int start)" in text
    # A method with a container is the class's own: C# cannot override it.
    assert "public int Total(" in text
    assert "virtual int Total" not in text
    # A pure virtual method has to be overridden when the object is made.
    assert "DirectorOverrides(GetType(), true)" in text


def test_a_class_with_a_director_checks_what_a_class_of_the_user_overrides(
    tmp_path: Path,
) -> None:
    files = generate_with_directors(tmp_path, DIRECTOR_CODE, ["mod_B", "mod_D"])
    text = files["csharp/Mod/Api.cs"]
    assert 'DirectorBox.Overrides(t, typeof(global::Mod.Api.B), "G"' in text
    assert "must override the pure virtual method B.g" in text
    # The module has the runtime of the directors; one without them does not.
    assert "internal sealed class DirectorBox" in files["csharp/Mod/BridgefexCallbacks.cs"]
    plain = generate(tmp_path, SMALL)
    assert "csharp/Mod/BridgefexCallbacks.cs" not in plain
    assert "Director" not in plain["csharp/Mod/Objects.cs"]


def test_every_call_of_a_module_with_directors_can_raise_the_exception_of_an_override(
    tmp_path: Path,
) -> None:
    text = generate_with_directors(tmp_path, DIRECTOR_CODE, ["mod_B", "mod_D"])["csharp/Mod/Api.cs"]
    body = text.split("public int Call()")[1].split("\n    }")[0]
    assert "using var __scope = new CallScope();" in body
    assert "__scope.Check(__status);" in body


def test_a_class_whose_pure_virtual_method_has_a_type_that_c_sharp_cannot_override_is_plain(
    tmp_path: Path,
) -> None:
    code = """
#include <vector>
class P { public: P(); virtual ~P(); virtual int n(const std::vector<int>& v) const = 0;
          virtual int k() const; };
class Q { public: Q(); virtual ~Q(); virtual int k() const; };
"""
    text = generate_with_directors(tmp_path, code, ["mod_P", "mod_Q"])["csharp/Mod/Api.cs"]
    # P cannot be derived from (it would have to implement n): its methods are plain.
    assert "virtual int N" not in text
    assert "public int K()" in text
    # Q can.
    assert "public virtual int K()" in text


DIRECTOR_COMPILES = {
    "names like the locals of the director code": (
        "class B { public: B(int __box, int __vt); virtual ~B();"
        " virtual int f(int __over, int __director, int __d) const; protected: B(); };",
        ["mod_B"],
    ),
    "names that the C layer gives to the parameters of the functions of a director": (
        "class B { public: B(int vtable); B(int out_object, int out_director); virtual ~B();"
        " virtual int f(int director, int Director, int vtable) const;"
        " protected: virtual int p(int self, int out_object) const; };",
        ["mod_B"],
    ),
    "a virtual method named like a member of every object": (
        "class B { public: B(); virtual ~B(); virtual int equals(int a) const;"
        " virtual int hold() const; virtual int object() const; };",
        ["mod_B"],
    ),
    "members named like what the directors add": (
        "class B { public: B(); virtual ~B(); virtual int director_box() const;"
        " int Director() const; int hold_director(bool x) const; int set_director() const; };",
        ["mod_B"],
    ),
    "a derived class that hides an overload of its base": (
        "class B { public: B(); virtual ~B(); virtual int add(int a, int b) const;"
        " virtual double add(double a, double b) const; };\n"
        "class D : public B { public: D(); int add(int a, int b) const override; };",
        ["mod_B", "mod_D"],
    ),
    "an enum, a struct and strings in the signature": (
        "enum class E : unsigned char { A, B };\nstruct S { int x; };\n"
        "class B { public: B(); virtual ~B(); virtual E e(E v) const;"
        " virtual S s(S v, const S& w) const; virtual std::string t(const std::string& a,"
        " std::string_view b, const char* c) const; };",
        ["mod_B"],
    ),
    "a method that is protected in the base and public in the derived class": (
        "class B { public: B(); virtual ~B(); protected: virtual int f() const; };\n"
        "class D : public B { public: D(); int f() const override; };",
        ["mod_B", "mod_D"],
    ),
    "an override named like the derived class or like a type that it defines": (
        "class Base { public: Base(); virtual ~Base(); virtual int circle() const;"
        " virtual int mode() const; };\n"
        "class Circle : public Base { public: enum class Mode { A }; Circle();"
        " int circle() const override; int mode() const override; };",
        ["mod_Base", "mod_Circle"],
    ),
    "a class whose virtual methods cannot be overridden from C#": (
        "class B { public: B(); virtual ~B(); protected: virtual int* p(); };",
        ["mod_B"],
    ),
    "a virtual method that hides one of the base with the same name": (
        "class A { public: A(); virtual ~A(); virtual int g() const;"
        " protected: virtual int h() const; };\n"
        "class B : public A { public: B(); virtual int g(); int h(); };",
        ["mod_A", "mod_B"],
    ),
    "a private implementation of a virtual method": (
        "class A { public: A(); virtual ~A(); virtual int f() const; int callf() const; };\n"
        "class D : public A { public: D(); virtual int g() const;"
        " private: int f() const override; };",
        ["mod_A", "mod_D"],
    ),
}


@needs_dotnet
@pytest.mark.parametrize(
    ("code", "classes"), DIRECTOR_COMPILES.values(), ids=DIRECTOR_COMPILES.keys()
)
def test_the_code_of_a_director_compiles_with_hostile_names(
    tmp_path: Path, code: str, classes: list[str]
) -> None:
    generate_with_directors(tmp_path, "#include <string_view>\n" + code, classes, verify=True)


def test_a_class_that_cannot_be_overridden_where_it_implements_a_method_seals_it(
    tmp_path: Path,
) -> None:
    code, classes = DIRECTOR_COMPILES["a private implementation of a virtual method"]
    text = generate_with_directors(tmp_path, code, classes)["csharp/Mod/Api.cs"]
    # A class of the user cannot override what C++ would not call.
    assert "public sealed override int F() => base.F();" in text


def test_an_override_keeps_the_access_of_the_method_it_overrides(tmp_path: Path) -> None:
    code, classes = DIRECTOR_COMPILES[
        "a method that is protected in the base and public in the derived class"
    ]
    text = generate_with_directors(tmp_path, code, classes)["csharp/Mod/Api.cs"]
    assert "protected virtual int F()" in text
    assert "protected override int F()" in text


def test_a_class_without_a_director_refuses_the_overrides_of_a_class_of_the_user(
    tmp_path: Path,
) -> None:
    code = (
        "class A { public: A(); virtual ~A(); virtual int x() const; };\n"
        "class B : public A { public: B(); int y() const; };"
    )
    text = generate_with_directors(tmp_path, code, ["mod_A"])["csharp/Mod/Api.cs"]
    assert "DirectorBox.RefuseOverrides(GetType(), typeof(B));" in text
    assert "DirectorBox.RefuseOverrides" not in text.split("class B ")[0]


@needs_dotnet
def test_the_director_code_of_the_example_compiles(tmp_path: Path) -> None:
    generate_with_directors(tmp_path, DIRECTOR_CODE, ["mod_B", "mod_D"], verify=True)


# -- What it refuses ------------------------------------------------------------------------------

WIDE = "std::function<void(" + ", ".join(["int"] * 17) + ")>"


@pytest.mark.parametrize(
    ("code", "std", "what"),
    [
        (f"#include <functional>\nint f({WIDE} g);", "c++17", "a callback with 17 arguments"),
        (
            f"#include <functional>\nclass K {{ public: K(); ~K(); void m({WIDE} g); }};",
            "c++17",
            "a callback with 17 arguments",
        ),
    ],
)
def test_what_the_csharp_bindings_do_not_have_is_refused_clearly(
    tmp_path: Path, code: str, std: str, what: str
) -> None:
    message = error_of(tmp_path, code, std=std)
    assert "C#:" in message
    assert what in message
    assert "no delegate with more than 16" in message
    # The Python bindings of the same header are generated all the same.
    assert generate(tmp_path, code, languages=("python",), std=std)


def test_a_failure_of_the_csharp_backend_names_the_function(tmp_path: Path) -> None:
    code = f"#include <functional>\nint add(int a, int b);\nint apply({WIDE} f);"
    assert "apply" in error_of(tmp_path, code)


@pytest.mark.parametrize(
    "code",
    [
        f"#include <functional>\nint sum({WIDE} g);",
        f"#include <functional>\nclass K {{ public: K({WIDE} g); ~K(); }};",
        f"#include <functional>\nclass K {{ public: K(); ~K(); void put({WIDE} g); }};",
    ],
)
def test_a_refusal_says_which_declaration_and_where(tmp_path: Path, code: str) -> None:
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code)
    [diagnostic] = raised.value.diagnostics
    assert "C#:" in diagnostic.message
    assert "17 arguments" in diagnostic.message
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
