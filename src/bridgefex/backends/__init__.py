# SPDX-License-Identifier: Apache-2.0
"""Language backends: each turns the plan of a module into the bindings of one
language, on top of the C layer that the plan itself describes.

A backend only reads the plan. The C layer (headers, sources, runtime) is the
same for every language, so it is not a backend: :mod:`bridgefex.generator`
renders it first.
"""

from __future__ import annotations

from ..plan import Reservations
from . import csharp, python
from .base import Backend

__all__ = ["BACKENDS", "DEFAULT_LANGUAGES", "Backend", "registry", "reservations"]

BACKENDS: tuple[Backend, ...] = (python.BACKEND, csharp.BACKEND)
"""Every backend, in the order their files are generated."""


DEFAULT_LANGUAGES: tuple[str, ...] = (python.BACKEND.name,)
"""The languages that are generated when none is asked for."""


def registry() -> dict[str, Backend]:
    """The backends by language."""
    return {backend.name: backend for backend in BACKENDS}


def reservations() -> Reservations:
    """The names that every backend needs left alone by the C API. Whichever
    languages are generated, they all count: the C API does not depend on them."""
    return Reservations.merge(backend.reservations for backend in BACKENDS)
