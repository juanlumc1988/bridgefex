# SPDX-License-Identifier: Apache-2.0
"""Language backends: each turns the plan of a module into the bindings of one
language, on top of the C layer that the plan itself describes.

A backend only reads the plan. The C layer (headers, sources, runtime) is the
same for every language, so it is not a backend: :mod:`bridgefex.generator`
renders it first.
"""

from __future__ import annotations

from ..plan import Reservations
from . import python
from .base import Backend

__all__ = ["BACKENDS", "Backend", "registry", "reservations"]

BACKENDS: tuple[Backend, ...] = (python.BACKEND,)
"""Every backend, in the order their files are generated."""


def registry() -> dict[str, Backend]:
    """The backends by language."""
    return {backend.name: backend for backend in BACKENDS}


def reservations() -> Reservations:
    """The names that every backend needs left alone by the C API. Whichever
    languages are generated, they all count: the C API does not depend on them."""
    return Reservations.merge(backend.reservations for backend in BACKENDS)
