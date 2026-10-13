# SPDX-License-Identifier: Apache-2.0
"""Tests of the libclang parser: what it extracts from valid headers."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridgefex.errors import ConfigurationError, GenerationError
from bridgefex.model import (
    Class,
    Enumeration,
    EnumRef,
    Function,
    Header,
    StringKind,
    StringType,
    Struct,
    StructRef,
    VectorType,
)
from bridgefex.parser import ParseOptions, parse_header

pytestmark = pytest.mark.usefixtures("libclang_loaded")

PRELUDE = "#pragma once\n#include <cstddef>\n#include <cstdint>\n"


def parse(tmp_path: Path, code: str, std: str = "c++17") -> Header:
    path = tmp_path / "input.h"
    path.write_text(PRELUDE + code, encoding="utf-8")
    return parse_header(path, "input.h", ParseOptions(std=std)).header


def only_class(header: Header) -> Class:
    (declaration,) = header.declarations
    assert isinstance(declaration, Class)
    return declaration


def test_class_members(tmp_path: Path) -> None:
    cls = only_class(
        parse(
            tmp_path,
            """
            namespace a { namespace b {
            class Widget {
            public:
                Widget();
                Widget(std::int32_t x, double y);
                Widget(const Widget&);
                Widget(Widget&&);
                Widget& operator=(const Widget&) = delete;
                ~Widget();
                std::int32_t get() const;
                void set(std::int32_t value);
                static bool check(bool flag);
                void removed() = delete;
            private:
                void hidden();
                int field_;
            protected:
                void alsoHidden();
            };
            }}
            """,
        )
    )
    assert cls.name == "Widget"
    assert cls.namespace == ("a", "b")
    assert cls.class_key == "class"
    assert [len(ctor.parameters) for ctor in cls.constructors] == [0, 2]
    assert [p.type.c_name for p in cls.constructors[1].parameters] == ["int32_t", "double"]
    assert [(m.name, m.is_const, m.is_static) for m in cls.methods] == [
        ("get", True, False),
        ("set", False, False),
        ("check", False, True),
    ]
    assert cls.methods[0].result.c_name == "int32_t"
    assert cls.methods[1].result.is_void


def test_struct_with_implicit_constructor(tmp_path: Path) -> None:
    cls = only_class(parse(tmp_path, "struct Point { double norm() const; };"))
    assert cls.class_key == "struct"
    assert cls.namespace == ()
    (constructor,) = cls.constructors
    assert constructor.implicit
    assert constructor.parameters == ()


def test_virtual_destructor_is_accepted(tmp_path: Path) -> None:
    cls = only_class(parse(tmp_path, "class A { public: virtual ~A(); int f(); };"))
    assert [m.name for m in cls.methods] == ["f"]


def test_free_functions_and_typedefs(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        namespace n {
        std::int64_t f(std::int8_t a, int8_t b, std::size_t c, size_t d, unsigned e, long g);
        void g();
        }
        """,
    )
    f, g = header.declarations
    assert isinstance(f, Function)
    assert f.qualified_name == "n::f"
    assert [p.type.c_name for p in f.parameters] == [
        "int8_t",
        "int8_t",
        "size_t",
        "size_t",
        "unsigned int",
        "long",
    ]
    assert f.result.c_name == "int64_t"
    assert isinstance(g, Function)
    assert g.result.is_void


def test_const_values_and_const_references_are_passed_by_value(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        "const double& pick(const std::int32_t& index, const double scale, const bool flag);",
    )
    (function,) = header.declarations
    assert isinstance(function, Function)
    assert [p.type.c_name for p in function.parameters] == ["int32_t", "double", "bool"]
    assert function.result.c_name == "double"


def test_unnamed_parameters(tmp_path: Path) -> None:
    (function,) = parse(tmp_path, "int f(int, double);").declarations
    assert isinstance(function, Function)
    assert [p.name for p in function.parameters] == ["", ""]


def test_includes_and_forward_declarations_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "other.h").write_text(
        "#pragma once\nclass Other { public: int f(); };\n", encoding="utf-8"
    )
    header = parse(
        tmp_path,
        """
        #include "other.h"
        class Later;
        class Later { public: int f(); };
        """,
    )
    assert [d.name for d in header.declarations] == ["Later"]


