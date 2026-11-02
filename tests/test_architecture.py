# SPDX-License-Identifier: Apache-2.0
"""The layers stay apart: the plan and the C layer know no language, and a backend
only reads the plan.

These tests read the source, not the behavior, so that a backend cannot grow into
the neutral code (or into another backend) without a test failing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import bridgefex
from bridgefex.backends import BACKENDS

SOURCE = Path(bridgefex.__file__).parent

# Modules that assemble the pipeline: they are the only ones that know the backends.
_PIPELINE = {"__init__", "__main__", "api", "cli", "generator", "verify"}

# What a backend may use of the rest of the package. The pipeline (generator, api,
# verify, parser, libclang, cli) calls the backends, never the other way round.
_BACKEND_MAY_IMPORT = {
    "bridgefex.errors",
    "bridgefex.model",
    "bridgefex.naming",
    "bridgefex.plan",
    "bridgefex.rendering",
    "bridgefex.typemap",
}

# Words (of identifiers, strings and comments) that name a target language.
_LANGUAGE_WORDS = frozenset(
    "python ctypes py jni ffm java kotlin csharp pinvoke rust swift cython".split()
)
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


def _words(text: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text)}


def _imports(path: Path, package: str) -> set[str]:
    """The absolute names of the modules that ``path`` imports (a ``from . import x``
    counts as the module ``x`` of the package)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            names.add(module)
            if node.module is None:
                names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def _is_inside(name: str, package: str) -> bool:
    return name == package or name.startswith(f"{package}.")


def _neutral_modules() -> list[Path]:
    return sorted(path for path in SOURCE.glob("*.py") if path.stem not in _PIPELINE)


def _backend_modules() -> list[Path]:
    """The modules of the backends (not the registry and the base they share)."""
    return sorted(
        path for path in (SOURCE / "backends").rglob("*.py") if path.parent != SOURCE / "backends"
    )


def _package_of(path: Path) -> str:
    """The package of a module (the package itself for an ``__init__.py``)."""
    return ".".join(path.relative_to(SOURCE.parent).parent.parts)


@pytest.mark.parametrize("path", _neutral_modules(), ids=lambda path: path.name)
def test_neutral_modules_do_not_import_the_backends(path: Path) -> None:
    imported = _imports(path, _package_of(path))
    assert not [name for name in imported if _is_inside(name, "bridgefex.backends")]


# They talk about the Python that runs bridgefex (the libclang bindings, the TOML
# reader), not about a language of the bindings.
_ABOUT_THE_TOOL = {"libclang", "annotations"}


@pytest.mark.parametrize(
    "path",
    [path for path in _neutral_modules() if path.stem not in _ABOUT_THE_TOOL],
    ids=lambda path: path.name,
)
def test_the_neutral_code_knows_no_language(path: Path) -> None:
    assert _words(path.read_text(encoding="utf-8")) & _LANGUAGE_WORDS == set()


@pytest.mark.parametrize(
    "path", sorted((SOURCE / "templates").glob("*.j2")), ids=lambda path: path.name
)
def test_the_templates_of_the_c_layer_know_no_language(path: Path) -> None:
    assert _words(path.read_text(encoding="utf-8")) & _LANGUAGE_WORDS == set()


@pytest.mark.parametrize("path", _backend_modules(), ids=lambda path: str(path.relative_to(SOURCE)))
def test_a_backend_only_reads_the_plan(path: Path) -> None:
    own = f"bridgefex.backends.{path.relative_to(SOURCE / 'backends').parts[0]}"
    for name in sorted(_imports(path, _package_of(path))):
        if name.startswith("bridgefex") and name != "bridgefex":
            assert (
                _is_inside(name, own)
                or name == "bridgefex.backends.base"
                or name in _BACKEND_MAY_IMPORT
            ), f"{path.relative_to(SOURCE)} imports {name}"


def test_every_backend_package_is_registered_under_its_directory_name() -> None:
    directories = sorted(
        path.name
        for path in (SOURCE / "backends").iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    )
    assert sorted(backend.name for backend in BACKENDS) == directories
