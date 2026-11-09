# SPDX-License-Identifier: Apache-2.0
"""The C# spelling of the C types of the plan."""

from __future__ import annotations

from dataclasses import dataclass

from ...plan import Ffi, FfiKind


@dataclass(frozen=True, slots=True)
class Scalar:
    """A scalar of the C layer in C#: the type the API shows, and the blittable type the
    P/Invoke declaration uses."""

    public: str
    native: str
    to_native: str = "{}"
    from_native: str = "{}"
    """The expression that converts from one to the other (``{}``: the value)."""

    @property
    def converts(self) -> bool:
        return self.public != self.native


_CLONG = Scalar("long", "CLong", "new CLong(checked((nint){}))", "(long){}.Value")
_CULONG = Scalar("ulong", "CULong", "new CULong(checked((nuint){}))", "(ulong){}.Value")

SCALARS: dict[str, Scalar] = {
    "bool": Scalar("bool", "byte", "({} ? (byte)1 : (byte)0)", "({} != 0)"),
    "signed char": Scalar("sbyte", "sbyte"),
    "unsigned char": Scalar("byte", "byte"),
    "short": Scalar("short", "short"),
    "unsigned short": Scalar("ushort", "ushort"),
    "int": Scalar("int", "int"),
    "unsigned int": Scalar("uint", "uint"),
    "long": _CLONG,
    "unsigned long": _CULONG,
    "long long": Scalar("long", "long"),
    "unsigned long long": Scalar("ulong", "ulong"),
    "float": Scalar("float", "float"),
    "double": Scalar("double", "double"),
    "int8_t": Scalar("sbyte", "sbyte"),
    "int16_t": Scalar("short", "short"),
    "int32_t": Scalar("int", "int"),
    "int64_t": Scalar("long", "long"),
    "uint8_t": Scalar("byte", "byte"),
    "uint16_t": Scalar("ushort", "ushort"),
    "uint32_t": Scalar("uint", "uint"),
    "uint64_t": Scalar("ulong", "ulong"),
    "size_t": Scalar("nuint", "nuint"),
}


def scalar(c_name: str) -> Scalar:
    return SCALARS[c_name]


def native_type(ffi: Ffi, *, out: bool = False) -> str:
    """The type of a parameter or a result of a P/Invoke declaration. ``out``: the
    parameter is where the function writes (a pointer to a scalar is then ``out T``)."""
    kind = ffi.kind
    if kind is FfiKind.VOID:
        return "void"
    if kind is FfiKind.STATUS:
        return "int"
    if kind is FfiKind.SCALAR:
        return scalar(ffi.name).native
    if kind in (FfiKind.HANDLE, FfiKind.VOID_POINTER):
        return "IntPtr"
    if kind is FfiKind.CHAR_POINTER:
        return "byte*"
    if kind is FfiKind.STRUCT:
        return f"Native.{ffi.name}"
    if kind is FfiKind.STRING_IN:
        return "Native.StringIn"
    if kind is FfiKind.STRING:
        return "Native.StringOut"
    assert ffi.inner is not None
    inner = native_type(ffi.inner)
    return f"out {inner}" if out else f"{inner}*"
