#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the generated C library of an example case for one target, and package it.

The CI runs this for every target it supports (see .github/workflows/libraries.yml) and
publishes the archives, so that the generated C layer can be seen working, linked and
run, on each of them: the headers, the shared library (and its import library on
Windows) and the static library. Each library is linked into the C program of the case,
which is run before anything is packaged (under an emulator for a target that the host
cannot run).

    python scripts/build_libraries.py --target linux-x86_64 --output dist
    python scripts/build_libraries.py --target linux-armhf --output dist \\
        --cc arm-linux-gnueabihf-gcc-14 --cxx arm-linux-gnueabihf-g++-14 \\
        --ar arm-linux-gnueabihf-ar --clang-target armv7a-linux-gnueabihf \\
        --runner "qemu-arm -L /usr/arm-linux-gnueabihf"

Without ``--cc`` and ``--cxx`` it uses ``$CC`` and ``$CXX``, else ``gcc`` and ``g++``
(``cl`` on Windows, where the environment of the Visual Studio tools must be loaded).

``--csharp-runtime linux-arm`` also generates the C# bindings of the case, with the same
parsing options, and compiles them with ``dotnet build`` for that runtime identifier. They are
compiled, not run: .NET under an emulator is slow and fragile, and a target that cannot run
the C# is still checked to build.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import bridgefex

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"


@dataclass(frozen=True)
class Case:
    name: str
    module: str
    headers: tuple[Path, ...]
    std: str
    input_dir: Path
    implementation: tuple[Path, ...]
    check: Path
    """The C program that uses the library and exits with 0 if it works."""

    annotations: tuple[Path, ...] = ()
    """The annotation files of the case (the classes that ask for a director...)."""


def load_case(name: str) -> Case:
    directory = GOLDEN / name
    with (directory / "case.toml").open("rb") as file:
        config = tomllib.load(file)
    case = Case(
        name=name,
        module=config["module"],
        headers=tuple(directory / "input" / header for header in config["headers"]),
        std=config.get("std", "c++17"),
        input_dir=directory / "input",
        implementation=tuple(sorted((directory / "impl").glob("*.cpp"))),
        check=directory / "check_c.c",
        annotations=tuple(directory / item for item in config.get("annotations", ())),
    )
    if not case.check.exists() or not case.implementation:
        raise SystemExit(f"the case '{name}' cannot be built as a library on its own")
    return case


def run(command: Sequence[str | Path], *, env: dict[str, str] | None = None) -> None:
    print("+", shlex.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, env=env)


def first_line(command: Sequence[str]) -> str:
    """The first line that a compiler prints for ``--version`` (``/?`` for cl)."""
    flag = "/?" if Path(command[0]).stem.lower() == "cl" else "--version"
    result = subprocess.run(
        [*command, flag], capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
    )
    lines = (result.stdout or result.stderr).strip().splitlines()
    return lines[0].strip() if lines else command[0]


def cross_clang_args(cxx: str, triple: str) -> tuple[str, ...]:
    """The libclang arguments that make it parse the headers as the cross compiler does:
    its target and the C++ and C library headers of its sysroot, not those of the host."""
    result = subprocess.run(
        [cxx, "-E", "-x", "c++", "-", "-v"],
        input="",
        capture_output=True,
        text=True,
        check=True,
    )
    directories: list[str] = []
    listing = False
    for line in result.stderr.splitlines():
        if line.startswith("#include <...>"):
            listing = True
        elif line.startswith("End of search list"):
            break
        elif listing and line.startswith(" "):
            directory = os.path.normpath(line.strip())
            # The headers of the host: the sysroot of the target has its own.
            if directory not in ("/usr/include", "/usr/local/include"):
                directories.append(directory)
    if not directories:
        raise SystemExit(f"{cxx} does not say where its headers are")
    return (
        f"--target={triple}",
        "-nostdinc++",
        *(f"-isystem{directory}" for directory in directories),
    )


@dataclass(frozen=True)
class Tools:
    cc: str
    cxx: str
    ar: str
    runner: list[str]
    """The command that runs a program of the target (empty: the host runs it)."""


