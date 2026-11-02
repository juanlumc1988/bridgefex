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
    assert {case.name for case in CASES} >= {"scalars"}


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
    assert len(problems) == 1, problems
    assert expected in problems[0]


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
