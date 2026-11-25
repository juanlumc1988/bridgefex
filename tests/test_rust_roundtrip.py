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
    # What C++ calls when the process ends, after main has returned (a stored closure that a
    # static destructor runs): the check cannot see its own output.
    if case.name == "callbacks":
        assert "at exit: Level::ERROR exit" in completed.stdout, completed.stdout


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
    "containers": """\
#![allow(unused)]
use cont::vectors::*;

fn main() -> cont::Result<()> {
""",
    "spans": """\
#![allow(unused)]
use spn::views::*;

fn main() -> spn::Result<()> {
""",
    "callbacks": """\
#![allow(unused)]
use cbx::events::*;
use std::sync::atomic::{AtomicI32, Ordering};

fn main() -> cbx::Result<()> {
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
        "fn send<T: Send>(_: T) {} let mut node = Node::new(1)?; send(&mut *node);",
        "fn send<T>(_: T) {} let mut node = Node::new(1)?; send(&mut *node);",
        "E0277",
        id="a-mutable-reference-is-not-send",
    ),
    pytest.param(
        "fn send<T: Send>(_: T) {} let node = Node::new(1)?; send(&*node);",
        "fn send<T>(_: T) {} let node = Node::new(1)?; send(&*node);",
        "E0277",
        id="a-shared-reference-is-not-send",
    ),
    pytest.param(
        "fn share<T: Sync>(_: &T) {} let node = Node::new(1)?; share(&*node);",
        "fn share<T>(_: &T) {} let node = Node::new(1)?; share(&*node);",
        "E0277",
        id="a-class-is-not-sync",
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


REFUSED_CONTAINERS = [
    pytest.param(
        "twice(&[1.0, 2.0])?;",
        "twice(&[1.0, 2.0, 3.0, 4.0])?;",
        "E0308",
        id="an-array-has-the-length-of-its-type",
    ),
    pytest.param(
        'let words: Vec<String> = vec!["a".to_string()]; total_size(&words)?;',
        'let words: Vec<&str> = vec!["a"]; total_size(&words)?;',
        "E0308",
        id="strings-go-in-as-str",
    ),
    pytest.param(
        "rotate(&[1u8])?;",
        "rotate(&[Color::GREEN])?;",
        "E0308",
        id="an-enum-goes-in-as-its-type",
    ),
]

REFUSED_SPANS = [
    pytest.param(
        "let first = [1, 2, 3]; let mut ledger = Ledger::new(&first)?;",
        "let first = [1, 2, 3]; let mut ledger = unsafe { Ledger::new(&first)? };",
        "E0133",
        id="a-span-that-c-plus-plus-keeps-is-unsafe",
    ),
    pytest.param(
        "let first = [1, 2, 3]; let mut ledger = unsafe { Ledger::new(&first)? }; "
        "ledger.add(&[4, 5])?;",
        "let first = [1, 2, 3]; let mut ledger = unsafe { Ledger::new(&first)? }; "
        "ledger.add_copy(&[4, 5])?;",
        "E0133",
        id="a-method-that-keeps-a-span-is-unsafe",
    ),
]

REFUSED_CALLBACKS = [
    pytest.param(
        "let shared = std::rc::Rc::new(1); sum(3, move |i| i + *shared)?;",
        "let shared = std::sync::Arc::new(1); sum(3, move |i| i + *shared)?;",
        "E0277",
        id="a-closure-is-send",
    ),
    pytest.param(
        "let cell = std::cell::Cell::new(0); sum(3, move |i| { cell.set(i); i })?;",
        "let cell = AtomicI32::new(0); sum(3, move |i| { cell.store(i, Ordering::SeqCst); i })?;",
        "E0277",
        id="a-closure-is-sync",
    ),
    pytest.param(
        "let total = 5; sum(3, |i| i + total)?;",
        "let total = 5; sum(3, move |i| i + total)?;",
        "E0373",
        id="a-closure-does-not-borrow-what-it-captures",
    ),
    pytest.param(
        "let mut count = 0; sum(3, move |i| { count += 1; i + count })?;",
        "let count = AtomicI32::new(0); "
        "sum(3, move |i| { count.fetch_add(1, Ordering::SeqCst); i })?;",
        "E0594",
        id="a-closure-is-fn-not-fnmut",
    ),
    pytest.param(
        "is_empty(|| {})?;",
        "is_empty(Some(|| {}))?;",
        "E0308",
        id="a-nullable-callback-is-an-option",
    ),
    pytest.param(
        "sum(3, |i: i64| i)?;",
        "sum(3, |i: i32| i)?;",
        "E0631",
        id="the-closure-has-the-types-of-the-function",
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
    [
        *_for("ownership", REFUSED),
        *_for("inherit", REFUSED_INHERIT),
        *_for("containers", REFUSED_CONTAINERS),
        *_for("callbacks", REFUSED_CALLBACKS),
        *_for("spans", REFUSED_SPANS),
    ],
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


def _run(case: Case, library: Path, output: Path, name: str, program: str) -> str:
    """The output of ``program``, built against the crate of ``case`` and run with ``library``."""
    executable = runner_rust.build_program(case, library, output, name, program)
    completed = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        check=False,
        env=runner_rust.environment(case, output, library),
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def test_an_enum_without_a_fixed_type_is_checked_in_rust_before_the_call(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """A value that no enumerator's bits can make is refused: below and above the range."""
    case = adhoc_case(
        tmp_path / "case",
        "calc",
        "enum Mode { Off, On, Auto = 5 };\nnamespace calc { int f(Mode m); }\n",
        "namespace calc { int f(Mode m) { return static_cast<int>(m); } }\n",
    )
    library, output = build(case, toolchain, tmp_path / "build")
    program = """\
use calc::{ErrorKind, Result};
use calc::calc::{f, Mode};

fn main() -> Result<()> {
    for value in [0, 1, 5, 7] {
        assert_eq!(f(Mode(value))?, value, "{value} is a value of the enum");
    }
    for value in [-1, 8, i32::MAX, i32::MIN] {
        let error = f(Mode(value)).expect_err("a value out of the range");
        assert_eq!(error.kind(), ErrorKind::InvalidArgument);
        assert!(error.message().contains("is not a value of the enum"), "{error}");
        assert!(error.message().contains("0..7"), "{error}");
    }
    println!("checked");
    Ok(())
}
"""
    assert "checked" in _run(case, library, output, "enum_range", program)