class Build:
    """The libraries of a case, built with one toolchain."""

    def __init__(self, case: Case, tools: Tools, work: Path):
        self.case = case
        self.cc, self.cxx, self.ar = tools.cc, tools.cxx, tools.ar
        self.msvc = Path(tools.cxx).stem.lower() == "cl"
        self.work = work
        self.runner = tools.runner
        self.include = work / "c"
        self.lib = work / "lib"
        self.macro = case.module.upper()
        # The STL of MSVC needs C++14, also for the libclang that parses it.
        self.std = "c++14" if self.msvc and case.std == "c++11" else case.std
        self.sources: list[Path] = []

    # Names -------------------------------------------------------------------------

    @property
    def shared(self) -> Path:
        name = f"{self.case.module}.dll" if self.msvc else f"lib{self.case.module}.so"
        return self.lib / name

    @property
    def static(self) -> Path:
        name = f"{self.case.module}_static.lib" if self.msvc else f"lib{self.case.module}.a"
        return self.lib / name

    @property
    def import_library(self) -> Path | None:
        return self.lib / f"{self.case.module}.lib" if self.msvc else None

    # Steps -------------------------------------------------------------------------

    def generate(self, clang_args: tuple[str, ...]) -> None:
        options = bridgefex.Options(
            module=self.case.module,
            std=self.std,
            clang_args=clang_args,
            annotations=self.case.annotations,
        )
        result = bridgefex.generate(self.case.headers, options)
        bridgefex.write_files(result.files, self.work)
        self.sources = sorted(self.include.glob("*.cpp")) + list(self.case.implementation)
        self.lib.mkdir(parents=True, exist_ok=True)

    def compile_csharp(self, runtime: str, clang_args: tuple[str, ...]) -> None:
        """The C# bindings of the case, generated as the C layer was, compiled for ``runtime``."""
        options = bridgefex.Options(
            module=self.case.module,
            std=self.std,
            clang_args=clang_args,
            annotations=self.case.annotations,
            languages=("csharp",),
            verify=False,
        )
        result = bridgefex.generate(self.case.headers, options)
        output = self.work / "csharp-check"
        bridgefex.write_files(result.files, output)
        project = next(output.rglob("*.csproj"))
        run(
            [
                "dotnet",
                "build",
                project,
                "-c",
                "Release",
                "-r",
                runtime,
                "--no-self-contained",
                "-p:TreatWarningsAsErrors=true",
                "-p:UseSharedCompilation=false",
                "-nologo",
                "-v",
                "q",
                "-o",
                output / "bin",
            ]
        )

    def _includes(self) -> list[str]:
        return [f"-I{self.include}", f"-I{self.case.input_dir}"]

    def build_shared(self) -> None:
        if self.msvc:
            (self.work / "obj_shared").mkdir(exist_ok=True)
            run(
                [
                    self.cxx,
                    "/nologo",
                    "/LD",
                    "/EHsc",
                    "/permissive-",
                    f"/std:{self.std}",
                    "/O2",
                    "/W4",
                    *self._includes(),
                    *self.sources,
                    f"/Fe:{self.shared}",
                    f"/Fo:{self.work / 'obj_shared'}{os.sep}",
                    "/link",
                    "/NOLOGO",
                ]
            )
            return
        run(
            [
                self.cxx,
                f"-std={self.std}",
                "-O2",
                "-Wall",
                "-Wextra",
                "-fPIC",
                "-fvisibility=hidden",
                "-shared",
                "-pthread",
                *self._includes(),
                *self.sources,
                "-o",
                self.shared,
                "-Wl,-z,defs",
                f"-Wl,-soname,{self.shared.name}",
            ]
        )

    def build_static(self) -> None:
        objects = self.work / "obj_static"
        objects.mkdir(exist_ok=True)
        define = f"{self.macro}_C_API_STATIC"
        if self.msvc:
            run(
                [
                    self.cxx,
                    "/nologo",
                    "/c",
                    "/EHsc",
                    "/permissive-",
                    f"/std:{self.std}",
                    "/O2",
                    "/W4",
                    f"/D{define}",
                    *self._includes(),
                    *self.sources,
                    f"/Fo:{objects}{os.sep}",
                ]
            )
            files = [objects / f"{source.stem}.obj" for source in self.sources]
            run(["lib", "/nologo", f"/OUT:{self.static}", *files])
            return
        files = []
        for source in self.sources:
            target = objects / f"{source.stem}.o"
            run(
                [
                    self.cxx,
                    f"-std={self.std}",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-fPIC",
                    f"-D{define}",
                    *self._includes(),
                    "-c",
                    source,
                    "-o",
                    target,
                ]
            )
            files.append(target)
        self.static.unlink(missing_ok=True)
        run([self.ar, "rcs", self.static, *files])

    def check(self, *, static: bool) -> None:
        """Link the C program of the case with the library and run it."""
        kind = "static" if static else "shared"
        define = f"{self.macro}_C_API_STATIC"
        directory = self.work / f"check_{kind}"
        directory.mkdir(exist_ok=True)
        program = directory / ("check.exe" if self.msvc else "check")
        env = dict(os.environ)
        if self.msvc:
            library = self.static if static else self.import_library
            assert library is not None
            run(
                [
                    self.cc,
                    "/nologo",
                    "/TC",
                    "/std:c11",
                    *([f"/D{define}"] if static else []),
                    f"/I{self.include}",
                    self.case.check,
                    f"/Fe:{program}",
                    f"/Fo:{directory}{os.sep}",
                    "/link",
                    "/NOLOGO",
                    library,
                ]
            )
            env["PATH"] = f"{self.lib}{os.pathsep}{env['PATH']}"
        elif static:
            run(
                [
                    self.cc,
                    "-std=c11",
                    f"-D{define}",
                    f"-I{self.include}",
                    "-pthread",
                    self.case.check,
                    "-o",
                    program,
                    self.static,
                    "-lstdc++",
                    "-lm",
                ]
            )
        else:
            run(
                [
                    self.cc,
                    "-std=c11",
                    f"-I{self.include}",
                    "-pthread",
                    self.case.check,
                    "-o",
                    program,
                    self.shared,
                    f"-Wl,-rpath,{self.lib}",
                ]
            )
        run([*self.runner, program], env=env)


