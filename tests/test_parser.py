# SPDX-License-Identifier: Apache-2.0
"""Tests of the libclang parser: what it extracts from valid headers."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridgefex.errors import ConfigurationError, GenerationError
from bridgefex.model import Class, Function, Header
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
