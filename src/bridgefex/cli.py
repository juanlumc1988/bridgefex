# SPDX-License-Identifier: Apache-2.0
"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, api, generator, libclang
from .errors import BridgefexError

_LANGUAGE_NAMES = {"java": "jvm", "kotlin": "jvm", "c#": "csharp"}
"""What people call the languages: Java and Kotlin are the ``jvm`` bindings."""


def _language(value: str) -> str:
    return _LANGUAGE_NAMES.get(value.lower(), value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bridgefex",
        description="Generate a pure C API from C++ headers, and bindings for other languages.",
    )
    parser.add_argument("headers", nargs="+", type=Path, metavar="HEADER", help="C++ headers")
    parser.add_argument(
        "-m",
        "--module",
        required=True,
        help=(
            "module name: prefix of the runtime C symbols, and name of the Python package, "
            "the C# namespace and the Java package"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        metavar="DIR",
        help="output directory; the files go to its c/ subdirectory and one for each language",
    )
    parser.add_argument(
        "--lang",
        action="append",
        type=_language,
        choices=(*generator.LANGUAGES, "none"),
        metavar="LANG",
        help=(
            f"bindings generated on top of the C layer: {', '.join(generator.LANGUAGES)} or "
            f"none (repeatable; default: {', '.join(generator.DEFAULT_LANGUAGES)})"
        ),
    )
    parser.add_argument(
        "--std", default="c++17", help="C++ standard used to parse the headers (default: c++17)"
    )
    parser.add_argument(
        "-I",
        dest="include_dirs",
        action="append",
        type=Path,
        default=[],
        metavar="DIR",
        help="include directory for parsing (repeatable)",
    )
    parser.add_argument(
        "-D",
        dest="defines",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help="macro definition for parsing (repeatable)",
    )
    parser.add_argument(
        "--clang-arg",
        dest="clang_args",
        action="append",
        default=[],
        metavar="ARG",
        help="extra libclang argument, e.g. --clang-arg=-fms-extensions (repeatable)",
    )
    parser.add_argument(
        "--include-root",
        type=Path,
        metavar="DIR",
        help="make the #include paths of the generated sources relative to DIR "
        "(default: include each header by its file name)",
    )
    parser.add_argument(
        "--annotations",
        dest="annotations",
        action="append",
        type=Path,
        default=[],
        metavar="FILE",
        help="TOML file that says who owns the objects that cross the boundary: raw "
        "pointers, objects that are taken or kept (repeatable)",
    )
    parser.add_argument(
        "--libclang",
        type=Path,
        metavar="PATH",
        help=f"libclang shared library (default: ${libclang.ENV_VAR}, then the usual "
        "install locations)",
    )
    parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help=(
            "do not compile the generated code before writing it (the C layer with libclang, "
            "the C# project with dotnet, the Java sources with javac)"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _tolerant_output() -> None:
    """Never fail on a message that the console encoding cannot represent."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def main(argv: Sequence[str] | None = None) -> int:
    _tolerant_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    chosen = list(dict.fromkeys(args.lang or generator.DEFAULT_LANGUAGES))
    if "none" in chosen and len(chosen) > 1:
        parser.error("--lang none (only the C layer) cannot be combined with a language")
    languages = tuple(language for language in chosen if language != "none")
    options = api.Options(
        module=args.module,
        std=args.std,
        include_dirs=tuple(args.include_dirs),
        defines=tuple(args.defines),
        clang_args=tuple(args.clang_args),
        include_root=args.include_root,
        languages=languages,
        annotations=tuple(args.annotations),
        verify=args.verify,
    )
    try:
        libclang.load(args.libclang)
        result = api.generate(args.headers, options)
        written = generator.write_files(result.files, args.output)
    except (BridgefexError, OSError) as error:
        for line in str(error).splitlines():
            print(f"bridgefex: error: {line}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"bridgefex: warning: {warning}", file=sys.stderr)
    print(f"bridgefex: wrote {len(written)} files to {args.output}")
    return 0
