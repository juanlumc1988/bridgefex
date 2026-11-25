# SPDX-License-Identifier: Apache-2.0
"""The Rust spelling of the C types of the plan."""

from __future__ import annotations

from collections.abc import Mapping

from ...errors import Location
from ...plan import CFunction, CParam, Ffi, FfiKind

CORE = "::core::ffi"

TEXT = "crate::runtime::NativeText"
"""The string of an array of strings (``struct { const char* data; size_t size; }``)."""


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


def _not_yet(kind: FfiKind) -> Unsupported:
    return Unsupported(f"the C type of kind '{kind.value}' is not supported in Rust yet")


def pointer(param: CParam, inner: str) -> str:
    """A pointer to ``inner`` as the C declaration of the parameter says it (``const T*``)."""
    return f"*const {inner}" if param.c_type.lstrip().startswith("const ") else f"*mut {inner}"


def native_type(
    param: CParam, names: Mapping[str, str], enums: Mapping[str, str] | None = None
) -> str:
    """The type of a parameter of an ``extern "C"`` declaration. ``names`` has the Rust path of
    the type of each class and struct, by the C type, and ``enums`` the Rust type of the integer
    of each enum (the C type of an enum is that integer)."""
    ffi = param.ffi
    kind = ffi.kind
    if kind is FfiKind.SCALAR:
        return scalar(ffi.name)
    if kind is FfiKind.VOID_POINTER:
        # The user_data of a callback.
        return f"*mut {CORE}::c_void"
    if kind in (FfiKind.CALLBACK, FfiKind.RELEASE):
        return _function_pointer(param, names, enums or {})
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


def function_types(param: CParam, names: Mapping[str, str], enums: Mapping[str, str]) -> list[str]:
    """The Rust types of the parameters of a callback (or of its release function), as the C
    declaration of the parameter says them (``void (*f)(void*, int32_t, int32_t*)``)."""
    assert param.function_params is not None
    return [_c_type(text.strip(), names, enums) for text in param.function_params.split(",")]


def function_pointer(param: CParam, names: Mapping[str, str], enums: Mapping[str, str]) -> str:
    """The type of the function that a callback is, without the NULL that C accepts."""
    return f'unsafe extern "C" fn({", ".join(function_types(param, names, enums))})'


def _function_pointer(param: CParam, names: Mapping[str, str], enums: Mapping[str, str]) -> str:
    """A callback, or the function that releases it. C may give NULL (an empty
    ``std::function``), which is ``None``."""
    return f"::core::option::Option<{function_pointer(param, names, enums)}>"


def _c_type(text: str, names: Mapping[str, str], enums: Mapping[str, str]) -> str:
    """The Rust type of a C type that a callback has: the ones that the plan writes (a scalar, an
    enum, a struct, and a pointer to any of them, ``void*`` and ``const char*`` too)."""
    if text.endswith("*"):
        pointee = text[:-1].strip()
        constant = pointee.startswith("const ")
        pointee = pointee.removeprefix("const ").strip()
        inner = {"void": f"{CORE}::c_void", "char": f"{CORE}::c_char"}.get(pointee)
        return f"*{'const' if constant else 'mut'} {inner or _c_type(pointee, names, enums)}"
    if text in SCALARS:
        return SCALARS[text]
    if text in enums:
        return enums[text]
    if text in names:
        return names[text]
    raise Unsupported(f"the C type '{text}' of a callback is not supported in Rust yet")


def _pointee(param: CParam, ffi: Ffi, names: Mapping[str, str]) -> str:
    if ffi.kind is FfiKind.SCALAR:
        return scalar(ffi.name)
    if ffi.kind in (FfiKind.STRING_IN, FfiKind.STRING):
        # One string of an array of strings: its bytes and their number.
        return TEXT
    if ffi.kind is FfiKind.POINTER and ffi.inner is not None:
        # The array that a container result is: C++ allocates it and writes its address here.
        return f"*mut {_pointee(param, ffi.inner, names)}"
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