def test_a_library_that_is_not_the_one_of_the_crate_is_refused_on_the_first_call(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """The fingerprint of the API is compared: the same C names with other types (a function that
    took an ``int``) are not the library that the crate was generated for."""
    old = adhoc_case(
        tmp_path / "old",
        "calc",
        "namespace calc { int f(int x); }\n",
        "namespace calc { int f(int x) { return x + 1; } }\n",
    )
    new = adhoc_case(
        tmp_path / "new",
        "calc",
        "namespace calc { long long f(long long x); }\n",
        "namespace calc { long long f(long long x) { return x + 1; } }\n",
    )
    library, output = build(old, toolchain, tmp_path / "build_old")
    other, _ = build(new, toolchain, tmp_path / "build_new")
    program = """\
use calc::ErrorKind;

fn main() {
    for _ in 0..2 {
        let error = calc::calc::f(1).expect_err("the library of another API");
        assert_eq!(error.kind(), ErrorKind::StaleLibrary, "{error}");
        assert!(error.message().contains("fingerprint"), "{error}");
    }
    println!("refused");
}
"""
    assert "refused" in _run(old, other, output, "stale", program)
    # The library that the crate was generated for is accepted.
    good = """\
fn main() -> calc::Result<()> {
    assert_eq!(calc::calc::f(1)?, 2);
    println!("accepted");
    Ok(())
}
"""
    assert "accepted" in _run(old, library, output, "fresh", good)


def test_an_object_that_may_be_null_is_an_option_and_a_default_is_what_cpp_makes(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """``Option<Owned<T>>`` for a ``std::unique_ptr`` or an annotated ``transfer`` pointer that
    may be NULL: ``None`` is NULL, and ``Some`` is given up (destroyed once); and ``defaults()``
    is the value that C++ makes of ``T{}`` (not zeros)."""
    case = adhoc_case(
        tmp_path / "case",
        "calc",
        "#include <memory>\nstruct Config { int level = 7; double scale = 2.5; };\n"
        "namespace calc {\n"
        "class Item { public: explicit Item(int id); ~Item(); int id() const; "
        "static int live(); private: int id_; };\n"
        "int eat(std::unique_ptr<Item> item);\nint take(Item* item);\n}\n",
        "namespace calc {\nstatic int g_live = 0;\n"
        "Item::Item(int id) : id_(id) { ++g_live; }\nItem::~Item() { --g_live; }\n"
        "int Item::id() const { return id_; }\nint Item::live() { return g_live; }\n"
        "int eat(std::unique_ptr<Item> item) { return item ? item->id() : -1; }\n"
        "int take(Item* item) {\n    std::unique_ptr<Item> own(item);\n"
        "    return own ? own->id() : -1;\n}\n}\n",
        annotations='[calc_eat]\nitem = "nullable"\n[calc_take]\nitem = ["transfer", "nullable"]\n',
    )
    library, output = build(case, toolchain, tmp_path / "build")
    program = """\
use calc::calc::{eat, take, Config, Item};

fn main() -> calc::Result<()> {
    assert_eq!(eat(Some(Item::new(5)?))?, 5);
    assert_eq!(Item::live()?, 0, "the object that was given is destroyed once");
    assert_eq!(eat(None)?, -1);
    assert_eq!(take(Some(Item::new(6)?))?, 6);
    assert_eq!(Item::live()?, 0);
    assert_eq!(take(None)?, -1);
    let config = Config::defaults()?;
    assert_eq!((config.level, config.scale), (7, 2.5));
    println!("checked");
    Ok(())
}
"""
    assert "checked" in _run(case, library, output, "options", program)


def test_what_cpp_keeps_is_passed_as_the_caller_gave_it(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """A ``const char*`` that C++ keeps is the caller's ``&CStr`` itself (the pointer that the
    library stores is the one of the text), a ``std::string_view`` the caller's ``&str``, and the
    guard that keeps ``this`` works as long as ``self`` is there."""
    case = adhoc_case(
        tmp_path / "case",
        "calc",
        "#include <cstdint>\n#include <memory>\n#include <string_view>\nnamespace calc {\n"
        "class Guard { public: Guard(int level); ~Guard(); int level() const; private: int l_; };\n"
        "class Mutex { public: Mutex(); ~Mutex(); std::unique_ptr<Guard> lock(); "
        "private: int locks_; };\n"
        "class Label { public: Label(); ~Label(); void setRaw(const char* text); "
        "void setView(std::string_view text); std::uint64_t rawAddress() const; "
        "std::uint64_t viewAddress() const; int viewLength() const; "
        "private: const char* raw_; std::string_view view_; };\n}\n",
        "namespace calc {\n"
        "Guard::Guard(int level) : l_(level) {}\nGuard::~Guard() {}\n"
        "int Guard::level() const { return l_; }\n"
        "Mutex::Mutex() : locks_(0) {}\nMutex::~Mutex() {}\n"
        "std::unique_ptr<Guard> Mutex::lock() { return std::make_unique<Guard>(++locks_); }\n"
        "Label::Label() : raw_(nullptr) {}\nLabel::~Label() {}\n"
        "void Label::setRaw(const char* text) { raw_ = text; }\n"
        "void Label::setView(std::string_view text) { view_ = text; }\n"
        "std::uint64_t Label::rawAddress() const {\n"
        "    return static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(raw_));\n}\n"
        "std::uint64_t Label::viewAddress() const {\n"
        "    const char* data = view_.data();\n"
        "    return static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(data));\n}\n"
        "int Label::viewLength() const { return static_cast<int>(view_.size()); }\n}\n",
        annotations=(
            '[calc_Mutex_lock]\nthis = "retain"\n'
            '[calc_Label_setRaw]\ntext = "retain"\n[calc_Label_setView]\ntext = "retain"\n'
        ),
    )
    library, output = build(case, toolchain, tmp_path / "build")
    program = """\
use calc::calc::{Label, Mutex};
use std::ffi::CStr;

fn main() -> calc::Result<()> {
    let mut label = Label::new()?;
    let raw = CStr::from_bytes_with_nul(b"kept\\0").unwrap();
    let view = "a longer text that C++ views";
    unsafe {
        label.set_raw(raw)?;
        label.set_view(view)?;
    }
    assert_eq!(label.raw_address()?, raw.as_ptr() as usize as u64);
    assert_eq!(label.view_address()?, view.as_ptr() as usize as u64);
    assert_eq!(label.view_length()?, view.len() as i32);

    let mut mutex = Mutex::new()?;
    let first = unsafe { mutex.lock()? }.expect("a guard");
    let second = unsafe { mutex.lock()? }.expect("a guard");
    assert_eq!((first.level()?, second.level()?), (1, 2));
    drop((first, second));
    println!("checked");
    Ok(())
}
"""
    assert "checked" in _run(case, library, output, "kept", program)


def test_closures_are_released_once_whatever_happens_to_the_call(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """C++ releases every closure that a call was given, once: when the call fails before the C
    function runs (a text with a NUL), when a closure panics (the other one is released too), and
    when the call has several closures. A call that gives results (a vector) beside closures."""
    case = adhoc_case(
        tmp_path / "case",
        "calc",
        "#include <cstring>\n#include <functional>\n#include <memory>\n#include <stdexcept>\n"
        "#include <vector>\nnamespace calc {\n"
        "class Thing { public: explicit Thing(std::function<int()> f); ~Thing();\n"
        "    static std::unique_ptr<Thing> make(std::function<int()> f);\n"
        "    static int live(); private: static int live_; };\n"
        "int pair(std::function<int()> first, std::function<int()> second);\n"
        "int then_fail(std::function<int()> f);\n"
        "int text(const char* name, std::function<int(int)> f);\n"
        "std::vector<int> each(const std::vector<int>& values, std::function<int(int)> f);\n}\n",
        "namespace calc {\n"
        "int Thing::live_ = 0;\nint Thing::live() { return live_; }\n"
        "Thing::Thing(std::function<int()> f) { ++live_; f(); }\nThing::~Thing() { --live_; }\n"
        "std::unique_ptr<Thing> Thing::make(std::function<int()> f) {\n"
        "    return std::make_unique<Thing>(std::move(f));\n}\n"
        "int then_fail(std::function<int()> f) {\n    f();\n"
        '    throw std::runtime_error("c++");\n}\n'
        "int pair(std::function<int()> first, std::function<int()> second) {\n"
        "    return first() * 10 + second();\n}\n"
        "int text(const char* name, std::function<int(int)> f) {\n"
        "    return f(static_cast<int>(std::strlen(name)));\n}\n"
        "std::vector<int> each(const std::vector<int>& values, std::function<int(int)> f) {\n"
        "    std::vector<int> result;\n"
        "    for (int value : values) {\n        result.push_back(f(value));\n    }\n"
        "    return result;\n}\n}\n",
    )
    library, output = build(case, toolchain, tmp_path / "build")
    program = """\
use calc::calc::{each, pair, text, then_fail, Thing};
use calc::ErrorKind;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

struct Held(Arc<AtomicUsize>);

impl Held {
    fn new(live: &Arc<AtomicUsize>) -> Held {
        live.fetch_add(1, Ordering::SeqCst);
        Held(Arc::clone(live))
    }
}

impl Drop for Held {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

fn main() -> calc::Result<()> {
    std::panic::set_hook(Box::new(|_| {}));
    let live = Arc::new(AtomicUsize::new(0));
    let (a, b) = (Held::new(&live), Held::new(&live));
    assert_eq!(pair(move || { let _ = &a; 1 }, move || { let _ = &b; 2 })?, 12);
    assert_eq!(live.load(Ordering::SeqCst), 0, "both closures were released");

    // The second closure panics: the call raises it, and both closures are gone.
    let (a, b) = (Held::new(&live), Held::new(&live));
    let raised = catch_unwind(AssertUnwindSafe(|| {
        pair(move || { let _ = &a; 1 }, move || { let _ = &b; panic!("two") })
    }));
    assert_eq!(*raised.expect_err("the panic").downcast_ref::<&str>().unwrap(), "two");
    assert_eq!(live.load(Ordering::SeqCst), 0, "both closures were released");

    // The call fails before the C function runs: the closure never ran, and it is dropped once.
    let ran = Arc::new(AtomicUsize::new(0));
    let (held, seen) = (Held::new(&live), Arc::clone(&ran));
    let error = text("a\\0b", move |_| { let _ = &held; seen.fetch_add(1, Ordering::SeqCst); 0 })
        .expect_err("the text has a NUL");
    assert_eq!(error.kind(), ErrorKind::InvalidArgument);
    assert_eq!((ran.load(Ordering::SeqCst), live.load(Ordering::SeqCst)), (0, 0));
    let held = Held::new(&live);
    assert_eq!(text("abc", move |length| { let _ = &held; length * 2 })?, 6);
    assert_eq!(live.load(Ordering::SeqCst), 0);

    // C++ fails after it ran a closure that panicked: the panic is what the call raises, as the
    // documentation says; without a panic the failure of C++ is the error.
    let (held, other) = (Held::new(&live), Held::new(&live));
    let raised = catch_unwind(AssertUnwindSafe(|| {
        then_fail(move || { let _ = &held; panic!("first") })
    }));
    assert_eq!(*raised.expect_err("a panic").downcast_ref::<&str>().unwrap(), "first");
    let error = then_fail(move || { let _ = &other; 1 }).expect_err("C++ throws");
    assert_eq!(error.kind(), ErrorKind::Exception);
    assert_eq!(live.load(Ordering::SeqCst), 0);

    // The object that C++ made while a closure panicked is not lost: the call that raises the
    // panic destroys it first (a constructor, and a result in a std::unique_ptr).
    let raised = catch_unwind(AssertUnwindSafe(|| Thing::new(|| panic!("constructor"))));
    assert!(raised.is_err());
    assert_eq!(Thing::live()?, 0, "the object of a constructor whose closure panicked");
    let raised = catch_unwind(AssertUnwindSafe(|| Thing::make(|| panic!("factory"))));
    assert!(raised.is_err());
    assert_eq!(Thing::live()?, 0, "the object of a function whose closure panicked");
    let thing = Thing::new(|| 1)?;
    assert_eq!(Thing::live()?, 1);
    drop(thing);
    assert_eq!(Thing::live()?, 0);

    // A vector in and a vector out, beside a closure.
    assert_eq!(each(&[1, 2, 3], |value| value * value)?, [1, 4, 9]);
    assert!(each(&[], |value| value)?.is_empty());
    println!("checked");
    Ok(())
}
"""
    assert "checked" in _run(case, library, output, "released", program)


def test_a_closure_gets_empty_texts_and_text_that_is_not_utf8(
    toolchain: Toolchain, tmp_path: Path
) -> None:
    """A ``std::string_view`` that C++ makes empty has a NULL pointer (a slice of it would be
    undefined behavior), a ``const char*`` may be NULL, and text that is not UTF-8 is converted."""
    case = adhoc_case(
        tmp_path / "case",
        "calc",
        "#include <functional>\n#include <string>\n#include <string_view>\n#include <vector>\n"
        "namespace calc {\n"
        "void texts(std::function<void(std::string_view, const std::string&, const char*)> f);\n"
        "std::vector<std::string> blobs();\n}\n",
        "namespace calc {\n"
        "std::vector<std::string> blobs() {\n"
        '    return {std::string("\\xff\\xfe", 2), std::string("a\\0b", 3), "",\n'
        '            std::string("\\xe2", 1)};\n}\n'
        "void texts(std::function<void(std::string_view, const std::string&, const char*)> f) {\n"
        '    f(std::string_view(), std::string(), nullptr);\n    f("", "", "");\n'
        '    f(std::string_view("\\xff\\xfe", 2), std::string("a\\xc3", 2), "\\xe2\\x82");\n}\n}\n',
    )
    library, output = build(case, toolchain, tmp_path / "build")
    program = """\
use calc::calc::{blobs, texts};
use std::sync::{Arc, Mutex};

fn main() -> calc::Result<()> {
    let seen: Arc<Mutex<Vec<(String, String, Option<String>)>>> = Arc::default();
    let sink = Arc::clone(&seen);
    texts(move |view, text, c_text| {
        sink.lock().unwrap().push((view.to_string(), text.to_string(), c_text.map(str::to_string)));
    })?;
    let seen = seen.lock().unwrap();
    assert_eq!(seen[0], (String::new(), String::new(), None));
    assert_eq!(seen[1], (String::new(), String::new(), Some(String::new())));
    let bad = |text: &str| text.to_string();
    assert_eq!(
        seen[2],
        (bad("\\u{fffd}\\u{fffd}"), bad("a\\u{fffd}"), Some(bad("\\u{fffd}")))
    );
    // Text that is not UTF-8 comes back converted, one by one; a NUL is text.
    let bad = |text: &str| text.to_string();
    assert_eq!(
        blobs()?,
        [bad("\\u{fffd}\\u{fffd}"), bad("a\\0b"), bad(""), bad("\\u{fffd}")]
    );
    println!("checked");
    Ok(())
}
"""
    assert "checked" in _run(case, library, output, "texts", program)
