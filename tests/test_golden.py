# SPDX-License-Identifier: Apache-2.0
"""Golden tests: the generated files must match tests/golden/<case>/expected byte by byte.

After an intended change of the output, regenerate the expected files with
``pytest tests/test_golden.py --update-goldens`` and review the diff.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import bridgefex
from tests.support.cases import Case, all_cases, read_tree

CASES = all_cases()

pytestmark = pytest.mark.usefixtures("libclang_loaded")


def generate(case: Case) -> dict[str, bytes]:
    result = bridgefex.generate(case.headers, case.options())
    assert result.warnings == ()
    return {path: content.encode("utf-8") for path, content in result.files.items()}


def describe_differences(expected: dict[str, bytes], actual: dict[str, bytes]) -> str:
    lines: list[str] = []
    for path in sorted(expected.keys() - actual.keys()):
        lines.append(f"missing: {path}")
    for path in sorted(actual.keys() - expected.keys()):
        lines.append(f"unexpected: {path}")
    for path in sorted(expected.keys() & actual.keys()):
        if expected[path] != actual[path]:
            diff = difflib.unified_diff(
                expected[path].decode("utf-8", "replace").splitlines(keepends=True),
                actual[path].decode("utf-8", "replace").splitlines(keepends=True),
                fromfile=f"expected/{path}",
                tofile=f"generated/{path}",
            )
            lines.append("".join(diff))
    return "\n".join(lines)


def test_cases_exist() -> None:
    assert {case.name for case in CASES} >= {"demo", "scalars"}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_golden(case: Case, update_goldens: bool) -> None:
    actual = generate(case)
    if update_goldens:
        shutil.rmtree(case.expected_dir, ignore_errors=True)
        bridgefex.write_files(
            {path: content.decode("utf-8") for path, content in actual.items()},
            case.expected_dir,
        )
    expected = read_tree(case.expected_dir)
    assert expected == actual, (
        "generated files differ from the golden files "
        "(run 'pytest --update-goldens' if the change is intended):\n"
        + describe_differences(expected, actual)
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_golden_files_are_lf_utf8(case: Case) -> None:
    for path, content in read_tree(case.expected_dir).items():
        assert b"\r" not in content, path
        content.decode("utf-8")
        assert content.endswith(b"\n"), path
        assert not content.endswith(b"\n\n"), path


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_generated_python_compiles(case: Case) -> None:
    for path, content in read_tree(case.expected_dir).items():
        if path.endswith(".py"):
            compile(content, path, "exec", dont_inherit=True)


def test_output_does_not_depend_on_hash_seed(tmp_path: Path) -> None:
    """Generating in two processes with different hash seeds gives the same bytes."""
    case = next(case for case in CASES if case.name == "demo")
    trees = []
    for seed in ("1", "2"):
        output = tmp_path / f"seed{seed}"
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        command = [
            sys.executable,
            "-m",
            "bridgefex",
            "--module",
            case.module,
            "--output",
            str(output),
            *(str(header) for header in case.headers),
        ]
        completed = subprocess.run(
            command, env=environment, capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr
        trees.append(read_tree(output))
    assert trees[0] == trees[1]
    assert trees[0] == read_tree(case.expected_dir)
