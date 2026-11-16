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

from ...errors import Diagnostic, GenerationError
from ...model import (
    Access,
    ClassForm,
    ClassRef,
    ContainerKind,
    ContainerType,
    EnumRef,
    ScalarType,
    StringType,
    StructRef,
)
from ...plan import (
    CallablePlan,
    CallbackPart,
    CallbackSpec,
    ClassPlan,
    DirectorEntry,
    FfiKind,
    FunctionPlan,
    ModulePlan,
)
from .containers import element_of
from .mapping import R, Unsupported, scalar
from .naming import LOCAL_PREFIX

if TYPE_CHECKING:
    from .lower import Names

L = LOCAL_PREFIX

WIDEST = 64
"""The most arguments of a callback that the bindings have interfaces for."""

FFM_SLOTS = 252
"""The most parameter slots that the linker of the foreign function API accepts (a pointer and a
``long`` take two, and it keeps three of the 255 of a method)."""

JNI_SLOTS = 255
"""The most parameter slots of a Java method (a pointer is a ``long`` over JNI)."""

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

    prelude: tuple[str, ...] = ()
    """Statements that make the value (a container is read element by element): ``{0}``, ``{1}``
    are the C parameters, ``{v}`` the local that ``read`` is, ``{n}``, ``{b}`` and ``{i}`` the
    count, the memory and the index, ``{h}`` a handle and ``{lent}`` the objects that C++ lends."""

    lends: bool = False
    """An object that C++ lends for the call: the thunk makes ``{lent}`` and closes it."""

    adopts: str = ""
    """The class of an object that C++ gives up: the thunk declares ``{v}`` before anything can
    fail, and destroys the object if the override never gets it."""


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
    if isinstance(type_, ContainerType):
        return _container_argument(part, type_, names)
    if isinstance(type_, ClassRef) and part.cls is not None:
        return _object_argument(part, type_, names)
    raise Unsupported(
        f"the type '{type_.cpp_spelling}' of an argument of a callback is not supported in Java"
    )


def _object_argument(part: CallbackPart, type_: ClassRef, names: Names) -> Part:
    """An object that C++ gives to an override: a reference or a pointer is lent (valid while the
    override runs), a ``std::unique_ptr`` is given for good (a plain object that owns it)."""
    info = part.cls
    assert info is not None
    cls = names.cls(info)
    pointer = (f"{R}.ADDRESS",)
    if type_.form is ClassForm.UNIQUE:
        adopt = (
            "if ({0}.address() != 0L) {",
            f"    BridgefexHandle {{h}} = {names.destroyer(info.c_type)};",
            "    {h}.assign({0});",
            f"    {{v}} = {cls}.wrap({{h}});",
            "}",
        )
        return Part(cls, ("pointer",), pointer, "{v}", adopt, adopts=cls)
    readonly = (
        "true" if type_.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER) else "false"
    )
    prelude = (
        f"{cls} {{v}} = {{lent}}.known({{0}}.address(), {cls}.class);",
        "if ({v} == null) {",
        f"    {{v}} = {{lent}}.borrowed({cls}.borrow({{0}}, null, {readonly}));",
        "}",
    )
    return Part(cls, ("pointer",), pointer, "{v}", prelude, lends=True)


def _container_argument(part: CallbackPart, type_: ContainerType, names: Names) -> Part:
    """A container that C++ gives to an override: a Java array, a copy of what C++ holds (the
    memory is only valid while the override runs)."""
    element = element_of(part.element, names)
    if type_.kind is ContainerKind.ARRAY:
        carriers: tuple[str, ...] = ("pointer",)
        layouts: tuple[str, ...] = (f"{R}.ADDRESS",)
        count = f"{type_.length}"
    else:
        carriers = ("pointer", "long")
        layouts = (f"{R}.ADDRESS", f"{R}.C_SIZE")
        count = f"{R}.count({{1}})"
    segment = names.flavor.declare(names.flavor.segment)
    if element.kind == "string":
        read = (
            f"{R}.takeText({{b}}.get({R}.ADDRESS, {{i}} * {element.size}), "
            f"{R}.getSize({{b}}, {{i}} * {element.size} + {R}.ADDRESS.byteSize()))"
        )
    else:
        read = element.read.replace("{seg}", "{b}").replace("{off}", f"{{i}} * {element.size}")
    prelude = (
        f"int {{n}} = {count};",
        f"{segment} {{b}} = {R}.view({{0}}, (long) {{n}} * {element.size});",
        f"{element.public}[] {{v}} = new {element.public}[{{n}}];",
        "for (int {i} = 0; {i} < {n}; {i}++) {",
        f"    {{v}}[{{i}}] = {read};",
        "}",
    )
    return Part(f"{element.public}[]", carriers, layouts, "{v}", prelude)


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


