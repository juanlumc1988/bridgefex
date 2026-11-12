# SPDX-License-Identifier: Apache-2.0
"""The Java spelling of the C types of the plan, and how a value crosses the boundary.

The bindings use the foreign function API of Java 22 (``java.lang.foreign``). Java has no
unsigned types: an 8, 16 or 32-bit unsigned integer is the next bigger signed type, whose
values the bindings check (0..255 for ``uint8_t``), and a 64-bit one (and ``size_t``) is a
``long`` marked ``@BridgefexUnsigned``."""

from __future__ import annotations

from dataclasses import dataclass

from ...errors import Location
from ...plan import Ffi, FfiKind

R = "BridgefexRuntime"
"""The class of the runtime, which the generated code of the headers imports."""

STRING = "java.lang.String"
"""``java.lang`` types are named with their package: a class of a header may hide ``String``."""

SEGMENT = "java.lang.foreign.MemorySegment"


class Unsupported(Exception):
    """The plan has something that the Java bindings do not have yet."""

    def __init__(self, message: str, location: Location | None = None) -> None:
        super().__init__(message)
        self.location = location


@dataclass(frozen=True, slots=True)
class Scalar:
    """A scalar of the C layer in Java."""

    public: str
    """The type that the API shows."""

    native: str
    """The type that the downcall handle takes (what its layout carries)."""

    layout: str
    """The layout of the C type, an expression of the runtime."""

    to_native: str = "{}"
    """The expression that gives the native value from the public one (``{}``: the public
    value, ``{name}``: the name of the parameter, for the message of a check)."""

    read: str = ""
    """The expression that reads the public value from the memory ``{seg}`` (an out
    parameter, a member of a struct at ``{seg}`` and ``{off}``)."""

    write: str = ""
    """The statement that writes the public value ``{}`` to ``{seg}`` at ``{off}``."""

    unsigned: bool = False
    """A 64-bit unsigned integer, carried by a ``long``."""

    @property
    def annotation(self) -> str:
        return "@BridgefexUnsigned " if self.unsigned else ""


def _plain(java: str, layout: str, getter: str) -> Scalar:
    return Scalar(
        java,
        java,
        f"{R}.{layout}",
        read=f"{{seg}}.get({R}.{layout}, {{off}})",
        write=f"{{seg}}.set({R}.{layout}, {{off}}, {{}});",
    )


def _widened(public: str, narrow: str, layout: str, convert: str, widen: str) -> Scalar:
    """An unsigned integer that Java holds in a bigger signed type."""
    return Scalar(
        public,
        narrow,
        f"{R}.{layout}",
        to_native=f"{R}.{convert}({{name}}, {{}})",
        read=f"{R}.{widen}({{seg}}.get({R}.{layout}, {{off}}))",
        write=f"{{seg}}.set({R}.{layout}, {{off}}, {R}.{convert}({{name}}, {{}}));",
    )


_C_LONG = Scalar(
    "long",
    "long",
    f"{R}.C_LONG",
    to_native=f"{R}.toCLong({{name}}, {{}})",
    read=f"{R}.getCLong({{seg}}, {{off}})",
    write=f"{R}.putCLong({{seg}}, {{off}}, {R}.toCLong({{name}}, {{}}));",
)
_C_ULONG = Scalar(
    "long",
    "long",
    f"{R}.C_LONG",
    to_native=f"{R}.toCULong({{name}}, {{}})",
    read=f"{R}.getCULong({{seg}}, {{off}})",
    write=f"{R}.putCLong({{seg}}, {{off}}, {R}.toCULong({{name}}, {{}}));",
    unsigned=True,
)
_SIZE = Scalar(
    "long",
    "long",
    f"{R}.C_SIZE",
    to_native=f"{R}.toSize({{name}}, {{}})",
    read=f"{R}.getSize({{seg}}, {{off}})",
    write=f"{R}.putSize({{seg}}, {{off}}, {R}.toSize({{name}}, {{}}));",
    unsigned=True,
)
_U64 = Scalar(
    "long",
    "long",
    f"{R}.C_LONG_LONG",
    read=f"{{seg}}.get({R}.C_LONG_LONG, {{off}})",
    write=f"{{seg}}.set({R}.C_LONG_LONG, {{off}}, {{}});",
    unsigned=True,
)
_BOOL = Scalar(
    "boolean",
    "boolean",
    f"{R}.C_BOOL",
    read=f"{{seg}}.get({R}.C_BOOL, {{off}})",
    write=f"{{seg}}.set({R}.C_BOOL, {{off}}, {{}});",
)

