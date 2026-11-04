# SPDX-License-Identifier: Apache-2.0
"""The Python functions that C++ calls through a function pointer: for the callbacks
(``std::function`` parameters) and for the virtual methods that a Python class overrides
(directors). Both have the same C signature (the user data, the arguments, a pointer to
the result), so they convert the arguments and the result in the same way."""

from __future__ import annotations

from dataclasses import dataclass

from ...model import EnumRef, ScalarType, StringKind, StringType, StructRef, TypeCategory
from ...plan import CallbackPart, CallbackSpec
from .ffi import ctypes_scalar
from .lower import callback_thunk_name, enum_ctypes
from .model import python_call

# Callback thunks ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PyThunk:
    """The Python function that C++ calls for the callbacks of one C signature."""

    name: str
    ctypes_args: tuple[str, ...]
    """The ctypes types of the C parameters, user_data first."""

    params: tuple[str, ...]
    arguments: tuple[str, ...]
    """The Python values of the arguments of ``_function``."""

    check: tuple[str, ...] | None
    """The check of the result ``_value``, as the head and the arguments of a
    call, or None for ``void``."""

    @property
    def body(self) -> str:
        """The statements that call ``_function`` and write the checked result,
        indented for the thunk's ``try``."""
        if self.check is None:
            return python_call("_function", self.arguments, 8)
        call = python_call("_value = _function", self.arguments, 8)
        head, *arguments = self.check
        return f"{call}\n{python_call(f'_out_result[0] = {head}', arguments, 8)}"

    @property
    def type_name(self) -> str:
        return f"{self.name}__type"


def _thunk_argument(part: CallbackPart, local: str) -> tuple[list[str], list[str], str]:
    """(the ctypes types, the Python parameters, the Python value) of an argument."""
    type_ = part.type
    if isinstance(type_, StringType):
        if type_.kind is StringKind.C_STRING:
            return ["ctypes.c_void_p"], [local], f"_runtime.callback_c_string({local})"
        return (
            ["ctypes.c_void_p", "ctypes.c_size_t"],
            [local, f"{local}_size"],
            f"_runtime.callback_string({local}, {local}_size)",
        )
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        return (
            [enum_ctypes(part.enum)],
            [local],
            f"_runtime.enum_result({part.enum.c_type}, {local})",
        )
    if isinstance(type_, StructRef):
        assert part.struct is not None
        mirror = f"_{part.struct.c_type}"
        return [f"ctypes.POINTER({mirror})"], [local], f"{mirror}__from_c({local}.contents)"
    assert isinstance(type_, ScalarType)
    return [f"ctypes.{ctypes_scalar(type_.c_name)}"], [local], local


def _thunk_result(
    part: CallbackPart, name: str = "_name", what: str = '"the result of callback"'
) -> tuple[str, tuple[str, ...]] | None:
    """(the ctypes type, the check of ``_value``) of the result, None for ``void``.
    ``name`` and ``what`` are the expressions that the messages of the check use."""
    type_ = part.type
    what = f"what={what}"
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        enum = part.enum
        valid = enum.model.valid_range
        check = (
            "_runtime.to_enum",
            "_value",
            enum.c_type,
            enum_ctypes(enum),
            name,
            "None" if valid is None else f"({valid[0]}, {valid[1]})",
            what,
        )
        return enum_ctypes(enum), check
    assert isinstance(type_, ScalarType)
    if type_.is_void:
        return None
    ctypes_type = f"ctypes.{ctypes_scalar(type_.c_name)}"
    if type_.category is TypeCategory.BOOL:
        return ctypes_type, ("_runtime.to_bool", "_value", name, what)
    if type_.category is TypeCategory.FLOATING:
        return ctypes_type, ("_runtime.to_float", "_value", ctypes_type, name, what)
    return ctypes_type, ("_runtime.to_int", "_value", ctypes_type, name, what)


def thunk(callback: CallbackSpec) -> PyThunk:
    ctypes_args = ["ctypes.c_void_p"]
    params = ["_user_data"]
    arguments: list[str] = []
    for position, part in enumerate(callback.arguments, start=1):
        types, names, value = _thunk_argument(part, f"_arg{position}")
        ctypes_args += types
        params += names
        arguments.append(value)
    check: tuple[str, ...] | None = None
    result = _thunk_result(callback.result)
    if result is not None:
        ctypes_type, check = result
        ctypes_args.append(f"ctypes.POINTER({ctypes_type})")
        params.append("_out_result")
    return PyThunk(
        name=callback_thunk_name(callback),
        ctypes_args=tuple(ctypes_args),
        params=tuple(params),
        arguments=tuple(arguments),
        check=check,
    )


# Virtual methods that a Python class overrides ----------------------------------


@dataclass(frozen=True, slots=True)
class PyDirectorThunk:
    """The Python function that C++ calls for a virtual method of a director: it finds the
    object that the director belongs to and calls its method."""

    name: str
    method: str
    """The name of the method of the object that overrides it."""

    ctypes_args: tuple[str, ...]
    """The ctypes types of the C parameters, user_data first."""

    params: tuple[str, ...]
    arguments: tuple[str, ...]
    """The Python values of the arguments of ``_function``."""

    check: tuple[str, ...] | None
    """The check of the result ``_value`` of a number or an enum, as the head and the
    arguments of a call, which is written to the result."""

    statement: str | None
    """What gives a string or a struct result to C++ (None for the other types)."""

    def body(self, indent: int) -> str:
        """The statements that call ``_function`` and give its result to C++, indented by
        ``indent`` spaces."""
        if self.check is None and self.statement is None:
            return python_call("_function", self.arguments, indent)
        call = python_call("_value = _function", self.arguments, indent)
        if self.statement is not None:
            return f"{call}\n{' ' * indent}{self.statement}"
        assert self.check is not None
        head, *arguments = self.check
        return f"{call}\n{python_call(f'_out_result[0] = {head}', arguments, indent)}"

    @property
    def type_name(self) -> str:
        return f"{self.name}__type"


def director_thunk(c_type: str, field: str, method: str, callback: CallbackSpec) -> PyDirectorThunk:
    """The thunk of the vtable member ``field`` of the director of ``c_type``."""
    ctypes_args = ["ctypes.c_void_p"]
    params = ["_user_data"]
    arguments: list[str] = []
    for position, part in enumerate(callback.arguments, start=1):
        types, names, value = _thunk_argument(part, f"_arg{position}")
        ctypes_args += types
        params += names
        arguments.append(value)
    check: tuple[str, ...] | None = None
    statement: str | None = None
    result = callback.result
    name = repr(method)
    if isinstance(result.type, StringType):
        ctypes_args.append("ctypes.c_void_p")
        params.append("_out_result")
        statement = f"_runtime.sink_set(_out_result, _value, {name})"
    elif isinstance(result.type, StructRef):
        assert result.struct is not None
        mirror = f"_{result.struct.c_type}"
        ctypes_args.append(f"ctypes.POINTER({mirror})")
        params.append("_out_result")
        statement = f"_out_result[0] = {mirror}__to_c(_value, {name})"
    else:
        scalar = _thunk_result(result, name, '"the result of the override"')
        if scalar is not None:
            ctypes_type, check = scalar
            ctypes_args.append(f"ctypes.POINTER({ctypes_type})")
            params.append("_out_result")
    return PyDirectorThunk(
        name=f"_director__{c_type}__{field}",
        method=method,
        ctypes_args=tuple(ctypes_args),
        params=tuple(params),
        arguments=tuple(arguments),
        check=check,
        statement=statement,
    )
