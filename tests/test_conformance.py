# SPDX-License-Identifier: Apache-2.0
"""The conformance suite: every backend plays the same scenarios on the same libraries.

``tests/golden/<case>/scenarios.toml`` tells, in terms of the C++ API, what the bindings
of any language must do (see ``tests/conformance/scenarios.py``). The scenarios are
checked against the neutral plan, so they stay true to the headers; each backend has a
runner that plays them with its own bindings of the library built from the case. What a
language does beyond that (its idioms, its failures) is tested apart, in the
``check_<language>`` program of the case.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from bridgefex.backends import registry
from tests.conformance import scenarios
from tests.support.build import build
from tests.support.cases import GOLDEN_DIR, Case, all_cases, load_case
from tests.support.plans import plan_of
from tests.support.toolchain import Toolchain

CASES = [case for case in all_cases() if case.scenarios.exists()]
RUNNER_DIR = Path(__file__).parent / "conformance"

pytestmark = pytest.mark.usefixtures("libclang_loaded")


def run_python(
    case: Case, library: Path, output: Path, path: Path
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(output / "python"),
        f"{case.module.upper()}_LIBRARY": str(library),
    }
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "dev",
            "-W",
            "error",
            str(RUNNER_DIR / "runner_python.py"),
            case.module,
            str(path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


RUNNERS: dict[str, Callable[[Case, Path, Path, Path], subprocess.CompletedProcess[str]]] = {
    "python": run_python,
}


def test_every_backend_has_a_runner() -> None:
    assert set(registry()) == set(RUNNERS)


def test_there_are_scenarios() -> None:
    cases = {"scalars", "demo", "strings", "enums", "structs", "containers", "inherit", "directors"}
    assert {case.name for case in CASES} >= cases
    for case in CASES:
        assert scenarios.load(case.scenarios), f"{case.name} has no scenario"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_scenarios_are_true_to_the_headers(case: Case) -> None:
    assert scenarios.check(scenarios.load(case.scenarios), plan_of(case)) == []


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def built(
    request: pytest.FixtureRequest, toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Case, Path, Path]:
    case: Case = request.param
    library, output = build(case, toolchain, tmp_path_factory.mktemp(f"conformance_{case.name}"))
    return case, library, output


@pytest.mark.parametrize("language", sorted(RUNNERS))
def test_backend_conforms(language: str, built: tuple[Case, Path, Path]) -> None:
    case, library, output = built
    completed = RUNNERS[language](case, library, output, case.scenarios)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "conformance OK" in completed.stdout
    assert f"({language})" in completed.stdout


# The runner must fail when the bindings do not do what a scenario says: a scenario
# that cannot fail proves nothing.
_WRONG = {
    "a value that differs": 'call = "numbers.twice", args = [21], expect = 43',
    "a type that differs": 'call = "numbers.twice", args = [21], expect = 42.0',
    "an int where a bool is expected": 'call = "numbers.echo_bool", args = [true], expect = 1',
    "no error where one is expected": 'call = "numbers.twice", args = [21], error = "exception"',
    "another kind of error": 'call = "numbers.echo_int8", args = [128], error = "exception"',
    "a message that is not there": (
        'call = "numbers.divide", args = [1, 0], error = "exception", message = "overflow"'
    ),
    "an error where none is expected": 'call = "numbers.divide", args = [1, 0], expect = 0',
    "a value that is not valid, as a closed object": (
        'call = "numbers.echo_float", args = [1e39], error = "closed"'
    ),
    "a range error where a length error is expected": (
        'call = "numbers.echo_int8", args = [128], error = "length"'
    ),
}


@pytest.fixture(scope="module")
def scalars(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Case, Path, Path]:
    case = load_case(GOLDEN_DIR / "scalars")
    library, output = build(case, toolchain, tmp_path_factory.mktemp("conformance_negative"))
    return case, library, output


@pytest.mark.parametrize("language", sorted(RUNNERS))
@pytest.mark.parametrize("wrong", sorted(_WRONG))
def test_a_scenario_that_is_not_true_fails(
    language: str, wrong: str, scalars: tuple[Case, Path, Path], tmp_path: Path
) -> None:
    case, library, output = scalars
    path = tmp_path / "wrong.toml"
    path.write_text(
        f'[[scenario]]\nname = "wrong"\nsteps = [{{ {_WRONG[wrong]} }}]\n', encoding="utf-8"
    )
    completed = RUNNERS[language](case, library, output, path)
    assert completed.returncode == 1, completed.stdout
    assert "FAILED wrong, step 1" in completed.stderr


@pytest.mark.parametrize("language", sorted(RUNNERS))
def test_a_scenario_that_is_true_passes(
    language: str, scalars: tuple[Case, Path, Path], tmp_path: Path
) -> None:
    case, library, output = scalars
    path = tmp_path / "right.toml"
    step = 'call = "numbers.twice", args = [21], expect = 42'
    path.write_text(f'[[scenario]]\nname = "right"\nsteps = [{{ {step} }}]\n', encoding="utf-8")
    completed = RUNNERS[language](case, library, output, path)
    assert completed.returncode == 0, completed.stderr


# The check against the plan must refuse what is not true of the headers.
_UNTRUE = {
    "numbers.nothing": ('call = "numbers.nothing"', "no callable"),
    "other.twice": ('call = "other.twice", args = [1]', "no callable"),
    "arity": ('call = "numbers.twice", args = [1, 2]', "takes 1 arguments, not 2"),
    "variable": ('call = "acc.count"', "no callable"),
    "class": ('new = "numbers.Nothing"', "no class"),
    "static arity": ('call = "numbers.Accumulator.version", args = [1]', "takes 0 arguments"),
    "result and error": (
        'call = "numbers.twice", args = [1], expect = 2, error = "range"',
        "cannot",
    ),
    "error kind": ('call = "numbers.twice", args = [1], error = "boom"', "unknown error kind"),
    "message without exception": (
        'call = "numbers.twice", args = [1], error = "range", message = "x"',
        "message goes with an error of the C++ code",
    ),
    "overload on a single constructor": (
        'new = "numbers.Accumulator", overload = "_int32", as = "a"',
        "one constructor",
    ),
    "close of nothing": ('close = "acc"', "does not exist"),
    "reference": ('call = "numbers.twice", args = [{ ref = "acc" }]', "no variable"),
    "value table": ('call = "numbers.twice", args = [{ what = 1 }]', "needs exactly one"),
    "unknown key": ('call = "numbers.twice", args = [1], expected = 2', "unknown keys"),
    "two actions": ('call = "numbers.twice", close = "x"', "exactly one"),
}


@pytest.mark.parametrize("name", sorted(_UNTRUE))
def test_the_check_refuses_a_scenario_that_is_not_true_of_the_headers(
    name: str, tmp_path: Path
) -> None:
    step, expected = _UNTRUE[name]
    path = tmp_path / "scenarios.toml"
    path.write_text(f'[[scenario]]\nname = "x"\nsteps = [{{ {step} }}]\n', encoding="utf-8")
    problems = scenarios.check(scenarios.load(path), plan_of(load_case(GOLDEN_DIR / "scalars")))
    assert any(expected in problem for problem in problems), problems


# What is not true of the types in the headers, or of the objects: each is a list of
# steps, the case whose headers they refer to, and what the problem says.
_DERIVE = 'derive = "shape.Shape", named = "Mine", overrides = '
_UNTRUE_TYPES = {
    "a class without a director": (
        "directors",
        ['derive = "shape.Plain", named = "Mine", overrides = { value = { returns = 1 } }'],
        "has no director",
    ),
    "a method that is not virtual": (
        "directors",
        [_DERIVE + '{ area = { returns = 1.0 }, describe = { returns = "x" } }'],
        "no virtual method 'describe'",
    ),
    "a pure method that is not overridden": (
        "directors",
        [_DERIVE + '{ name = { returns = "x" } }'],
        "pure virtual methods ['area'] have to be overridden",
    ),
    "a base call of a pure method": (
        "directors",
        [_DERIVE + "{ area = { base = true } }"],
        "no implementation to call",
    ),
    "a value of another type": (
        "directors",
        [_DERIVE + '{ area = { returns = "wide" } }'],
        "the result",
    ),
    "a value of another enum": (
        "directors",
        [_DERIVE + "{ area = { returns = 1.0 }, mode = { returns = 3 } }"],
        "is not a member",
    ),
    "a method that returns nothing": (
        "directors",
        [_DERIVE + "{ area = { returns = 1.0 }, scale = { returns = 1 } }"],
        "returns nothing",
    ),
    "two behaviors": (
        "directors",
        [_DERIVE + "{ area = { returns = 1.0, base = true } }"],
        "does exactly one",
    ),
    "an argument that is not there": (
        "directors",
        [_DERIVE + "{ area = { argument = 0 } }"],
        "position of one of its 0 arguments",
    ),
    "a class that was not defined": (
        "directors",
        ['new = "Mine", overload = "_void", as = "m"'],
        "no class 'Mine'",
    ),
    "a constructor that the class does not show": (
        "directors",
        [
            _DERIVE + "{ area = { returns = 1.0 } }",
            'new = "Mine", overload = "_int32", as = "m"',
        ],
        "is not one of",
    ),
    "a result of a function that returns nothing": (
        "scalars",
        ['call = "numbers.do_nothing", expect = 1'],
        "returns nothing",
    ),
    "a string for an int": (
        "scalars",
        ['call = "numbers.twice", args = ["x"]'],
        "is not a int",
    ),
    "a float for an int": ("scalars", ['call = "numbers.twice", args = [1.5]'], "is not a int"),
    "a bool for an int": ("scalars", ['call = "numbers.twice", args = [true]'], "is not a int"),
    "an int for a bool": (
        "scalars",
        ['call = "numbers.echo_bool", args = [1]'],
        "is not a bool",
    ),
    "an int that is not digits": (
        "scalars",
        ['call = "numbers.echo_int64", args = [{ int = "abc" }]'],
        "is not a",
    ),
    "a string as the result of an int": (
        "scalars",
        ['call = "numbers.twice", args = [1], expect = "two"'],
        "the result",
    ),
    "an int as the result of a double": (
        "scalars",
        ['call = "numbers.echo_double", args = [1.0], expect = 1'],
        "the result",
    ),
    "a negative approx": (
        "scalars",
        ['call = "numbers.echo_double", args = [1.0], expect = 1.0, approx = -1.0'],
        "approx is a number",
    ),
    "approx for an int": (
        "scalars",
        ['call = "numbers.twice", args = [1], expect = 2, approx = 0.5'],
        "approx is a number",
    ),
    "a result of new": (
        "scalars",
        ['new = "numbers.Accumulator", expect = 1'],
        "no result to expect",
    ),
    "a use after close": (
        "scalars",
        [
            'new = "numbers.Accumulator", as = "acc"',
            'close = "acc"',
            'call = "acc.count", expect = 0',
        ],
        "was closed",
    ),
    "a closed object given to a call": (
        "inherit",
        [
            'new = "shapes.Circle", args = [1.0], as = "c"',
            'close = "c"',
            'call = "shapes.call_name", args = [{ ref = "c" }], expect = "x"',
        ],
        "was closed",
    ),
    "an object of another class": (
        "inherit",
        [
            'new = "widget.Counter", as = "counter"',
            'call = "shapes.call_name", args = [{ ref = "counter" }], expect = "x"',
        ],
        "is not a",
    ),
    "an enum of another type": (
        "enums",
        ['call = "colors.toggle", args = [{ enum = "colors.Color", member = "Red" }]'],
        "is not a member of",
    ),
    "an enum without a member": (
        "enums",
        ['call = "colors.toggle", args = [{ enum = "colors.Mode" }]'],
        "is not a member of",
    ),
    "a struct of another type": (
        "structs",
        [
            'call = "geo.area_geo_Point", args = '
            '[{ struct = "geo.Config", baud = 1, parity = true, kind = '
            '{ enum = "geo.Kind", member = "Plain" } }]'
        ],
        "is not a",
    ),
    "a number for a list": (
        "containers",
        ['call = "vectors.scale", args = [1, 2]'],
        "is not a list",
    ),
    "a struct with its members in another order": (
        "structs",
        ['call = "geo.area_geo_Point", args = [{ struct = "geo.Point", y = 1.0, x = 2.0 }]'],
        "needs all its members",
    ),
}


@pytest.mark.parametrize("name", sorted(_UNTRUE_TYPES))
def test_the_check_refuses_what_does_not_fit_the_types(name: str, tmp_path: Path) -> None:
    case, steps, expected = _UNTRUE_TYPES[name]
    body = ",\n  ".join("{ " + step + " }" for step in steps)
    path = tmp_path / "scenarios.toml"
    path.write_text(f'[[scenario]]\nname = "x"\nsteps = [\n  {body},\n]\n', encoding="utf-8")
    problems = scenarios.check(scenarios.load(path), plan_of(load_case(GOLDEN_DIR / case)))
    assert any(expected in problem for problem in problems), problems


@pytest.fixture(scope="module")
def directors(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Case, Path, Path]:
    case = load_case(GOLDEN_DIR / "directors")
    library, output = build(case, toolchain, tmp_path_factory.mktemp("conformance_directors"))
    return case, library, output


_UNTRUE_OVERRIDES = {
    "a value that differs": (
        '{ call = "s.describe", expect = "sq: 5 units" }',
        "FAILED wrong, step 3",
    ),
    "an override that does not fail": (
        '{ call = "s.describe", error = "override" }',
        "FAILED wrong, step 3",
    ),
    "the text of the failure": (
        '{ call = "s.describe", error = "override", message = "other" }',
        "FAILED wrong, step 3",
    ),
}


@pytest.mark.parametrize("language", sorted(RUNNERS))
@pytest.mark.parametrize("wrong", sorted(_UNTRUE_OVERRIDES))
def test_a_scenario_of_overrides_that_is_not_true_fails(
    language: str, wrong: str, directors: tuple[Case, Path, Path], tmp_path: Path
) -> None:
    case, library, output = directors
    step, failure = _UNTRUE_OVERRIDES[wrong]
    path = tmp_path / "wrong.toml"
    path.write_text(
        '[[scenario]]\nname = "wrong"\nsteps = [\n'
        '  { derive = "shape.Shape", named = "Sq", overrides = '
        '{ area = { returns = 4.0 }, name = { returns = "sq" } } },\n'
        '  { new = "Sq", overload = "_void", as = "s" },\n'
        f"  {step},\n]\n",
        encoding="utf-8",
    )
    completed = RUNNERS[language](case, library, output, path)
    assert completed.returncode == 1, completed.stdout
    assert failure in completed.stderr


def test_the_check_follows_the_objects_and_their_classes(tmp_path: Path) -> None:
    path = tmp_path / "scenarios.toml"
    steps = [
        'new = "counter.Counter", overload = "_void", as = "c"',
        'call = "c.add_int32", args = [1]',
        'call = "c.add_double", args = [1.5]',
        'call = "counter.Counter.liveInstances"',
        'new = "geometry.Circle", args = [1.0], as = "circle"',
        'call = "circle.scale", args = [2.0]',
        'close = "circle"',
    ]
    body = ",\n  ".join("{ " + step + " }" for step in steps)
    path.write_text(f'[[scenario]]\nname = "x"\nsteps = [\n  {body},\n]\n', encoding="utf-8")
    plan = plan_of(load_case(GOLDEN_DIR / "demo"))
    assert scenarios.check(scenarios.load(path), plan) == []
    path.write_text(
        '[[scenario]]\nname = "x"\nsteps = [\n'
        '  { new = "counter.Counter", as = "c" },\n'  # several constructors: the overload is needed
        '  { call = "c.radius" },\n'  # a method of another class
        "]\n",
        encoding="utf-8",
    )
    problems = scenarios.check(scenarios.load(path), plan)
    assert len(problems) == 2
    assert "is not one of" in problems[0]
    assert "no callable" in problems[1]
