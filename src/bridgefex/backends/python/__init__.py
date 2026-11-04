# SPDX-License-Identifier: Apache-2.0
"""The Python backend: ctypes bindings, one package per module."""

from __future__ import annotations

from pathlib import Path

from ...model import Class, Module
from ...plan import ModulePlan, Reservations
from ...rendering import Renderer
from ..base import Backend
from .check import check
from .lower import class_name
from .module import PyClass, PyEnum, PyFunction, PyHandle, PyStruct, lower
from .naming import (
    DIRECTOR_WORDS,
    KEYWORDS,
    PARAMETER_NAMES,
    header_problem,
    module_alias,
    module_problem,
)

_TEMPLATES = Path(__file__).parent / "templates"

# What the templates ask of an item (``item is py_class``).
_TESTS = {
    "py_class": lambda value: isinstance(value, PyClass),
    "py_enum": lambda value: isinstance(value, PyEnum),
    "py_function": lambda value: isinstance(value, PyFunction),
    "py_handle": lambda value: isinstance(value, PyHandle),
    "py_struct": lambda value: isinstance(value, PyStruct),
}


def render(plan: ModulePlan, fingerprint: str) -> dict[str, str]:
    """The package ``python/<module>``: the runtime, the shared types and a module
    for each header."""
    module = lower(plan)
    files: dict[str, str] = {}
    renderer = Renderer(_TEMPLATES, plan, files, _TESTS)
    package = f"python/{plan.name}"
    renderer.put(f"{package}/__init__.py", "python_init.py.j2", m=module)
    renderer.put(f"{package}/_runtime.py", "python_runtime.py.j2", fingerprint=fingerprint)
    renderer.put(f"{package}/_types.py", "python_types.py.j2", m=module)
    for header in module.headers:
        renderer.put(f"{package}/{header.stem}.py", "python_module.py.j2", h=header)
    return files


def _reserved_names(module: Module) -> frozenset[str]:
    """What the bindings use unqualified: the classes of the module (a parameter of
    the same name would hide them) and the aliases of the header modules."""
    classes = {
        class_name(declaration.name)
        for header in module.headers
        for declaration in header.declarations
        if isinstance(declaration, Class)
    }
    return frozenset(classes | {module_alias(header.stem) for header in module.headers})


def _reserved_locals(count: int) -> frozenset[str]:
    """The locals of a function that takes ``count`` parameters: the C function, and a
    handle for each argument (a call argument is one parameter at most three, plus
    self and the outputs)."""
    return frozenset({"_c"} | {f"_h{position}" for position in range(1, 3 * count + 5)})


BACKEND = Backend(
    "python",
    render,
    Reservations(
        words=KEYWORDS,
        parameter_words=PARAMETER_NAMES,
        director_words=DIRECTOR_WORDS,
        names=_reserved_names,
        locals=_reserved_locals,
        module_problem=module_problem,
        header_problem=header_problem,
    ),
    check=check,
)
