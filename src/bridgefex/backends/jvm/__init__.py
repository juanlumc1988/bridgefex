# SPDX-License-Identifier: Apache-2.0
"""The JVM backend: Java bindings over the foreign function API (Java 22 or later), which
Kotlin uses too. One project per module, a package per header."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from ...errors import Diagnostic, GenerationError
from ...plan import (
    ClassPlan,
    EnumPlan,
    FunctionPlan,
    HeaderPlan,
    ModulePlan,
    Reservations,
    StructPlan,
)
from ...rendering import Renderer
from ..base import Backend
from .check import check
from .classes import class_source
from .lower import Names
from .mapping import Unsupported
from .module import build_names, functions_source, header_package, native_source, type_source
from .naming import file_problem, locals_of, module_problem, package_word, pascal

_TEMPLATES = Path(__file__).parent / "templates"


def _module_problems(plan: ModulePlan) -> list[Diagnostic]:
    """What Java cannot have in the names of the module and its headers."""
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
    return [Diagnostic(reason) for reason in reasons]


class _Sources:
    """The Java files of a module, and what could not be written: all of it is reported, not
    only the first."""

    def __init__(self, plan: ModulePlan, names: Names, files: dict[str, str], base: str) -> None:
        self.plan = plan
        self.names = names
        self.files = files
        self.base = f"{base}/src/main/java"
        self.by_type = {
            item.c_type: item
            for header in plan.headers
            for item in header.items
            if isinstance(item, ClassPlan)
        }
        self.problems: list[Diagnostic] = []
        self.refused = False

    def put(self, path: str, make: Callable[[], str]) -> None:
        try:
            self.files[path] = make()
        except Unsupported as error:
            self.refused = True
            self.problems.append(Diagnostic(f"Java: {error}", error.location))
        except GenerationError as error:
            self.refused = self.refused or any(
                d.message.startswith("Java:") for d in error.diagnostics
            )
            self.problems.extend(error.diagnostics)

    def header(self, header: HeaderPlan) -> None:
        plan, names = self.plan, self.names
        folder = f"{self.base}/{header_package(plan, header).replace('.', '/')}"
        for item in header.items:
            path = (
                f"{folder}/{pascal(item.model.name)}.java"
                if not isinstance(item, FunctionPlan)
                else ""
            )
            if isinstance(item, ClassPlan):
                self.put(path, partial(class_source, plan, header, item, names, self.by_type))
            elif isinstance(item, EnumPlan | StructPlan):
                self.put(path, partial(type_source, plan, header, item, names))
        functions = [item for item in header.items if isinstance(item, FunctionPlan)]
        if functions:
            self.put(
                f"{folder}/Functions.java",
                partial(functions_source, plan, header, functions, names),
            )

    def result(self) -> dict[str, str]:
        if self.refused:
            self.problems.append(
                Diagnostic(
                    "Java: the declarations above are not supported by the Java bindings yet; "
                    "the other languages do not have these limits (leave 'jvm' out of the "
                    "languages to generate them), or take the declarations out of the header"
                )
            )
        if self.problems:
            raise GenerationError(self.problems)
        return self.files


def render(plan: ModulePlan, fingerprint: str) -> dict[str, str]:
    """The project ``jvm/<module>``: the runtime, the native declarations and a package for
    each header."""
    problems = _module_problems(plan)
    if problems:
        raise GenerationError(problems)
    names = build_names(plan)
    root = names.root
    files: dict[str, str] = {}
    renderer = Renderer(_TEMPLATES, plan, files)
    base = f"jvm/{root}"
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
        renderer.put(f"{base}/src/main/java/{root}/{name}.java", f"{name}.java.j2", **context)
    sources = _Sources(plan, names, files, base)
    sources.put(
        f"{base}/src/main/java/{root}/BridgefexNative.java", partial(native_source, plan, names)
    )
    for header in plan.headers:
        sources.header(header)
    return sources.result()


__all__ = ["BACKEND", "pascal"]

BACKEND = Backend(
    "jvm",
    render,
    # Only the locals of the bindings are reserved in the C layer. A module or a header that
    # Java cannot have is an error of the Java generation, not of the other languages.
    Reservations(locals=locals_of),
    check=check,
)
