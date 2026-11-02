# SPDX-License-Identifier: Apache-2.0
"""Renders a binding plan into files."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from .backends import registry
from .plan import ModulePlan, api_fingerprint
from .rendering import Renderer

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
        files.update(backends[language].render(plan, fingerprint))
    return files


def write_files(files: Mapping[str, str], output: Path) -> list[Path]:
    """Write generated files below ``output``; existing files are overwritten.

    Files are written as UTF-8 with LF line endings on every platform.
    """
    written: list[Path] = []
    for relative, content in files.items():
        target = output.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
        written.append(target)
    return written
