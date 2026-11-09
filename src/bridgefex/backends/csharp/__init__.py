# SPDX-License-Identifier: Apache-2.0
"""The C# backend: P/Invoke bindings, one project per module."""

from __future__ import annotations

from pathlib import Path

from ...errors import Diagnostic, GenerationError
from ...plan import ModulePlan, Reservations
from ...rendering import Renderer
from ..base import Backend
from .check import check
from .lower import Unsupported
from .module import build_names, header_source, namespace, native_source
from .naming import header_problem, locals_of, module_problem, pascal

_TEMPLATES = Path(__file__).parent / "templates"


def render(plan: ModulePlan, fingerprint: str) -> dict[str, str]:
    """The project ``csharp/<Module>``: the runtime, the P/Invoke declarations and a file
    for each header."""
    root = namespace(plan)
    names = build_names(plan)
    files: dict[str, str] = {}
    renderer = Renderer(_TEMPLATES, plan, files)
    base = f"csharp/{root}"
    context = {
        "ns": root,
        "library": plan.name,
        "env": f"{plan.name.upper()}_LIBRARY",
        "fingerprint": fingerprint,
        "last_error": plan.last_error,
        "api_fingerprint": plan.api_fingerprint_function,
    }
    renderer.put(f"{base}/{root}.csproj", "Project.csproj.j2", **context)
    renderer.put(f"{base}/Runtime.cs", "Runtime.cs.j2", **context)
    renderer.put(f"{base}/Objects.cs", "Objects.cs.j2", **context)
    try:
        files[f"{base}/Native.cs"] = native_source(plan, names)
        for header in plan.headers:
            files[f"{base}/{pascal(header.stem)}.cs"] = header_source(plan, header, names)
    except Unsupported as error:
        raise GenerationError([Diagnostic(f"C#: {error}")]) from error
    return files


BACKEND = Backend(
    "csharp",
    render,
    Reservations(
        module_problem=module_problem,
        header_problem=header_problem,
        locals=locals_of,
    ),
    check=check,
)
