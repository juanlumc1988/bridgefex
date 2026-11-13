# SPDX-License-Identifier: Apache-2.0
"""The JNI bindings on the Javas that the ones over the foreign function API cannot run on.

The bindings are Java 8, and run on what 32-bit ARM and Android have. These tests run the checks
of the bindings (written in Java 8, and compiled for it) on every older JDK that the machine has:
``BRIDGEFEX_JAVA_RUNTIMES`` lists their homes (separated as a path is), and without it the JDKs
of 8, 11 and 17 that ``/usr/lib/jvm`` or the variables ``JAVA_HOME_<n>_<arch>`` of the CI name are
used. The conformance scenarios run on those that can run the runner (Java 17 or later).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.conformance import runner_jvm
from tests.support.build import build
from tests.support.cases import Case
from tests.support.toolchain import Toolchain
from tests.test_jvm_roundtrip import CASES

pytestmark = pytest.mark.usefixtures("libclang_loaded")

ENV_RUNTIMES = "BRIDGEFEX_JAVA_RUNTIMES"


def _java_of(home: Path) -> Path | None:
    for name in ("java", "java.exe"):
        candidate = home / "bin" / name
        if candidate.exists():
            return candidate
    return None


def _major(java: Path) -> int | None:
    completed = subprocess.run(
        [str(java), "-version"], capture_output=True, text=True, check=False, timeout=120
    )
    match = re.search(r'version "(?:1\.)?(\d+)', completed.stdout + completed.stderr)
    return int(match.group(1)) if match else None


def runtimes() -> dict[str, Path]:
    """The ``java`` commands of the JDKs older than the one that compiles, by ``java<major>``."""
    homes = [Path(item) for item in os.environ.get(ENV_RUNTIMES, "").split(os.pathsep) if item]
    if not homes:
        for name, value in sorted(os.environ.items()):
            if re.fullmatch(r"JAVA_HOME_(8|11|17)_\w+", name):
                homes.append(Path(value))
        for number in (8, 11, 17):
            homes += sorted(Path("/usr/lib/jvm").glob(f"java-{number}-*"))
    found: dict[str, Path] = {}
    for home in homes:
        java = _java_of(home)
        major = _major(java) if java else None
        if java is not None and major is not None and major < 21:
            found.setdefault(f"java{major}", java)
    return found


RUNTIMES = runtimes()


def _on(names: list[str]) -> list[object]:
    """The runtimes to parametrize over: without any, a skipped test (an empty set of parameters is
    an error of the collection, which would stop a run that has no old JDK at all)."""
    return names or [pytest.param("none", marks=pytest.mark.skip(reason="no older JDK"))]


def _require() -> None:
    runner_jvm.skip_without_java()


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def built(
    request: pytest.FixtureRequest,
    toolchain: Toolchain,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Case, Path, Path]:
    _require()
    case: Case = request.param
    library, output = build(case, toolchain, tmp_path_factory.mktemp(f"other_{case.name}"))
    return case, library, output


def test_there_is_an_older_java_or_it_is_not_asked_for() -> None:
    if os.environ.get("BRIDGEFEX_REQUIRE_OLD_JAVA") == "1":
        assert "java8" in RUNTIMES, "a Java 8 is required and there is none"


@pytest.mark.parametrize("runtime", _on(sorted(RUNTIMES)))
def test_the_checks_of_the_bindings_run_on_an_older_java(
    built: tuple[Case, Path, Path], runtime: str
) -> None:
    case, library, output = built
    if "jni" not in case.languages and case.name not in {"lifetime", "wire"}:
        pytest.skip("the case has no JNI bindings")
    classes = runner_jvm.build_program(
        case,
        output,
        case.check_jni,
        "CheckOld",
        support=(runner_jvm.SUPPORT,),
        language="jni",
        release="8",
    )
    completed = runner_jvm.execute(case, classes, library, "Check", java=str(RUNTIMES[runtime]))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


@pytest.mark.parametrize("runtime", _on([name for name in sorted(RUNTIMES) if int(name[4:]) >= 17]))
def test_the_scenarios_run_on_an_older_java_that_can_run_the_runner(
    built: tuple[Case, Path, Path], runtime: str
) -> None:
    case, library, output = built
    if not case.scenarios.exists() or "jni" not in case.languages:
        pytest.skip("the case has no scenarios, or no JNI bindings")
    completed = runner_jvm.run(
        case,
        library,
        output,
        case.scenarios,
        "jni",
        java=str(RUNTIMES[runtime]),
        release=runtime[4:],
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "conformance OK" in completed.stdout
