# SPDX-License-Identifier: Apache-2.0
"""The layers stay apart: the plan and the C layer know no language, and a backend
only reads the plan.

These tests read the source, not the behavior, so that a backend cannot grow into the
neutral code (or into another backend) without a test failing. They look for the ways
that knowledge of a language has leaked before: an import of a backend, an import of
what only Python has (``keyword``, ``ctypes``), a word that names a language, and a
dynamic import that would hide any of them. The last tests try the checks on sources
that break the rules, so that a check that stops seeing is noticed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import bridgefex
from bridgefex.backends import BACKENDS

SOURCE = Path(bridgefex.__file__).parent
BACKENDS_DIR = SOURCE / "backends"

# Modules that assemble the pipeline: they are the only ones that call the backends.
_PIPELINE = {"__init__", "__main__", "api", "cli", "generator", "verify"}

# Of these, the ones whose text is for the user (the help of the command, the docstring of
# the API) may name the languages; the rest of the pipeline may not.
_USER_FACING = {"__init__", "__main__", "api", "cli"}

# What a backend may use of the rest of the package. The pipeline (generator, api,
# verify, parser, libclang, cli) calls the backends, never the other way round.
_BACKEND_MAY_IMPORT = {
    "bridgefex.backends.base",
    "bridgefex.errors",
    "bridgefex.model",
    "bridgefex.naming",
    "bridgefex.plan",
    "bridgefex.rendering",
    "bridgefex.typemap",
}

# The shared base of the backends depends on the plan and nothing else.
_BASE_MAY_IMPORT = {"bridgefex.errors", "bridgefex.plan"}

# What only some languages have: neutral code must not need it.
_LANGUAGE_MODULES = {"keyword", "ctypes"}

# These two talk about the Python that runs bridgefex (the libclang bindings, the TOML
# reader), not about a language of the bindings. What each may say is listed.
_ABOUT_THE_TOOL = {"libclang": {"python", "ctypes"}, "annotations": {"python"}}

# Words that name a language. A strong one is found inside an identifier too (``PythonName``,
# ``ctypes_name``); a weak one only as a whole token, because it is also a word of English
# or a part of another (``JavaScript``).
_STRONG = frozenset(
    "python ctypes jni kotlin csharp pinvoke cython pybind kwlist iskeyword".split()
)
_WEAK = frozenset("java rust swift ffm".split())
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
_PY = re.compile(r"\bpy_[a-z]|\b[a-z0-9_]*_py\b|\bPy[A-Z]|\bstdlib_module_names\b")


def _language_words(text: str) -> set[str]:
    found: set[str] = set()
    for token in _TOKEN.findall(text):
        found |= {piece for piece in token.lower().split("_") if piece in _STRONG | _WEAK}
        found |= {piece.lower() for piece in _CAMEL.findall(token) if piece.lower() in _STRONG}
    if _PY.search(text):
        found.add("py")
    return found


def _is_module(name: str) -> bool:
    path = SOURCE.parent / name.replace(".", "/")
    return path.with_suffix(".py").exists() or (path / "__init__.py").exists()


def _imports(source: str, package: str) -> set[str]:
    """The absolute names of the modules that ``source`` imports, wherever it does it
    (``from . import x`` and ``from bridgefex import x`` import the module ``x``)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
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
            names.update(
                f"{module}.{alias.name}"
                for alias in node.names
                if _is_module(f"{module}.{alias.name}")
            )
    return names


def _dynamic_imports(source: str) -> list[str]:
    """The calls that import a module by a name that is not in the source."""
    calls = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else getattr(function, "id", "")
            )
            if name in {"import_module", "__import__"}:
                calls.append(name)
    return calls


def _is_inside(name: str, package: str) -> bool:
    return name == package or name.startswith(f"{package}.")


def _package_of(path: Path) -> str:
    """The package of a module (the package itself for an ``__init__.py``)."""
    return ".".join(path.relative_to(SOURCE.parent).parent.parts)


def _name(path: Path) -> str:
    return path.relative_to(SOURCE).as_posix()


def _sources() -> list[Path]:
    return sorted(path for path in SOURCE.rglob("*.py") if "__pycache__" not in path.parts)


def _neutral() -> list[Path]:
    """Every module that is neither the pipeline nor a backend (sub-packages too)."""
    return [
        path
        for path in _sources()
        if BACKENDS_DIR not in path.parents
        and not (path.parent == SOURCE and path.stem in _PIPELINE)
    ]


def _must_know_no_language() -> list[Path]:
    """The neutral code and the internals of the pipeline."""
    return [
        path
        for path in _sources()
        if BACKENDS_DIR not in path.parents or path == BACKENDS_DIR / "base.py"
        if not (path.parent == SOURCE and path.stem in _USER_FACING)
    ]


def _backend_modules() -> list[Path]:
    """The modules inside the packages of the backends."""
    return [
        path for path in _sources() if BACKENDS_DIR in path.parents and path.parent != BACKENDS_DIR
    ]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# -- the neutral code ---------------------------------------------------------------------


@pytest.mark.parametrize("path", _neutral(), ids=_name)
def test_neutral_modules_do_not_import_the_backends(path: Path) -> None:
    imported = _imports(_text(path), _package_of(path))
    assert not [name for name in imported if _is_inside(name, "bridgefex.backends")]


