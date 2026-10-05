# SPDX-License-Identifier: Apache-2.0
"""Golden test cases: tests/golden/<case>/.

Each case has:
  case.toml        options for bridgefex (module, headers, std)
  input/           the C++ headers
  expected/        the expected output (c/ and python/), compared byte by byte
  impl/            optional C++ implementation, for the round-trip tests
  check_python.py  optional Python program run against the built library
  check_c.c        optional C program linked against the built library
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import bridgefex

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"


@dataclass(frozen=True)
class Case:
    name: str
    directory: Path
    module: str
    headers: tuple[Path, ...]
    std: str

    @property
    def input_dir(self) -> Path:
        return self.directory / "input"

    @property
    def expected_dir(self) -> Path:
        return self.directory / "expected"

    @property
    def impl_sources(self) -> tuple[Path, ...]:
        return tuple(sorted((self.directory / "impl").glob("*.cpp")))

    @property
    def check_python(self) -> Path:
        return self.directory / "check_python.py"

    @property
    def check_c(self) -> Path:
        return self.directory / "check_c.c"

    def options(self) -> bridgefex.Options:
        return bridgefex.Options(module=self.module, std=self.std)


def load_case(directory: Path) -> Case:
    with (directory / "case.toml").open("rb") as file:
        config = tomllib.load(file)
    return Case(
        name=directory.name,
        directory=directory,
        module=config["module"],
        headers=tuple(directory / "input" / name for name in config["headers"]),
        std=config.get("std", "c++17"),
    )


def all_cases() -> list[Case]:
    return [load_case(path.parent) for path in sorted(GOLDEN_DIR.glob("*/case.toml"))]


def read_tree(root: Path) -> dict[str, bytes]:
    """Every file below root, keyed by relative POSIX path."""
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
