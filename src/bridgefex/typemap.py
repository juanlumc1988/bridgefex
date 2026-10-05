# SPDX-License-Identifier: Apache-2.0
"""Scalar types that can cross the C boundary, and how C++ types map to them.

The functions here take plain strings (libclang type kind names, spellings)
instead of libclang objects so that they can be tested without libclang.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .model import ScalarType, TypeCategory

_SIGNED = TypeCategory.SIGNED_INTEGER
_UNSIGNED = TypeCategory.UNSIGNED_INTEGER
_FLOATING = TypeCategory.FLOATING

VOID = ScalarType("void", TypeCategory.VOID, "None", "void")

# Builtin C++ types, keyed by libclang TypeKind name. Their spelling is the
# same in C (C99 <stdbool.h> provides bool) and their ABI matches on every
# supported platform.
BUILTIN_TYPES: dict[str, ScalarType] = {
    "BOOL": ScalarType("bool", TypeCategory.BOOL, "c_bool", "bool"),
    "SCHAR": ScalarType("signed char", _SIGNED, "c_byte", "schar"),
    "UCHAR": ScalarType("unsigned char", _UNSIGNED, "c_ubyte", "uchar"),
    "SHORT": ScalarType("short", _SIGNED, "c_short", "short"),
    "USHORT": ScalarType("unsigned short", _UNSIGNED, "c_ushort", "ushort"),
    "INT": ScalarType("int", _SIGNED, "c_int", "int"),
    "UINT": ScalarType("unsigned int", _UNSIGNED, "c_uint", "uint"),
    "LONG": ScalarType("long", _SIGNED, "c_long", "long"),
    "ULONG": ScalarType("unsigned long", _UNSIGNED, "c_ulong", "ulong"),
    "LONGLONG": ScalarType("long long", _SIGNED, "c_longlong", "llong"),
    "ULONGLONG": ScalarType("unsigned long long", _UNSIGNED, "c_ulonglong", "ullong"),
    "FLOAT": ScalarType("float", _FLOATING, "c_float", "float"),
    "DOUBLE": ScalarType("double", _FLOATING, "c_double", "double"),
}

_SIGNED_INTEGER_KINDS = frozenset({"SCHAR", "SHORT", "INT", "LONG", "LONGLONG"})
_UNSIGNED_INTEGER_KINDS = frozenset({"UCHAR", "USHORT", "UINT", "ULONG", "ULONGLONG"})


@dataclass(frozen=True, slots=True)
class _StandardTypedef:
    type: ScalarType
    size: int | None
    """Required size in bytes, or None when the size depends on the platform."""

    def matches(self, canonical_kind: str, size: int) -> bool:
        signed = self.type.category is _SIGNED
        kinds = _SIGNED_INTEGER_KINDS if signed else _UNSIGNED_INTEGER_KINDS
        return canonical_kind in kinds and (self.size is None or self.size == size)


def _fixed(name: str, category: TypeCategory, size: int) -> _StandardTypedef:
    token = name.removesuffix("_t")
    return _StandardTypedef(ScalarType(name, category, f"c_{token}", token), size)


# Typedefs from <cstdint>/<cstddef> that are kept by name. Their canonical
# type differs between platforms (int64_t is "long" on Linux and "long long" on
# Windows), so mapping them through the canonical type would make the output
# platform dependent.
STANDARD_TYPEDEFS: dict[str, _StandardTypedef] = {
    "int8_t": _fixed("int8_t", _SIGNED, 1),
    "int16_t": _fixed("int16_t", _SIGNED, 2),
    "int32_t": _fixed("int32_t", _SIGNED, 4),
    "int64_t": _fixed("int64_t", _SIGNED, 8),
    "uint8_t": _fixed("uint8_t", _UNSIGNED, 1),
    "uint16_t": _fixed("uint16_t", _UNSIGNED, 2),
    "uint32_t": _fixed("uint32_t", _UNSIGNED, 4),
    "uint64_t": _fixed("uint64_t", _UNSIGNED, 8),
    "size_t": _StandardTypedef(ScalarType("size_t", _UNSIGNED, "c_size_t", "size"), None),
}

# Reasons for rejecting types, keyed by libclang TypeKind name.
_UNSUPPORTED_KINDS: dict[str, str] = {
    "CHAR_S": (
        "plain 'char' is not supported: its signedness depends on the platform; "
        "use 'signed char', 'unsigned char', 'int8_t' or 'uint8_t'"
    ),
    "CHAR_U": (
        "plain 'char' is not supported: its signedness depends on the platform; "
        "use 'signed char', 'unsigned char', 'int8_t' or 'uint8_t'"
    ),
    "WCHAR": "character type 'wchar_t' is not supported yet",
    "CHAR16": "character type 'char16_t' is not supported yet",
    "CHAR32": "character type 'char32_t' is not supported yet",
    "LONGDOUBLE": "'long double' is not supported: its ABI differs between platforms",
    "INT128": "128-bit integers are not supported",
    "UINT128": "128-bit integers are not supported",
    "POINTER": "pointers are not supported yet",
    "LVALUEREFERENCE": "references are not supported yet",
    "RVALUEREFERENCE": "references are not supported yet",
    "MEMBERPOINTER": "pointers to members are not supported",
    "RECORD": "classes passed by value are not supported yet",
    "ENUM": "enums are not supported yet",
    "CONSTANTARRAY": "arrays are not supported yet",
    "INCOMPLETEARRAY": "arrays are not supported yet",
    "FUNCTIONPROTO": "function types are not supported",
    "NULLPTR": "'std::nullptr_t' is not supported",
}

# Kinds that are only sugar on top of another type (aliases, qualified names).
_SUGAR_KINDS = frozenset({"ELABORATED", "TYPEDEF", "UNEXPOSED"})

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class UnsupportedTypeError(Exception):
    """The type cannot cross the C boundary (yet)."""


def typedef_name(spelling: str) -> str | None:
    """Return the unqualified name of a type spelled as an alias.

    ``"const std::int32_t"`` and ``"::int32_t"`` both give ``"int32_t"``. Only
    the global and the ``std`` namespaces are stripped; any other qualified
    name gives ``None``.
    """
    words = [word for word in spelling.split() if word != "const"]
    if len(words) != 1:
        return None
    name = words[0].removeprefix("::").removeprefix("std::")
    return name if _IDENTIFIER.fullmatch(name) else None


def resolve(
    kind: str,
    spelling: str,
    canonical_kind: str,
    size: int,
    *,
    is_volatile: bool = False,
    allow_void: bool = False,
    user_alias: bool = False,
    standard_kinds: Mapping[str, str] | None = None,
) -> ScalarType:
    """Map a C++ type, described by libclang properties, to a scalar type.

    Top-level ``const`` is ignored: the value is copied across the boundary.

    A type spelled like a standard typedef (``int64_t``, ``size_t``...) is only
    accepted if it is not a ``user_alias`` (a typedef declared outside the
    system headers) and, when ``standard_kinds`` is given, if its canonical
    kind is the one of the real typedef in the same translation unit.

    Raises:
        UnsupportedTypeError: with a message that explains why.
    """
    if is_volatile:
        raise UnsupportedTypeError("volatile-qualified types are not supported")

    if kind == "VOID":
        if allow_void:
            return VOID
        raise UnsupportedTypeError("'void' is only valid as a return type")

    builtin = BUILTIN_TYPES.get(kind)
    if builtin is not None:
        return builtin

    if kind in _SUGAR_KINDS:
        name = typedef_name(spelling)
        if name is not None and name in STANDARD_TYPEDEFS:
            standard = STANDARD_TYPEDEFS[name]
            same_as_standard = standard_kinds is None or standard_kinds.get(name) == canonical_kind
            if not user_alias and same_as_standard and standard.matches(canonical_kind, size):
                return standard.type
            raise UnsupportedTypeError(
                f"'{spelling}' does not name the standard '{name}' type of <cstdint>/<cstddef>"
            )
        if canonical_kind in BUILTIN_TYPES or canonical_kind == "VOID":
            raise UnsupportedTypeError(
                f"type alias '{spelling}' is not supported yet; use a builtin type or a "
                "fixed-width type from <cstdint>"
            )
        reason = _UNSUPPORTED_KINDS.get(canonical_kind)
        if reason is not None:
            raise UnsupportedTypeError(f"'{spelling}': {reason}")
        raise UnsupportedTypeError(f"type '{spelling}' is not supported yet")

    reason = _UNSUPPORTED_KINDS.get(kind)
    if reason is not None:
        raise UnsupportedTypeError(reason)
    raise UnsupportedTypeError(f"type '{spelling}' is not supported yet")
