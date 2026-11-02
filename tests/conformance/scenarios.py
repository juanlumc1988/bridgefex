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
    ClassRef,
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
    EnumPlan,
    FunctionPlan,
    HeaderPlan,
    ModulePlan,
    StructPlan,
)

CXX_ERRORS = frozenset({"exception", "unknown_exception", "out_of_memory"})
"""The failures of the C++ code, by the status of the C API: a ``std::exception``, any
other exception, and ``std::bad_alloc``."""

ERROR_KINDS = CXX_ERRORS | {"range", "length", "closed"}
"""The failures that every backend reports, each in the way of its language."""

_ACTIONS = ("new", "call", "close")
_KEYS = frozenset({*_ACTIONS, "args", "as", "expect", "error", "message", "approx", "overload"})
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
        for number, step in enumerate(steps, start=1):
            where = f"{name}, step {number}"
            for problem in _check_step(step, index, variables, closed):
                problems.append(f"{where}: {problem}")
    return problems


def _check_step(
    step: Mapping[str, object],
    index: _Index,
    variables: dict[str, ClassPlan | None],
    closed: set[str],
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
    if action == "close":
        if target not in variables:
            problems.append(f"close of {target!r}, which does not exist")
        closed.add(target)
        return problems

    produced: ClassPlan | None = None
    if action == "new":
        cls = _class(target, index)
        if cls is None:
            return [*problems, f"no class {target!r}"]
        constructor, overload_problems = _constructor(cls, step.get("overload"))
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
    if "message" in step and step.get("error") not in CXX_ERRORS:
        problems.append(f"message goes with an error of the C++ code: {sorted(CXX_ERRORS)}")
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
        return None if isinstance(value, list) else f"{value!r} is not a list"
    return "callbacks are not in the scenarios yet"


def _class(target: str, index: _Index) -> ClassPlan | None:
    header, _, name = target.partition(".")
    return index.classes.get((header, name))


def _constructor(cls: ClassPlan, overload: object) -> tuple[CallablePlan | None, list[str]]:
    """The constructor that ``overload`` names (the only one if there is one)."""
    if len(cls.constructors) == 1:
        if overload is not None:
            return cls.constructors[0], ["the class has one constructor: no overload"]
        return cls.constructors[0], []
    matching = [item for item in cls.constructors if item.suffix == overload]
    if not matching:
        options = [item.suffix for item in cls.constructors]
        return None, [f"overload {overload!r} is not one of {options}"]
    return matching[0], []


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
