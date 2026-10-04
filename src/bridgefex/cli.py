# SPDX-License-Identifier: Apache-2.0
"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, api, generator, libclang
from .errors import BridgefexError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bridgefex",
        description="Generate a pure C API, and Python ctypes bindings, from C++ headers.",
    )
    parser.add_argument("headers", nargs="+", type=Path, metavar="HEADER", help="C++ headers")
    parser.add_argument(
        "-m",
        "--module",
        required=True,
        help="module name: prefix of the runtime C symbols and name of the Python package",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        metavar="DIR",
        help="output directory; the files go to its c/ and python/ subdirectories",
    )
    parser.add_argument(
        "--lang",
        choices=("python", "none"),
        default="python",
        help="bindings generated on top of the C layer (default: python)",
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
        "--libclang",
        type=Path,
        metavar="PATH",
        help=f"libclang shared library (default: ${libclang.ENV_VAR}, then the usual "
        "install locations)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    languages = () if args.lang == "none" else (args.lang,)
    options = api.Options(
        module=args.module,
        std=args.std,
        include_dirs=tuple(args.include_dirs),
        defines=tuple(args.defines),
        clang_args=tuple(args.clang_args),
        include_root=args.include_root,
        languages=languages,
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
