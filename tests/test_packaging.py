# SPDX-License-Identifier: Apache-2.0
"""What the wheel carries: the templates of every backend.

The generator reads its templates from the package. A template that the ``package-data`` of
pyproject.toml does not name is not in the wheel, and a normal (not editable) install fails on the
first use of the backend that needs it, which an editable install, the one that the tests and the
CI use, never shows.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import bridgefex

ROOT = Path(__file__).parent.parent
PACKAGE = Path(bridgefex.__file__).parent


def _declared() -> set[Path]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["bridgefex"]
    return {path for pattern in patterns for path in PACKAGE.glob(pattern) if path.is_file()}


def test_every_template_is_in_the_package_data() -> None:
    templates = {path for path in PACKAGE.rglob("*.j2")}
    assert templates
    missing = sorted(str(path.relative_to(PACKAGE)) for path in templates - _declared())
    assert not missing, f"not in the package-data of pyproject.toml, so not in the wheel: {missing}"


def test_the_templates_of_the_jni_flavor_are_among_them() -> None:
    declared = {path.relative_to(PACKAGE).as_posix() for path in _declared()}
    assert "backends/jvm/templates/jni/glue.cpp.j2" in declared
