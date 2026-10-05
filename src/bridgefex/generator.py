# SPDX-License-Identifier: Apache-2.0
"""Renders a binding plan into files."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

import jinja2

from .plan import ClassPlan, ModulePlan

LANGUAGES = ("python",)
"""Languages that bindings can be generated for, on top of the C layer."""

_TEMPLATES = Path(__file__).parent / "templates"


def _is_class_plan(value: object) -> bool:
    return isinstance(value, ClassPlan)


def _environment() -> jinja2.Environment:
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES)),
        # A typo in a template must fail loudly instead of rendering nothing.
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    environment.tests["class_plan"] = _is_class_plan
    return environment


def _tidy(text: str) -> str:
    """Remove trailing whitespace and end the text with exactly one newline."""
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


def render(plan: ModulePlan, languages: Sequence[str] = LANGUAGES) -> dict[str, str]:
    """Generate every file of a module.

    Returns a mapping from relative POSIX paths (``c/...`` and
    ``python/<module>/...``) to file contents. The result depends only on
    the plan, so it is the same on every platform.
    """
    unknown = sorted(set(languages) - set(LANGUAGES))
    if unknown:
        raise ValueError(f"unknown languages: {', '.join(unknown)}")
    environment = _environment()
    files: dict[str, str] = {}

    def put(path: str, template: str, **context: object) -> None:
        files[path] = _tidy(environment.get_template(template).render(m=plan, **context))

    put(f"c/{plan.runtime_header}", "runtime.h.j2")
    put(f"c/{plan.runtime_internal_header}", "runtime_internal.hpp.j2")
    put(f"c/{plan.runtime_source}", "runtime.cpp.j2")
    for header in plan.headers:
        put(f"c/{header.c_header}", "header_c.h.j2", h=header)
        put(f"c/{header.c_source}", "source_c.cpp.j2", h=header)

    if "python" in languages:
        package = f"python/{plan.name}"
        put(f"{package}/__init__.py", "python_init.py.j2")
        put(f"{package}/_runtime.py", "python_runtime.py.j2")
        for header in plan.headers:
            put(f"{package}/{header.stem}.py", "python_module.py.j2", h=header)
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