@pytest.mark.parametrize("path", _must_know_no_language(), ids=_name)
def test_the_neutral_code_knows_no_language(path: Path) -> None:
    allowed = _ABOUT_THE_TOOL.get(path.stem, set()) if path.parent == SOURCE else set()
    assert _language_words(_text(path)) - allowed == set()


@pytest.mark.parametrize("path", _must_know_no_language(), ids=_name)
def test_the_neutral_code_does_not_need_what_only_some_languages_have(path: Path) -> None:
    allowed = {"ctypes"} if path.parent == SOURCE and path.stem == "libclang" else set()
    imported = {name.split(".")[0] for name in _imports(_text(path), _package_of(path))}
    assert (imported & _LANGUAGE_MODULES) - allowed == set()


@pytest.mark.parametrize("path", [*_neutral(), *_backend_modules()], ids=_name)
def test_no_import_hides_behind_a_name(path: Path) -> None:
    assert _dynamic_imports(_text(path)) == []


@pytest.mark.parametrize("path", sorted((SOURCE / "templates").rglob("*.j2")), ids=_name)
def test_the_templates_of_the_c_layer_know_no_language(path: Path) -> None:
    assert _language_words(_text(path)) == set()


# -- the backends -------------------------------------------------------------------------


@pytest.mark.parametrize("path", _backend_modules(), ids=_name)
def test_a_backend_only_reads_the_plan(path: Path) -> None:
    own = f"bridgefex.backends.{path.relative_to(BACKENDS_DIR).parts[0]}"
    for name in sorted(_imports(_text(path), _package_of(path))):
        if name.startswith("bridgefex") and name != "bridgefex":
            assert _is_inside(name, own) or name in _BACKEND_MAY_IMPORT, (
                f"{_name(path)} imports {name}"
            )


def test_the_base_of_the_backends_depends_on_the_plan_only() -> None:
    path = BACKENDS_DIR / "base.py"
    for name in _imports(_text(path), _package_of(path)):
        if name.startswith("bridgefex") and name != "bridgefex":
            assert name in _BASE_MAY_IMPORT, f"base.py imports {name}"


# A package may have more than one backend: the same bindings over another way of calling C.
_FLAVORS = {"jvm": {"jni"}}


def test_every_backend_package_is_registered_under_its_directory_name() -> None:
    directories = sorted(
        path.name
        for path in BACKENDS_DIR.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    )
    flavors = {name for names in _FLAVORS.values() for name in names}
    assert (
        sorted(backend.name for backend in BACKENDS if backend.name not in flavors) == directories
    )
    # Each flavor belongs to a package that is a backend itself.
    assert all(package in directories for package in _FLAVORS)


# -- the checks themselves: each must see what it is for ----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Python",
        "PythonName",
        "the ctypes module",
        "ctypes_name",
        "class PyObject",
        "py_name = 1",
        "the_py",
        "sys.stdlib_module_names",
        "keyword.kwlist",
        "rust, java and swift",
        "using JNI",
    ],
)
def test_the_word_check_sees_a_language(text: str) -> None:
    assert _language_words(text) != set()


@pytest.mark.parametrize(
    "text",
    [
        "JavaScript",
        "x, py = point",
        "scripts/regen.py",
        "a trusted source and a rusty nail",
        "pythagoras and pybrid",
        "the keyword of C",
    ],
)
def test_the_word_check_does_not_see_what_is_not_a_language(text: str) -> None:
    assert _language_words(text) == set()


@pytest.mark.parametrize(
    "source",
    [
        "import bridgefex.backends",
        "from bridgefex import backends",
        "from bridgefex.backends import python",
        "from . import backends",
        "from .backends import registry",
        "def f():\n    from bridgefex import backends",
        "if True:\n    import bridgefex.backends.python.lower",
    ],
)
def test_the_import_check_sees_the_backends(source: str) -> None:
    names = _imports(source, "bridgefex")
    assert [name for name in names if _is_inside(name, "bridgefex.backends")]


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("from bridgefex import generator", "bridgefex.generator"),
        ("from ... import generator", "bridgefex.generator"),
        ("import bridgefex.api", "bridgefex.api"),
        ("from ..other import x", "bridgefex.backends.other"),
        ("import keyword", "keyword"),
    ],
)
def test_the_import_check_sees_what_a_backend_must_not_use(source: str, name: str) -> None:
    assert name in _imports(source, "bridgefex.backends.python")


def test_the_import_check_does_not_take_a_name_for_a_module() -> None:
    names = _imports("from bridgefex.plan import ModulePlan", "bridgefex")
    assert names == {"bridgefex.plan"}


@pytest.mark.parametrize(
    "source",
    [
        'importlib.import_module("bridgefex." + "backends")',
        "from importlib import import_module\nimport_module(name)",
        '__import__("keyword")',
    ],
)
def test_the_dynamic_import_check_sees_them(source: str) -> None:
    assert _dynamic_imports(source)


def test_the_modules_that_are_checked_are_the_ones_there_are() -> None:
    names = {_name(path) for path in _neutral()}
    assert {"plan.py", "model.py", "naming.py", "parser.py", "rendering.py"} <= names
    assert not {"generator.py", "api.py", "verify.py"} & names
    assert all("backends" not in path.parts for path in _neutral())
    assert {_name(path) for path in _backend_modules()} >= {"backends/python/lower.py"}
    assert "templates/runtime.h.j2" in {_name(p) for p in (SOURCE / "templates").rglob("*.j2")}
