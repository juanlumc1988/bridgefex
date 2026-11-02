# SPDX-License-Identifier: Apache-2.0
"""The Python backend: ctypes bindings, one package per module."""

from __future__ import annotations

from pathlib import Path

from ...plan import ModulePlan
from ...rendering import Renderer
from ..base import Backend

_TEMPLATES = Path(__file__).parent / "templates"


def render(plan: ModulePlan, fingerprint: str) -> dict[str, str]:
    """The package ``python/<module>``: the runtime, the shared types and a module
    for each header."""
    files: dict[str, str] = {}
    renderer = Renderer(_TEMPLATES, plan, files)
    package = f"python/{plan.name}"
    renderer.put(f"{package}/__init__.py", "python_init.py.j2")
    renderer.put(f"{package}/_runtime.py", "python_runtime.py.j2", fingerprint=fingerprint)
    renderer.put(f"{package}/_types.py", "python_types.py.j2")
    for header in plan.headers:
        renderer.put(f"{package}/{header.stem}.py", "python_module.py.j2", h=header)
    return files


BACKEND = Backend("python", render)