def test_redeclarations_and_out_of_line_definitions(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        namespace n {
        int f(int a);
        int f(int a);
        class C { public: int get() const; };
        }
        inline int n::f(int a) { return a; }
        inline int n::C::get() const { return 1; }
        """,
    )
    assert [type(d).__name__ for d in header.declarations] == ["Function", "Class"]
    function = header.declarations[0]
    assert isinstance(function, Function)
    assert function.namespace == ("n",)


def test_ignored_declarations(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        namespace n {
        using Id = int;
        typedef double Real;
        namespace alias = n;
        static_assert(sizeof(int) >= 2, "int");
        class C {
        public:
            using Inner = int;
            friend void touch(C&);
            static_assert(true, "");
            int f();
        };
        }
        """,
    )
    assert [d.name for d in header.declarations] == ["C"]


def test_declaration_order_is_kept(tmp_path: Path) -> None:
    header = parse(tmp_path, "int z(); class B { public: int f(); }; int a();")
    assert [d.name for d in header.declarations] == ["z", "B", "a"]


def test_cxx23_header(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        #include <expected>
        #include <span>
        namespace n { [[nodiscard]] constexpr int twice(int v) noexcept { return 2 * v; } }
        """,
        std="c++23",
    )
    assert [d.name for d in header.declarations] == ["twice"]


def test_missing_header(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        parse_header(tmp_path / "nope.h", "nope.h", ParseOptions())


# The MSVC target, whatever the host: there the compiler itself declares size_t.
_MSVC_TARGET = ("--target=x86_64-pc-windows-msvc", "-fms-compatibility", "-fms-extensions")


@pytest.mark.parametrize(
    "code",
    [
        # No include at all: size_t is predeclared.
        "namespace n { size_t f(size_t a); }",
        # Redeclared by a system header (as vcruntime.h does) and by the user.
        "#include <vcruntime_like.h>\ntypedef decltype(sizeof 0) size_t;\n"
        "namespace n { size_t f(size_t a); }",
    ],
)
def test_size_t_predeclared_by_the_compiler(tmp_path: Path, code: str) -> None:
    system = tmp_path / "system"
    system.mkdir()
    (system / "vcruntime_like.h").write_text(
        "#pragma once\ntypedef unsigned long long size_t;\n", encoding="utf-8"
    )
    path = tmp_path / "input.h"
    path.write_text("#pragma once\n" + code + "\n", encoding="utf-8")
    options = ParseOptions(extra_args=(*_MSVC_TARGET, "-isystem", str(system)))
    (function,) = parse_header(path, "input.h", options).header.declarations
    assert isinstance(function, Function)
    assert function.result.c_name == "size_t"
    assert [parameter.type.c_name for parameter in function.parameters] == ["size_t"]


def test_alias_named_size_t_is_still_rejected_on_msvc(tmp_path: Path) -> None:
    path = tmp_path / "input.h"
    path.write_text(
        "#pragma once\nnamespace my { typedef unsigned long long size_t; size_t g(); }\n",
        encoding="utf-8",
    )
    with pytest.raises(GenerationError, match="type alias 'my::size_t' is not supported"):
        parse_header(path, "input.h", ParseOptions(extra_args=_MSVC_TARGET))


def test_std_size_t_declared_like_libcxx(tmp_path: Path) -> None:
    """libc++ declares std::size_t with 'using' in the inline namespace std::__1,
    and <type_traits> or <limits> do not declare ::size_t."""
    system = tmp_path / "system"
    system.mkdir()
    (system / "libcxx_like.h").write_text(
        "#pragma once\nnamespace std { inline namespace __1 {"
        " using size_t = decltype(sizeof(int)); } }\n",
        encoding="utf-8",
    )
    path = tmp_path / "input.h"
    path.write_text(
        "#pragma once\n#include <libcxx_like.h>\nnamespace n { std::size_t f(std::size_t a); }\n",
        encoding="utf-8",
    )
    options = ParseOptions(extra_args=("-nostdinc++", "-isystem", str(system)))
    (function,) = parse_header(path, "input.h", options).header.declarations
    assert isinstance(function, Function)
    assert function.result.c_name == "size_t"


def parameter_types(header: Header) -> list[object]:
    (function,) = header.declarations
    assert isinstance(function, Function)
    return [parameter.type for parameter in function.parameters] + [function.result]


STRING = StringType(StringKind.STRING)
STRING_REF = StringType(StringKind.STRING, by_reference=True)
VIEW = StringType(StringKind.STRING_VIEW)
VIEW_REF = StringType(StringKind.STRING_VIEW, by_reference=True)
C_STRING = StringType(StringKind.C_STRING)


@pytest.mark.parametrize("unsigned_char", [False, True])
def test_string_forms(tmp_path: Path, unsigned_char: bool) -> None:
    """Every accepted spelling, also where plain char is unsigned (ARM)."""
    code = """
        #include <string>
        #include <string_view>
        namespace s {
        using Text = std::string;
        typedef const char* cstr_t;
        const std::string& f(std::string a, const std::string b, const std::string& c,
                             std::string const& d, Text e, const Text& g,
                             std::basic_string<char> h, std::string_view i,
                             const std::string_view j, const std::string_view& k,
                             const char* l, char const* const m, cstr_t n);
        }
        """
    path = tmp_path / "input.h"
    path.write_text(PRELUDE + code, encoding="utf-8")
    options = ParseOptions(extra_args=("-funsigned-char",) if unsigned_char else ())
    header = parse_header(path, "input.h", options).header
    assert parameter_types(header) == [
        STRING,
        STRING,
        STRING_REF,
        STRING_REF,
        STRING,
        STRING_REF,
        STRING,
        VIEW,
        VIEW,
        VIEW_REF,
        C_STRING,
        C_STRING,
        C_STRING,
        STRING_REF,
    ]


def test_string_results(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        #include <string>
        #include <string_view>
        namespace s { std::string a(); const std::string& b(); std::string_view c();
                      const char* d(); const std::string e(); }
        """,
    )
    results = [declaration.result for declaration in header.declarations]  # type: ignore[union-attr]
    assert results == [STRING, STRING_REF, VIEW, C_STRING, STRING]


