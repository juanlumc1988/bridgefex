# SPDX-License-Identifier: Apache-2.0
"""How the parameters and results of a wrapped function become Python.

The plan describes each parameter semantically (:class:`~bridgefex.plan.ParamSpec`);
this module decides what the generated Python function does with it: the checks
and conversions before the call, and the arguments of the call.
"""

from __future__ import annotations

import dataclasses

from ... import naming
from ...model import (
    CallbackType,
    ClassForm,
    ClassRef,
    ContainerKind,
    ContainerType,
    EnumRef,
    ScalarType,
    StringKind,
    StringType,
    StructRef,
    TypeCategory,
)
from ...plan import (
    CallbackPart,
    CallbackSpec,
    ClassInfo,
    ElementSpec,
    ParamSpec,
    PyArg,
)


def scalar_check(value: str, scalar: ScalarType, name: str) -> str:
    """Python expression that checks ``value`` for a scalar of C; ``name`` is the
    expression of its name in error messages."""
    if scalar.category is TypeCategory.BOOL:
        return f"_runtime.to_bool({value}, {name})"
    if scalar.category is TypeCategory.FLOATING:
        return f"_runtime.to_float({value}, ctypes.{scalar.ctypes_name}, {name})"
    return f"_runtime.to_int({value}, ctypes.{scalar.ctypes_name}, {name})"


def scalar_conversion(name: str, scalar: ScalarType) -> str:
    return scalar_check(name, scalar, f'"{name}"')


def check_object(name: str, py_class: str, nullable: bool) -> str:
    """The statement that checks the type of a wrapped object, before any conversion."""
    suffix = ", nullable=True" if nullable else ""
    return f'_runtime.check_object({name}, {py_class}, "{name}"{suffix})'


def py_class(info: ClassInfo, header_stem: str | None) -> tuple[str, tuple[str, ...]]:
    """The Python expression of a class, and the module (stem) to import for it
    when it belongs to another header than the one being generated."""
    if header_stem is None or info.header.stem == header_stem:
        return info.py_name, ()
    return f"{naming.module_alias(info.header.stem)}.{info.py_name}", (info.header.stem,)


def handle(info: ClassInfo, obj: str, *, writable: bool) -> str:
    """The expression of the handle of ``obj`` as the class ``info``. A class that
    others derive from reads it through the method that its objects, whatever
    their class, give the handle of this one with (see ``ClassView``)."""
    accessor = "_mptr" if writable else "_ptr"
    if info.is_base:
        accessor += f"_{info.c_type}"
    return f"{obj}.{accessor}()"


@dataclasses.dataclass(frozen=True, slots=True)
class PyElement:
    """How the elements of a container cross the boundary, in Python."""

    py_in: str
    """Python expression that converts the argument ``{name}`` to a ctypes array."""


def element(spec: ElementSpec) -> PyElement:
    type_ = spec.type
    if isinstance(type_, StringType):
        return PyElement(py_in='_runtime.to_string_array({name}, "{name}")')
    if isinstance(type_, EnumRef):
        enum = spec.enum
        assert enum is not None
        valid = enum.model.valid_range
        extra = ""
        if valid is not None:
            extra = f", ({valid[0]}, {valid[1]})"
        return PyElement(
            py_in=(
                f"_runtime.to_enum_array({{name}}, {enum.py_type}, {enum.ctypes_type}, "
                f'"{{name}}"{extra})'
            )
        )
    if isinstance(type_, StructRef):
        struct = spec.struct
        assert struct is not None
        mirror = f"_types.{struct.mirror}"
        return PyElement(
            py_in=f'_runtime.to_struct_array({{name}}, {mirror}, {mirror}__fill, "{{name}}")'
        )
    assert isinstance(type_, ScalarType)
    check = {TypeCategory.BOOL: "check_bool", TypeCategory.FLOATING: "check_float"}.get(
        type_.category, "check_int"
    )
    is_bytes = type_.c_name in ("uint8_t", "unsigned char")
    return PyElement(
        py_in=(
            f'_runtime.to_array({{name}}, ctypes.{type_.ctypes_name}, "{{name}}", '
            f"_runtime.{check}{', True' if is_bytes else ''})"
        )
    )


# Callbacks ---------------------------------------------------------------------


def callback_thunk_name(callback: CallbackSpec) -> str:
    """The name, in ``_types.py``, of the Python function that C++ calls for the
    callbacks of one C signature."""
    tokens = [_argument_token(part.type, part) for part in callback.arguments]
    return "_callback__" + "__".join([_result_token(callback), *tokens])


def _argument_token(type_: object, part: CallbackPart) -> str:
    if isinstance(type_, StringType):
        return "cstr" if type_.kind is StringKind.C_STRING else "string"
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        return part.enum.c_type
    if isinstance(type_, StructRef):
        assert part.struct is not None
        return part.struct.c_type
    assert isinstance(type_, ScalarType)
    return type_.token


def _result_token(callback: CallbackSpec) -> str:
    part = callback.result
    type_ = part.type
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        return part.enum.c_type
    assert isinstance(type_, ScalarType)
    return "void" if type_.is_void else type_.token


# Parameters --------------------------------------------------------------------