def director_result(part: CallbackPart, names: Names) -> Returns | Pointed | None:
    """What an override gives back: as for a callback, and also a string (copied by C++ through
    its sink) and a struct (written where C++ says)."""
    type_ = part.type
    if isinstance(type_, StringType):
        return Pointed("string", "java.lang.String", type_.sized)
    if isinstance(type_, StructRef):
        assert part.struct is not None
        return Pointed("struct", names.struct(part.struct))
    if isinstance(type_, ContainerType):
        return Pointed("container", f"{element_of(part.element, names).public}[]", part=part)
    if isinstance(type_, ClassRef) and part.cls is not None:
        return Pointed("object", names.cls(part.cls), part=part)
    return result(part, names)


def out_of(thunk: Thunk, names: Names) -> Returns | Pointed | None:
    """What a thunk gives back: the result of the callback, or of the override."""
    if thunk.target is not None:
        return director_result(thunk.callback.result, names)
    return result(thunk.callback.result, names)


def _slots(callback: CallbackSpec, names: Names) -> int:
    """The parameter slots of the thunk of a callback: ``user_data`` is a pointer, a pointer and a
    ``long`` or a ``double`` take two, and over the foreign function API the result pointer is one
    more (over JNI the thunk returns the result)."""
    total = 2
    for part in callback.arguments:
        for carrier in argument(part, names).carriers:
            total += 2 if carrier in ("pointer", "long", "double") else 1
    if names.flavor.modern and result(callback.result, names) is not None:
        total += 2
    return total


def _check_width(entry: DirectorEntry, names: Names) -> None:
    """The thunk of a virtual method has the parameters of the vtable member: as many as a
    function of the bindings can have (a protected pure virtual method has no C function that
    would have been refused for it)."""
    ffm = names.flavor.modern
    out = director_result(entry.callback.result, names)
    slots = 2 + sum(
        2 if carrier in ("pointer", "long", "double") else 1
        for part in entry.callback.arguments
        for carrier in argument(part, names).carriers
    )
    if out is not None and (ffm or isinstance(out, Pointed)):
        slots += 2
    limit = FFM_SLOTS if ffm else JNI_SLOTS
    if slots > limit:
        raise GenerationError(
            [
                Diagnostic(
                    f"Java: '{entry.description}' takes {slots} slots of the {limit} that the "
                    "thunk of a virtual method can have (a pointer and a long take 2; a string is "
                    "a pointer and a size)",
                    entry.location,
                )
            ]
        )


