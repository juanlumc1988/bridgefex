# SPDX-License-Identifier: Apache-2.0
"""Renders a binding plan into files."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .backends import registry
from .plan import ModulePlan, api_fingerprint
from .rendering import Renderer, write_files

__all__ = ["LANGUAGES", "render", "write_files"]

LANGUAGES = tuple(registry())
"""Languages that bindings can be generated for, on top of the C layer."""

_TEMPLATES = Path(__file__).parent / "templates"


def render(plan: ModulePlan, languages: Sequence[str] = LANGUAGES) -> dict[str, str]:
    """Generate every file of a module.

    Returns a mapping from relative POSIX paths (``c/...`` and
    ``<language>/...``) to file contents. The result depends only on the plan,
    so it is the same on every platform.
    """
    backends = registry()
    unknown = sorted(set(languages) - set(backends))
    if unknown:
        raise ValueError(f"unknown languages: {', '.join(unknown)}")
    files: dict[str, str] = {}
    renderer = Renderer(_TEMPLATES, plan, files)
    put = renderer.put

    # The public headers first: the fingerprint is their hash.
    put(f"c/{plan.runtime_header}", "runtime.h.j2")
    put(f"c/{plan.types_header}", "types.h.j2")
    for header in plan.headers:
        put(f"c/{header.c_header}", "header_c.h.j2", h=header)
    fingerprint = api_fingerprint(files[f"c/{name}"] for name in plan.public_headers)

    put(f"c/{plan.runtime_internal_header}", "runtime_internal.hpp.j2")
    put(f"c/{plan.runtime_source}", "runtime.cpp.j2", fingerprint=fingerprint)
    for header in plan.headers:
        put(f"c/{header.c_source}", "source_c.cpp.j2", h=header)
        if header.structs:
            put(f"c/{header.c_internal_header}", "source_c_internal.hpp.j2", h=header)

    for language in languages:
        backend = backends[language]
        reserved = plan.reservations
        if (backend.reservations.words - reserved.words) or (
            backend.reservations.parameter_words - reserved.parameter_words
        ):
            raise ValueError(
                f"the plan was built without the reservations of the {language} backend: "
                "pass bridgefex.backends.reservations() to build_plan"
            )
        produced = backend.render(plan, fingerprint)
        stray = sorted(path for path in produced if not path.startswith(f"{language}/"))
        if stray:
            raise ValueError(f"the {language} backend wrote outside of {language}/: {stray}")
        files.update(produced)
    return files
