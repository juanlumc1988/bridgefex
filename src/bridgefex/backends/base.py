# SPDX-License-Identifier: Apache-2.0
"""What a language backend is."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..plan import NO_RESERVATIONS, ModulePlan, Reservations


@dataclass(frozen=True, slots=True)
class Backend:
    name: str
    """The language, as ``--lang`` and ``Options.languages`` name it."""

    render: Callable[[ModulePlan, str], dict[str, str]]
    """``render(plan, fingerprint)``: the files of the bindings, by relative POSIX
    path below the output directory (``<language>/...``)."""

    reservations: Reservations = NO_RESERVATIONS
    """The names that the C API leaves alone for the bindings."""
