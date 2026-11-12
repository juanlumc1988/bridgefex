# SPDX-License-Identifier: Apache-2.0
"""The JVM backend: Java bindings over the foreign function API (Java 22 or later), which
Kotlin uses too. One project per module, a package per header."""

from __future__ import annotations

from pathlib import Path

from ...errors import Diagnostic, GenerationError
from ...plan import ClassPlan, EnumPlan, FunctionPlan, ModulePlan, Reservations, StructPlan
from ...rendering import Renderer
from ..base import Backend
from .check import check
from .classes import class_source
from .mapping import Unsupported
from .module import build_names, functions_source, header_package, native_source, type_source
from .naming import file_problem, locals_of, module_problem, package_word, pascal

_TEMPLATES = Path(__file__).parent / "templates"


def render(plan: ModulePlan, fingerprint: str) -> dict[str, str]:
    """The project ``jvm/<module>``: the runtime, the native declarations and a package for
    each header."""
    reasons = []
    problem = module_problem(plan.name)
    if problem:
        reasons.append(f"Java: cannot name the module '{plan.name}': {problem}")
    seen: dict[str, str] = {}
    for header in plan.headers:
        word = package_word(header.stem)
        problem = file_problem(word, "package")
        if problem:
            reasons.append(f"Java: cannot name the package of '{header.stem}.h': {problem}")
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
        "BridgefexHandle",
        "BridgefexLease",
        "BridgefexKeep",
        "BridgefexObject",
    ):
        renderer.put(f"{java}/{name}.java", f"{name}.java.j2", **context)
    by_type = {
        item.c_type: item
        for header in plan.headers
        for item in header.items
        if isinstance(item, ClassPlan)
    }
    try:
        files[f"{java}/BridgefexNative.java"] = native_source(plan, names)
        for header in plan.headers:
            package = header_package(plan, header)
            package_dir = package.replace(".", "/")
            for item in header.items:
                if isinstance(item, ClassPlan):
                    cls = pascal(item.model.name)
                    path = f"{base}/src/main/java/{package_dir}/{cls}.java"
                    files[path] = class_source(plan, header, item, names, by_type)
                elif isinstance(item, EnumPlan | StructPlan):
                    cls = pascal(item.model.name)
                    path = f"{base}/src/main/java/{package_dir}/{cls}.java"
                    files[path] = type_source(plan, header, item, names)
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
