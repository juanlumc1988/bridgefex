# SPDX-License-Identifier: Apache-2.0
"""The C# backend: P/Invoke bindings, one project per module."""

from __future__ import annotations

from pathlib import Path

from ...errors import Diagnostic, GenerationError
from ...plan import ModulePlan, Reservations
from ...rendering import Renderer
from ..base import Backend
from .check import check
from .mapping import Unsupported
from .module import assembly_name, build_names, header_source, namespace, native_source
from .naming import header_problem, locals_of, module_problem, pascal

_TEMPLATES = Path(__file__).parent / "templates"


def render(plan: ModulePlan, fingerprint: str) -> dict[str, str]:
    """The project ``csharp/<Module>``: the runtime, the P/Invoke declarations and a file
    for each header."""
    problems = [module_problem(plan.name)] + [
        header_problem(header.stem) for header in plan.headers
    ]
    reasons = [
        f"C#: {what}: {problem}"
        for what, problem in zip(
            [f"cannot name the module '{plan.name}'"]
            + [f"cannot use header name '{header.stem}'" for header in plan.headers],
            problems,
            strict=True,
        )
        if problem
    ]
    # Headers whose files differ only by case (a_b.h and ab.h) are one file on Windows and macOS.
    seen: dict[str, str] = {}
    for header in plan.headers:
        word = pascal(header.stem)
        other = seen.setdefault(word.casefold(), header.stem)
        if other != header.stem:
            reasons.append(
                f"C#: the headers '{other}.h' and '{header.stem}.h' get the C# file "
                f"'{word}.cs', which only differs by case from the other one"
            )
    if reasons:
        raise GenerationError([Diagnostic(reason) for reason in reasons])
    root = namespace(plan)
    names = build_names(plan)
    files: dict[str, str] = {}
    renderer = Renderer(_TEMPLATES, plan, files)
    base = f"csharp/{root}"
    context = {
        "ns": root,
        "assembly": assembly_name(plan),
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
        raise GenerationError([Diagnostic(f"C#: {error}", error.location)]) from error
    return files


BACKEND = Backend(
    "csharp",
    render,
    # Only the locals of the bindings are reserved in the C layer. A module or a header that
    # C# cannot have is an error of the C# generation, not of the other languages.
    Reservations(locals=locals_of),
    check=check,
)