SCALARS: dict[str, Scalar] = {
    "bool": _BOOL,
    "signed char": _plain("byte", "C_BYTE", "get"),
    "unsigned char": _widened("short", "byte", "C_BYTE", "u8", "fromU8"),
    "short": _plain("short", "C_SHORT", "get"),
    "unsigned short": _widened("int", "short", "C_SHORT", "u16", "fromU16"),
    "int": _plain("int", "C_INT", "get"),
    "unsigned int": _widened("long", "int", "C_INT", "u32", "fromU32"),
    "long": _C_LONG,
    "unsigned long": _C_ULONG,
    "long long": _plain("long", "C_LONG_LONG", "get"),
    "unsigned long long": _U64,
    "float": _plain("float", "C_FLOAT", "get"),
    "double": _plain("double", "C_DOUBLE", "get"),
    "int8_t": _plain("byte", "C_BYTE", "get"),
    "int16_t": _plain("short", "C_SHORT", "get"),
    "int32_t": _plain("int", "C_INT", "get"),
    "int64_t": _plain("long", "C_LONG_LONG", "get"),
    "uint8_t": _widened("short", "byte", "C_BYTE", "u8", "fromU8"),
    "uint16_t": _widened("int", "short", "C_SHORT", "u16", "fromU16"),
    "uint32_t": _widened("long", "int", "C_INT", "u32", "fromU32"),
    "uint64_t": _U64,
    "size_t": _SIZE,
}


def scalar(c_name: str) -> Scalar:
    return SCALARS[c_name]


def native_layout(ffi: Ffi) -> str:
    """The layout of a parameter or a result of a downcall."""
    kind = ffi.kind
    if kind is FfiKind.STATUS:
        return f"{R}.C_INT"
    if kind is FfiKind.SCALAR:
        return scalar(ffi.name).layout
    if kind in (
        FfiKind.HANDLE,
        FfiKind.VOID_POINTER,
        FfiKind.CHAR_POINTER,
        FfiKind.POINTER,
        FfiKind.CALLBACK,
        FfiKind.RELEASE,
    ):
        return f"{R}.ADDRESS"
    raise Unsupported(f"the C type of kind '{kind.value}' is not supported in Java yet")


def native_java(ffi: Ffi) -> str:
    """The Java type of a parameter or a result of a downcall handle."""
    kind = ffi.kind
    if kind is FfiKind.VOID:
        return "void"
    if kind is FfiKind.STATUS:
        return "int"
    if kind is FfiKind.SCALAR:
        return scalar(ffi.name).native
    if kind in (
        FfiKind.HANDLE,
        FfiKind.VOID_POINTER,
        FfiKind.CHAR_POINTER,
        FfiKind.POINTER,
        FfiKind.CALLBACK,
        FfiKind.RELEASE,
    ):
        return SEGMENT
    raise Unsupported(f"the C type of kind '{kind.value}' is not supported in Java yet")


RANGES: dict[str, tuple[int, int] | None] = {
    "bool": (0, 1),
    "signed char": (-128, 127),
    "unsigned char": (0, 255),
    "short": (-32768, 32767),
    "unsigned short": (0, 65535),
    "int": (-(2**31), 2**31 - 1),
    "unsigned int": (0, 2**32 - 1),
    "int8_t": (-128, 127),
    "uint8_t": (0, 255),
    "int16_t": (-32768, 32767),
    "uint16_t": (0, 65535),
    "int32_t": (-(2**31), 2**31 - 1),
    "uint32_t": (0, 2**32 - 1),
}
"""The values of the underlying types of an enum that a Java ``long`` can range-check (the 64-bit
types and those of the size of the platform have no range to check here)."""
