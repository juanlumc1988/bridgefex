# SPDX-License-Identifier: Apache-2.0
"""Jinja environments and the writing of rendered files, shared by the C layer
and the backends."""

from __future__ import annotations

from pathlib import Path

import jinja2

from .plan import ClassPlan, EnumPlan, ModulePlan, StructPlan


def _is_class_plan(value: object) -> bool:
    return isinstance(value, ClassPlan)


def _is_enum_plan(value: object) -> bool:
    return isinstance(value, EnumPlan)


def _is_struct_plan(value: object) -> bool:
    return isinstance(value, StructPlan)


def environment(directory: Path) -> jinja2.Environment:
    """An environment for the templates of ``directory``."""
    result = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(directory)),
        # A typo in a template must fail loudly instead of rendering nothing.
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    result.tests["class_plan"] = _is_class_plan
    result.tests["enum_plan"] = _is_enum_plan
    result.tests["struct_plan"] = _is_struct_plan
    return result


def tidy(text: str) -> str:
    """Remove trailing whitespace and end the text with exactly one newline."""
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


class Renderer:
    """Renders the templates of one directory for a module, into ``files``."""

    def __init__(self, directory: Path, plan: ModulePlan, files: dict[str, str]) -> None:
        self._environment = environment(directory)
        self._plan = plan
        self.files = files

    def put(self, path: str, template: str, **context: object) -> None:
        text = self._environment.get_template(template).render(m=self._plan, **context)
        self.files[path] = tidy(text)
