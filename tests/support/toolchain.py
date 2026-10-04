# SPDX-License-Identifier: Apache-2.0
"""Minimal compiler driver for the native tests (GCC/Clang and MSVC).

On Linux and macOS the compilers come from $CC and $CXX (default: cc and c++).
On Windows, cl.exe must be on PATH, as in a Visual Studio developer prompt.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Standards that the generated code must compile with, oldest first. MSVC has
# no C99 or C++11 mode; its oldest modes are C11 and C++14.
GNU_C_STANDARDS = ("c99", "c11", "c17", "c23")
GNU_CXX_STANDARDS = ("c++11", "c++14", "c++17", "c++20", "c++23")
MSVC_C_STANDARDS = ("c11", "c17", "clatest")
MSVC_CXX_STANDARDS = ("c++14", "c++17", "c++20", "c++latest")

# Older spellings, tried when a compiler does not know the final name.
_ALIASES = {"c23": "c2x", "c++23": "c++2b", "c++20": "c++2a"}

_GNU_WARNINGS = ("-Wall", "-Wextra", "-Wpedantic", "-Wundef", "-Werror")
_MSVC_WARNINGS = ("/W4", "/WX")


class ToolchainError(Exception):
    pass


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    output: str

    def check(self) -> None:
        if self.returncode != 0:
            raise ToolchainError(
                f"command failed with exit code {self.returncode}:\n"
                f"{' '.join(self.command)}\n{self.output}"
            )


def run(command: Sequence[str], cwd: Path | None = None) -> CommandResult:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    return CommandResult(tuple(command), completed.returncode, completed.stdout)


@dataclass(frozen=True)
class Toolchain:
    kind: str
    """``gnu`` (GCC or Clang) or ``msvc``."""

    cc: str
    cxx: str

    @property
    def c_standards(self) -> tuple[str, ...]:
        return MSVC_C_STANDARDS if self.kind == "msvc" else GNU_C_STANDARDS

    @property
    def cxx_standards(self) -> tuple[str, ...]:
        return MSVC_CXX_STANDARDS if self.kind == "msvc" else GNU_CXX_STANDARDS

    @property
    def shared_library_suffix(self) -> str:
        if self.kind == "msvc":
            return ".dll"
        return ".dylib" if sys.platform == "darwin" else ".so"

    def shared_library_name(self, module: str) -> str:
        """The file name that the generated Python loader looks for by default."""
        if self.kind == "msvc":
            return f"{module}.dll"
        return f"lib{module}{self.shared_library_suffix}"

    # Standards ---------------------------------------------------------------

    def resolve_standard(self, language: str, standard: str, scratch: Path) -> str | None:
        """The flag value that selects ``standard``, or None if it is unsupported."""
        return _resolve_standard(self, language, standard, str(scratch))

    def _std_flag(self, standard: str) -> str:
        return f"/std:{standard}" if self.kind == "msvc" else f"-std={standard}"

    # Compilation -------------------------------------------------------------

    def syntax_check(
        self,
        source: Path,
        language: str,
        standard: str,
        include_dirs: Sequence[Path],
    ) -> CommandResult:
        """Compile ``source`` without producing output, with warnings as errors."""
        includes = [f"-I{directory}" for directory in include_dirs]
        if self.kind == "msvc":
            mode = "/TC" if language == "c" else "/TP"
            extra = [] if language == "c" else ["/EHsc", "/permissive-"]
            command = [
                self.cc,
                "/nologo",
                "/Zs",
                mode,
                self._std_flag(standard),
                *_MSVC_WARNINGS,
                *extra,
                *includes,
                str(source),
            ]
        else:
            compiler = self.cc if language == "c" else self.cxx
            command = [
                compiler,
                "-fsyntax-only",
                "-x",
                "c" if language == "c" else "c++",
                self._std_flag(standard),
                *_GNU_WARNINGS,
                *includes,
                str(source),
            ]
        return run(command, cwd=source.parent)

    def compile_object(
        self,
        source: Path,
        language: str,
        standard: str,
        include_dirs: Sequence[Path],
        output: Path,
    ) -> CommandResult:
        """Compile ``source`` to an object file, with warnings as errors.

        Unlike :meth:`syntax_check`, this runs the whole compiler, which is
        needed for warnings that GCC only reports late (such as -Wunused-result).
        """
        includes = [f"-I{directory}" for directory in include_dirs]
        if self.kind == "msvc":
            mode = "/TC" if language == "c" else "/TP"
            extra = [] if language == "c" else ["/EHsc", "/permissive-"]
            command = [
                self.cc,
                "/nologo",
                "/c",
                mode,
                self._std_flag(standard),
                *_MSVC_WARNINGS,
                *extra,
                *includes,
                str(source),
                f"/Fo:{output}",
            ]
        else:
            compiler = self.cc if language == "c" else self.cxx
            command = [
                compiler,
                "-c",
                "-x",
                "c" if language == "c" else "c++",
                self._std_flag(standard),
                *_GNU_WARNINGS,
                *includes,
                str(source),
                "-o",
                str(output),
            ]
        return run(command, cwd=output.parent)

    def build_cxx_executable(
        self,
        sources: Sequence[Path],
        include_dirs: Sequence[Path],
        output: Path,
        cxx_standard: str,
    ) -> CommandResult:
        """Build a C++ program (with thread support) from ``sources``."""
        includes = [f"-I{directory}" for directory in include_dirs]
        if self.kind == "msvc":
            command = [
                self.cxx,
                "/nologo",
                "/EHsc",
                "/permissive-",
                self._std_flag(cxx_standard),
                *_MSVC_WARNINGS,
                *includes,
                *(str(source) for source in sources),
                f"/Fe:{output}",
                f"/Fo:{output.parent}{os.sep}",
                "/link",
                "/NOLOGO",
            ]
        else:
            command = [
                self.cxx,
                self._std_flag(cxx_standard),
                *_GNU_WARNINGS,
                "-pthread",
                *includes,
                *(str(source) for source in sources),
                "-o",
                str(output),
            ]
        return run(command, cwd=output.parent)

    def build_shared_library(
        self,
        sources: Sequence[Path],
        include_dirs: Sequence[Path],
        output: Path,
        cxx_standard: str,
    ) -> CommandResult:
        """Build a shared library from C++ sources; only exported symbols are visible."""
        includes = [f"-I{directory}" for directory in include_dirs]
        if self.kind == "msvc":
            command = [
                self.cxx,
                "/nologo",
                "/LD",
                "/EHsc",
                "/permissive-",
                self._std_flag(cxx_standard),
                *_MSVC_WARNINGS,
                *includes,
                *(str(source) for source in sources),
                f"/Fe:{output}",
                f"/Fo:{output.parent}{os.sep}",
                "/link",
                "/NOLOGO",
            ]
        else:
            linker_flags = []
            if sys.platform.startswith("linux"):
                # Fail at link time on undefined symbols instead of at load time.
                linker_flags = ["-Wl,-z,defs", f"-Wl,-soname,{output.name}"]
            command = [
                self.cxx,
                self._std_flag(cxx_standard),
                *_GNU_WARNINGS,
                "-fPIC",
                "-fvisibility=hidden",
                "-shared",
                *includes,
                *(str(source) for source in sources),
                "-o",
                str(output),
                *linker_flags,
            ]
        return run(command, cwd=output.parent)

    def build_c_executable(
        self,
        source: Path,
        include_dirs: Sequence[Path],
        library: Path,
        output: Path,
        c_standard: str,
    ) -> CommandResult:
        """Build a C program linked against ``library`` (in the same directory)."""
        includes = [f"-I{directory}" for directory in include_dirs]
        if self.kind == "msvc":
            import_library = library.with_suffix(".lib")
            command = [
                self.cc,
                "/nologo",
                "/TC",
                self._std_flag(c_standard),
                *_MSVC_WARNINGS,
                *includes,
                str(source),
                f"/Fe:{output}",
                f"/Fo:{output.parent}{os.sep}",
                "/link",
                "/NOLOGO",
                str(import_library),
            ]
        else:
            command = [
                self.cc,
                self._std_flag(c_standard),
                *_GNU_WARNINGS,
                *includes,
                "-pthread",
                str(source),
                "-o",
                str(output),
                str(library),
                f"-Wl,-rpath,{library.parent}",
            ]
        return run(command, cwd=output.parent)

    # Inspection --------------------------------------------------------------

    def exported_symbols(self, library: Path) -> set[str] | None:
        """Names exported by a shared library, or None if no tool is available."""
        if self.kind == "msvc":
            if shutil.which("dumpbin") is None:
                return None
            result = run(["dumpbin", "/nologo", "/exports", str(library)])
            result.check()
            return _parse_dumpbin_exports(result.output)
        if not sys.platform.startswith("linux") or shutil.which("nm") is None:
            return None
        result = run(["nm", "-D", "--defined-only", str(library)])
        result.check()
        # Every defined dynamic symbol, whatever its kind (functions, data, weak...).
        return {parts[2] for parts in map(str.split, result.output.splitlines()) if len(parts) == 3}


def _parse_dumpbin_exports(text: str) -> set[str]:
    symbols = set()
    in_table = False
    for line in text.splitlines():
        parts = line.split()
        if parts[:4] == ["ordinal", "hint", "RVA", "name"]:
            in_table = True
            continue
        if in_table:
            if not parts:
                if symbols:
                    break
                continue
            if len(parts) >= 4 and parts[0].isdigit():
                symbols.add(parts[3])
    return symbols


@functools.cache
def _resolve_standard(
    toolchain: Toolchain, language: str, standard: str, scratch: str
) -> str | None:
    directory = Path(scratch)
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / ("probe.c" if language == "c" else "probe.cpp")
    probe.write_text(
        "int bridgefex_probe(void);\nint bridgefex_probe(void) { return 0; }\n", encoding="utf-8"
    )
    for candidate in (standard, _ALIASES.get(standard)):
        if candidate is None:
            continue
        if toolchain.syntax_check(probe, language, candidate, []).returncode == 0:
            return candidate
    return None


def detect() -> Toolchain:
    """The toolchain used by the native tests."""
    if sys.platform == "win32":
        if shutil.which("cl") is None:
            raise ToolchainError(
                "cl.exe is not on PATH; run the tests from a Visual Studio developer prompt"
            )
        return Toolchain("msvc", "cl", "cl")
    cc = os.environ.get("CC", "cc")
    cxx = os.environ.get("CXX", "c++")
    for compiler in (cc, cxx):
        if shutil.which(compiler) is None:
            raise ToolchainError(f"compiler '{compiler}' not found (set CC and CXX)")
    return Toolchain("gnu", cc, cxx)
