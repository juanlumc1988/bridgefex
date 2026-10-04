# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the scalar type mapping (no libclang needed)."""

from __future__ import annotations

import pytest

from bridgefex import typemap
from bridgefex.model import TypeCategory
from bridgefex.typemap import UnsupportedTypeError, resolve, typedef_name


@pytest.mark.parametrize(
    ("kind", "c_name", "ctypes_name", "token"),
    [
        ("BOOL", "bool", "c_bool", "bool"),
        ("SCHAR", "signed char", "c_byte", "schar"),
        ("UCHAR", "unsigned char", "c_ubyte", "uchar"),
        ("SHORT", "short", "c_short", "short"),
        ("USHORT", "unsigned short", "c_ushort", "ushort"),
        ("INT", "int", "c_int", "int"),
        ("UINT", "unsigned int", "c_uint", "uint"),
        ("LONG", "long", "c_long", "long"),
        ("ULONG", "unsigned long", "c_ulong", "ulong"),
        ("LONGLONG", "long long", "c_longlong", "llong"),
        ("ULONGLONG", "unsigned long long", "c_ulonglong", "ullong"),
        ("FLOAT", "float", "c_float", "float"),
        ("DOUBLE", "double", "c_double", "double"),
    ],
)
def test_builtin_types(kind: str, c_name: str, ctypes_name: str, token: str) -> None:
    scalar = resolve(kind, c_name, kind, 0)
    assert (scalar.c_name, scalar.ctypes_name, scalar.token) == (c_name, ctypes_name, token)


def test_builtin_tokens_are_unique() -> None:
    tokens = [scalar.token for scalar in typemap.BUILTIN_TYPES.values()]
    tokens += [typedef.type.token for typedef in typemap.STANDARD_TYPEDEFS.values()]
    assert len(tokens) == len(set(tokens))


@pytest.mark.parametrize(
    ("spelling", "canonical", "size", "expected"),
    [
        ("int32_t", "INT", 4, "int32_t"),
        ("std::int32_t", "INT", 4, "int32_t"),
        ("::int32_t", "INT", 4, "int32_t"),
        ("::std::int64_t", "LONG", 8, "int64_t"),
        ("const std::int64_t", "LONGLONG", 8, "int64_t"),
        ("std::int8_t", "SCHAR", 1, "int8_t"),
        ("std::int16_t", "SHORT", 2, "int16_t"),
        ("std::uint8_t", "UCHAR", 1, "uint8_t"),
        ("std::uint16_t", "USHORT", 2, "uint16_t"),
        ("std::uint32_t", "UINT", 4, "uint32_t"),
        ("std::uint64_t", "ULONG", 8, "uint64_t"),
        ("std::uint64_t", "ULONGLONG", 8, "uint64_t"),
        ("std::size_t", "ULONG", 8, "size_t"),
        ("size_t", "UINT", 4, "size_t"),
    ],
)
def test_standard_typedefs_keep_their_name(
    spelling: str, canonical: str, size: int, expected: str
) -> None:
    for kind in ("ELABORATED", "TYPEDEF", "UNEXPOSED"):
        assert resolve(kind, spelling, canonical, size).c_name == expected


@pytest.mark.parametrize(
    ("spelling", "canonical", "size"),
    [
        ("int32_t", "INT", 8),  # wrong size
        ("int32_t", "UINT", 4),  # wrong signedness
        ("uint8_t", "SCHAR", 1),
        ("size_t", "INT", 4),  # size_t must be unsigned
        ("int32_t", "FLOAT", 4),
    ],
)
def test_fake_standard_typedefs_are_rejected(spelling: str, canonical: str, size: int) -> None:
    with pytest.raises(UnsupportedTypeError, match="does not name the standard"):
        resolve("TYPEDEF", spelling, canonical, size)


def test_user_alias_of_builtin_is_rejected() -> None:
    with pytest.raises(UnsupportedTypeError, match="type alias 'my::Id' is not supported yet"):
        resolve("ELABORATED", "my::Id", "INT", 4)


def test_qualified_standard_name_outside_std_is_rejected() -> None:
    with pytest.raises(UnsupportedTypeError, match="type alias 'other::int32_t'"):
        resolve("ELABORATED", "other::int32_t", "INT", 4)


def test_alias_of_unsupported_type_explains_the_underlying_kind() -> None:
    with pytest.raises(UnsupportedTypeError, match="classes passed by value"):
        resolve("ELABORATED", "std::string", "RECORD", 32)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("CHAR_S", "plain 'char'"),
        ("CHAR_U", "plain 'char'"),
        ("WCHAR", "wchar_t"),
        ("LONGDOUBLE", "long double"),
        ("POINTER", "pointers"),
        ("LVALUEREFERENCE", "references"),
        ("RVALUEREFERENCE", "references"),
        ("RECORD", "classes passed by value"),
        ("ENUM", "enums"),
        ("INT128", "128-bit"),
        ("SOMETHING_NEW", "type 'x' is not supported yet"),
    ],
)
def test_unsupported_kinds(kind: str, message: str) -> None:
    with pytest.raises(UnsupportedTypeError, match=message):
        resolve(kind, "x", kind, 0)


def test_void_only_as_return_type() -> None:
    assert resolve("VOID", "void", "VOID", 0, allow_void=True).category is TypeCategory.VOID
    with pytest.raises(UnsupportedTypeError, match="only valid as a return type"):
        resolve("VOID", "void", "VOID", 0)


def test_volatile_is_rejected() -> None:
    with pytest.raises(UnsupportedTypeError, match="volatile"):
        resolve("INT", "volatile int", "INT", 4, is_volatile=True)


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("int32_t", "int32_t"),
        ("const int32_t", "int32_t"),
        ("std::int32_t const", "int32_t"),
        ("::std::size_t", "size_t"),
        ("other::int32_t", None),
        ("unsigned int", None),
        ("struct Foo", None),
        ("", None),
    ],
)
def test_typedef_name(spelling: str, expected: str | None) -> None:
    assert typedef_name(spelling) == expected
