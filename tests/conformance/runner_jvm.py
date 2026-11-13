# SPDX-License-Identifier: Apache-2.0
"""Plays the conformance scenarios against the Java bindings.

The bindings are generated from the case and compiled together with the runner
(``runner_jvm/Runner.java``, which plays the scenarios by reflection), and the runner is run
with the library of the case. Scenarios are given as JSON: the JDK has no TOML reader, and each
``new`` and ``call`` says the C function it wraps, so that the runner finds what to call
without knowing how Java names things.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import bridgefex
from bridgefex.backends.jvm.check import java_runtime, javac
from bridgefex.backends.jvm.module import build_names
from bridgefex.backends.jvm.naming import upper_snake
from tests.conformance import scenarios
from tests.support.cases import Case
from tests.support.plans import plan_of

RUNNER = Path(__file__).parent / "runner_jvm" / "Runner.java"
SUPPORT = Path(__file__).parent.parent / "support" / "jvm" / "Checks.java"
"""What the check programs share: assertions and the garbage collector."""

ENV_REQUIRED = "BRIDGEFEX_REQUIRE_JAVA"
"""Set by the CI: a missing JDK is then a failure of the tests that need it, not a skip."""

ARGUMENTS = ("--enable-native-access=ALL-UNNAMED",)


def available() -> bool:
    return javac() is not None


def skip_without_java() -> None:
    """Skip the test if there is no JDK of Java 22 or later, or fail it if the environment
    requires one."""
    if available():
        return
    if os.environ.get(ENV_REQUIRED) == "1":
        pytest.fail(f"a JDK 22 or later is required ({ENV_REQUIRED} is set) and there is none")
    pytest.skip("needs a JDK of Java 22 or later")


def build_program(
    case: Case,
    output: Path,
    program: Path,
    name: str,
    *,
    support: tuple[Path, ...] = (),
    edit: Callable[[dict[str, str]], dict[str, str]] | None = None,
    language: str = "jvm",
    release: str | None = None,
) -> Path:
    """``program`` (a Java file with ``main``) compiled with the bindings of ``case`` (those of
    ``language``, ``jvm`` or ``jni``) and the files of ``support`` (``edit`` may change the
    generated files first): the directory of the classes. Raises RuntimeError if it does not
    compile. ``release`` is the Java that the program is written for, if it has to run on an
    older one than the JDK that compiles it (the JNI bindings run on Java 8)."""
    work = output / f"{language}-{name}"
    classes = work / "classes"
    if classes.exists():
        return classes
    options = dataclasses.replace(case.options((language,)), verify=False)
    result = bridgefex.generate(case.headers, options)
    generated = work / "generated"
    files = edit(dict(result.files)) if edit else result.files
    bridgefex.write_files(files, generated)
    sources = [str(generated / path) for path in files if path.endswith(".java")]
    command = javac()
    assert command is not None
    classes.mkdir(parents=True)
    # The bindings of the JNI flavor are Java 8, the programs that use them are not.
    extra = [*map(str, support), str(program)]
    if language == "jvm":
        steps = [([*sources, *extra], "22", "-Xlint:all")]
    else:
        steps = [
            (sources, "8", "-Xlint:all,-options"),
            (extra, release or "22", "-Xlint:all,-options"),
        ]
    for step, level, lint in steps:
        completed = subprocess.run(
            [
                command,
                "--release",
                level,
                lint,
                "-Werror",
                "-encoding",
                "UTF-8",
                "-cp",
                str(classes),
                "-d",
                str(classes),
                *step,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"{name} does not compile:\n" + completed.stdout + completed.stderr)
    return classes


def execute(
    case: Case,
    classes: Path,
    library: Path,
    main: str,
    *arguments: str,
    java: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``main`` of a compiled program with the library of ``case`` (with the ``java`` given,
    or that of the JDK that compiles)."""
    command = javac()
    assert command is not None
    return subprocess.run(
        # An older java does not know the flag of the foreign function API.
        [
            java or java_runtime(command),
            *(() if java else ARGUMENTS),
            "-cp",
            str(classes),
            main,
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, f"{case.module.upper()}_LIBRARY": str(library)},
        timeout=600,
    )


def _json_safe(value: object) -> object:
    """JSON has no NaN nor infinities: they become ``{ "float": "nan" }``."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _types(output: Path, name: str, language: str) -> list[str]:
    """The classes of the bindings, by the files that were generated."""
    generated = output / f"{language}-{name}" / "generated"
    roots = sorted(generated.glob(f"{language}/*/src/main/java"))
    result = []
    for root in roots:
        for path in sorted(root.rglob("*.java")):
            result.append(".".join(path.relative_to(root).with_suffix("").parts))
    return result


def _binary(name: str, nested: int) -> str:
    """The name that ``Class.forName`` wants: a nested class is separated by ``$``."""
    if nested == 0:
        return name
    head, _, last = name.rpartition(".")
    return f"{head}${last}"


def _map(case: Case, output: Path, language: str, name: str) -> dict[str, object]:
    plan = plan_of(case)
    names = build_names(plan)
    enums: dict[str, str] = {}
    members: dict[str, dict[str, str]] = {}
    for key, enum in scenarios.enum_types(plan).items():
        enums[key] = _binary(names.enums[enum.c_type], key.count(".") - 1)
        members[key] = {item.model_name: upper_snake(item.model_name) for item in enum.enumerators}
    structs = {
        key: names.structs[struct.c_type] for key, struct in scenarios.struct_types(plan).items()
    }
    return {
        "language": language,
        "types": _types(output, name, language),
        "enums": enums,
        "members": members,
        "structs": structs,
    }


def run(
    case: Case,
    library: Path,
    output: Path,
    path: Path,
    language: str = "jvm",
    *,
    java: str | None = None,
    release: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Play the scenarios of ``path`` against the Java bindings of ``case`` (``jvm`` or ``jni``),
    with the ``java`` given and the runner written for ``release``, if they are not the JDK's."""
    name = "Runner" if release is None else f"Runner{release}"
    try:
        classes = build_program(case, output, RUNNER, name, language=language, release=release)
    except RuntimeError as error:
        return subprocess.CompletedProcess([], 3, "", str(error))
    plan = plan_of(case)
    data = {
        "scenarios": scenarios.resolve(scenarios.load(path), plan),
        "map": _map(case, output, language, name),
    }
    target = classes.parent / f"data-{path.parent.name}-{path.stem}.json"
    target.write_text(json.dumps(_json_safe(data)), encoding="utf-8")
    return execute(case, classes, library, "Runner", str(target), java=java)
