# SPDX-License-Identifier: Apache-2.0
"""The layouts of the structs that the JNI bindings compute are those of the C compiler.

The bindings read and write the structs of the C layer in memory, with offsets that they compute
from the sizes and the alignments of the platform, which they take from what Java says about it
(the library is not loaded when a layout is made). A target where C places a member somewhere
else is a struct that reads garbage, without an error: this runs the layout code of the bindings
with the properties of each platform and compares it with what clang places for the target
(``_Static_assert`` on ``sizeof``, ``_Alignof`` and ``offsetof`` of every struct).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conformance import runner_jvm
from tests.support.cases import GOLDEN_DIR, load_case

pytestmark = pytest.mark.usefixtures("libclang_loaded")

DUMP = Path(__file__).parent / "support" / "jvm" / "LayoutDump.java"

# (the target of clang, sun.arch.data.model, os.arch, os.name): what the JVM of the platform says.
PLATFORMS = [
    ("x86_64-linux-gnu", "64", "amd64", "Linux"),
    ("aarch64-linux-gnu", "64", "aarch64", "Linux"),
    ("armv7a-linux-gnueabihf", "32", "arm", "Linux"),
    ("armv7a-linux-androideabi", "32", "armv8l", "Linux"),
    # 64-bit values are aligned to 4 bytes in a struct of i386 (not on Windows).
    ("i686-linux-gnu", "32", "x86", "Linux"),
    ("i686-linux-android", "32", "i386", "Linux"),
    ("i686-pc-windows-msvc", "32", "x86", "Windows 10"),
    ("x86_64-pc-windows-msvc", "64", "amd64", "Windows 10"),
    ("aarch64-pc-windows-msvc", "64", "aarch64", "Windows 11"),
]

_STRUCT = re.compile(r"typedef struct (\w+) \{(.*?)\} \w+;", re.DOTALL)
_MEMBER = re.compile(r"[\w\s*]+?\b(\w+)(?:\[\d+\])?;$")


def _members(types_header: str) -> dict[str, list[str]]:
    """The structs of the C layer and the names of their members."""
    structs: dict[str, list[str]] = {}
    for match in _STRUCT.finditer(types_header):
        names = []
        for line in match.group(2).splitlines():
            found = _MEMBER.match(line.strip())
            if found:
                names.append(found.group(1))
        structs[match.group(1)] = names
    return structs


def _dump(output: str) -> dict[str, dict[str, int]]:
    """``LayoutDump``: {simple name of the class: {size, align, OFFSET_*}}."""
    layouts: dict[str, dict[str, int]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3 or "=" not in parts[1]:
            continue  # a line of the JVM
        layouts[parts[0].rsplit(".", 1)[-1].lower()] = {
            key: int(value) for key, value in (item.split("=") for item in parts[1:])
        }
    return layouts


def _assertions(structs: dict[str, list[str]], layouts: dict[str, dict[str, int]]) -> list[str]:
    lines: list[str] = []
    for c_name, members in structs.items():
        layout = layouts.get(c_name.split("_", 1)[-1].lower())
        if layout is None:
            continue  # a struct that is not a class of its own (a nested one)
        size, align = layout["size"], layout["align"]
        lines.append(f'_Static_assert(sizeof({c_name}) == {size}, "size of {c_name}: {size}");')
        lines.append(
            f'_Static_assert(_Alignof({c_name}) == {align}, "alignment of {c_name}: {align}");'
        )
        for member in members:
            offset = layout.get("OFFSET_" + member.rstrip("_").upper())
            if offset is not None:
                lines.append(
                    f"_Static_assert(offsetof({c_name}, {member}) == {offset}, "
                    f'"offset of {c_name}.{member}: {offset}");'
                )
    return lines


@pytest.mark.parametrize(
    ("target", "model", "arch", "system"), PLATFORMS, ids=[p[0] for p in PLATFORMS]
)
def test_the_layouts_of_the_structs_are_those_of_the_target(
    tmp_path: Path, target: str, model: str, arch: str, system: str
) -> None:
    runner_jvm.skip_without_java()
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("needs clang")
    case = load_case(GOLDEN_DIR / "structs")
    classes = runner_jvm.build_program(case, tmp_path, DUMP, "LayoutDump", language="jni")
    names = [
        ".".join(path.relative_to(classes).with_suffix("").parts)
        for path in sorted(classes.rglob("*.class"))
        if "$" not in path.name and path.name != "LayoutDump.class"
    ]
    java = runner_jvm.java_runtime(runner_jvm.javac() or "")
    ran = subprocess.run(
        [
            java,
            f"-Dsun.arch.data.model={model}",
            f"-Dos.arch={arch}",
            f"-Dos.name={system}",
            "-cp",
            str(classes),
            "LayoutDump",
            *names,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    layouts = _dump(ran.stdout)

    generated = tmp_path / "jni-LayoutDump" / "generated" / "c"
    if not generated.exists():  # build_program writes the project of the case there
        generated = next((tmp_path / "jni-LayoutDump" / "generated").rglob("c"))
    headers = sorted(generated.glob("*_types.h"))
    assert headers
    structs = _members(headers[0].read_text(encoding="utf-8"))
    lines = _assertions(structs, layouts)
    # Not an empty check: the structs of the case are there, with members at their offsets.
    assert sum("offsetof" in line for line in lines) >= 15, lines
    assert any("geo_Wide" in line for line in lines)

    source = tmp_path / "layout.c"
    runtime = sorted(generated.glob("*_runtime.h"))
    includes = ["<stddef.h>", "<stdint.h>", "<stdbool.h>"] + [f'"{path.name}"' for path in runtime]
    includes.append(f'"{headers[0].name}"')
    source.write_text(
        "".join(f"#include {name}\n" for name in includes) + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    resource = subprocess.run(
        [clang, "-print-resource-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    compiled = subprocess.run(
        [
            clang,
            f"--target={target}",
            "-x",
            "c",
            "-std=c11",
            "-fsyntax-only",
            "-ferror-limit=0",
            "-nostdinc",
            "-isystem",
            f"{resource}/include",
            f"-I{generated}",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    wrong = re.findall(r"static assertion failed[^:\n]*: ([^\n]*)", compiled.stderr)
    assert compiled.returncode == 0 or wrong, compiled.stderr[-2000:]
    assert not wrong, (
        f"the layouts of the bindings are not those of {target}:\n"
        + "\n".join(wrong)
        + f"\n(bindings: {model} bits, {arch}, {system})"
    )
