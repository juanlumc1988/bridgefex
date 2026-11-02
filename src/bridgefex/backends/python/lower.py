# SPDX-License-Identifier: Apache-2.0
"""How the parameters and results of a wrapped function become Python.

The plan describes each parameter semantically (:class:`~bridgefex.plan.ParamSpec`);
this module decides what the generated Python function does with it: the checks
and conversions before the call, and the arguments of the call.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

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
    CFunction,
    ClassInfo,
    ElementSpec,
    EnumPlan,
    ParamSpec,
    ResultSpec,
    StructPlan,
)
from .ffi import ctypes_scalar
from .model import PyArg, PyCallable, PyResult


def enum_type(enum: EnumPlan) -> str:
    """The IntEnum of an enum, as the generated header modules name it."""
    return f"_types.{enum.c_type}"


def enum_ctypes(enum: EnumPlan) -> str:
    return f"ctypes.{ctypes_scalar(enum.model.underlying.c_name)}"


def struct_mirror(struct: StructPlan) -> str:
    """The ctypes mirror of a value struct, as the generated header modules name it."""
    return f"_types.{_mirror(struct)}"


def _mirror(struct: StructPlan) -> str:
    return f"_{struct.c_type}"


def scalar_check(value: str, scalar: ScalarType, name: str) -> str:
    """Python expression that checks ``value`` for a scalar of C; ``name`` is the
    expression of its name in error messages."""
    if scalar.category is TypeCategory.BOOL:
        return f"_runtime.to_bool({value}, {name})"
    if scalar.category is TypeCategory.FLOATING:
        return f"_runtime.to_float({value}, ctypes.{ctypes_scalar(scalar.c_name)}, {name})"
    return f"_runtime.to_int({value}, ctypes.{ctypes_scalar(scalar.c_name)}, {name})"


def scalar_conversion(name: str, scalar: ScalarType) -> str:
    return scalar_check(name, scalar, f'"{name}"')


def check_object(name: str, py_class: str, nullable: bool) -> str:
    """The statement that checks the type of a wrapped object, before any conversion."""
    suffix = ", nullable=True" if nullable else ""
    return f'_runtime.check_object({name}, {py_class}, "{name}"{suffix})'


def class_name(name: str) -> str:
    """The name of a wrapped class in the module of its header."""
    return naming.module_level_name(naming.python_name(name))


def py_class(info: ClassInfo, header_stem: str | None) -> tuple[str, tuple[str, ...]]:
    """The Python expression of a class, and the module (stem) to import for it
    when it belongs to another header than the one being generated."""
    name = class_name(info.model.name)
    if header_stem is None or info.header.stem == header_stem:
        return name, ()
    return f"{naming.module_alias(info.header.stem)}.{name}", (info.header.stem,)


def handle(c_type: str, is_base: bool, obj: str, *, writable: bool) -> str:
    """The expression of the handle of ``obj`` as the class ``c_type``. A class that
    others derive from reads it through the method that its objects, whatever
    their class, give the handle of this one with (see ``ClassView``)."""
    accessor = "_mptr" if writable else "_ptr"
    if is_base:
        accessor += f"_{c_type}"
    return f"{obj}.{accessor}()"


@dataclasses.dataclass(frozen=True, slots=True)
class PyElement:
    """How the elements of a container cross the boundary, in Python."""

    py_in: str
    """Python expression that converts the argument ``{name}`` to a ctypes array."""

    py_read: str
    """Python function that copies the elements of a result out of the C block."""

    py_value: str
    """Python expression of the result from the copy ``{data}``."""

    ctypes_out: str
    """The ctypes type of an element of a result."""

    read_extra: str = ""
    """More arguments of ``py_read``."""


def element(spec: ElementSpec) -> PyElement:
    type_ = spec.type
    if isinstance(type_, StringType):
        return PyElement(
            py_in='_runtime.to_string_array({name}, "{name}")',
            py_read="_runtime.string_items",
            py_value="_runtime.decode_all({data})",
            ctypes_out="_runtime.String",
        )
    if isinstance(type_, EnumRef):
        enum = spec.enum
        assert enum is not None
        valid = enum.model.valid_range
        extra = ""
        if valid is not None:
            extra = f", ({valid[0]}, {valid[1]})"
        return PyElement(
            py_in=(
                f"_runtime.to_enum_array({{name}}, {enum_type(enum)}, {enum_ctypes(enum)}, "
                f'"{{name}}"{extra})'
            ),
            py_read="_runtime.array_items",
            py_value=f"_runtime.enum_results({enum_type(enum)}, {{data}})",
            ctypes_out=enum_ctypes(enum),
        )
    if isinstance(type_, StructRef):
        struct = spec.struct
        assert struct is not None
        mirror = struct_mirror(struct)
        return PyElement(
            py_in=f'_runtime.to_struct_array({{name}}, {mirror}, {mirror}__fill, "{{name}}")',
            py_read="_runtime.struct_items",
            py_value="{data}",
            ctypes_out=mirror,
            read_extra=f", {mirror}__from_c",
        )
    assert isinstance(type_, ScalarType)
    check = {TypeCategory.BOOL: "check_bool", TypeCategory.FLOATING: "check_float"}.get(
        type_.category, "check_int"
    )
    is_bytes = type_.c_name in ("uint8_t", "unsigned char")
    return PyElement(
        py_in=(
            f'_runtime.to_array({{name}}, ctypes.{ctypes_scalar(type_.c_name)}, "{{name}}", '
            f"_runtime.{check}{', True' if is_bytes else ''})"
        ),
        py_read="_runtime.array_items",
        py_value="{data}",
        ctypes_out=f"ctypes.{ctypes_scalar(type_.c_name)}",
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
        arg = PyArg(name, (f'{name} = _types.{_mirror(struct)}__to_c({name}, "{name}")',))
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
    conversion = (
        f'{name} = _runtime.to_enum({name}, {enum_type(enum)}, {enum_ctypes(enum)}, "{name}"'
    )
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
    expression = handle(info.c_type, info.is_base, name, writable=not constant)
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


# Results -----------------------------------------------------------------------

_NO_RESULT = PyResult()


def borrowable(spec: ParamSpec) -> bool:
    """A reference or a pointer to a wrapped object that the function does not
    take: what a function without ``self`` returns by reference is borrowed from
    these."""
    return (
        isinstance(spec.type, ClassRef)
        and "transfer" not in spec.words
        and spec.type.form is not ClassForm.VALUE
    )


def lower_result(
    spec: ResultSpec | None,
    params: Sequence[ParamSpec],
    *,
    module: str,
    is_method: bool,
    header_stem: str | None,
) -> tuple[PyResult, tuple[str, ...]]:
    """The Python side of a result: the output locals, how the value is read after
    the call, and the arguments that the call passes for them."""
    if spec is None:
        return _NO_RESULT, ()
    type_ = spec.type
    if isinstance(type_, StringType):
        return _string_result(spec, type_, module)
    if isinstance(type_, EnumRef):
        enum = spec.enum
        assert enum is not None
        result = PyResult(
            setup=(f"_out = {enum_ctypes(enum)}()",),
            value=f"_runtime.enum_result({enum_type(enum)}, _out.value)",
        )
        return result, ("_out",)
    if isinstance(type_, ClassRef):
        if is_method:
            owners = "(self,)"
        else:
            names = [param.name for param in params if borrowable(param)]
            owners = "(" + ", ".join(names) + ("," if len(names) == 1 else "") + ")"
        return _object_result(spec, type_, owners, header_stem)
    if isinstance(type_, StructRef):
        struct = spec.struct
        assert struct is not None
        result = PyResult(
            setup=(f"_out = _types.{_mirror(struct)}()",),
            value=f"_types.{_mirror(struct)}__from_c(_out)",
        )
        return result, ("_out",)
    if isinstance(type_, ContainerType) and type_.kind is ContainerKind.ARRAY:
        assert spec.element is not None
        py = element(spec.element)
        items = f"{py.py_read}(_out, {type_.length}{py.read_extra})"
        result = PyResult(
            setup=(f"_out = ({py.ctypes_out} * {type_.length})()",),
            value=py.py_value.replace("{data}", items),
        )
        return result, ("_out",)
    if isinstance(type_, ContainerType):
        assert spec.element is not None
        py = element(spec.element)
        result = PyResult(
            setup=(
                f"_out = ctypes.POINTER({py.ctypes_out})()",
                "_out_size = ctypes.c_size_t()",
            ),
            # The elements are copied out of the block before it is released.
            read=f"_out_data = {py.py_read}(_out, _out_size.value{py.read_extra})",
            value=py.py_value.replace("{data}", "_out_data"),
            cleanup=("if _out:", f"    _lib.{module}_free(_out)"),
        )
        return result, ("_out", "_out_size")
    assert isinstance(type_, ScalarType)
    result = PyResult(setup=(f"_out = ctypes.{ctypes_scalar(type_.c_name)}()",), value="_out.value")
    return result, ("_out",)


def _string_result(
    spec: ResultSpec, type_: StringType, module: str
) -> tuple[PyResult, tuple[str, ...]]:
    if type_.sized:
        read = "_out_data = _runtime.string_at(_out, _out_size.value)"
        value = "_out_data" if spec.as_bytes else "_runtime.decode(_out_data)"
    else:
        # A NULL const char* is None.
        read = "_out_data = _runtime.string_at(_out, _out_size.value) if _out else None"
        value = (
            "_out_data"
            if spec.as_bytes
            else "None if _out_data is None else _runtime.decode(_out_data)"
        )
    result = PyResult(
        setup=("_out = ctypes.c_void_p()", "_out_size = ctypes.c_size_t()"),
        value=value,
        read=read,
        cleanup=("if _out:", f"    _lib.{module}_free(_out)"),
    )
    return result, ("_out", "_out_size")


def _object_result(
    spec: ResultSpec, ref: ClassRef, owners: str, header_stem: str | None
) -> tuple[PyResult, tuple[str, ...]]:
    info = spec.cls
    assert info is not None
    class_expression, imports = py_class(info, header_stem)
    pointer = f"_types._{info.c_type}__p"
    borrowed = ref.form in (ClassForm.REFERENCE, ClassForm.CONST_REFERENCE) or (
        spec.ownership == "borrowed"
    )
    if borrowed:
        constant = ref.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER)
        result = PyResult(
            setup=(f"_out = {pointer}()",),
            value=f"_runtime.borrow({class_expression}, _out, {owners}, {constant})",
            imports=imports,
        )
        return result, ("_out",)
    # An object that the caller owns: the Python object that will own it exists
    # before the call, and from the moment the C function writes the handle, its
    # collection destroys the object.
    nullable = ref.form is not ClassForm.VALUE
    finish: tuple[str, ...]
    if nullable:
        finish = ("if _out:", "    _result._adopt(_out)")
        value = "_result if _out else None"
    else:
        finish = ("_result._adopt(_out)",)
        value = "_result"
    result = PyResult(
        setup=(
            f"_result = {class_expression}.__new__({class_expression})",
            "_out = _result._watch()",
        ),
        value=value,
        finish=finish,
        imports=imports,
    )
    return result, ("_out",)


# Functions ---------------------------------------------------------------------


def build_callable(
    name: str,
    function: CFunction,
    params: Sequence[ParamSpec],
    result: ResultSpec | None,
    *,
    leading: Sequence[str] = (),
    kind: str = "static",
    module: str,
    header_stem: str | None,
) -> PyCallable:
    """The Python function that calls a C function.

    ``leading`` are the call arguments before the parameters (``self._ptr()``...).
    ``kind`` is how the function belongs to its class: ``method``, ``constructor``
    or ``static`` (also a free function): the object that keeps alive what the
    objects taken retained.
    """
    call_args = [*leading]
    transfers: list[tuple[str, str, str | None]] = []
    args: list[PyArg] = []
    for spec in params:
        arg, passed = lower_param(spec, header_stem)
        args.append(arg)
        for value in passed:
            if arg.transfer:
                # Given up last, so that every failure comes first.
                transfers.append((arg.name, f"_h{len(call_args) + 1}", arg.view))
            call_args.append(value)
    py_result, result_args = lower_result(
        result, params, module=module, is_method=kind == "method", header_stem=header_stem
    )
    call_args += result_args
    prepare: list[str] = []
    if transfers:
        # Every handle is read before any object is given up: a closed or
        # read-only object must fail while nothing has been taken.
        taken = {int(local[2:]) for _, local, _ in transfers}
        for position, value in enumerate(call_args, start=1):
            if position not in taken and "(" in value:
                prepare.append(f"_h{position} = {value}")
                call_args[position - 1] = f"_h{position}"
        for position in taken:
            call_args[position - 1] = f"_h{position}"
    retained, keep = {
        "method": ("self._retained", "self"),
        "constructor": ("{owner}._retained", "{owner}"),
        "static": ("_runtime.forever", None),
    }[kind]
    return PyCallable(
        name,
        function,
        tuple(args),
        tuple(call_args),
        py_result,
        prepare=tuple(prepare),
        transfers=tuple(transfers),
        retained=retained,
        keep=keep,
    )