def test_strings_of_a_std_inside_extern_cxx(tmp_path: Path) -> None:
    """The MSVC STL declares namespace std inside 'extern "C++"' in C++20."""
    system = tmp_path / "system"
    system.mkdir()
    (system / "fake_string").write_text(
        """
        #pragma once
        extern "C++" {
        namespace std {
        template <class C> struct char_traits {};
        template <class T> struct allocator {};
        inline namespace __1 {
        template <class C, class T = char_traits<C>, class A = allocator<C>>
        class basic_string {};
        typedef basic_string<char> string;
        }
        template <class C, class T = char_traits<C>> class basic_string_view {};
        typedef basic_string_view<char> string_view;
        }
        }
        """,
        encoding="utf-8",
    )
    path = tmp_path / "input.h"
    path.write_text(
        "#pragma once\n#include <fake_string>\n"
        "namespace s { void f(std::string a, std::string_view b); }\n",
        encoding="utf-8",
    )
    header = parse_header(
        path, "input.h", ParseOptions(extra_args=("-nostdinc++", "-isystem", str(system)))
    ).header
    assert parameter_types(header)[:2] == [STRING, VIEW]
    # The same declarations outside a system header are not the standard library's.
    with pytest.raises(GenerationError, match="classes passed by value"):
        parse_header(path, "input.h", ParseOptions(extra_args=("-nostdinc++", "-I", str(system))))


def test_a_namespace_std_that_is_not_the_global_one(tmp_path: Path) -> None:
    system = tmp_path / "system"
    system.mkdir()
    (system / "other.h").write_text(
        "#pragma once\nnamespace lib { namespace std { class basic_string {}; } }\n",
        encoding="utf-8",
    )
    path = tmp_path / "input.h"
    path.write_text(
        "#pragma once\n#include <other.h>\nnamespace s { void f(lib::std::basic_string a); }\n",
        encoding="utf-8",
    )
    with pytest.raises(GenerationError, match="classes passed by value"):
        parse_header(path, "input.h", ParseOptions(extra_args=("-isystem", str(system))))


def enums_of(header: Header) -> dict[str, Enumeration]:
    return {d.name: d for d in header.declarations if isinstance(d, Enumeration)}


def test_fixed_underlying_types_are_detected(tmp_path: Path) -> None:
    enums = enums_of(
        parse(
            tmp_path,
            """
            #define BASE : unsigned int
            namespace e {
            enum [[deprecated("use X : now")]] Attr1 { a1 };
            enum [[deprecated("x : unsigned int")]] Attr3 { a3 };
            enum Attr2 : unsigned int { a2 };
            enum ViaMacro BASE { m };
            typedef enum : std::uint8_t { t1 } TypedefFixed;
            typedef enum { t2 } TypedefUnfixed;
            enum Long : long { l };
            enum Plain { p };
            enum class Scoped { s };
            enum __attribute__((packed)) Packed { k };
            }
            """,
        )
    )
    fixed = {name: enum.fixed for name, enum in enums.items()}
    assert fixed == {
        "Attr1": False,
        "Attr3": False,
        "Attr2": True,
        "ViaMacro": True,
        "TypedefFixed": True,
        "TypedefUnfixed": False,
        "Long": True,
        "Plain": False,
        "Scoped": True,
        "Packed": False,
    }
    assert enums["Scoped"].underlying.c_name == "int"
    assert enums["TypedefFixed"].underlying.c_name == "uint8_t"
    assert enums["Long"].underlying.c_name == "long"
    # The typedef'd one is visited once.
    assert [d.name for d in enums_of(parse(tmp_path, "typedef enum { a } T;")).values()] == ["T"]


