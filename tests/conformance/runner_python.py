# SPDX-License-Identifier: Apache-2.0
"""Plays the conformance scenarios (see scenarios.py) against the Python bindings.

    python runner_python.py <module> <scenarios.toml>

PYTHONPATH has the generated package and <MODULE>_LIBRARY the built library. The runner
uses nothing but the generated package and the standard library, as a user would.
"""

from __future__ import annotations

import dataclasses
import importlib
import keyword
import math
import sys
import tomllib
import traceback
from types import ModuleType

_ERRORS = {"range": OverflowError, "closed": ValueError}
_STATUSES = {
    "exception": "ERROR_EXCEPTION",
    "unknown_exception": "ERROR_UNKNOWN_EXCEPTION",
    "out_of_memory": "ERROR_OUT_OF_MEMORY",
}
_NOTHING = object()


class Failure(Exception):
    pass


def name_of(name: str) -> str:
    """The Python name of a C++ one."""
    return f"{name}_" if keyword.iskeyword(name) else name


class Player:
    def __init__(self, package: str) -> None:
        self.package = importlib.import_module(package)
        self.error = self.package.Error
        self.name = package
        self.variables: dict[str, object] = {}

    def header(self, stem: str) -> ModuleType:
        return importlib.import_module(f"{self.name}.{stem}")

    def lookup(self, dotted: str) -> object:
        """``stem.Name`` or ``stem.Class.Name`` (a nested type)."""
        stem, *path = dotted.split(".")
        found: object = self.header(stem)
        for part in path:
            found = getattr(found, name_of(part))
        return found

    # -- values ---------------------------------------------------------------------

    def decode(self, value: object) -> object:
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "int" in value:
            return int(value["int"])
        if "null" in value:
            return None
        if "ref" in value:
            return self.variables[value["ref"]]
        if "enum" in value:
            return getattr(self.lookup(value["enum"]), name_of(value["member"]))
        return self.lookup(value["struct"])(*(self.decode(item) for item in self.members(value)))

    @staticmethod
    def members(value: dict[str, object]) -> list[object]:
        """The members of a struct value (all of them, in declaration order)."""
        return [item for name, item in value.items() if name != "struct"]

    def differences(
        self, actual: object, expected: object, approx: float, where: str
    ) -> str | None:
        """None if ``actual`` is ``expected`` (strictly typed), else what differs."""
        if isinstance(expected, dict) and "struct" in expected:
            cls = self.lookup(expected["struct"])
            if type(actual) is not cls:
                return f"{where}: {type(actual).__name__}, not {cls.__name__}"
            # The members of a dataclass are in declaration order, as in the scenario.
            for field, member in zip(dataclasses.fields(cls), self.members(expected), strict=True):
                problem = self.differences(
                    getattr(actual, field.name), member, approx, f"{where}.{field.name}"
                )
                if problem:
                    return problem
            return None
        if isinstance(expected, list):
            if type(actual) is not list or len(actual) != len(expected):
                return f"{where}: {actual!r}, not {expected!r}"
            for position, item in enumerate(expected):
                problem = self.differences(actual[position], item, approx, f"{where}[{position}]")
                if problem:
                    return problem
            return None
        wanted = self.decode(expected)
        if isinstance(wanted, float):
            same = type(actual) is float and (
                actual == wanted
                or (math.isnan(actual) and math.isnan(wanted))
                or (approx > 0 and abs(actual - wanted) <= approx)
            )
        elif wanted is None or isinstance(wanted, bool) or type(wanted) not in (int, str):
            same = actual is wanted  # None, bool and the members of an enum are singletons
        else:
            same = type(actual) is type(wanted) and actual == wanted
        return None if same else f"{where}: {actual!r} ({type(actual).__name__}), not {wanted!r}"

    # -- steps ----------------------------------------------------------------------

    def step(self, step: dict[str, object]) -> None:
        if "close" in step:
            self.variables[str(step["close"])].close()
            return
        arguments = [self.decode(item) for item in step.get("args", [])]
        if "new" in step:
            cls = self.lookup(str(step["new"]))
            overload = step.get("overload")
            make = getattr(cls, f"create{overload}") if overload else cls
            call = lambda: make(*arguments)  # noqa: E731
        else:
            call = self.callable(str(step["call"]), arguments)
        result = self.attempt(call, step)
        if "as" in step:
            self.variables[str(step["as"])] = result
        if "expect" in step:
            problem = self.differences(
                result, step["expect"], float(step.get("approx", 0)), "result"
            )
            if problem:
                raise Failure(problem)

    def callable(self, target: str, arguments: list[object]):
        parts = target.split(".")
        if len(parts) == 2 and parts[0] in self.variables:
            function = getattr(self.variables[parts[0]], name_of(parts[1]))
        else:
            function = self.lookup(target)
        return lambda: function(*arguments)

    def attempt(self, call, step: dict[str, object]) -> object:
        kind = step.get("error")
        try:
            result = call()
        except BaseException as error:
            if kind is None:
                raise
            wanted = _ERRORS.get(str(kind), self.error)
            if not isinstance(error, wanted) or (
                kind in _STATUSES and error.status != getattr(self.package, _STATUSES[str(kind)])
            ):
                raise Failure(f"{type(error).__name__} ({error}), not a {kind} error") from error
            if "message" in step and str(step["message"]) not in str(error):
                raise Failure(f"the message {str(error)!r} lacks {step['message']!r}") from error
            return _NOTHING
        if kind is not None:
            raise Failure(f"no {kind} error; the result is {result!r}")
        return result

    def finish(self) -> None:
        """Close what the scenario left open: its objects do not outlive it."""
        for item in reversed(list(self.variables.values())):
            close = getattr(item, "close", None)
            if callable(close):
                close()
        self.variables.clear()


def main(argv: list[str]) -> int:
    package, path = argv
    with open(path, "rb") as file:
        scenarios = tomllib.load(file).get("scenario", [])
    player = Player(package)
    failures = 0
    steps = 0
    for scenario in scenarios:
        for number, step in enumerate(scenario["steps"], start=1):
            steps += 1
            try:
                player.step(step)
            except Exception as error:
                failures += 1
                detail = error if isinstance(error, Failure) else traceback.format_exc()
                print(f"FAILED {scenario['name']}, step {number} {step}: {detail}", file=sys.stderr)
                break
        player.finish()
    if failures:
        return 1
    print(f"conformance OK: {len(scenarios)} scenarios, {steps} steps (python)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
