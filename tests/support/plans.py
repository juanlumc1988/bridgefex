# SPDX-License-Identifier: Apache-2.0
"""The neutral plan of a golden case, as the generator builds it."""

from __future__ import annotations

from bridgefex import annotations
from bridgefex.api import include_spelling
from bridgefex.backends import reservations
from bridgefex.model import Module
from bridgefex.parser import ParseOptions, parse_header, system_names
from bridgefex.plan import ModulePlan, build_plan
from tests.support.cases import Case


def plan_of(case: Case) -> ModulePlan:
    """Parse the headers of ``case`` and plan them (libclang must be loaded)."""
    options = ParseOptions(std=case.parse_std)
    headers = tuple(
        parse_header(header, include_spelling(header, None), options).header
        for header in case.headers
    )
    module = Module(case.module, headers, system_names=system_names())
    return build_plan(module, annotations.load(case.annotations), reservations())
