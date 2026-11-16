# SPDX-License-Identifier: Apache-2.0
"""Round trip through the generated Rust bindings.

For each golden case with a check_rust.rs, the library of the case is built (the generated C
layer and the C++ implementation), the crate is generated, the check is built against it and run
in its own process. The tests of the conformance suite play the same scenarios in every language;
these ones check what is particular of Rust: what the compiler refuses (the claims of safety of the
bindings), and what the types promise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from _pytest.mark.structures import ParameterSet

from tests.conformance import runner_rust
from tests.support.build import build
from tests.support.cases import Case, adhoc_case, all_cases
from tests.support.toolchain import Toolchain, asan_runtime

CASES = [case for case in all_cases() if case.check_rust.exists()]

pytestmark = pytest.mark.usefixtures("libclang_loaded")

Built = tuple[Case, Path, Path]


@pytest.fixture(autouse=True)
def _cargo() -> None:
    runner_rust.skip_without_rust()


def test_there_are_checks() -> None:
    assert {case.name for case in CASES} >= {"edge", "ownership", "inherit"}


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def built(
    request: pytest.FixtureRequest,
    toolchain: Toolchain,
    tmp_path_factory: pytest.TempPathFactory,
) -> Built:
    case: Case = request.param
    directory = tmp_path_factory.mktemp(f"rust_{case.name}")
    library, output = build(case, toolchain, directory)
    return case, library, output


def test_rust_round_trip(built: Built) -> None:
    case, library, output = built
    try:
        executable = runner_rust.build_program(case, library, output, "check", case.check_rust)
    except RuntimeError as error:
        pytest.fail(str(error))
    completed = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        check=False,
        env=runner_rust.environment(case, output, library),
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


@pytest.fixture(scope="module", params=CASES, ids=lambda case: case.name)
def asan_build(
    request: pytest.FixtureRequest,
    toolchain: Toolchain,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Case, Path, Path, str]:
    """A case built with AddressSanitizer: (case, library, output, runtime). The program is not
    instrumented: the runtime is preloaded, and it sees what C++ does."""
    runtime = asan_runtime(toolchain)
    if runtime is None:
        pytest.skip("needs GCC with AddressSanitizer on Linux")
    case: Case = request.param
    flags = ["-fsanitize=address", "-fno-omit-frame-pointer"]
    library, output = build(
        case, toolchain, tmp_path_factory.mktemp(f"rust_asan_{case.name}"), flags
    )
    return case, library, output, runtime


def test_the_round_trip_is_clean_under_asan(asan_build: tuple[Case, Path, Path, str]) -> None:
    """Rust destroys each object once (a double drop is a double free), and every object that
    Rust owns is destroyed (what is not, is reported as a leak; the objects that the check leaks
    on purpose are still reachable from C++ globals)."""
    case, library, output, runtime = asan_build
    executable = runner_rust.build_program(case, library, output, "check", case.check_rust)
    completed = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **runner_rust.environment(case, output, library),
            "LD_PRELOAD": runtime,
            "ASAN_OPTIONS": "detect_leaks=1:abort_on_error=0",
        },
        timeout=600,
    )
    assert "Sanitizer" not in completed.stderr, completed.stderr
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def _archive(toolchain: Toolchain, case: Case, output: Path, directory: Path) -> Path:
    """The library of ``case`` as a static archive (what the C layer in ``output`` and the
    implementation of the case make), in ``directory``."""
    directory.mkdir(exist_ok=True)
    objects = []
    sources = [*sorted((output / "c").glob("*.cpp")), *case.impl_sources]
    for source in sources:
        target = directory / f"{source.stem}.o"
        completed = subprocess.run(
            [
                toolchain.cxx,
                "-std=c++17",
                "-fPIC",
                "-c",
                f"-I{output / 'c'}",
                f"-I{case.input_dir}",
                str(source),
                "-o",
                str(target),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        objects.append(str(target))
    archive = directory / f"lib{case.module}.a"
    archive.unlink(missing_ok=True)
    subprocess.run(["ar", "rcs", str(archive), *objects], check=True)
    return archive


def test_a_static_library_is_linked_with_the_runtime_of_cpp(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """With ``<MODULE>_LIB_KIND=static`` the build script also links the C++ runtime that the
    library needs: the program is then run without the library on its path."""
    if toolchain.kind != "gnu" or not sys.platform.startswith("linux") or not shutil.which("ar"):
        pytest.skip("needs GCC or Clang on Linux, and ar")
    case = next(case for case in CASES if case.name == "ownership")
    shared, output = build(case, toolchain, tmp_path / "shared")
    archive = _archive(toolchain, case, output, tmp_path / "static")
    executable = runner_rust.build_program(
        case,
        archive,
        output,
        "check_static",
        case.check_rust,
        variables={"OWN_LIB_KIND": "static"},
    )
    clean = {key: value for key, value in os.environ.items() if key != "LD_LIBRARY_PATH"}
    completed = subprocess.run(
        [str(executable)], capture_output=True, text=True, check=False, env=clean, timeout=600
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout
    assert shared.exists()


# What the compiler refuses ----------------------------------------------------------------------
#
# Each claim of safety is a pair of programs: one that the compiler refuses with the error that
# says why, and the same one, corrected, that it accepts (so that the refusal is of the claim and
# not of something else in the program).

PRELUDES = {
    "ownership": """\
