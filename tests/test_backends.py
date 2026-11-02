# SPDX-License-Identifier: Apache-2.0
"""The language backends: what they are and what each of them generates."""

from __future__ import annotations

import pytest

import bridgefex
from bridgefex.backends import BACKENDS, registry
from bridgefex.generator import render
from bridgefex.model import Module
from bridgefex.plan import build_plan


def test_every_backend_is_registered_by_its_language() -> None:
    assert [backend.name for backend in BACKENDS] == ["python"]
    assert tuple(registry()) == bridgefex.LANGUAGES == ("python",)


def test_the_c_layer_does_not_depend_on_the_languages() -> None:
    plan = build_plan(Module("m", ()))
    only_c = render(plan, ())
    with_python = render(plan)
    assert {path.split("/")[0] for path in only_c} == {"c"}
    assert {path for path in with_python if path.startswith("c/")} == set(only_c)
    assert all(with_python[path] == only_c[path] for path in only_c)


def test_a_backend_only_writes_below_its_language() -> None:
    plan = build_plan(Module("m", ()))
    backend = registry()["python"]
    files = backend.render(plan, "0" * 32)
    assert files
    assert all(path.startswith("python/m/") for path in files)


def test_an_unknown_language_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown languages: csharp"):
        render(build_plan(Module("m", ())), ("csharp",))
