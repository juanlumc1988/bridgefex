# SPDX-License-Identifier: Apache-2.0
"""What a language backend is."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..errors import Diagnostic
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

    check: Callable[[ModulePlan, Mapping[str, str], Path], list[Diagnostic]] | None = None
    """``check(plan, files, scratch)``: verifies the files of this backend before they
    are written (compiles them, imports them...), writing what it needs below the
    scratch directory. Returns the errors found."""
