# SPDX-License-Identifier: Apache-2.0
"""Python directors on headers made to break the generator: names that the generated code
uses, methods that a macro declares at one place. The library is built and the Python runs
against it (the generated code is also imported when it is verified, but a wrong name only
fails when a method is called)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import bridgefex
from tests.support.toolchain import Toolchain

pytestmark = pytest.mark.usefixtures("libclang_loaded")

PRELUDE = "#pragma once\n#include <cstdint>\n#include <string>\n"


def run_script(
    tmp_path: Path,
    toolchain: Toolchain,
    header: str,
    implementation: str,
    directors: list[str],
    script: str,
) -> subprocess.CompletedProcess[str]:
    """Generate the bindings of ``header`` (its classes ask for ``directors``), build it with
    ``implementation`` and run ``script`` (Python, with the package ``mod``)."""
    sources = tmp_path / "src"
    sources.mkdir()
    (sources / "api.h").write_text(PRELUDE + textwrap.dedent(header), encoding="utf-8")
    (sources / "api.cpp").write_text(
        '#include "api.h"\n' + textwrap.dedent(implementation), encoding="utf-8"
    )
    annotations = sources / "annotations.toml"
    names = ", ".join(f'"{name}"' for name in directors)
    annotations.write_bytes(f"directors = [{names}]\n".encode())
    result = bridgefex.generate(
        [sources / "api.h"], bridgefex.Options("mod", annotations=(annotations,))
    )
    output = tmp_path / "out"
    bridgefex.write_files(result.files, output)
    library = tmp_path / toolchain.shared_library_name("mod")
    toolchain.build_shared_library(
        [*sorted((output / "c").glob("*.cpp")), sources / "api.cpp"],
        [output / "c", sources],
        library,
        "c++17",
    ).check()
    environment = {**os.environ, "PYTHONPATH": str(output / "python"), "MOD_LIBRARY": str(library)}
    return subprocess.run(
        [sys.executable, "-X", "dev", "-W", "error", "-c", textwrap.dedent(script)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_names_that_the_generated_code_uses_can_be_names_of_the_header(
    tmp_path: Path, toolchain: Toolchain
) -> None:
    """A parameter called ``type`` or ``super``, and functions called so, in the same module
    as the classes (the generated code names the builtins through ``_builtins``)."""
    completed = run_script(
        tmp_path,
        toolchain,
        """
        namespace n {
        class A { public: explicit A(std::int32_t type); virtual ~A();
          virtual std::int32_t f(std::int32_t super) const;
          virtual std::int32_t g(std::int32_t type) const;
          std::int32_t call_f(std::int32_t x) const; std::int32_t seed() const;
        private: std::int32_t seed_; };
        class B : public A { public: B(std::int32_t type, std::int32_t RuntimeError);
          std::int32_t f(std::int32_t super) const override; };
        std::int32_t type(std::int32_t kind);
        std::int32_t super(std::int32_t kind);
        }
        """,
        """
        namespace n {
        A::A(std::int32_t type) : seed_(type) {}
        A::~A() {}
        std::int32_t A::f(std::int32_t super) const { return super + seed_; }
        std::int32_t A::g(std::int32_t type) const { return type * 2; }
        std::int32_t A::call_f(std::int32_t x) const { return f(x); }
        std::int32_t A::seed() const { return seed_; }
        B::B(std::int32_t type, std::int32_t) : A(type) {}
        std::int32_t B::f(std::int32_t super) const { return super * 10 + seed(); }
        std::int32_t type(std::int32_t kind) { return kind + 1; }
        std::int32_t super(std::int32_t kind) { return kind + 2; }
        }
        """,
        ["n_A", "n_B"],
        """
        from mod import api

        class X(api.A):
            def f(self, super):
                return 1000 + super
        class Y(api.B):
            def g(self, type):  # inherited from A: B repeats its wrapper
                return super().g(type) + 1

        assert api.A(3).call_f(1) == 4
        assert X(3).call_f(1) == 1001
        assert api.B(3, 4).call_f(1) == 13
        assert Y(3, 4).call_f(1) == 13 and Y(3, 4).g(5) == 11
        assert api.type(1) == 2 and api.super(1) == 3
        print("ok")
        """,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "ok"


def test_methods_that_a_macro_declares_at_one_place_keep_their_own_wrappers(
    tmp_path: Path, toolchain: Toolchain
) -> None:
    """Several declarations at one location (a macro), virtual or not: a location does not
    tell them apart, the declaration does."""
    completed = run_script(
        tmp_path,
        toolchain,
        """
        #define PROP(name) virtual std::int32_t on_##name(std::int32_t x) const; \\
                           std::int32_t get_##name() const;
        #define CTORS(T) T(); explicit T(std::int32_t a);
        #define PAIR(a, b) virtual std::int32_t a() const; virtual std::int32_t b() const;
        namespace n {
        class K { public: CTORS(K) virtual ~K();
          PROP(a) PROP(b) PAIR(alpha, beta)
          std::int32_t call_on_a(std::int32_t x) const;
          std::int32_t call_alpha() const; std::int32_t call_beta() const; };
        }
        """,
        """
        namespace n {
        K::K() {}
        K::K(std::int32_t) {}
        K::~K() {}
        std::int32_t K::on_a(std::int32_t x) const { return x; }
        std::int32_t K::get_a() const { return 7; }
        std::int32_t K::on_b(std::int32_t x) const { return x + 100; }
        std::int32_t K::get_b() const { return 8; }
        std::int32_t K::alpha() const { return 1; }
        std::int32_t K::beta() const { return 2; }
        std::int32_t K::call_on_a(std::int32_t x) const { return on_a(x); }
        std::int32_t K::call_alpha() const { return alpha(); }
        std::int32_t K::call_beta() const { return beta(); }
        }
        """,
        ["n_K"],
        """
        from mod import api

        class X(api.K):
            def on_a(self, x):
                return 1000 + x
            def beta(self):
                return 20

        for make in (X.create_void, lambda: X.create_int32(3)):
            x = make()
            assert x.call_on_a(1) == 1001, "the override of on_a"
            assert x.get_a() == 7 and x.get_b() == 8, "the wrappers that are not virtual"
            assert x.call_alpha() == 1 and x.call_beta() == 20
            x.close()
        print("ok")
        """,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "ok"


def test_a_module_without_directors_keeps_the_parameter_names_of_its_header(
    tmp_path: Path,
) -> None:
    """The names that the director constructors use for their locals are only reserved in a
    module that has directors: any other module is generated as it always was."""
    header = tmp_path / "api.h"
    header.write_text(
        PRELUDE + "namespace n { class K { public: K(); virtual ~K();"
        " virtual std::int32_t f(std::int32_t _found, std::int32_t _vtable) const; }; }\n",
        encoding="utf-8",
    )
    plain = bridgefex.generate([header], bridgefex.Options("mod"))
    assert "int32_t _found, int32_t _vtable" in plain.files["c/api_c.h"]
    annotations = tmp_path / "annotations.toml"
    annotations.write_bytes(b'directors = ["n_K"]\n')
    with_director = bridgefex.generate(
        [header], bridgefex.Options("mod", annotations=(annotations,))
    )
    assert "int32_t _found_, int32_t _vtable_" in with_director.files["c/api_c.h"]
