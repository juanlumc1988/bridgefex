# SPDX-License-Identifier: Apache-2.0
"""The scenarios of the conformance suite: what every backend must do the same.

A scenario is a short story told in terms of the C++ API, not of any language: create
objects, call functions and methods, and say what comes back or which kind of failure.
Each case of ``tests/golden`` may have a ``scenarios.toml``; every backend has a runner
that plays them against its bindings of the built library. This module reads the
scenarios and checks them against the neutral plan, so that they stay true to the
headers whichever language runs them.

Format (TOML)::

    [[scenario]]
    name = "a counter counts"
    steps = [
      { new = "counter.Counter", overload = "_int32", args = [5], as = "c" },
      { call = "c.add_int32", args = [2] },
      { call = "c.value", expect = 7 },
      { call = "counter.multiply_int32_int32", args = [3, 4], expect = 12 },
      { call = "c.setLimit", args = [-1], error = "exception", message = "negative" },
      { close = "c" },
      { call = "c.value", error = "closed" },
    ]

Names. A header is its stem (``counter``). A free function is ``<header>.<name>``, a
static method ``<header>.<Class>.<name>``, a method ``<variable>.<name>``; the name of
an overloaded function or method is followed by its overload suffix, as in the C API
(``add_int32``). A constructor is ``new``, with its ``overload`` suffix when the class
has several (``_void``, ``_int32``).

Steps. ``new``, ``call`` and ``close`` (exactly one); ``args``; ``as`` names the object
that a ``new`` or a ``call`` returns; ``expect`` is the result; ``error`` is the kind
of the failure (``exception``: C++ threw a ``std::exception``, with an optional
``message`` that the text of the error contains; ``unknown_exception``: it threw
something else; ``out_of_memory``: ``std::bad_alloc``; ``range``: an argument does not
fit its C type; ``length``: an array does not have the length of its type; ``closed``:
the object was closed, or it is borrowed from one that was); ``approx`` allows that
much difference in a floating-point result.

A class that has a director (the annotations file asks for it) can be derived from, to
override its virtual methods: ``{ derive = "shapes.Shape", named = "Mine", overrides = {
area = { returns = 4.0 }, name = { base = true } } }`` defines a class called ``Mine``,
and ``{ new = "Mine", overload = "_void", as = "m" }`` makes an object of it. An override
is named as the member of the vtable of the C API (``add_int32_int32``) and does one of
``{ returns = value }`` (it gives that value, whatever its arguments), ``{ base = true }``
(it calls the implementation of the class with its arguments and gives what it gives),
``{ argument = 0 }`` (it gives its first argument, which has the type of its result) or
``{ raises = "message" }`` (it fails: the call that made C++ run it reports it, as an
``override`` error whose text has the message). Objects: ``{ asks = { argument = 0, call =
"length", args = [] } }`` (it calls a method of the object that C++ lends it, or gives it,
as its first argument, and gives what the method gives) and ``{ makes = { new = "shapes.Beam",
overload = "_int32", args = [3] } }`` (a ``std::unique_ptr`` result: it gives C++ a new
object); ``{ returns = { null = true } }`` gives a NULL pointer. The constructors of a
derived class are those that the class shows (``overload`` is their suffix), or, if it
shows none, those of its director.

Values: numbers, booleans and strings are themselves; a list is a list; a table is one
of ``{ int = "18446744073709551615" }`` (beyond 64-bit signed TOML integers),
``{ null = true }`` (a NULL pointer or an empty pointer), ``{ ref = "c" }`` (an object
created before), ``{ enum = "colors.Color", member = "Red" }`` and
``{ struct = "geo.Point", x = 1.0, y = 2.0 }`` (names as above, nested ones as
``colors.Lamp.State``; a struct gives all its members, in declaration order). Results
are compared strictly: the type counts (an ``int`` is not an enumerator, a ``bool`` is
not an ``int``).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from bridgefex.model import (
    ClassForm,
    ClassRef,
    ContainerKind,
    ContainerType,
    EnumRef,
    ScalarType,
    StringType,
    StructRef,
    Type,
    TypeCategory,
)
from bridgefex.plan import (
    CallableKind,
    CallablePlan,
    ClassPlan,
    DirectorEntry,
    EnumPlan,
    FunctionPlan,
    HeaderPlan,
    ModulePlan,
    StructPlan,
)

CXX_ERRORS = frozenset({"exception", "unknown_exception", "out_of_memory"})
"""The failures of the C++ code, by the status of the C API: a ``std::exception``, any
other exception, and ``std::bad_alloc``."""

ERROR_KINDS = CXX_ERRORS | {"range", "length", "closed", "override"}
"""The failures that every backend reports, each in the way of its language."""

_ACTIONS = ("new", "call", "close", "derive")
_KEYS = frozenset(
    {
        *_ACTIONS,
        "args",
        "as",
        "expect",
        "error",
        "message",
        "approx",
        "overload",
        "named",
        "overrides",
    }
)
_BEHAVIORS = frozenset({"returns", "base", "argument", "raises", "asks", "makes"})
_DERIVE_KEYS = frozenset({"derive", "named", "overrides"})
_INTEGER_BITS = {
    "bool": None,
    "signed char": (8, True),
    "unsigned char": (8, False),
    "short": (16, True),
    "unsigned short": (16, False),
    "int": (32, True),
    "unsigned int": (32, False),
    "int8_t": (8, True),
    "uint8_t": (8, False),
    "int16_t": (16, True),
    "uint16_t": (16, False),
    "int32_t": (32, True),
    "uint32_t": (32, False),
    "int64_t": (64, True),
    "uint64_t": (64, False),
}
_VALUE_TABLES = frozenset({"int", "null", "ref", "enum", "struct"})


def load(path: Path) -> list[dict[str, object]]:
    """The scenarios of a file, as TOML tables (``name``, ``steps``)."""
    with path.open("rb") as file:
        data = tomllib.load(file)
    unknown = set(data) - {"scenario"}
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    scenarios = data.get("scenario", [])
    assert isinstance(scenarios, list)
    return scenarios


@dataclass(frozen=True)
class _Index:
    """What the plan offers to a scenario, by name."""

    headers: dict[str, HeaderPlan]
    classes: dict[tuple[str, str], ClassPlan]
    by_usr: dict[str, ClassPlan]
    functions: dict[tuple[str, str], CallablePlan]
    enums: dict[str, EnumPlan]
    structs: dict[str, StructPlan]

    @staticmethod
    def of(plan: ModulePlan) -> _Index:
        classes: dict[tuple[str, str], ClassPlan] = {}
        functions: dict[tuple[str, str], CallablePlan] = {}
        enums: dict[str, EnumPlan] = {}
        structs: dict[str, StructPlan] = {}
        for header in plan.headers:
            for item in header.items:
                if isinstance(item, ClassPlan):
                    classes[(header.stem, item.model.name)] = item
                    for nested in item.enums:
                        enums[f"{header.stem}.{item.model.name}.{nested.model.name}"] = nested
                elif isinstance(item, FunctionPlan):
                    call = item.callable
                    functions[(header.stem, call.name + call.suffix)] = call
                elif isinstance(item, EnumPlan):
                    enums[f"{header.stem}.{item.model.name}"] = item
                else:
                    structs[f"{header.stem}.{item.model.name}"] = item
                    for nested in item.enums:
                        enums[f"{header.stem}.{item.model.name}.{nested.model.name}"] = nested
        return _Index(
            {header.stem: header for header in plan.headers},
            classes,
            {plan_.model.usr: plan_ for plan_ in classes.values()},
            functions,
            enums,
            structs,
        )

    def methods(self, plan: ClassPlan) -> Iterator[ClassPlan]:
        """The class and its ancestors: the bindings inherit their methods."""
        yield plan
        for base in plan.bases:
            yield from self.methods(self.by_usr[base.model.usr])


def check(scenarios: list[dict[str, object]], plan: ModulePlan) -> list[str]:
    """The problems of ``scenarios`` with respect to ``plan``: a name that is not in
    the headers, a wrong number of arguments, a variable used before it exists."""
    index = _Index.of(plan)
    problems: list[str] = []
    names: set[str] = set()
    for scenario in scenarios:
        name = scenario.get("name")
        steps = scenario.get("steps")
        if not isinstance(name, str) or not isinstance(steps, list) or not steps:
            problems.append(f"a scenario needs a name and steps: {scenario!r}")
            continue
        if name in names:
            problems.append(f"{name}: the name is used twice")
        names.add(name)
        variables: dict[str, ClassPlan | None] = {}
        closed: set[str] = set()
        derived: dict[str, ClassPlan] = {}
        for number, step in enumerate(steps, start=1):
            where = f"{name}, step {number}"
            for problem in _check_step(step, index, variables, closed, derived):
                problems.append(f"{where}: {problem}")
    return problems


def _check_step(
    step: Mapping[str, object],
    index: _Index,
    variables: dict[str, ClassPlan | None],
    closed: set[str],
    derived: dict[str, ClassPlan],
) -> list[str]:
    unknown = set(step) - _KEYS
    actions = [action for action in _ACTIONS if action in step]
    if unknown or len(actions) != 1:
        return [f"unknown keys {sorted(unknown)} or not exactly one of {_ACTIONS}: {dict(step)}"]
    problems: list[str] = []
    action = actions[0]
    target = step[action]
    args = step.get("args", [])
    if not isinstance(target, str) or not isinstance(args, list):
        return [f"{action} needs a name, and args a list: {dict(step)}"]
    problems += _option_problems(step)
    for value in [*args, *([step["expect"]] if "expect" in step else [])]:
        problems += _check_value(value, index, variables)
    if action == "derive":
        return [*problems, *_derive_problems(step, index, variables, derived)]
    if action == "close":
        if target not in variables:
            problems.append(f"close of {target!r}, which does not exist")
        closed.add(target)
        return problems

    produced: ClassPlan | None = None
    if action == "new":
        cls = derived.get(target) or _class(target, index)
        if cls is None:
            return [*problems, f"no class {target!r}"]
        constructor, overload_problems = _constructor(
            cls, step.get("overload"), director=target in derived
        )
        problems += overload_problems
        call = constructor
        produced = cls
        if "expect" in step:
            problems.append("new gives an object (as): there is no result to expect")
    else:
        found = _callable(target, index, variables)
        if isinstance(found, str):
            return [*problems, found]
        call, produced = found
        if "overload" in step:
            problems.append("overload is for new")
        if "expect" in step:
            problems += _result_problems(step["expect"], call, index)
        if target.split(".")[0] in closed and step.get("error") != "closed":
            problems.append(
                f"{target.split('.')[0]!r} was closed: this call must say error = 'closed'"
            )
    if call is not None:
        problems += _argument_problems(args, call, target, index, variables, closed, step)
    if "as" in step:
        variable = step["as"]
        if not isinstance(variable, str) or variable in variables or variable in index.headers:
            problems.append(f"the variable {variable!r} is not a new name")
        else:
            variables[variable] = produced
    return problems


def _option_problems(step: Mapping[str, object]) -> list[str]:
    """What is wrong with ``expect``, ``error``, ``message`` and ``approx`` themselves."""
    problems = []
    if "expect" in step and "error" in step:
        problems.append("a step cannot expect a result and an error")
    if step.get("error", "exception") not in ERROR_KINDS:
        problems.append(f"unknown error kind {step['error']!r}")
    if step.get("error") == "override" and "message" not in step:
        problems.append("an override error says the message of the failure that it reports")
    if "message" in step and step.get("error") not in {*CXX_ERRORS, "override"}:
        problems.append(
            f"message goes with an error of the C++ code: {sorted(CXX_ERRORS)}, or an override"
        )
    if "approx" in step:
        approx = step["approx"]
        number = isinstance(approx, int | float) and not isinstance(approx, bool)
        if not number or approx < 0 or not isinstance(step.get("expect"), float):  # type: ignore[operator]
            problems.append("approx is a number that is not negative, for a floating-point result")
    return problems


def _argument_problems(
    args: list[object],
    call: CallablePlan,
    target: str,
    index: _Index,
    variables: Mapping[str, ClassPlan | None],
    closed: set[str],
    step: Mapping[str, object],
) -> list[str]:
    if len(args) != len(call.params):
        return [f"{target} takes {len(call.params)} arguments, not {len(args)}"]
    problems = []
    for position, (value, spec) in enumerate(zip(args, call.params, strict=True), start=1):
        reason = _kind_problem(value, spec.type, index, variables, result=False)
        if reason is not None:
            problems.append(f"argument {position} ({spec.name}): {reason}")
        if isinstance(value, dict) and value.get("ref") in closed and step.get("error") != "closed":
            problems.append(
                f"argument {position}: {value['ref']!r} was closed: say error = 'closed'"
            )
    return problems


def _result_problems(expected: object, call: CallablePlan, index: _Index) -> list[str]:
    if call.result is None:
        return [f"{call.name + call.suffix} returns nothing: there is no result to expect"]
    reason = _kind_problem(expected, call.result.type, index, {}, result=True)
    return [f"the result: {reason}"] if reason is not None else []


def _is_integer(value: object) -> bool:
    if isinstance(value, dict) and set(value) == {"int"}:
        return isinstance(value["int"], str) and re.fullmatch(r"-?[0-9]+", value["int"]) is not None
    return isinstance(value, int) and not isinstance(value, bool)


def _kind_problem(
    value: object,
    type_: Type,
    index: _Index,
    variables: Mapping[str, ClassPlan | None],
    *,
    result: bool,
) -> str | None:
    """Why ``value`` cannot be an argument (or the result) of ``type_``, or None. A float
    argument may be written as an integer; a result is compared strictly."""
    table = value if isinstance(value, dict) else {}
    if isinstance(type_, ScalarType):
        if type_.category is TypeCategory.BOOL:
            fits = isinstance(value, bool)
        elif type_.category is TypeCategory.FLOATING:
            fits = isinstance(value, float) or (not result and _is_integer(value))
        else:
            fits = _is_integer(value)
        return None if fits else f"{value!r} is not a {type_.c_name}"
    if isinstance(type_, StringType):
        null = result and table.get("null") is True  # a const char* result may be NULL
        return None if isinstance(value, str) or null else f"{value!r} is not a string"
    if isinstance(type_, EnumRef):
        enum = index.enums.get(str(table.get("enum")))
        same = enum is not None and enum.model.usr == type_.usr and "member" in table
        return None if same else f"{value!r} is not a member of {type_.qualified_name}"
    if isinstance(type_, StructRef):
        struct = index.structs.get(str(table.get("struct")))
        same = struct is not None and struct.model.usr == type_.usr
        return None if same else f"{value!r} is not a {type_.qualified_name}"
    if isinstance(type_, ClassRef):
        if table.get("null") is True:
            return None
        owner = variables.get(str(table.get("ref")))
        if not result and owner is not None:
            usrs = {ancestor.model.usr for ancestor in index.methods(owner)}
            if type_.usr in usrs:
                return None
        return f"{value!r} is not a {type_.qualified_name}" + (
            " (a result of a class can only be null here)" if result else ""
        )
    if isinstance(type_, ContainerType):
        if not isinstance(value, list):
            return f"{value!r} is not a list"
        # (An argument of another length is how a scenario asks for a 'length' error.)
        if result and type_.kind is ContainerKind.ARRAY and len(value) != type_.length:
            return f"{value!r} needs {type_.length} elements"
        for position, item in enumerate(value):
            reason = _kind_problem(item, type_.element, index, variables, result=result)
            if reason is not None:
                return f"element {position}: {reason}"
        return None
    return "callbacks are not in the scenarios yet"


def _class(target: str, index: _Index) -> ClassPlan | None:
    header, _, name = target.partition(".")
    return index.classes.get((header, name))


def _constructor(
    cls: ClassPlan, overload: object, *, director: bool = False
) -> tuple[CallablePlan | None, list[str]]:
    """The constructor that ``overload`` names (the only one if there is one). A class
    that is derived from has the ones that it shows, or those of its director."""
    constructors = cls.constructors
    if director and not constructors and cls.director is not None:
        constructors = cls.director.constructors
    if len(constructors) == 1:
        if overload is not None:
            return constructors[0], ["the class has one constructor: no overload"]
        return constructors[0], []
    matching = [item for item in constructors if item.suffix == overload]
    if not matching:
        options = [item.suffix for item in constructors]
        return None, [f"overload {overload!r} is not one of {options}"]
    return matching[0], []


def _derive_problems(
    step: Mapping[str, object],
    index: _Index,
    variables: Mapping[str, ClassPlan | None],
    derived: dict[str, ClassPlan],
) -> list[str]:
    """What is wrong with the definition of a class that overrides virtual methods."""
    target, named, overrides = step["derive"], step.get("named"), step.get("overrides")
    cls = _class(target, index) if isinstance(target, str) else None
    if cls is None:
        return [f"no class {target!r}"]
    if cls.director is None:
        return [f"{target} has no director: nothing can derive from it"]
    problems = [
        f"a derive step has no {key!r}: it is not played"
        for key in sorted(set(step) - _DERIVE_KEYS)
    ]
    if (
        not isinstance(named, str)
        or named in derived
        or named in variables
        or named in index.headers
    ):
        problems.append(f"the class {named!r} is not a new name")
    if not isinstance(overrides, dict) or not overrides:
        return [*problems, "overrides is a table of the methods to override"]
    entries = {entry.field: entry for entry in cls.director.entries}
    for field, behavior in overrides.items():
        entry = entries.get(field)
        if entry is None:
            problems.append(
                f"{target} has no virtual method {field!r} to override: {sorted(entries)}"
            )
            continue
        problems += [
            f"{field}: {problem}" for problem in _behavior_problems(behavior, entry, index)
        ]
    missing = sorted(field for field, entry in entries.items() if entry.is_pure)
    missing = [field for field in missing if field not in overrides]
    if missing:
        problems.append(f"the pure virtual methods {missing} have to be overridden")
    if isinstance(named, str):
        derived[named] = cls
    return problems


def _behavior_problems(behavior: object, entry: DirectorEntry, index: _Index) -> list[str]:
    if not isinstance(behavior, dict) or len(set(behavior) & _BEHAVIORS) != 1 or len(behavior) != 1:
        return [f"an override does exactly one of {sorted(_BEHAVIORS)}: {behavior!r}"]
    (kind,) = behavior
    value = behavior[kind]
    result = entry.callback.result.type
    void = isinstance(result, ScalarType) and result.is_void
    if kind == "returns":
        if void:
            return ["the method returns nothing: there is nothing to return"]
        problems = _check_value(value, index, {})
        reason = _kind_problem(value, result, index, {}, result=True)
        if reason is None:
            reason = _result_problem(value, result)
        return problems + ([f"the result: {reason}"] if reason is not None else [])
    if kind == "base":
        return ["a pure virtual method has no implementation to call"] if entry.is_pure else []
    if kind == "argument":
        arguments = entry.callback.arguments
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < len(arguments):
            return [f"argument is the position of one of its {len(arguments)} arguments"]
        if void:
            return ["the method returns nothing: it cannot give an argument back"]
        if not _same_type(arguments[value].type, result):
            return [f"argument {value} is not of the type of the result"]
        return []
    if kind == "asks":
        return _asks_problems(value, entry, index)
    if kind == "makes":
        return _makes_problems(value, entry, index)
    return [] if isinstance(value, str) else ["raises takes the message of the failure"]


def _asks_problems(value: object, entry: DirectorEntry, index: _Index) -> list[str]:
    """``asks``: a method of an object that the override gets, and what it gives."""
    keys = {"argument", "call", "args"}
    if not isinstance(value, dict) or not {"argument", "call"} <= set(value) <= keys:
        return ["asks is { argument = N, call = 'method', args = [...] }"]
    arguments = entry.callback.arguments
    position = value["argument"]
    if (
        not isinstance(position, int)
        or isinstance(position, bool)
        or not 0 <= position < len(arguments)
    ):
        return [f"asks: argument is the position of one of its {len(arguments)} arguments"]
    type_ = arguments[position].type
    owner = index.by_usr.get(type_.usr) if isinstance(type_, ClassRef) else None
    if owner is None:
        return [f"asks: argument {position} is not an object of a wrapped class"]
    name = value["call"]
    found = None
    for ancestor in index.methods(owner):
        found = _member(ancestor, str(name), static=False)
        if found is not None:
            break
    if found is None:
        return [f"asks: no method {name!r} in {owner.model.name}"]
    args = value.get("args", [])
    if not isinstance(args, list):
        return ["asks: args is a list"]
    problems = _argument_problems(args, found, str(name), index, {}, set(), {})
    problems += [problem for item in args for problem in _check_value(item, index, {})]
    if type_.form in (ClassForm.CONST_REFERENCE, ClassForm.CONST_POINTER) and not found.is_const:
        problems.append(f"asks: {name} changes the object, which C++ lends as const")
    result = entry.callback.result.type
    if found.result is None:
        problems.append(f"asks: {name} returns nothing")
    elif not _same_type(found.result.type, result):
        problems.append(f"asks: {name} does not give the type of the result")
    return problems


def _makes_problems(value: object, entry: DirectorEntry, index: _Index) -> list[str]:
    """``makes``: a new object, for a ``std::unique_ptr`` result."""
    result = entry.callback.result.type
    if not isinstance(result, ClassRef) or result.form is not ClassForm.UNIQUE:
        return ["makes: the result of the method is not a std::unique_ptr"]
    if not isinstance(value, dict) or not {"new"} <= set(value) <= {"new", "overload", "args"}:
        return ["makes is { new = 'header.Class', overload = '_suffix', args = [...] }"]
    cls = _class(value["new"], index) if isinstance(value["new"], str) else None
    if cls is None:
        return [f"makes: no class {value['new']!r}"]
    if result.usr not in {ancestor.model.usr for ancestor in index.methods(cls)}:
        return [f"makes: {cls.model.name} is not a {result.qualified_name}"]
    constructor, problems = _constructor(cls, value.get("overload"))
    args = value.get("args", [])
    if not isinstance(args, list):
        return [*problems, "makes: args is a list"]
    if constructor is not None:
        problems += _argument_problems(args, constructor, cls.model.name, index, {}, set(), {})
    problems += [problem for item in args for problem in _check_value(item, index, {})]
    return problems


def _result_problem(value: object, result: Type) -> str | None:
    """What the checks of the types leave: an override cannot give null for a string, and
    an integer must fit the type of the result (also in the elements of a container)."""
    if isinstance(result, ContainerType) and isinstance(value, list):
        for position, item in enumerate(value):
            reason = _result_problem(item, result.element)
            if reason is not None:
                return f"element {position}: {reason}"
        return None
    if isinstance(result, StringType) and isinstance(value, dict):
        return f"{value!r}: an override gives a string, not null"
    if isinstance(result, ScalarType) and result.c_name in _INTEGER_BITS:
        bits = _INTEGER_BITS[result.c_name]
        number = int(value["int"]) if isinstance(value, dict) else value
        if bits is not None and isinstance(number, int) and not isinstance(number, bool):
            width, signed = bits
            low, high = (-(2 ** (width - 1)), 2 ** (width - 1) - 1) if signed else (0, 2**width - 1)
            if not low <= number <= high:
                return f"{number} does not fit a {result.c_name} ([{low}, {high}])"
    return None


def _same_type(first: Type, second: Type) -> bool:
    """Whether a value of ``first`` can be given back as a ``second``."""
    if isinstance(first, StringType) and isinstance(second, StringType):
        return True
    if isinstance(first, ScalarType) and isinstance(second, ScalarType):
        return first.c_name == second.c_name
    if isinstance(first, EnumRef) and isinstance(second, EnumRef):
        return first.usr == second.usr
    if isinstance(first, StructRef) and isinstance(second, StructRef):
        return first.usr == second.usr
    if isinstance(first, ContainerType) and isinstance(second, ContainerType):
        return (
            _same_type(first.element, second.element)
            and (first.kind is ContainerKind.ARRAY) == (second.kind is ContainerKind.ARRAY)
            and first.length == second.length
        )
    return False


def _callable(
    target: str, index: _Index, variables: Mapping[str, ClassPlan | None]
) -> tuple[CallablePlan, ClassPlan | None] | str:
    """The callable a ``call`` names, and the class of the object it returns."""
    parts = target.split(".")
    found: CallablePlan | None = None
    if len(parts) == 2 and parts[0] in variables:
        owner = variables[parts[0]]
        if owner is None:
            return f"{parts[0]!r} is not an object of a wrapped class"
        for ancestor in index.methods(owner):
            found = _member(ancestor, parts[1], static=False)
            if found is not None:
                break
    elif len(parts) == 2:
        found = index.functions.get((parts[0], parts[1]))
    elif len(parts) == 3:
        cls = index.classes.get((parts[0], parts[1]))
        found = _member(cls, parts[2], static=True) if cls is not None else None
    if found is None:
        return f"no callable {target!r}"
    return found, _result_class(found, index)


def _member(cls: ClassPlan, name: str, *, static: bool) -> CallablePlan | None:
    wanted = CallableKind.STATIC if static else CallableKind.METHOD
    for member in cls.members:
        if member.kind is wanted and member.name + member.suffix == name:
            return member
    return None


def _result_class(call: CallablePlan, index: _Index) -> ClassPlan | None:
    if call.result is None or call.result.cls is None:
        return None
    return index.by_usr.get(call.result.cls.model.usr)


def _check_value(
    value: object, index: _Index, variables: Mapping[str, ClassPlan | None]
) -> list[str]:
    if isinstance(value, list):
        return [problem for item in value for problem in _check_value(item, index, variables)]
    if not isinstance(value, dict):
        return []
    kinds = _VALUE_TABLES & set(value)
    if len(kinds) != 1:
        return [f"a table value needs exactly one of {sorted(_VALUE_TABLES)}: {value!r}"]
    (kind,) = kinds
    if kind == "ref":
        return [] if value["ref"] in variables else [f"no variable {value['ref']!r}"]
    if kind == "enum":
        enum = index.enums.get(str(value["enum"]))
        if enum is None:
            return [f"no enum {value['enum']!r}"]
        if "member" in value and value["member"] not in {e.model_name for e in enum.enumerators}:
            return [f"{value['enum']} has no enumerator {value['member']!r}"]
        return []
    if kind == "struct":
        struct = index.structs.get(str(value["struct"]))
        if struct is None:
            return [f"no struct {value['struct']!r}"]
        declared = [field.model.name for field in struct.fields]
        given = [name for name in value if name != "struct"]
        if given != declared:
            return [f"{value['struct']} needs all its members, in this order: {declared}"]
        return [
            problem for name in given for problem in _check_value(value[name], index, variables)
        ]
    return []
