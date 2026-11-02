# SPDX-License-Identifier: Apache-2.0
"""The ctypes spelling of the C types of the plan."""

from __future__ import annotations

from dataclasses import dataclass

from ...plan import CFunction, Ffi, FfiKind

_SCALARS = {
    "bool": "c_bool",
    "signed char": "c_byte",
    "unsigned char": "c_ubyte",
    "short": "c_short",
    "unsigned short": "c_ushort",
    "int": "c_int",
    "unsigned int": "c_uint",
    "long": "c_long",
    "unsigned long": "c_ulong",
    "long long": "c_longlong",
    "unsigned long long": "c_ulonglong",
    "float": "c_float",
    "double": "c_double",
    "int8_t": "c_int8",
    "int16_t": "c_int16",
    "int32_t": "c_int32",
    "int64_t": "c_int64",
    "uint8_t": "c_uint8",
    "uint16_t": "c_uint16",
    "uint32_t": "c_uint32",
    "uint64_t": "c_uint64",
    "size_t": "c_size_t",
}
"""The ctypes name of each scalar type of the C layer, by its C name."""


def ctypes_scalar(c_name: str) -> str:
    return _SCALARS[c_name]


def ctypes_expr(ffi: Ffi, *, local: bool = False) -> str:
    """The ctypes expression of a C type. ``local``: in ``_types.py``, where the
    types it defines are not qualified."""
    prefix = "" if local else "_types."
    kind = ffi.kind
    if kind is FfiKind.VOID:
        return "None"
    if kind is FfiKind.STATUS:
        return "_runtime.Status"
    if kind is FfiKind.SCALAR:
        return f"ctypes.{ctypes_scalar(ffi.name)}"
    if kind is FfiKind.VOID_POINTER:
        return "ctypes.c_void_p"
    if kind is FfiKind.CHAR_POINTER:
        return "ctypes.c_char_p"
    if kind is FfiKind.HANDLE:
        return f"{prefix}_{ffi.name}__p"
    if kind is FfiKind.STRUCT:
        return f"{prefix}_{ffi.name}"
    if kind is FfiKind.STRING:
        return "_runtime.String"
    if kind is FfiKind.STRING_IN:
        return "_runtime.StringIn"
    if kind is FfiKind.CALLBACK:
        return f"{prefix}_callback__{ffi.name}__type"
    if kind is FfiKind.RELEASE:
        return "_runtime.Release"
    assert ffi.inner is not None
    return f"ctypes.POINTER({ctypes_expr(ffi.inner, local=local)})"


@dataclass(frozen=True, slots=True)
class PyBinding:
    """The signature that is declared for a C function."""

    name: str
    argtypes: str
    restype: str


def binding(function: CFunction, *, local: bool = False) -> PyBinding:
    argtypes = ", ".join(ctypes_expr(param.ffi, local=local) for param in function.params)
    return PyBinding(function.name, f"[{argtypes}]", ctypes_expr(function.ffi_restype, local=local))
