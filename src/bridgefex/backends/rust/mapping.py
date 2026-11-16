# SPDX-License-Identifier: Apache-2.0
"""The Rust spelling of the C types of the plan."""

from __future__ import annotations

from collections.abc import Mapping

from ...errors import Location
from ...plan import CFunction, CParam, Ffi, FfiKind

CORE = "::core::ffi"


class Unsupported(Exception):
    """The plan has something that the Rust bindings do not have yet."""

    def __init__(self, message: str, location: Location | None = None) -> None:
        super().__init__(message)
        self.location = location


SCALARS: dict[str, str] = {
    "bool": "bool",
    "signed char": f"{CORE}::c_schar",
    "unsigned char": f"{CORE}::c_uchar",
    "short": f"{CORE}::c_short",
    "unsigned short": f"{CORE}::c_ushort",
    "int": f"{CORE}::c_int",
    "unsigned int": f"{CORE}::c_uint",
    "long": f"{CORE}::c_long",
    "unsigned long": f"{CORE}::c_ulong",
    "long long": f"{CORE}::c_longlong",
    "unsigned long long": f"{CORE}::c_ulonglong",
    "float": "f32",
    "double": "f64",
    "int8_t": "i8",
    "int16_t": "i16",
    "int32_t": "i32",
    "int64_t": "i64",
    "uint8_t": "u8",
    "uint16_t": "u16",
    "uint32_t": "u32",
    "uint64_t": "u64",
    "size_t": "usize",
}
"""The Rust type of each scalar of the C layer: the one that a C compiler of the target gives it
(``int`` is ``c_int``), and the exact one for the types of <stdint.h>."""


def scalar(c_name: str) -> str:
    return SCALARS[c_name]


_NOT_YET = {
    FfiKind.CALLBACK: "a callback (a std::function)",
    FfiKind.RELEASE: "a callback (a std::function)",
    FfiKind.POINTER: "a container (std::vector, std::array) or a span",
}


def _not_yet(kind: FfiKind) -> Unsupported:
    what = _NOT_YET.get(kind, f"the C type of kind '{kind.value}'")
    return Unsupported(f"{what} is not supported in Rust yet")


def pointer(param: CParam, inner: str) -> str:
    """A pointer to ``inner`` as the C declaration of the parameter says it (``const T*``)."""
    return f"*const {inner}" if param.c_type.lstrip().startswith("const ") else f"*mut {inner}"


def native_type(param: CParam, names: Mapping[str, str]) -> str:
    """The type of a parameter of an ``extern "C"`` declaration. ``names`` has the Rust path of
    the type of each class and struct, by the C type."""
    ffi = param.ffi
    kind = ffi.kind
    if kind is FfiKind.SCALAR:
        return scalar(ffi.name)
    if kind is FfiKind.POINTER and ffi.inner is not None:
        if ffi.inner.kind is FfiKind.HANDLE:
            # A pointer to the pointer of a class (the object that a function makes or gives):
            # a const in its C type is the one of the class, not of the pointer.
            return f"*mut *mut {names[ffi.inner.name]}"
        return pointer(param, _pointee(param, ffi.inner, names))
    if kind is FfiKind.HANDLE:
        return pointer(param, names[ffi.name])
    if kind is FfiKind.CHAR_POINTER:
        return pointer(param, f"{CORE}::c_char")
    raise _not_yet(kind)


def _pointee(param: CParam, ffi: Ffi, names: Mapping[str, str]) -> str:
    if ffi.kind is FfiKind.SCALAR:
        return scalar(ffi.name)
    if ffi.kind is FfiKind.HANDLE:
        # The handle of a class is itself a pointer to it.
        return f"*mut {names[ffi.name]}"
    if ffi.kind is FfiKind.STRUCT:
        return names[ffi.name]
    if ffi.kind is FfiKind.VOID_POINTER and param.c_type.replace(" ", "") == "char**":
        # The text of a result: the library allocates it.
        return f"*mut {CORE}::c_char"
    raise _not_yet(ffi.kind)


def restype(function: CFunction, names: Mapping[str, str]) -> str:
    """The result of an ``extern "C"`` declaration: the status, nothing, or the pointer that a
    plain function (the conversion to a base class) gives."""
    ffi = function.ffi_restype
    if ffi.kind is FfiKind.STATUS:
        return " -> i32"
    if ffi.kind is FfiKind.VOID:
        return ""
    if ffi.kind is FfiKind.HANDLE:
        constant = function.c_return.lstrip().startswith("const ")
        return f" -> {'*const' if constant else '*mut'} {names[ffi.name]}"
    raise _not_yet(ffi.kind)
