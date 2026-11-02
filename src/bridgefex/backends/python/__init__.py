# SPDX-License-Identifier: Apache-2.0
"""The Python backend: ctypes bindings, one package per module."""

from __future__ import annotations

from pathlib import Path

from ...plan import ModulePlan
from ...rendering import Renderer
from ..base import Backend
from .module import PyClass, PyEnum, PyFunction, PyHandle, PyStruct, lower

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


BACKEND = Backend("python", render)