def package(build: Build, target: str, output: Path, compilers: str) -> Path:
    """The archive: ``include/``, ``lib/`` and a README, in a directory named after it."""
    case = build.case
    root = f"bridgefex-{case.name}-{target}"
    stage = build.work / "stage" / root
    (stage / "include").mkdir(parents=True)
    (stage / "lib").mkdir()
    for header in sorted(build.include.glob("*.h")):
        shutil.copy2(header, stage / "include" / header.name)
    for library in (build.shared, build.static, build.import_library):
        if library is not None:
            shutil.copy2(library, stage / "lib" / library.name)
    revision = os.environ.get("GITHUB_SHA", "unknown")
    macro = build.macro
    imports = " and its import library" if build.msvc else ""
    (stage / "README.txt").write_text(
        f"""The C library that bridgefex generates for the '{case.name}' example, for {target}.

    include/   the generated C headers (include "{case.module}_runtime.h" and the
               ones of the headers of the example)
    lib/       the shared library{imports}, and the static library

Built with: {compilers}
bridgefex {bridgefex.__version__}, commit {revision}

Using it from C (or C++): add include/ to the include path and link the library. For the
static library, define {macro}_C_API_STATIC when you compile your code, and link the C++
runtime too ({"the Visual C++ runtime does it" if build.msvc else "-lstdc++ -lm"}).
The program of the example, which links and runs both libraries, passed before this
archive was made.
""",
        encoding="utf-8",
        newline="\n",
    )
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in stage.rglob("*") if path.is_file())
    if build.msvc:
        archive = output / f"{root}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path in files:
                zipped.write(path, f"{root}/{path.relative_to(stage).as_posix()}")
    else:
        archive = output / f"{root}.tar.gz"
        with tarfile.open(archive, "w:gz") as tarred:
            for path in files:
                tarred.add(path, f"{root}/{path.relative_to(stage).as_posix()}", recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n"
    )
    return archive


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", required=True, help="name of the target, e.g. linux-x86_64")
    parser.add_argument("--output", type=Path, required=True, help="directory of the archive")
    parser.add_argument("--case", default="demo", help="golden case to build (default: demo)")
    parser.add_argument("--cc", help="C compiler (default: $CC, gcc or cl)")
    parser.add_argument("--cxx", help="C++ compiler (default: $CXX, g++ or cl)")
    parser.add_argument("--ar", help="archiver for the static library (default: $AR or ar)")
    parser.add_argument(
        "--clang-target", help="parse the headers for this target, with the headers of --cxx"
    )
    parser.add_argument(
        "--check-case",
        action="append",
        default=[],
        help="another golden case to build and run, without packaging it (repeatable)",
    )
    parser.add_argument(
        "--csharp-runtime",
        metavar="RID",
        help="also compile the C# bindings of the case for this runtime identifier (linux-arm...)",
    )
    parser.add_argument("--runner", default="", help="command that runs a program of the target")
    parser.add_argument("--work", type=Path, help="working directory (default: temporary)")
    args = parser.parse_args(argv)

    default_cc, default_cxx = ("cl", "cl") if sys.platform == "win32" else ("gcc", "g++")
    cc = args.cc or os.environ.get("CC") or default_cc
    cxx = args.cxx or os.environ.get("CXX") or default_cxx
    ar = args.ar or os.environ.get("AR") or "ar"
    for tool in (cc, cxx):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool}: compiler not found")

    tools = Tools(cc, cxx, ar, shlex.split(args.runner))
    clang_args = cross_clang_args(cxx, args.clang_target) if args.clang_target else ()
    base = (args.work or ROOT / "build" / "libraries" / args.target).resolve()
    shutil.rmtree(base, ignore_errors=True)

    def built(name: str) -> Build:
        build = Build(load_case(name), tools, base / name)
        build.work.mkdir(parents=True)
        build.generate(clang_args)
        build.build_shared()
        build.build_static()
        build.check(static=False)
        build.check(static=True)
        return build

    # The cases that are only checked first: nothing is packaged if one fails.
    for name in args.check_case:
        built(name)
    main_build = built(args.case)
    if args.csharp_runtime:
        if shutil.which("dotnet") is None:
            raise SystemExit("--csharp-runtime needs the .NET SDK (dotnet)")
        main_build.compile_csharp(args.csharp_runtime, clang_args)
    archive = package(main_build, args.target, args.output.resolve(), first_line([cxx]))
    print(f"{archive} ({archive.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
