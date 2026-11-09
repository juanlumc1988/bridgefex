# SPDX-License-Identifier: Apache-2.0
"""Golden test cases: tests/golden/<case>/.

Each case has:
  case.toml        options for bridgefex (module, headers, std, languages)
  input/           the C++ headers
  expected/        the expected output (c/ and python/), compared byte by byte
  impl/            optional C++ implementation, for the round-trip tests
  check_python.py  optional Python program run against the built library
  check_csharp.cs  optional C# program (top-level statements) run against the built library
  check_c.c        optional C program linked against the built library
  scenarios.toml   optional, what every backend must do the same (tests/conformance)
  annotations.toml optional, if case.toml lists it: who owns what (see bridgefex.annotations)
"""

from __future__ import annotations

import sys
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
    min_cxx: str
    """Oldest C++ standard that the generated sources compile with."""

    annotations: tuple[Path, ...] = ()
    """The annotation files of the case."""

    languages: tuple[str, ...] = ("python",)
    """The bindings that expected/ has (the C layer is always there)."""

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
    def check_csharp(self) -> Path:
        return self.directory / "check_csharp.cs"

    @property
    def check_c(self) -> Path:
        return self.directory / "check_c.c"

    @property
    def scenarios(self) -> Path:
        return self.directory / "scenarios.toml"

    @property
    def parse_std(self) -> str:
        """The standard the headers are parsed and verified with on this host: ``std``, or
        C++14 on Windows when it asks for C++11, because the STL of MSVC (the only one
        there) needs C++14 and libclang refuses to parse it as C++11. The output does not
        depend on the standard, so the golden files are the same."""
        if sys.platform == "win32" and self.std == "c++11":
            return "c++14"
        return self.std

    def options(self, languages: tuple[str, ...] | None = None) -> bridgefex.Options:
        """The options of the case; ``languages`` replaces the ones of the case (the tests
        that only need the C layer and Python do not wait for the compilers of the others)."""
        return bridgefex.Options(
            module=self.module,
            std=self.parse_std,
            annotations=self.annotations,
            languages=self.languages if languages is None else languages,
        )


def load_case(directory: Path) -> Case:
    with (directory / "case.toml").open("rb") as file:
        config = tomllib.load(file)
    return Case(
        name=directory.name,
        directory=directory,
        module=config["module"],
        headers=tuple(directory / "input" / name for name in config["headers"]),
        std=config.get("std", "c++17"),
        min_cxx=config.get("min_cxx", "c++11"),
        annotations=tuple(directory / name for name in config.get("annotations", ())),
        languages=tuple(config.get("languages", ("python",))),
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
