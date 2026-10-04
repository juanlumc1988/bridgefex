# SPDX-License-Identifier: Apache-2.0
"""Everything that bridgefex cannot translate must be rejected with a clear error."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridgefex.errors import GenerationError
from bridgefex.parser import ParseOptions, parse_header

pytestmark = pytest.mark.usefixtures("libclang_loaded")

PRELUDE = "#pragma once\n#include <cstddef>\n#include <cstdint>\n#include <string>\n"


def errors_for(tmp_path: Path, code: str) -> list[str]:
    path = tmp_path / "input.h"
    path.write_text(PRELUDE + code)
    with pytest.raises(GenerationError) as raised:
        parse_header(path, "input.h", ParseOptions())
    return [str(diagnostic) for diagnostic in raised.value.diagnostics]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        # Declarations at namespace scope.
        ("template <typename T> class Box {};", "template 'Box' is not supported"),
        ("template <typename T> T id(T v);", "template 'id' is not supported"),
        ("enum Color { red };", "enum 'Color' is not supported yet"),
        ("enum class Mode { a };", "enum 'Mode' is not supported yet"),
        ("union U { int a; };", "union 'U' is not supported"),
        ("extern int counter;", "global variable 'counter' is not supported"),
        ('extern "C" { int c_function(); }', 'extern "C" blocks are not supported'),
        ("namespace { int hidden(); }", "anonymous namespaces are not supported"),
        ("namespace n { inline namespace v1 { int f(); } }", "inline namespace 'v1'"),
        ("struct { int f(); } anonymous_instance;", "anonymous classes"),
        ("int operator+(int, struct Q);\nstruct Q {};", "operator function"),
        ("int sum(int count, ...);", "variadic functions are not supported"),
        # Classes.
        ("class Base {}; class D : public Base { public: int f(); };", "inheritance"),
        ("class A { public: virtual int f(); };", "virtual method 'A::f'"),
        ("class A { public: virtual int f() = 0; };", "abstract and cannot be instantiated"),
        ("class A { public: A operator+(int) const; };", "operator 'A::operator+'"),
        ("class A { public: operator int() const; };", "conversion operator of 'A'"),
        ("class A { public: template <typename T> void f(T); };", "member template 'A::f'"),
        ("class A { public: int value; };", "public data member 'A::value'"),
        ("class A { public: static int shared; };", "public data member 'A::shared'"),
        ("class A { public: struct Inner {}; };", "nested type 'A::Inner'"),
        ("class A { public: enum E { x }; };", "nested type 'A::E'"),
        ("class A { ~A(); public: int f(); };", "has no public destructor"),
        ("class A { public: ~A() = delete; };", "has no public destructor"),
        ("class A { A(); public: int f(); };", "no public constructor"),
        ("class A { public: A(const A&); };", "no public constructor"),
        ("class A { public: void f() &; };", "ref-qualified method"),
        ("class A { public: void f() &&; };", "ref-qualified method"),
        # Types.
        ("void f(char c);", "plain 'char' is not supported"),
        ("void f(wchar_t c);", "wchar_t"),
        ("void f(long double v);", "long double"),
        ("void f(int* p);", "pointers are not supported yet"),
        ("void f(int& r);", "non-const references are not supported yet"),
        ("void f(int&& r);", "references are not supported yet"),
        ("void f(const std::string& s);", "classes passed by value are not supported yet"),
        ("void f(std::string s);", "classes passed by value are not supported yet"),
        ("std::string f();", "return type of 'f'"),
        ("using Id = int; void f(Id id);", "type alias 'Id' is not supported yet"),
        ("void f(volatile int v);", "volatile"),
        ("void f(int (&values)[3]);", "non-const references"),
        ("enum class E { a }; void f(E e);", "enum"),
        ("void f(decltype(nullptr) p);", "nullptr_t"),
    ],
)
def test_rejected(tmp_path: Path, code: str, message: str) -> None:
    errors = errors_for(tmp_path, code)
    assert any(message in error for error in errors), errors


def test_errors_have_locations(tmp_path: Path) -> None:
    (error,) = errors_for(tmp_path, "void f(char c);")
    # PRELUDE has 4 lines, so the declaration is on line 5.
    assert error.startswith(f"{tmp_path / 'input.h'}:5:")
    assert "parameter 'c' of 'f'" in error


def test_all_problems_are_reported_at_once(tmp_path: Path) -> None:
    errors = errors_for(
        tmp_path,
        """
        void a(char c);
        class B { public: int value; virtual void v(); };
        enum E { x };
        """,
    )
    assert len(errors) == 4, errors


def test_libclang_errors_abort_before_the_ast_is_used(tmp_path: Path) -> None:
    # libclang recovers from an unknown type by assuming 'int'; bridgefex must
    # not generate anything from such an AST.
    errors = errors_for(tmp_path, "void f(QString s);")
    assert any("unknown type name 'QString'" in error for error in errors), errors


def test_missing_include_is_an_error(tmp_path: Path) -> None:
    errors = errors_for(tmp_path, '#include "does_not_exist.h"\n')
    assert any("does_not_exist.h" in error and "not found" in error for error in errors), errors


def test_non_ascii_names_are_rejected(tmp_path: Path) -> None:
    errors = errors_for(tmp_path, "int café();")
    assert any("not an ASCII identifier" in error for error in errors), errors


def test_a_rejected_member_rejects_its_class_only(tmp_path: Path) -> None:
    errors = errors_for(tmp_path, "class Good { public: int f(); };\nclass Bad { public: int x; };")
    assert len(errors) == 1
    assert "Bad::x" in errors[0]
