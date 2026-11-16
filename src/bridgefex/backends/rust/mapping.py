# SPDX-License-Identifier: Apache-2.0
"""The Rust spelling of the C types of the plan."""

from __future__ import annotations

from ...errors import Location
from ...plan import CParam, Ffi, FfiKind

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


def pointer(param: CParam, inner: str) -> str:
    """A pointer to ``inner`` as the C declaration of the parameter says it (``const T*``)."""
    return f"*const {inner}" if param.c_type.lstrip().startswith("const ") else f"*mut {inner}"


def native_type(param: CParam, handles: dict[str, str]) -> str:
    """The type of a parameter of an ``extern "C"`` declaration. ``handles`` has the Rust path of
    the type of each class, by the C type."""
    ffi = param.ffi
    kind = ffi.kind
    if kind is FfiKind.SCALAR:
        return scalar(ffi.name)
    if kind is FfiKind.POINTER and ffi.inner is not None:
        return pointer(param, _pointee(ffi.inner, handles))
    if kind is FfiKind.HANDLE:
        return pointer(param, handles[ffi.name])
    raise Unsupported(f"the C type of kind '{kind.value}' is not supported in Rust yet")


def _pointee(ffi: Ffi, handles: dict[str, str]) -> str:
    if ffi.kind is FfiKind.SCALAR:
        return scalar(ffi.name)
    if ffi.kind is FfiKind.POINTER and ffi.inner is not None:
        # A pointer to a pointer: the ``out_self`` of a constructor.
        return f"*mut {_pointee(ffi.inner, handles)}"
    if ffi.kind is FfiKind.HANDLE:
        # The handle of a class is itself a pointer to it.
        return f"*mut {handles[ffi.name]}"
    raise Unsupported(f"the C type of kind '{ffi.kind.value}' is not supported in Rust yet")


def restype(ffi: Ffi) -> str:
    """The result of an ``extern "C"`` declaration: the status, or nothing."""
    if ffi.kind is FfiKind.STATUS:
        return " -> i32"
    if ffi.kind is FfiKind.VOID:
        return ""
    raise Unsupported(f"the C result of kind '{ffi.kind.value}' is not supported in Rust yet")