def test_enumerator_values(tmp_path: Path) -> None:
    enums = enums_of(
        parse(
            tmp_path,
            """
            namespace e {
            enum class U8 : std::uint8_t { x = 255 };
            enum class U64 : std::uint64_t { max = 18446744073709551615ULL, half = 1ULL << 63 };
            enum class I64 : std::int64_t { min = INT64_MIN };
            enum Neg { n = -5 };
            enum class Old { a, b [[deprecated]] = 2 };
            }
            """,
        )
    )
    values = {
        name: [(x.name, x.value, x.deprecated) for x in enum.enumerators]
        for name, enum in enums.items()
    }
    assert values == {
        "U8": [("x", 255, False)],
        "U64": [("max", 2**64 - 1, False), ("half", 2**63, False)],
        "I64": [("min", -(2**63), False)],
        "Neg": [("n", -5, False)],
        "Old": [("a", 0, False), ("b", 2, True)],
    }


def test_using_enum_and_opaque_declarations_are_skipped(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        namespace e {
        enum class Late : int;
        enum class Color { red };
        struct W { using enum Color; void f(); };
        using enum Color;
        enum class Late : int { x };
        }
        """,
        std="c++20",
    )
    assert [d.name for d in header.declarations] == ["Color", "W", "Late"]
    (cls,) = [d for d in header.declarations if isinstance(d, Class)]
    assert cls.enums == ()


def test_nested_enums_and_enum_references(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        namespace e {
        enum class Color { red };
        using Colour = Color;
        class W {
        public:
            enum Mode { a, b };
            void f(Mode m, const Colour& c, const Color k);
        private:
            enum Hidden { h };
        };
        }
        """,
    )
    cls = only_class_of(header)
    assert [enum.name for enum in cls.enums] == ["Mode"]
    (mode,) = cls.enums
    assert mode.qualified_name == "e::W::Mode"
    assert mode.scope == ("W",)
    types = [parameter.type for parameter in cls.methods[0].parameters]
    assert [t.qualified_name for t in types] == ["e::W::Mode", "e::Color", "e::Color"]  # type: ignore[union-attr]
    assert all(isinstance(t, EnumRef) for t in types)


def only_class_of(header: Header) -> Class:
    (cls,) = [d for d in header.declarations if isinstance(d, Class)]
    return cls


def test_enumerators_that_msvc_would_truncate_are_rejected_for_its_target(
    tmp_path: Path,
) -> None:
    """MSVC gives such an enum the type int: its target must reject it too."""
    path = tmp_path / "input.h"
    path.write_text("#pragma once\nenum E { big = 0x80000000 };\n", encoding="utf-8")
    for target, message in (
        ("x86_64-pc-windows-msvc", "not representable in the underlying type 'int'"),
        ("x86_64-linux-gnu", "does not fit in int"),
        ("armv7-none-eabi", "does not fit in int"),
    ):
        # Not even when the options ask for it.
        options = ParseOptions(extra_args=(f"--target={target}", "-Wno-microsoft-enum-value"))
        with pytest.raises(GenerationError, match=message):
            parse_header(path, "input.h", options)


