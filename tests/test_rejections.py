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
    path.write_text(PRELUDE + code, encoding="utf-8")
    with pytest.raises(GenerationError) as raised:
        parse_header(path, "input.h", ParseOptions())
    return [str(diagnostic) for diagnostic in raised.value.diagnostics]


@pytest.mark.parametrize(
    ("code", "message"),
    [
        # Declarations at namespace scope.
        ("template <typename T> class Box {};", "template 'Box' is not supported"),
        ("template <typename T> T id(T v);", "template 'id' is not supported"),
        ("enum { red };", "anonymous enums are not supported yet"),
        ("namespace n { enum { a }; }", "anonymous enums are not supported yet"),
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
        # Value structs.
        ("struct S { int x; private: int y; };", "member 'S::y' is not public"),
        ("struct S { int x; int f(); };", "methods are not supported yet ('S::f')"),
        ("struct S { int x; S(int v); };", "constructors with parameters are not supported yet"),
        (
            "struct S { int x; S(int v = 0); };",
            "constructors with parameters are not supported yet",
        ),
        (
            "struct S { int x; private: S(); };",
            "value struct 'S' has no public default constructor",
        ),
        ("struct S { int x; S(const S&) = delete; };", "cannot be copied"),
        ("struct S { int x; S() = default; S(S&&); };", "declares a move operation only"),
        (
            "struct S { int x; S() = default; S(const S&) = default; S(S&&) = delete; };",
            "cannot be moved",
        ),
        (
            "struct S { int x; S() = default; S(const S&) = default; private: S(S&&) = default; };",
            "cannot be moved",
        ),
        ("struct S { int x; S() = default; explicit S(const S&) = default; };", "cannot be copied"),
        ("struct S { int x; S() = default; S(S&) = default; };", "cannot be copied"),
        ("struct S { int x; ~S() noexcept(false); };", "destructors that may throw"),
        ("struct S { int x; ~S() noexcept(sizeof(int) == 2); };", "destructors that may throw"),
        ("struct S { int x; S& operator=(int); };", "methods are not supported yet"),
        ("struct S { int x; int y __attribute__((unavailable)); };", "is unavailable"),
        ("struct S { int a[0]; int b; };", "arrays of size 0 are not supported"),
        ("union U { int a; float b; }; void f(U u);", "unions are not supported"),
        ("struct S { int x; virtual ~S(); };", "needs a public, non-virtual destructor"),
        ("struct B {}; struct S : B { int x; };", "inheritance is not supported yet"),
        ("struct S { int x; operator int() const; };", "conversion operators are not supported"),
        ("struct S { int x; bool operator==(const S&) const; };", "methods are not supported yet"),
        ("struct S { int x : 3; };", "bit-fields are not supported yet"),
        ("struct S { const int x; };", "const members are not supported"),
        ("struct S { int& x; };", "reference members are not supported"),
        ("struct S { int* x; };", "pointers are not supported yet"),
        ("struct S { volatile int x; };", "volatile"),
        ("struct S { int x[2][2]; };", "arrays of arrays are not supported yet"),
        ("struct S { char name[8]; };", "char arrays (fixed-size strings) are not supported yet"),
        ("struct S { int n; int x[]; };", "arrays without a size are not supported"),
        ("struct S { std::string s; };", "strings in value structs are not supported yet"),
        ("class H { public: void f(); }; struct S { H h; };", "classes passed by value"),
        ("struct S { union { int a; float b; }; };", "anonymous unions and structs"),
        (
            "struct S { int x; struct In { int y; } in; };",
            "nested type 'S::In' is not supported yet",
        ),
        ("struct S { int __x; };", "the name is reserved in C and C++"),
        ("struct S { int _X; };", "the name is reserved in C and C++"),
        ("struct S { int x; }; void f(S& s);", "non-const references are not supported yet"),
        ("struct S { int x; }; void f(S* s);", "pointers are not supported yet"),
        ("struct S { int x; }; void f(S&& s);", "references are not supported yet"),
        ("struct S { int x; }; S& f();", "non-const references are not supported yet"),
        # Containers.
        (
            "#include <vector>\nvoid f(std::vector<int>& v);",
            "non-const references are not supported yet",
        ),
        ("#include <vector>\nvoid f(const std::vector<int>* v);", "pointers are not supported yet"),
        ("#include <vector>\nstd::vector<int>&& f();", "references are not supported yet"),
        (
            "#include <vector>\nvoid f(std::vector<std::vector<int>> v);",
            "std::vector of 'std::vector<int>'",
        ),
        ("#include <vector>\nvoid f(std::vector<char> v);", "plain 'char' is not supported"),
        ("#include <vector>\nvoid f(std::vector<const char*> v);", "std::vector of 'const char *'"),
        ("#include <vector>\nvoid f(std::vector<std::byte> v);", "std::byte is not supported yet"),
        (
            "#include <vector>\nvoid f(std::vector<std::pair<int, int>> v);",
            "std::vector of 'std::pair<int, int>'",
        ),
        ("#include <vector>\nvoid f(std::vector<int*> v);", "pointers are not supported yet"),
        (
            "#include <vector>\ntemplate <class A, class T> using Second = std::vector<T>;"
            " void f(Second<long, std::int64_t> v);",
            "alias templates are not supported",
        ),
        (
            "#include <vector>\ntemplate <class T> struct MyAlloc : std::allocator<T> {};"
            " void f(std::vector<int, MyAlloc<int>> v);",
            "only std::vector with std::allocator is supported",
        ),
        (
            "#include <vector>\nusing MyInt = int; void f(std::vector<MyInt> v);",
            "type alias 'MyInt'",
        ),
        (
            "#include <vector>\nstruct S { std::vector<int> v; };",
            "containers in value structs are not supported yet",
        ),
        ("#include <deque>\nvoid f(std::deque<int> d);", "std::deque is not supported yet"),
        ("#include <array>\nvoid f(std::array<int, 3> a);", "std::array is not supported yet"),
        ("#include <utility>\nvoid f(std::pair<int, int> p);", "std::pair is not supported yet"),
        ("class A { public: static int shared; };", "public data member 'A::shared'"),
        ("class A { public: struct Inner {}; };", "nested type 'A::Inner'"),
        ("class A { public: struct N {}; };", "nested type 'A::N'"),
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
        ("class P {}; void f(P p);", "classes passed by value are not supported yet"),
        ("class P {}; P f();", "return type of 'f'"),
        # Strings.
        ("void f(std::string& s);", "non-const references to std::string are not supported"),
        ("void f(std::string&& s);", "rvalue references to std::string are not supported yet"),
        ("const std::string&& f();", "rvalue references to std::string are not supported yet"),
        ("void f(char* s);", "use 'const char*' or std::string"),
        ("char* f();", "use 'const char*' or std::string"),
        ("void f(const volatile char* s);", "pointers to volatile char are not supported"),
        ("void f(volatile std::string s);", "volatile std::string is not supported"),
        ("void f(std::wstring s);", "only std::string and std::string_view"),
        ("void f(const std::u16string& s);", "only std::string and std::string_view"),
        ("void f(std::basic_string<signed char> s);", "only std::string and std::string_view"),
        ("void f(const std::string* s);", "pointers are not supported yet"),
        ("void f(const unsigned char* s);", "pointers are not supported yet"),
        ("void f(const char* const& s);", "pointers are not supported yet"),
        ("void f(const char s[]);", "arrays are not supported yet"),
        ("using Id = int; void f(Id id);", "type alias 'Id' is not supported yet"),
        ("void f(volatile int v);", "volatile"),
        ("void f(int (&values)[3]);", "non-const references"),
        # Enums.
        ("void f(std::byte b);", "std::byte is not supported yet; use std::uint8_t"),
        ("#include <new>\nvoid f(std::align_val_t a);", "standard library enum 'std::align_val_t'"),
        ("enum E { a = 0x80000000 };", "does not fit in int"),
        ("enum E { a = -2147483649LL };", "does not fit in int"),
        ("enum class E : bool { a };", "underlying type 'bool' is not supported"),
        ("enum class E : std::size_t { a };", "underlying type 'size_t' is not supported"),
        ("enum class E : char { a };", "plain 'char' is not supported"),
        ("enum class E : wchar_t { a };", "wchar_t"),
        ("typedef unsigned char u8; enum class E : u8 { a };", "type alias 'u8'"),
        ("enum class E { a }; void f(volatile E e);", "volatile"),
        ("enum class E { a }; void f(E& e);", "non-const references"),
        ("enum class E { a }; void f(E&& e);", "references are not supported yet"),
        ("enum class E { a }; void f(const E* e);", "pointers are not supported yet"),
        ("class A { enum P { x }; public: void f(P p); };", "enum 'P' is not public in its class"),
        ("enum { k = 1 }; void f(decltype(k) v);", "anonymous enums are not supported yet"),
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
        class B { public: int value : 2; virtual void v(); };
        enum { x };
        """,
    )
    # char, the bit-field, the (virtual) method of a value struct, the enum.
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
    errors = errors_for(
        tmp_path, "class Good { public: int f(); };\nclass Bad { public: int x : 3; };"
    )
    assert len(errors) == 1
    assert "Bad::x" in errors[0]