def lower_param(spec: ParamSpec, header_stem: str | None) -> tuple[PyArg, tuple[str, ...]]:
    """The Python side of a parameter: what the function does with the argument
    before the call, and the arguments that it passes to the C function."""
    arg, call_args = _lower(spec, header_stem)
    if "retain" in spec.words and not isinstance(spec.type, CallbackType):
        arg = dataclasses.replace(arg, retain=True)
    return arg, call_args


def _lower(spec: ParamSpec, header_stem: str | None) -> tuple[PyArg, tuple[str, ...]]:
    type_ = spec.type
    name = spec.name
    if isinstance(type_, CallbackType):
        return _callback(spec)
    if isinstance(type_, StringType):
        return _string(spec, type_)
    if isinstance(type_, EnumRef):
        return _enum(spec)
    if isinstance(type_, ClassRef):
        return _object(spec, type_, header_stem)
    if isinstance(type_, StructRef):
        assert spec.struct is not None
        struct = spec.struct
        arg = PyArg(name, (f'{name} = _types.{struct.mirror}__to_c({name}, "{name}")',))
        return arg, (name,)
    if isinstance(type_, ContainerType) and type_.kind is ContainerKind.ARRAY:
        return _array(spec, type_)
    if isinstance(type_, ContainerType):
        return _vector(spec)
    assert isinstance(type_, ScalarType)
    return PyArg(name, (f"{name} = {scalar_conversion(name, type_)}",)), (name,)


def _string(spec: ParamSpec, type_: StringType) -> tuple[PyArg, tuple[str, ...]]:
    name, size = spec.name, spec.size
    if size is None:
        # NUL-terminated: Python refuses NULs, which C++ would not see.
        conversion = f'_runtime.to_c_string({name}, "{name}")'
        if "nullable" in spec.words:
            conversion = f"None if {name} is None else {conversion}"
        return PyArg(name, (f"{name} = {conversion}",)), (name,)
    # The size of the exact bytes object: before self._ptr(), so that nothing
    # runs between it and the call.
    arg = PyArg(
        name,
        (
            f'{name} = _runtime.to_bytes({name}, "{name}")',
            f"{size} = _builtins.len({name})",
        ),
    )
    return arg, (name, size)


def _enum(spec: ParamSpec) -> tuple[PyArg, tuple[str, ...]]:
    name = spec.name
    enum = spec.enum
    assert enum is not None
    valid = enum.model.valid_range
    conversion = f'{name} = _runtime.to_enum({name}, {enum.py_type}, {enum.ctypes_type}, "{name}"'
    if valid is not None:
        low, high = valid
        conversion += f", ({low}, {high})"
    return PyArg(name, (conversion + ")",)), (name,)


def _object(
    spec: ParamSpec, ref: ClassRef, header_stem: str | None
) -> tuple[PyArg, tuple[str, ...]]:
    name = spec.name
    info = spec.cls
    assert info is not None
    nullable = "nullable" in spec.words
    class_expression, imports = py_class(info, header_stem)
    if "transfer" in spec.words:
        checks: tuple[str, ...] = (check_object(name, class_expression, nullable),)
        if info.is_base and not info.model.virtual_destructor:
            # The C++ code destroys the object through the base class, which is
            # only defined for an object of exactly that class.
            suffix = ", nullable=True" if nullable else ""
            checks += (f'_runtime.check_exact({name}, {class_expression}, "{name}"{suffix})',)
        arg = PyArg(
            name,
            checks,
            imports=imports,
            transfer=True,
            view=f"_mptr_{info.c_type}" if info.is_base else None,
        )
        return arg, (f"{name}._mptr()",)
    constant = ref.form in (ClassForm.VALUE, ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
    expression = handle(info, name, writable=not constant)
    if nullable:
        expression = f"({expression} if {name} is not None else None)"
    arg = PyArg(
        name,
        (check_object(name, class_expression, nullable),),
        imports=imports,
        borrowable=ref.form is not ClassForm.VALUE,
    )
    return arg, (expression,)


def _vector(spec: ParamSpec) -> tuple[PyArg, tuple[str, ...]]:
    name, size = spec.name, spec.size
    assert size is not None
    assert spec.element is not None
    py_in = element(spec.element).py_in
    arg = PyArg(
        name,
        (
            f"{name} = {py_in.replace('{name}', name)}",
            f"{size} = _builtins.len({name})",
        ),
    )
    return arg, (name, size)


def _array(spec: ParamSpec, type_: ContainerType) -> tuple[PyArg, tuple[str, ...]]:
    name = spec.name
    assert spec.element is not None
    conversion = element(spec.element).py_in.replace("{name}", name)
    arg = PyArg(name, (f'{name} = _runtime.fixed({conversion}, {type_.length}, "{name}")',))
    return arg, (name,)


def _callback(spec: ParamSpec) -> tuple[PyArg, tuple[str, ...]]:
    name = spec.name
    assert spec.callback is not None
    assert spec.user_data is not None
    assert spec.release is not None
    user_data, release = spec.user_data, spec.release
    thunk = callback_thunk_name(spec.callback)
    arg = PyArg(
        name,
        (f'{name} = _runtime.to_callback({name}, "{name}")',),
        registration=(
            f"{name}, {user_data}, {release}",
            (name, f"_types.{thunk}", f'"{name}"'),
        ),
    )
    return arg, (name, user_data, release)
