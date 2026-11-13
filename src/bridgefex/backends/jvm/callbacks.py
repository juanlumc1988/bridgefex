# SPDX-License-Identifier: Apache-2.0
"""The callbacks (``std::function`` parameters) in the Java wrappers.

A parameter is a functional interface (``java.util.function``, or one of the module for three
arguments or more), and C++ calls it through a *thunk*: a static method of ``BridgefexCallbacks``
with the C signature of the callback, which both flavors have, written the same:

- over the foreign function API, an upcall stub makes the thunk a function pointer, and the
  result is written where C++ says (``out_result``);
- over JNI, the glue has a function with the C signature for each thunk, which calls the thunk
  with what JNI carries (a pointer is a ``long``) and writes what it returns.

A thunk is shared by the callbacks that have the same C signature and the same Java types. It
finds the callback by the ``user_data`` (a key of a table, never an address), converts the
arguments, calls it, converts the result, and keeps its exception for the call that runs it: an
exception must not cross C++."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...model import EnumRef, ScalarType, StringType, StructRef
from ...plan import (
    CallablePlan,
    CallbackPart,
    CallbackSpec,
    ClassPlan,
    FfiKind,
    FunctionPlan,
    ModulePlan,
)
from .mapping import R, Unsupported, scalar
from .naming import LOCAL_PREFIX

if TYPE_CHECKING:
    from .lower import Names

L = LOCAL_PREFIX

WIDEST = 64
"""The most arguments of a callback that the bindings have interfaces for."""

_BOXES = {
    "boolean": "java.lang.Boolean",
    "byte": "java.lang.Byte",
    "short": "java.lang.Short",
    "int": "java.lang.Integer",
    "long": "java.lang.Long",
    "float": "java.lang.Float",
    "double": "java.lang.Double",
}


def boxed(java: str) -> str:
    """The class of a type that a generic cannot take (``int``)."""
    return _BOXES.get(java, java)


@dataclass(frozen=True, slots=True)
class Part:
    """An argument of a callback: the Java type that the interface shows, the C parameters that
    carry it, and the expression that makes the value from them (``{0}``, ``{1}``)."""

    public: str
    carriers: tuple[str, ...]
    """The Java type of each C parameter: a number, or ``pointer`` (``MemorySegment`` over the
    foreign function API, a ``BridgefexSegment`` made of a ``long`` over JNI)."""

    layouts: tuple[str, ...]
    read: str


@dataclass(frozen=True, slots=True)
class Returns:
    """What a callback gives back: ``None`` for ``void``."""

    public: str
    carrier: str
    """The Java type that carries it (what a thunk of the JNI flavor returns)."""

    layout: str
    convert: str
    """The expression of the native value, from the public one ``{}`` (``{name}``: the text of
    the message of a check)."""

    write: str
    """The statement that writes it to ``{seg}`` at ``{off}``."""

    check: tuple[int, int] | None = None
    """The range of an enum without a fixed type: C++ would not take any other value."""


def argument(part: CallbackPart, names: Names) -> Part:
    type_ = part.type
    if isinstance(type_, StringType):
        if type_.sized:
            return Part(
                "java.lang.String",
                ("pointer", "long"),
                (f"{R}.ADDRESS", f"{R}.C_SIZE"),
                f"{R}.takeText({{0}}, {R}.fromSize({{1}}))",
            )
        # A const char*: NULL is a null string.
        return Part("java.lang.String", ("pointer",), (f"{R}.ADDRESS",), f"{R}.cString({{0}})")
    if isinstance(type_, StructRef):
        assert part.struct is not None
        struct = names.struct(part.struct)
        return Part(
            struct,
            ("pointer",),
            (f"{R}.ADDRESS",),
            f"{struct}.read({R}.view({{0}}, {struct}.LAYOUT.byteSize()))",
        )
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        info = scalar(part.enum.model.underlying.c_name)
        enum = names.enum(part.enum)
        return Part(
            enum,
            (info.native,),
            (info.layout,),
            f"{enum}.of({info.from_native.replace('{}', '{0}')})",
        )
    if isinstance(type_, ScalarType) and not type_.is_void:
        info = scalar(type_.c_name)
        return Part(
            info.public,
            (info.native,),
            (info.layout,),
            info.from_native.replace("{}", "{0}"),
        )
    raise Unsupported(
        f"the type '{type_.cpp_spelling}' of an argument of a callback is not supported in Java"
    )


def result(part: CallbackPart, names: Names) -> Returns | None:
    """What a callback returns, or None if it returns nothing."""
    type_ = part.type
    if isinstance(type_, ScalarType):
        if type_.is_void:
            return None
        info = scalar(type_.c_name)
        return Returns(
            info.public,
            info.native,
            info.layout,
            info.convert,
            info.write,
        )
    if isinstance(type_, EnumRef):
        assert part.enum is not None
        info = scalar(part.enum.model.underlying.c_name)
        enum = names.enum(part.enum)
        value = f"{R}.nonNull({{}}, {{name}}).value()"
        if info.public != "long":
            value = f"({info.public}) {value}"
        return Returns(
            enum,
            info.native,
            info.layout,
            info.convert.replace("{}", value),
            info.write.replace("{}", value),
            part.enum.model.valid_range,
        )
    raise Unsupported(
        f"the type '{type_.cpp_spelling}' as the result of a callback is not supported in Java "
        "(a scalar, an enum or void)"
    )


def functional(callback: CallbackSpec, names: Names) -> tuple[str, str]:
    """The Java interface of a callback and the name of its method."""
    count = len(callback.arguments)
    if count > WIDEST:
        raise Unsupported(
            f"a callback with {count} arguments (the bindings have interfaces for up to {WIDEST})"
        )
    types = [boxed(argument(part, names).public) for part in callback.arguments]
    out = result(callback.result, names)
    if out is not None:
        types.append(boxed(out.public))
    generics = f"<{', '.join(types)}>" if types else ""
    if count > 2:
        kind = "Function" if out is not None else "Consumer"
        method = "apply" if out is not None else "accept"
        return f"{names.root}.BridgefexCallbacks.{kind}{count}{generics}", method
    plain = {
        (0, False): ("java.lang.Runnable", "run"),
        (0, True): ("java.util.function.Supplier", "get"),
        (1, False): ("java.util.function.Consumer", "accept"),
        (1, True): ("java.util.function.Function", "apply"),
        (2, False): ("java.util.function.BiConsumer", "accept"),
        (2, True): ("java.util.function.BiFunction", "apply"),
    }
    name, method = plain[(count, out is not None)]
    return f"{name}{generics}", method


@dataclass(frozen=True, slots=True)
class Thunk:
    """A function that C++ calls for the callbacks of one C signature and Java types."""

    index: int
    key: str
    """The C signature, as the plan names it."""

    callback: CallbackSpec
    interface: str
    method: str


@dataclass(slots=True)
class Thunks:
    """The thunks of a module, in the order that its declarations have."""

    items: list[Thunk]

    def of(self, key: str, callback: CallbackSpec, names: Names) -> Thunk:
        interface, _ = functional(callback, names)
        for item in self.items:
            if item.key == key and item.interface == interface:
                return item
        raise KeyError(f"no thunk for {key} {interface}")


def callables_of(plan: ModulePlan) -> list[CallablePlan]:
    """Every callable of the module: free functions, constructors, methods and static methods."""
    found: list[CallablePlan] = []
    for header in plan.headers:
        for item in header.items:
            if isinstance(item, FunctionPlan):
                found.append(item.callable)
            elif isinstance(item, ClassPlan):
                found.extend(item.constructors)
                found.extend(item.members)
    return found


def collect(plan: ModulePlan, names: Names) -> Thunks:
    """The thunks that the callable plans of the module use, each once."""
    items: list[Thunk] = []
    seen: set[tuple[str, str]] = set()
    if not names.flavor.modern:
        return Thunks(items)
    for call in callables_of(plan):
        for spec in call.params:
            if spec.callback is None:
                continue
            key = next(param.ffi.name for param in call.function.params if param.name == spec.name)
            try:
                interface, method = functional(spec.callback, names)
            except Unsupported:
                # The function is refused when it is lowered, saying why.
                continue
            if (key, interface) in seen:
                continue
            seen.add((key, interface))
            items.append(Thunk(len(items), key, spec.callback, interface, method))
    return Thunks(items)


def has_callbacks(plan: ModulePlan) -> bool:
    """True if a function of the C layer takes a callback."""
    return any(
        param.ffi.kind is FfiKind.CALLBACK
        for header in plan.headers
        for function in header.c_functions
        for param in function.params
    )


def interfaces(thunks: Thunks, names: Names) -> list[tuple[bool, int]]:
    """(returns, arguments) of each interface of the module that ``java.util.function`` lacks."""
    found: set[tuple[bool, int]] = set()
    for item in thunks.items:
        count = len(item.callback.arguments)
        if count > 2:
            found.add((result(item.callback.result, names) is not None, count))
    return sorted(found)


# The thunks ----------------------------------------------------------------------------------


_JNI_CARRIER = {"pointer": "long"}


def _parameters(thunk: Thunk, names: Names) -> tuple[list[str], list[str], list[str]]:
    """(the parameters of the thunk, what makes a segment of a pointer that JNI carried as a long,
    the expression of each argument)."""
    ffm = names.flavor.modern
    segment = names.flavor.segment
    parameters = [f"{segment if ffm else 'long'} userData"]
    prelude: list[str] = []
    values: list[str] = []
    position = 0
    for part in thunk.callback.arguments:
        argument_ = argument(part, names)
        locals_: list[str] = []
        for carrier in argument_.carriers:
            position += 1
            local = f"c{position}"
            if carrier == "pointer":
                if ffm:
                    parameters.append(f"{segment} {local}")
                else:
                    parameters.append(f"long r{position}")
                    prelude.append(f"{segment} {local} = {segment}.ofAddress(r{position});")
            else:
                parameters.append(f"{carrier} {local}")
            locals_.append(local)
        value = argument_.read
        for index, local in enumerate(locals_):
            value = value.replace(f"{{{index}}}", local)
        values.append(value)
    return parameters, prelude, values


def method_lines(thunk: Thunk, names: Names) -> list[str]:
    """The static method that C++ reaches: it has the C signature of the callback (``user_data``
    first and, over the foreign function API, ``out_result`` last)."""
    ffm = names.flavor.modern
    segment = names.flavor.segment
    out = result(thunk.callback.result, names)
    parameters, prelude, values = _parameters(thunk, names)
    if ffm and out is not None:
        parameters.append(f"{segment} out")
    # Over JNI the thunk returns what C++ gets, and the glue writes it.
    returns = "void" if ffm or out is None else out.carrier
    nothing = "" if returns == "void" else " false" if returns == "boolean" else " 0"
    lines = [
        '@SuppressWarnings("unchecked")',
        f"private static {returns} thunk{thunk.index}({', '.join(parameters)}) {{",
        "    Box box = null;",
        "    try {",
        f"        box = of({'userData.address()' if ffm else 'userData'});",
        "        if (box.skip()) {",
        f"            return{nothing};",
        "        }",
    ]
    lines += [f"        {line}" for line in prelude]
    lines.append(f"        {thunk.interface} function = ({thunk.interface}) box.function;")
    call = f"function.{thunk.method}({', '.join(values)})"
    if out is None:
        lines.append(f"        {call};")
    else:
        lines.append(f"        {boxed(out.public)} value = {call};")
        label = '"the result of the callback \'" + box.name + "\'"'
        if out.check is not None:
            low, high = out.check
            lines.append(
                f"        box.checkResult({R}.nonNull(value, {label}).value(), {low}, {high});"
            )
        if ffm:
            lines.append(f"        {segment} slot = out.reinterpret({out.layout}.byteSize());")
            write = out.write.replace("{seg}", "slot").replace("{off}", "0")
            write = write.replace("{name}", label).replace("{}", "value")
            lines.append(f"        {write}")
        else:
            convert = out.convert.replace("{name}", label).replace("{}", "value")
            lines.append(f"        return {convert};")
    lines += [
        "    } catch (java.lang.Throwable error) {",
        "        fail(box, error);",
        "    }",
    ]
    if not ffm and returns != "void":
        lines.append(f"    return{nothing};")
    lines.append("}")
    return lines


def holder_lines(thunk: Thunk, names: Names) -> list[str]:
    """The class that has the address of the thunk, made when it is first needed."""
    segment = names.flavor.segment
    lines = [f"public static final class Thunk{thunk.index} {{"]
    if names.flavor.modern:
        carriers = ["java.lang.foreign.MemorySegment"]
        layouts = [f"{R}.ADDRESS"]
        for part in thunk.callback.arguments:
            argument_ = argument(part, names)
            for carrier, layout in zip(argument_.carriers, argument_.layouts, strict=True):
                carriers.append(
                    "java.lang.foreign.MemorySegment" if carrier == "pointer" else carrier
                )
                layouts.append(layout)
        out = result(thunk.callback.result, names)
        if out is not None:
            carriers.append("java.lang.foreign.MemorySegment")
            layouts.append(f"{R}.ADDRESS")
        types = ", ".join(f"{carrier}.class" for carrier in carriers)
        lines.append(
            f'    public static final {segment} ADDRESS = stub("thunk{thunk.index}", '
            f"java.lang.invoke.MethodType.methodType(void.class, {types}), "
            f"java.lang.foreign.FunctionDescriptor.ofVoid({', '.join(layouts)}));"
        )
    else:
        lines.append(
            f"    public static final {segment} ADDRESS = "
            f"{segment}.ofAddress(BridgefexNative.callbackAddress({thunk.index}));"
        )
    lines.append("}")
    return lines


def _interface(returns: bool, count: int) -> list[str]:
    """An interface for a callback that has more arguments than ``java.util.function`` has."""
    kind = "Function" if returns else "Consumer"
    generics = [f"T{i}" for i in range(1, count + 1)]
    if returns:
        generics.append("TResult")
    parameters = ", ".join(f"T{i} arg{i}" for i in range(1, count + 1))
    method = f"TResult apply({parameters});" if returns else f"void accept({parameters});"
    return [
        "",
        f"    /** A callback with {count} arguments (java.util.function has no such interface). */",
        "    @FunctionalInterface",
        f"    public interface {kind}{count}<{', '.join(generics)}> {{",
        f"        {method}",
        "    }",
    ]


def context(plan: ModulePlan, names: Names) -> dict[str, object]:
    """What the template of ``BridgefexCallbacks`` needs."""
    assert names.thunks is not None
    ffm = names.flavor.modern
    lines: list[str] = []
    for thunk in names.thunks.items:
        lines.append("")
        lines.extend(f"    {line}" if line else line for line in method_lines(thunk, names))
        lines.append("")
        lines.extend(f"    {line}" if line else line for line in holder_lines(thunk, names))
    interface_lines: list[str] = []
    for returns, count in interfaces(names.thunks, names):
        interface_lines.extend(_interface(returns, count))
    return {
        "interfaces": interface_lines,
        "thunks": lines,
        "disable_function": plan.disable_callbacks,
        "segment_type": names.flavor.segment,
        "release_parameter": "MemorySegment userData" if ffm else "long userData",
        "release_key": "userData.address()" if ffm else "userData",
        "release_address": (
            'stub("release", MethodType.methodType(void.class, MemorySegment.class), '
            f"FunctionDescriptor.ofVoid({R}.ADDRESS))"
            if ffm
            else f"{names.flavor.segment}.ofAddress(BridgefexNative.releaseAddress())"
        ),
    }