def test_value_structs(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        namespace v {
        struct Point { double x; double y = 1.5; static const int kDims = 2; };
        typedef struct { std::int16_t a[3]; Point p; } Pair;
        struct Copy { int x; Copy() = default; Copy(const Copy&) = default;
                      Copy& operator=(const Copy&) = default; ~Copy() = default; };
        struct Tagged { enum class Mode : int { a }; Mode mode; };
        class Handle { public: int f(); };
        Point f(const Point& a, Point b, const Pair c);
        }
        """,
    )
    structs = {d.name: d for d in header.declarations if isinstance(d, Struct)}
    assert set(structs) == {"Point", "Pair", "Copy", "Tagged"}
    assert [(f.name, f.length) for f in structs["Point"].fields] == [("x", None), ("y", None)]
    pair = structs["Pair"].fields
    assert pair[0].length == 3
    assert isinstance(pair[1].type, StructRef)
    assert pair[1].type.qualified_name == "v::Point"
    assert [e.name for e in structs["Tagged"].enums] == ["Mode"]
    (function,) = [d for d in header.declarations if isinstance(d, Function)]
    types = [parameter.type for parameter in function.parameters]
    assert [t.by_reference for t in types] == [True, False, False]  # type: ignore[union-attr]
    assert [t.cpp_spelling for t in types] == ["const v::Point&", "v::Point", "v::Pair"]


def test_struct_references_without_a_definition(tmp_path: Path) -> None:
    header = parse(tmp_path, "namespace w { struct Later; int f(const Later& l); }")
    (function,) = header.declarations
    assert isinstance(function, Function)
    ref = function.parameters[0].type
    assert isinstance(ref, StructRef)
    assert ref.definition is None


def test_vector_forms(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        #include <string>
        #include <vector>
        namespace c {
        using Bytes = std::vector<std::uint8_t>;
        template <class T> using Vec = std::vector<T>;
        enum class E : int { a };
        struct P { int x; };
        std::vector<std::int64_t> f(const std::vector<std::int64_t>& a, std::vector<bool> b,
                                    Bytes c, Vec<std::int32_t> d, std::vector<std::string>&& e,
                                    const std::vector<E>& g, std::vector<P> h,
                                    std::vector<std::size_t, std::allocator<std::size_t>> i);
        }
        """,
    )
    (function,) = [d for d in header.declarations if isinstance(d, Function)]
    spellings = [parameter.type.cpp_spelling for parameter in function.parameters]
    assert spellings == [
        "const std::vector<int64_t>&",
        "std::vector<bool>",
        "std::vector<uint8_t>",
        "std::vector<int32_t>",
        "std::vector<std::string>&&",
        "const std::vector<c::E>&",
        "std::vector<c::P>",
        "std::vector<size_t>",
    ]
    assert isinstance(function.result, VectorType)
    assert function.result.cpp_spelling == "std::vector<int64_t>"


def test_vector_element_types_keep_their_spelling_on_every_target(tmp_path: Path) -> None:
    """int64_t is 'long' on Linux and 'long long' on Windows and 32-bit ARM."""
    system = tmp_path / "system"
    system.mkdir()
    (system / "vector").write_text(
        "#pragma once\nnamespace std { template <class T> struct allocator {};\n"
        "inline namespace __1 { template <class T, class A = allocator<T>> class vector {}; } }\n",
        encoding="utf-8",
    )
    path = tmp_path / "input.h"
    # The compiler's own type: 'long' or 'long long' depending on the target.
    (system / "fake_stdint.h").write_text(
        "#pragma once\ntypedef __INT64_TYPE__ int64_t;\n", encoding="utf-8"
    )
    path.write_text(
        "#pragma once\n#include <fake_stdint.h>\n#include <vector>\n"
        "namespace c { void f(std::vector<int64_t> v); }\n",
        encoding="utf-8",
    )
    for target in ("x86_64-linux-gnu", "armv7a-linux-gnueabihf", "x86_64-pc-windows-msvc"):
        options = ParseOptions(
            extra_args=(
                f"--target={target}",
                "-nostdinc",
                "-nostdinc++",
                "-isystem",
                str(system),
            )
        )
        (function,) = parse_header(path, "input.h", options).header.declarations
        assert isinstance(function, Function)
        assert function.parameters[0].type.cpp_spelling == "std::vector<int64_t>", target


def test_struct_members_of_array_aliases_and_allowed_specials(tmp_path: Path) -> None:
    header = parse(
        tmp_path,
        """
        typedef std::uint8_t mac_t[6];
        using Pair = std::int16_t[2];
        struct Eth { mac_t src; Pair p; };
        struct Ok { int x; ~Ok() noexcept; Ok& operator=(const Ok&) = default; };
        struct Ok2 { int x; ~Ok2() noexcept(true); };
        """,
    )
    structs = {d.name: d for d in header.declarations if isinstance(d, Struct)}
    eth = structs["Eth"].fields
    assert [(f.length, f.type.c_name) for f in eth] == [(6, "uint8_t"), (2, "int16_t")]  # type: ignore[union-attr]
    assert set(structs) == {"Eth", "Ok", "Ok2"}


def test_anonymous_enum_members_get_one_message(tmp_path: Path) -> None:
    path = tmp_path / "input.h"
    path.write_text(PRELUDE + "struct S { enum { A, B } kind; };\n", encoding="utf-8")
    with pytest.raises(GenerationError) as raised:
        parse_header(path, "input.h", ParseOptions())
    messages = [d.message for d in raised.value.diagnostics]
    assert all("anonymous enums" in message for message in messages), messages