#![allow(unused)]
use own::node::{Item, Node};
use own::tree::{Driver, Pair, Tree};
use own::Owned;

fn main() -> own::Result<()> {
""",
    "inherit": """\
#![allow(unused)]
use inh::shape::{Named, Shape};
use inh::shapes::{self, Circle, Ring, Square};
use inh::widget::{self, Counter, Labeled, Observer, Plain, PlainDerived, Shelf, Widget};
use inh::Owned;

fn main() -> inh::Result<()> {
""",
}

REFUSED = [
    pytest.param(
        "let item = Item::new(1)?; let mut tree = Tree::new()?; tree.adopt(item)?; item.id()?;",
        "let item = Item::new(1)?; let mut tree = Tree::new()?; tree.adopt(item)?;",
        "E0382",
        id="use-after-transfer",
    ),
    pytest.param(
        "let item = Item::new(1)?; drop(item); item.id()?;",
        "let item = Item::new(1)?; drop(item);",
        "E0382",
        id="use-after-drop",
    ),
    pytest.param(
        "let mut a = Node::new(1)?; own::tree::pick(&mut a, &mut a, true)?;",
        "let mut a = Node::new(1)?; let mut b = Node::new(2)?; "
        "own::tree::pick(&mut a, &mut b, true)?;",
        "E0499",
        id="two-mutable-borrows-of-one-object",
    ),
    pytest.param(
        "let mut tree = Tree::new()?; let first = tree.root()?; let second = tree.root()?; "
        "first.set_value(1)?; second.set_value(2)?;",
        "let mut tree = Tree::new()?; let first = tree.root()?; first.set_value(1)?; "
        "let second = tree.root()?; second.set_value(2)?;",
        "E0499",
        id="two-borrows-from-one-owner",
    ),
    pytest.param(
        "let root = { let mut tree = Tree::new()?; tree.root()? }; root.value()?;",
        "let mut tree = Tree::new()?; let root = tree.root()?; root.value()?;",
        "E0597",
        id="borrow-outlives-its-owner",
    ),
    pytest.param(
        "let mut tree = Tree::new()?; let root = tree.root()?; drop(tree); root.value()?;",
        "let mut tree = Tree::new()?; let root = tree.root()?; root.value()?; drop(tree);",
        "E0505",
        id="borrow-then-drop-the-owner",
    ),
    pytest.param(
        "let mut tree = Tree::new()?; tree.croot()?.set_value(1)?;",
        "let mut tree = Tree::new()?; tree.root()?.set_value(1)?;",
        "E0596",
        id="read-only-borrow-is-not-writable",
    ),
    pytest.param(
        "let mut tree = Tree::new()?; let mut node = Node::new(1)?; tree.bump(tree.croot()?)?;",
        "let mut tree = Tree::new()?; let mut node = Node::new(1)?; tree.bump(&mut node)?;",
        "E0308",
        id="read-only-borrow-where-the-object-is-changed",
    ),
    pytest.param(
        "let mut node = Node::new(1)?; let mut tree = Tree::new()?; "
        "let borrowed = own::tree::same(&node)?; node.set_value(2)?; borrowed.value()?;",
        "let mut node = Node::new(1)?; let mut tree = Tree::new()?; "
        "let borrowed = own::tree::same(&node)?; borrowed.value()?; node.set_value(2)?;",
        "E0502",
        id="a-result-borrowed-from-an-argument",
    ),
    pytest.param(
        "let mut node = Node::new(1)?; let mut driver = Driver::new(&mut node)?;",
        "let mut node = Node::new(1)?; let mut driver = unsafe { Driver::new(&mut node)? };",
        "E0133",
        id="retaining-constructor-is-unsafe",
    ),
    pytest.param(
        "let mut node = Node::new(1)?; let mut tree = Tree::new()?; tree.set_cursor(&mut node)?;",
        "let mut node = Node::new(1)?; let mut tree = Tree::new()?; "
        "unsafe { tree.set_cursor(&mut node)? };",
        "E0133",
        id="retaining-method-is-unsafe",
    ),
    pytest.param(
        "let tree = Tree::new()?; let other = tree.clone();",
        "let tree = Tree::new()?; let other = &tree;",
        "E0599",
        id="an-owned-object-is-not-cloned",
    ),
    pytest.param(
        "fn send<T: Send>(_: T) {} let node = Node::new(1)?; send(node);",
        "fn send<T>(_: T) {} let node = Node::new(1)?; send(node);",
        "E0277",
        id="an-owned-object-is-not-send",
    ),
    pytest.param(
        "fn share<T: Sync>(_: &T) {} let node = Node::new(1)?; share(&node);",
        "fn share<T>(_: &T) {} let node = Node::new(1)?; share(&node);",
        "E0277",
        id="an-owned-object-is-not-sync",
    ),
    pytest.param(
        "let node: Node = Node { _data: [], _marker: ::core::marker::PhantomData };",
        "let node = Node::new(1)?;",
        "E0451",
        id="a-class-cannot-be-built-by-hand",
    ),
    pytest.param(
        "let a = Node::new(1)?; let b = *a;",
        "let a = Node::new(1)?; let b = &*a;",
        "E0507",
        id="a-class-is-not-moved-out-of-its-owner",
    ),
]


REFUSED_INHERIT = [
    pytest.param(
        "widget::take_plain(PlainDerived::new()?)?;",
        "widget::take_plain(Plain::new()?)?;",
        "E0308",
        id="a-derived-object-is-not-a-base-without-a-virtual-destructor",
    ),
    pytest.param(
        "let plain = PlainDerived::new()?.into_base();",
        "let plain = Circle::new(1.0)?.into_base();",
        "E0599",
        id="no-into-base-without-a-virtual-destructor",
    ),
    pytest.param(
        "let named: Owned<Named> = Circle::new(1.0)?.into_base();",
        "let named: Owned<Named> = Circle::new(1.0)?.into_base().into_base();",
        "E0308",
        id="into-base-is-one-level",
    ),
    pytest.param(
        "let shape = shapes::make_shape(0, 1.0)?.unwrap(); let circle: Owned<Circle> = shape;",
        "let shape = shapes::make_shape(0, 1.0)?.unwrap(); let again: Owned<Shape> = shape;",
        "E0308",
        id="a-base-is-not-narrowed",
    ),
    pytest.param(
        "let mut w = Widget::new()?; let l = w.into_labeled(); w.next()?;",
        "let mut w = Widget::new()?; w.next()?; let l = w.into_labeled();",
        "E0382",
        id="an-object-given-up-as-a-base-is-gone",
    ),
    pytest.param(
        'let mut w = Widget::new()?; w.set_label("x")?;',
        'let mut w = Widget::new()?; w.as_labeled_mut().set_label("x")?;',
        "E0599",
        id="the-second-base-is-reached-through-as",
    ),
    pytest.param(
        "let c = Circle::new(1.0)?; let s: Owned<Shape> = c;",
        "let c = Circle::new(1.0)?; let s: Owned<Shape> = c.into_base();",
        "E0308",
        id="an-owned-derived-is-not-an-owned-base",
    ),
]


def _program(case: Case, body: str) -> str:
    return PRELUDES[case.name] + "    " + body + "\n    Ok(())\n}\n"


def _for(name: str, params: list[ParameterSet]) -> list[ParameterSet]:
    return [pytest.param(name, *p.values, id=f"{name}-{p.id}") for p in params]


@pytest.fixture(scope="module")
def crates(
    toolchain: Toolchain, tmp_path_factory: pytest.TempPathFactory
) -> Callable[[str], tuple[Case, Path, Path]]:
    """The cases that have refusals, built when first asked for: (case, library, output)."""
    built_crates: dict[str, tuple[Case, Path, Path]] = {}

    def crate(name: str) -> tuple[Case, Path, Path]:
        if name not in built_crates:
            case = next(case for case in CASES if case.name == name)
            directory = tmp_path_factory.mktemp(f"rust_refused_{name}")
            library, output = build(case, toolchain, directory)
            built_crates[name] = (case, library, output)
        return built_crates[name]

    return crate


@pytest.mark.parametrize(
    ("name", "bad", "good", "code"),
    [*_for("ownership", REFUSED), *_for("inherit", REFUSED_INHERIT)],
)
def test_the_compiler_refuses_it(
    crates: Callable[[str], tuple[Case, Path, Path]],
    request: pytest.FixtureRequest,
    name: str,
    bad: str,
    good: str,
    code: str,
) -> None:
    case, library, output = crates(name)
    unique = request.node.callspec.id.replace("-", "_")
    with pytest.raises(RuntimeError) as refused:
        runner_rust.check_program(case, library, output, f"bad_{unique}", _program(case, bad))
    assert f"error[{code}]" in str(refused.value), str(refused.value)
    # The corrected program is accepted: the refusal was of the claim, and of nothing else.
    runner_rust.check_program(case, library, output, f"good_{unique}", _program(case, good))


# Libraries that no golden case has ----------------------------------------------------------------


def test_a_parameter_named_like_a_local_of_the_bindings_gets_its_argument(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """The bodies call the C function with locals that start with two underscores (``__result``,
    ``__object``): a parameter of the header that is called so has another name in Rust."""
    case = adhoc_case(
        tmp_path / "case",
        "calc",
        "#include <string>\nnamespace calc {\nint a(int __result, int __object);\n"
        "int b(int x, int __result);\n"
        "std::string c(const char* __c_x, const std::string& __text);\n}\n",
        "namespace calc {\n"
        "int a(int __result, int __object) { return __result * 10 + __object; }\n"
        "int b(int x, int __result) { return x * 10 + __result; }\n"
        "std::string c(const char* __c_x, const std::string& __text) {\n"
        "    return std::string(__c_x) + __text;\n}\n}\n",
    )
    library, output = build(case, toolchain, tmp_path / "build")
    program = """\
fn main() -> calc::Result<()> {
    assert_eq!(calc::calc::a(7, 3)?, 73);
    assert_eq!(calc::calc::b(7, 3)?, 73);
    assert_eq!(calc::calc::c("ab", "cd")?, "abcd");
    println!("round trip OK");
    Ok(())
}
"""
    executable = runner_rust.build_program(case, library, output, "names", program)
    completed = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        check=False,
        env=runner_rust.environment(case, output, library),
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "round trip OK" in completed.stdout


def test_a_static_library_that_is_rebuilt_is_linked_again(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """A static library is part of the crate that links it: cargo does not see that the file
    changed unless the build script says so, and the program would keep the old C++ code."""
    if toolchain.kind != "gnu" or not sys.platform.startswith("linux") or not shutil.which("ar"):
        pytest.skip("needs GCC or Clang on Linux, and ar")
    case = adhoc_case(
        tmp_path / "case",
        "calc",
        "namespace calc { int g(int x); }\n",
        "namespace calc { int g(int x) { return x * 2; } }\n",
    )
    _, output = build(case, toolchain, tmp_path / "build")
    program = """\
fn main() -> calc::Result<()> {
    println!("{}", calc::calc::g(21)?);
    Ok(())
}
"""
    results = []
    for factor in (2, 3):
        implementation = case.directory / "impl" / "calc.cpp"
        implementation.write_text(
            f'#include "calc.h"\nnamespace calc {{ int g(int x) {{ return x * {factor}; }} }}\n',
            encoding="utf-8",
        )
        archive = _archive(toolchain, case, output, tmp_path / "static")
        executable = runner_rust.build_program(
            case,
            archive,
            output,
            f"static_{factor}",
            program,
            variables={"CALC_LIB_KIND": "static"},
        )
        clean = {key: value for key, value in os.environ.items() if key != "LD_LIBRARY_PATH"}
        completed = subprocess.run(
            [str(executable)], capture_output=True, text=True, check=False, env=clean, timeout=600
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        results.append(completed.stdout.strip())
    assert results == ["42", "63"]