def functional(callback: CallbackSpec, names: Names) -> tuple[str, str]:
    """The Java interface of a callback and the name of its method."""
    count = len(callback.arguments)
    if count > WIDEST:
        raise Unsupported(
            f"a callback with {count} arguments (the bindings have interfaces for up to {WIDEST})"
        )
    limit = FFM_SLOTS if names.flavor.modern else JNI_SLOTS
    slots = _slots(callback, names)
    if slots > limit:
        raise Unsupported(
            f"a callback with {count} arguments takes {slots} slots of the {limit} that a function "
            "of the bindings can have (a pointer and a long take 2; a string is a pointer and a "
            "size)"
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
    c_params: tuple[str, ...] = ()
    """The C types of the parameters of the thunk (``void*`` first, then the result pointer)."""

    slot: str = ""
    """For a virtual method of a director: its name (``D_<class>_<member>``)."""

    target: Target | None = None
    """For a virtual method of a director: the Java method of the object that it calls."""

    sink: str = ""
    """The C function that gives a string result to C++ (a thunk of a director)."""

    vector_sink: str = ""
    strings_sink: str = ""
    """The C functions that give a container to C++."""


@dataclass(frozen=True, slots=True)
class Target:
    """The Java method that a thunk of a director calls: the one of the class that declares the
    virtual method (the override of the class of the object is the one that runs)."""

    root: str
    """The Java class."""

    method: str
    what: str
    """The C++ method, for the messages (``Shape.name``)."""


@dataclass(frozen=True, slots=True)
class Pointed:
    """A result that an override gives through a pointer that C++ provides: the sink of a string
    (copied by C++) or the struct that C++ reads."""

    kind: str
    """``string``, ``struct`` or ``container``."""

    public: str
    sized: bool = True
    """A ``std::string`` or ``std::string_view`` cannot be null; a ``const char*`` can."""

    part: CallbackPart | None = None
    """The container, for a ``container``."""


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

    def for_slot(self, slot: str) -> Thunk:
        """The thunk of a virtual method of a director."""
        for item in self.items:
            if item.slot == slot:
                return item
        raise KeyError(f"no thunk for the virtual method {slot}")


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
            c_params = next(
                param.function_params or ""
                for param in call.function.params
                if param.name == spec.name
            )
            items.append(
                Thunk(
                    len(items),
                    key,
                    spec.callback,
                    interface,
                    method,
                    tuple(part.strip() for part in c_params.split(",")),
                )
            )
    slots: set[str] = set()
    for director in names.directors.values():
        for slot in director.slots:
            if not slot.ok or slot.thunk in slots:
                continue
            slots.add(slot.thunk)
            entry = slot.entry
            _check_width(entry, names)
            items.append(
                Thunk(
                    len(items),
                    entry.thunk_key,
                    entry.callback,
                    "",
                    "",
                    tuple(part.strip() for part in entry.function_params.split(",")),
                    slot=slot.thunk,
                    target=Target(
                        names.classes[slot.root.c_type],
                        # A protected method is reached through the bridge that the class has.
                        slot.name
                        if slot.root_entry.access is Access.PUBLIC
                        else f"bridgefex_{slot.name}",
                        f"{slot.root.model.name}.{slot.root_entry.name}",
                    ),
                    sink=plan.string_sink_set,
                    vector_sink=plan.vector_sink_set,
                    strings_sink=plan.vector_sink_set_strings,
                )
            )
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
        if item.target is not None:
            continue
        count = len(item.callback.arguments)
        if count > 2:
            found.add((result(item.callback.result, names) is not None, count))
    return sorted(found)


# The thunks ----------------------------------------------------------------------------------


_JNI_CARRIER = {"pointer": "long"}


@dataclass(slots=True)
class Arguments:
    """What a thunk does with the C arguments."""

    parameters: list[str]
    prelude: list[str]
    """What makes the arguments: the segments of the pointers that JNI carried as a long, then
    the objects that C++ gave up, then the rest."""

    values: list[str]
    """The expression of each argument."""

    adopted: list[tuple[str, str]]
    """(the class, the local) of each object that C++ gave up."""

    lends: bool

    early: int = 0
    """How many lines of ``prelude`` come first: the pointers and the objects that C++ gave up.
    A thunk that skips its override still has to destroy those."""


def _arguments(thunk: Thunk, names: Names) -> Arguments:
    ffm = names.flavor.modern
    segment = names.flavor.segment
    parameters = [f"{segment if ffm else 'long'} {L}userData"]
    pointers: list[str] = []
    adopts: list[str] = []
    rest: list[str] = []
    values: list[str] = []
    adopted: list[tuple[str, str]] = []
    lends = False
    position = 0
    for number, part in enumerate(thunk.callback.arguments, start=1):
        argument_ = argument(part, names)
        locals_: list[str] = []
        for carrier in argument_.carriers:
            position += 1
            local = f"{L}c{position}"
            if carrier == "pointer":
                if ffm:
                    parameters.append(f"{segment} {local}")
                else:
                    parameters.append(f"long {L}r{position}")
                    pointers.append(f"{segment} {local} = {segment}.ofAddress({L}r{position});")
            else:
                parameters.append(f"{carrier} {local}")
            locals_.append(local)
        names_ = {
            "v": f"{L}v{number}",
            "n": f"{L}n{number}",
            "b": f"{L}b{number}",
            "i": f"{L}k{number}",
            "h": f"{L}h{number}",
            "lent": f"{L}lent",
        }

        def fill(text: str, locals_: list[str] = locals_, names_: dict[str, str] = names_) -> str:
            for index, local in enumerate(locals_):
                text = text.replace(f"{{{index}}}", local)
            for key, new in names_.items():
                text = text.replace(f"{{{key}}}", new)
            return text

        (adopts if argument_.adopts else rest).extend(fill(line) for line in argument_.prelude)
        if argument_.adopts:
            adopted.append((argument_.adopts, names_["v"]))
        lends = lends or argument_.lends
        values.append(fill(argument_.read))
    return Arguments(
        parameters,
        [*pointers, *adopts, *rest],
        values,
        adopted,
        lends,
        early=len(pointers) + len(adopts),
    )


def _parameters(thunk: Thunk, names: Names) -> tuple[list[str], list[str], list[str]]:
    """(the parameters of the thunk, what makes its arguments, the expression of each)."""
    arguments = _arguments(thunk, names)
    return arguments.parameters, arguments.prelude, arguments.values


def method_lines(thunk: Thunk, names: Names) -> list[str]:
    """The static method that C++ reaches: it has the C signature of the callback (``user_data``
    first and, over the foreign function API, ``out_result`` last). Its locals have the prefix of
    the generated code: a name of the module in a type (``mod.api.Point``) is a package, which a
    local or a field of that name would hide."""
    if thunk.target is not None:
        return director_lines(thunk, names)
    ffm = names.flavor.modern
    segment = names.flavor.segment
    out = result(thunk.callback.result, names)
    parameters, prelude, values = _parameters(thunk, names)
    if ffm and out is not None:
        parameters.append(f"{segment} {L}out")
    # Over JNI the thunk returns what C++ gets, and the glue writes it.
    returns = "void" if ffm or out is None else out.carrier
    nothing = "" if returns == "void" else " false" if returns == "boolean" else " 0"
    key = f"{L}userData.address()" if ffm else f"{L}userData"
    lines = [
        '@SuppressWarnings("unchecked")',
        f"private static {returns} thunk{thunk.index}({', '.join(parameters)}) {{",
        f"    Box {L}box = null;",
        "    try {",
        f"        {L}box = of({key});",
        f"        if ({L}box.skip()) {{",
        f"            return{nothing};",
        "        }",
    ]
    lines += [f"        {line}" for line in prelude]
    lines.append(f"        {thunk.interface} {L}function = ({thunk.interface}) {L}box.function;")
    call = f"{L}function.{thunk.method}({', '.join(values)})"
    if out is None:
        lines.append(f"        {call};")
    else:
        label = f'"the result of the callback \'" + {L}box.name + "\'"'
        # A null result names the callback, whatever the type (a boxed scalar would fail to unbox).
        lines.append(f"        {boxed(out.public)} {L}value = {R}.nonNull({call}, {label});")
        if out.check is not None:
            low, high = out.check
            lines.append(f"        {L}box.checkResult({L}value.value(), {low}, {high});")
        if ffm:
            lines.append(
                f"        {segment} {L}slot = {L}out.reinterpret({out.layout}.byteSize());"
            )
            write = out.write.replace("{seg}", f"{L}slot").replace("{off}", "0")
            write = write.replace("{name}", label).replace("{}", f"{L}value")
            lines.append(f"        {write}")
        else:
            convert = out.convert.replace("{name}", label).replace("{}", f"{L}value")
            lines.append(f"        return {convert};")
    lines += [
        f"    }} catch (java.lang.Throwable {L}error) {{",
        f"        fail({L}box, {L}error);",
        "    }",
    ]
    if not ffm and returns != "void":
        lines.append(f"    return{nothing};")
    lines.append("}")
    return lines


_PRIMITIVES = frozenset({"boolean", "byte", "short", "int", "long", "float", "double", "char"})


def _container_result(thunk: Thunk, names: Names, out: Pointed, call: str) -> list[str]:
    """The statements that give C++ the array that an override returned: C++ copies it into the
    container before the thunk returns."""
    target = thunk.target
    assert target is not None
    assert out.part is not None
    type_ = out.part.type
    assert isinstance(type_, ContainerType)
    element = element_of(out.part.element, names)
    ffm = names.flavor.modern
    sink = f"{L}out" if ffm else f"{L}slot"
    shape = "std::array" if type_.kind is ContainerKind.ARRAY else "std::vector"
    arena = names.flavor.declare(names.flavor.arena)
    segment = names.flavor.declare(names.flavor.segment)
    value, block, index = f"{L}value", f"{L}block", f"{L}k"
    lines = [
        f"{out.public} {value} = {call};",
        f"if ({value} == null) {{",
        "    throw new IllegalStateException("
        f'"the override {target.what} returned null for a {shape}");',
        "}",
    ]
    if type_.kind is ContainerKind.ARRAY:
        lines += [
            f"if ({value}.length != {type_.length}) {{",
            "    throw new IllegalStateException("
            f'"the override {target.what} returned " + {value}.length + " elements for a '
            f'std::array of {type_.length}");',
            "}",
        ]
    where = f'"the override {target.what}[" + {index} + "]"'
    offset = f"{index} * {element.size}"
    lines += [
        f"try ({arena} {L}arena = {R}.arena()) {{",
        f"    {segment} {block} = {L}arena.allocate("
        f"Math.max(1L, {element.size} * (long) {value}.length));",
        f"    for (int {index} = 0; {index} < {value}.length; {index}++) {{",
    ]
    if element.kind == "string":
        lines += [
            f"        byte[] {L}bytes = {R}.utf8({R}.nonNull({value}[{index}], {where}), {where});",
            f"        {segment} {L}e = {R}.bytes({L}arena, {L}bytes);",
            f"        {block}.set({R}.ADDRESS, {offset}, {L}e);",
            f"        {R}.putSize({block}, {offset} + {R}.ADDRESS.byteSize(), "
            f"(long) {L}bytes.length);",
        ]
        send = f"BridgefexNative.{thunk.strings_sink}({sink}, {block}, (long) {value}.length);"
    else:
        write = element.write
        for key, new in (
            ("{seg}", block),
            ("{off}", offset),
            ("{value}", f"{value}[{index}]"),
            ("{name}", where),
        ):
            write = write.replace(key, new)
        lines.append(f"        {write}")
        send = (
            f"BridgefexNative.{thunk.vector_sink}({sink}, {block}, (long) {value}.length, "
            f"{element.size});"
        )
    lines += ["    }", f"    {send}", "}"]
    return lines


def _object_result(thunk: Thunk, names: Names, out: Pointed, call: str) -> list[str]:
    """The statements that give C++ the object that an override returned (a ``std::unique_ptr``):
    Java gives it up, as a call that takes the object gives it."""
    target = thunk.target
    assert target is not None
    assert out.part is not None
    info = out.part.cls
    assert info is not None
    ffm = names.flavor.modern
    segment = names.flavor.segment
    hold = f"hold_{info.c_type}(true, true)" if info.is_base else "hold(true, true)"
    value, lease, pointer = f"{L}value", f"{L}lease", f"{L}pointer"
    lines = [f"{out.public} {value} = {call};", f"if ({value} != null) {{"]
    if info.is_base and not info.model.virtual_destructor:
        # C++ destroys the object through this class: only an object of exactly it.
        what = f'"the override {target.what}"'
        lines.append(f"    BridgefexObject.checkExact({value}, {out.public}.class, {what});")
    lines += [
        f"    BridgefexLease {lease} = {value}.{hold};",
        "    try {",
        f"        {segment} {pointer} = {lease}.pointer();",
        f"        {lease}.give();",
        f"        {value}.surrender();",
    ]
    if ffm:
        lines.append(
            f"        {L}out.reinterpret({R}.ADDRESS.byteSize()).set({R}.ADDRESS, 0, {pointer});"
        )
    else:
        lines += [
            f"        {segment} {L}copy = {segment}.of(new byte[(int) {R}.ADDRESS.byteSize()]);",
            f"        {L}copy.set({R}.ADDRESS, 0, {pointer});",
            f"        {R}.copyIn({L}slot, {L}copy);",
        ]
    lines += ["    } finally {", f"        {lease}.close();", "    }", "}"]
    return lines


def _director_call(
    thunk: Thunk, names: Names, out: Returns | Pointed | None, call: str
) -> list[str]:
    """The statements that call the override and give its result to C++."""
    target = thunk.target
    assert target is not None
    what = f'"the override {target.what}"'
    label = f"\"the result of the override '{target.what}'\""
    body: list[str] = []
    segment = names.flavor.segment
    ffm = names.flavor.modern
    if out is None:
        body.append(f"{call};")
    elif isinstance(out, Returns):
        primitive = out.public in _PRIMITIVES
        value = call if primitive else f"{R}.nonNull({call}, {label})"
        body.append(f"{out.public} {L}value = {value};")
        if out.check is not None:
            low, high = out.check
            body.append(f"{L}box.checkResult({L}value.value(), {low}, {high}, {what});")
        if ffm:
            body.append(f"{segment} {L}slot = {L}out.reinterpret({out.layout}.byteSize());")
            write = out.write.replace("{seg}", f"{L}slot").replace("{off}", "0")
            body.append(write.replace("{name}", label).replace("{}", f"{L}value"))
        else:
            convert = out.convert.replace("{name}", label).replace("{}", f"{L}value")
            body.append(f"return {convert};")
    elif out.kind == "container":
        body += _container_result(thunk, names, out, call)
    elif out.kind == "object":
        body += _object_result(thunk, names, out, call)
    elif out.kind == "struct":
        body.append(f"{out.public} {L}value = {R}.nonNull({call}, {label});")
        if ffm:
            # The struct is checked while it is written (a member out of range fails): it is
            # written in memory of Java and copied, so that C++ gets all of it or nothing.
            arena = names.flavor.declare(names.flavor.arena)
            body += [
                f"try ({arena} {L}arena = {R}.arena()) {{",
                f"    {segment} {L}copy = {L}arena.allocate({out.public}.LAYOUT);",
                f"    {L}value.write({L}copy);",
                f"    {R}.view({L}out, {out.public}.LAYOUT.byteSize()).copyFrom({L}copy);",
                "}",
            ]
        else:
            # Java cannot write the memory that C++ gave: the struct is written in memory of Java,
            # and copied where C++ says.
            body += [
                f"{segment} {L}copy = "
                f"{segment}.of(new byte[(int) {out.public}.LAYOUT.byteSize()]);",
                f"{L}value.write({L}copy);",
                f"{R}.copyIn({L}slot, {L}copy);",
            ]
    else:
        body.append(f"java.lang.String {L}value = {call};")
        if out.sized:
            body += [
                f"if ({L}value == null) {{",
                "    throw new IllegalStateException("
                f'"the override {target.what} returned null for a std::string");',
                "}",
            ]
        sink = f"{L}out" if ffm else f"{L}slot"
        arena = names.flavor.declare(names.flavor.arena)
        body += [
            f"if ({L}value != null) {{",
            f"    byte[] {L}bytes = {R}.utf8({L}value, {label});",
            f"    try ({arena} {L}arena = {R}.arena()) {{",
            f"        BridgefexNative.{thunk.sink}({sink}, {R}.bytes({L}arena, {L}bytes), "
            f"(long) {L}bytes.length);",
            "    }",
            "}",
        ]
    return body


def director_lines(thunk: Thunk, names: Names) -> list[str]:
    """The static method that C++ reaches for a virtual method of a director: it has the C
    signature of the vtable member (``user_data`` first, then the arguments and, if the method
    gives something back, the pointer where it goes). It finds the Java object of the director by
    the key and calls the method of the class that declares the virtual method: the override of
    the class of the object is the one that runs."""
    target = thunk.target
    assert target is not None
    ffm = names.flavor.modern
    segment = names.flavor.segment
    out = director_result(thunk.callback.result, names)
    arguments = _arguments(thunk, names)
    parameters, prelude, values = arguments.parameters, arguments.prelude, arguments.values
    if out is not None and (ffm or isinstance(out, Pointed)):
        parameters.append(f"{segment} {L}out" if ffm else f"long {L}out")
        if not ffm:
            prelude.append(f"{segment} {L}slot = {segment}.ofAddress({L}out);")
    # Over JNI a scalar is what the thunk returns, and the glue writes it.
    returns = out.carrier if isinstance(out, Returns) and not ffm else "void"
    nothing = "" if returns == "void" else " false" if returns == "boolean" else " 0"
    key = f"{L}userData.address()" if ffm else f"{L}userData"
    lines = [f"private static {returns} thunk{thunk.index}({', '.join(parameters)}) {{"]
    # The objects that C++ gave up are ours from the start: they are destroyed if the override
    # never gets them.
    lines += [f"    {cls} {local} = null;" for cls, local in arguments.adopted]
    if arguments.adopted:
        lines.append(f"    boolean {L}passed = false;")
    if arguments.lends:
        lines.append(f"    Lent {L}lent = new Lent();")
    # The objects that C++ gave up are made before anything can skip the override.
    lines += [
        f"    DirectorBox {L}box = null;",
        "    try {",
    ]
    lines += [f"        {line}" for line in prelude[: arguments.early]]
    lines += [
        f"        {L}box = (DirectorBox) of({key});",
        f"        if ({L}box.skip()) {{",
        f"            return{nothing};",
        "        }",
    ]
    lines += [f"        {line}" for line in prelude[arguments.early :]]
    lines += [
        f"        {target.root} {L}target = ({target.root}) {L}box.target();",
        f"        if ({L}target == null) {{",
        "            throw new IllegalStateException(",
        '                "C++ called an override of an object that was collected: '
        'keep a reference to it while C++ uses it");',
        "        }",
        f"        {L}box.enter();",
        "        try {",
    ]
    if arguments.adopted:
        lines.append(f"            {L}passed = true;")
    call = f"{L}target.{target.method}({', '.join(values)})"
    body = _director_call(thunk, names, out, call)
    lines += [f"            {line}" for line in body]
    lines += [
        "        } finally {",
        f"            {L}box.leave();",
        "        }",
        f"    }} catch (java.lang.Throwable {L}error) {{",
        f"        fail({L}box, {L}error);",
        "    }",
    ]
    if arguments.lends or arguments.adopted:
        lines.append("    finally {")
        if arguments.lends:
            lines.append(f"        {L}lent.close();")
        if arguments.adopted:
            lines.append(f"        if (!{L}passed) {{")
            lines += [
                f"            if ({local} != null) {{ {local}.close(); }}"
                for _, local in arguments.adopted
            ]
            lines.append("        }")
        lines.append("    }")
    if returns != "void":
        lines.append(f"    return{nothing};")
    lines.append("}")
    return lines


def holder_lines(thunk: Thunk, names: Names) -> list[str]:
    """The class that has the address of the thunk. It is made when it is first needed, not when
    the class is initialised: that can fail (the library is not loaded yet), and a failure of a
    static initializer leaves the class unusable for ever."""
    segment = names.flavor.segment
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
        if out_of(thunk, names) is not None:
            carriers.append("java.lang.foreign.MemorySegment")
            layouts.append(f"{R}.ADDRESS")
        types = ", ".join(f"{carrier}.class" for carrier in carriers)
        make = (
            f'stub("thunk{thunk.index}", '
            f"java.lang.invoke.MethodType.methodType(void.class, {types}), "
            f"java.lang.foreign.FunctionDescriptor.ofVoid({', '.join(layouts)}))"
        )
    else:
        make = f"{segment}.ofAddress(BridgefexNative.callbackAddress({thunk.index}))"
    return [
        f"public static final class Thunk{thunk.index} {{",
        f"    private static volatile {segment} address;",
        "",
        f"    public static synchronized {segment} address() {{",
        "        if (address == null) {",
        f"            address = {make};",
        "        }",
        "        return address;",
        "    }",
        "}",
    ]


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


# The glue of the JNI flavor ------------------------------------------------------------------

_JNI = {
    "boolean": ("jboolean", "Z", "Boolean"),
    "byte": ("jbyte", "B", "Byte"),
    "short": ("jshort", "S", "Short"),
    "int": ("jint", "I", "Int"),
    "long": ("jlong", "J", "Long"),
    "float": ("jfloat", "F", "Float"),
    "double": ("jdouble", "D", "Double"),
    "pointer": ("jlong", "J", "Long"),
}


def _carriers(thunk: Thunk, names: Names) -> list[str]:
    """The Java type that carries each C parameter of a thunk, ``user_data`` included (the result
    pointer is not one: the glue writes it)."""
    found = ["pointer"]
    for part in thunk.callback.arguments:
        found.extend(argument(part, names).carriers)
    return found


def descriptor(thunk: Thunk, names: Names) -> str:
    """The JNI signature of the Java method of a thunk."""
    out = out_of(thunk, names)
    letters = "".join(_JNI[carrier][1] for carrier in _carriers(thunk, names))
    if isinstance(out, Pointed):
        # The result pointer is one more argument, and the thunk writes through it.
        return f"({letters}J)V"
    return f"({letters}){'V' if out is None else _JNI[out.carrier][1]}"


def trampoline(thunk: Thunk, names: Names) -> list[str]:
    """The C++ function that C++ calls for the callbacks of a thunk: it has the C signature of the
    callback and calls the Java method of the thunk, on the thread that C++ is on."""
    out = out_of(thunk, names)
    carriers = _carriers(thunk, names)
    c_types = list(thunk.c_params)
    inputs = c_types[: len(carriers)]
    parameters = [f"{c_type} a{index}" for index, c_type in enumerate(inputs)]
    pointee = ""
    if out is not None:
        pointee = c_types[-1].rstrip().removesuffix("*").rstrip()
        parameters.append(f"{c_types[-1]} out")
    arguments = []
    for index, carrier in enumerate(carriers):
        jni = _JNI[carrier][0]
        if carrier == "pointer":
            arguments.append(f"static_cast<jlong>(reinterpret_cast<uintptr_t>(a{index}))")
        elif carrier == "boolean":
            arguments.append(f"(a{index} ? JNI_TRUE : JNI_FALSE)")
        else:
            arguments.append(f"static_cast<{jni}>(a{index})")
    if isinstance(out, Pointed):
        # The thunk of the override writes the result where C++ says.
        arguments.append("static_cast<jlong>(reinterpret_cast<uintptr_t>(out))")
    call_name = "Void" if not isinstance(out, Returns) else _JNI[out.carrier][2]
    call = (
        f"env->CallStatic{call_name}Method(callbacks, g_thunks[{thunk.index}]"
        + "".join(f", {argument_}" for argument_ in arguments)
        + ")"
    )
    lines = [
        f"void glue_callback_{thunk.index}({', '.join(parameters)})",
        "{",
        "    JNIEnv* env = callback_env();",
        "    jclass callbacks = env == nullptr",
        "        ? nullptr",
        "        : static_cast<jclass>(env->NewLocalRef(g_callbacks));",
        "    if (callbacks == nullptr) {",
        "        return;",
        "    }",
    ]
    if not isinstance(out, Returns):
        lines.append(f"    {call};")
    else:
        lines.append(f"    const {_JNI[out.carrier][0]} value = {call};")
    lines += [
        "    if (env->ExceptionCheck()) {",
        "        // Nothing in Java caught it (a stack overflow where it starts): it is shown.",
        "        env->ExceptionDescribe();",
        "    } else {" if isinstance(out, Returns) else "    }",
    ]
    if isinstance(out, Returns):
        write = "value != JNI_FALSE" if out.carrier == "boolean" else f"({pointee}) value"
        lines += [f"        *out = {write};", "    }"]
    lines += ["    env->DeleteLocalRef(callbacks);", "}"]
    return lines
