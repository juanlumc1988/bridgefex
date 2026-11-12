# SPDX-License-Identifier: Apache-2.0
"""The JVM backend: Java bindings over the foreign function API (Java 22 or later), which
Kotlin uses too. One project per module, a package per header."""

from __future__ import annotations

from pathlib import Path

from ...errors import Diagnostic, GenerationError
from ...plan import FunctionPlan, ModulePlan, Reservations
from ...rendering import Renderer
from ..base import Backend
from .check import check
from .mapping import Unsupported
from .module import build_names, functions_source, header_package, native_source
from .naming import header_problem, locals_of, module_problem, package_word, pascal

_TEMPLATES = Path(__file__).parent / "templates"


def render(plan: ModulePlan, fingerprint: str) -> dict[str, str]:
    """The project ``jvm/<module>``: the runtime, the native declarations and a package for
    each header."""
    reasons = []
    problem = module_problem(plan.name)
    if problem:
        reasons.append(f"Java: cannot name the module '{plan.name}': {problem}")
    for header in plan.headers:
        problem = header_problem(header.stem)
        if problem:
            reasons.append(f"Java: cannot use header name '{header.stem}': {problem}")
    seen: dict[str, str] = {}
    for header in plan.headers:
        word = package_word(header.stem)
        other = seen.setdefault(word, header.stem)
        if other != header.stem:
            reasons.append(
                f"Java: the headers '{other}.h' and '{header.stem}.h' get the Java package '{word}'"
            )
    if reasons:
        raise GenerationError([Diagnostic(reason) for reason in reasons])
    names = build_names(plan)
    root = names.root
    files: dict[str, str] = {}
    renderer = Renderer(_TEMPLATES, plan, files)
    base = f"jvm/{root}"
    java = f"{base}/src/main/java/{root}"
    context = {
        "pkg": root,
        "library": plan.name,
        "fingerprint": fingerprint,
        "property": f"{plan.name}.library",
        "env": f"{plan.name.upper()}_LIBRARY",
        "api_fingerprint": plan.api_fingerprint_function,
    }
    renderer.put(f"{base}/pom.xml", "pom.xml.j2", **context)
    for name in (
        "BridgefexRuntime",
        "BridgefexException",
        "BridgefexFunction",
        "BridgefexUnsigned",
    ):
        renderer.put(f"{java}/{name}.java", f"{name}.java.j2", **context)
    try:
        files[f"{java}/BridgefexNative.java"] = native_source(plan, names)
        for header in plan.headers:
            package_dir = header_package(plan, header).replace(".", "/")
            functions = [item for item in header.items if isinstance(item, FunctionPlan)]
            if functions:
                files[f"{base}/src/main/java/{package_dir}/Functions.java"] = functions_source(
                    plan, header, functions, names
                )
    except Unsupported as error:
        raise GenerationError([Diagnostic(f"Java: {error}", error.location)]) from error
    return files


__all__ = ["BACKEND", "pascal"]

BACKEND = Backend(
    "jvm",
    render,
    # Only the locals of the bindings are reserved in the C layer. A module or a header that
    # Java cannot have is an error of the Java generation, not of the other languages.
    Reservations(locals=locals_of),
    check=check,
)
