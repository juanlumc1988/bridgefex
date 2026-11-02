# SPDX-License-Identifier: Apache-2.0
"""Jinja environments and the writing of rendered files, shared by the C layer
and the backends."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

import jinja2

from .plan import ClassPlan, EnumPlan, ModulePlan, StructPlan


def _is_class_plan(value: object) -> bool:
    return isinstance(value, ClassPlan)


def _is_enum_plan(value: object) -> bool:
    return isinstance(value, EnumPlan)


def _is_struct_plan(value: object) -> bool:
    return isinstance(value, StructPlan)


def environment(
    directory: Path, tests: Mapping[str, Callable[[object], bool]] | None = None
) -> jinja2.Environment:
    """An environment for the templates of ``directory``, with the Jinja ``tests``
    (``item is name``) that a backend adds."""
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
    result.tests.update(tests or {})
    return result


def tidy(text: str) -> str:
    """Remove trailing whitespace and end the text with exactly one newline."""
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip("\n") + "\n"


class Renderer:
    """Renders the templates of one directory for a module, into ``files``."""

    def __init__(
        self,
        directory: Path,
        plan: ModulePlan,
        files: dict[str, str],
        tests: Mapping[str, Callable[[object], bool]] | None = None,
    ) -> None:
        self._environment = environment(directory, tests)
        self._plan = plan
        self.files = files

    def put(self, path: str, template: str, **context: object) -> None:
        """Render ``template`` into ``files[path]``; ``m`` is the plan unless the
        context says another module object."""
        context.setdefault("m", self._plan)
        self.files[path] = tidy(self._environment.get_template(template).render(**context))


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
