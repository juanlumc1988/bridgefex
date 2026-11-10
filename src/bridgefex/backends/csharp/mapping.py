# SPDX-License-Identifier: Apache-2.0
"""The C# spelling of the C types of the plan."""

from __future__ import annotations

from dataclasses import dataclass

from ...errors import Location
from ...plan import Ffi, FfiKind

# The generated code names the types of .NET with their namespace: the namespace of a header or of
# the module (`Math`, `Type`, `Exception`...) would hide a type of that name.
INTEROP = "global::System.Runtime.InteropServices"
COMPILER = "global::System.Runtime.CompilerServices"
CDECL_ATTRIBUTE = (
    f"[{INTEROP}.UnmanagedCallConv(CallConvs = new[] {{ typeof({COMPILER}.CallConvCdecl) }})]"
)
CDECL_CALLERS = (
    f"[{INTEROP}.UnmanagedCallersOnly(CallConvs = new[] {{ typeof({COMPILER}.CallConvCdecl) }})]"
)


class Unsupported(Exception):
    """The plan has something that the C# bindings do not have yet."""

    def __init__(self, message: str, location: Location | None = None) -> None:
        super().__init__(message)
        self.location = location


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


_CLONG = Scalar(
    "long",
    f"{INTEROP}.CLong",
    f"new {INTEROP}.CLong(checked((nint){{}}))",
    "(long){}.Value",
)
_CULONG = Scalar(
    "ulong",
    f"{INTEROP}.CULong",
    f"new {INTEROP}.CULong(checked((nuint){{}}))",
    "(ulong){}.Value",
)

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
    if kind in (FfiKind.HANDLE, FfiKind.VOID_POINTER, FfiKind.CALLBACK, FfiKind.RELEASE):
        # A callback and its release function are the addresses of thunks.
        return "nint"
    if kind is FfiKind.CHAR_POINTER:
        return "byte*"
    if kind is FfiKind.STRUCT:
        return f"Native.{ffi.name}"
    if kind is FfiKind.STRING_IN:
        return "Native.StringIn"
    if kind is FfiKind.STRING:
        return "Native.StringOut"
    if kind is FfiKind.POINTER and ffi.inner is not None:
        inner = native_type(ffi.inner)
        return f"out {inner}" if out else f"{inner}*"
    raise Unsupported(f"the C type of kind '{kind.value}' is not supported in C# yet")
