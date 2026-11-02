# SPDX-License-Identifier: Apache-2.0
"""Directors in the C layer: what a class needs to have one, what the plan makes of its
virtual methods, and the names and checks of the generated functions."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import bridgefex
from bridgefex.errors import BridgefexError

pytestmark = pytest.mark.usefixtures("libclang_loaded")

PRELUDE = "#pragma once\n#include <cstdint>\n#include <string>\n#include <vector>\n"


def generate(directory: Path, code: str, *directors: str, verify: bool = True) -> bridgefex.Result:
    header = directory / "api.h"
    header.write_text(PRELUDE + code + "\n", encoding="utf-8")
    annotations = directory / "annotations.toml"
    names = ", ".join(f'"{name}"' for name in directors)
    annotations.write_bytes(f"directors = [{names}]\n".encode())
    options = bridgefex.Options("mod", annotations=(annotations,), verify=verify)
    return bridgefex.generate([header], options)


def error_of(directory: Path, code: str, *directors: str) -> str:
    with pytest.raises(BridgefexError) as raised:
        generate(directory, code, *directors)
    return str(raised.value)


def header_of(result: bridgefex.Result) -> str:
    return result.files["c/api_c.h"]


SHAPE = """
namespace n {
class Shape {
public:
    Shape();
    virtual ~Shape();
    virtual std::int32_t sides() const;
};
}
"""


def test_a_class_has_a_director_only_when_the_file_asks_for_it(tmp_path: Path) -> None:
    asked = generate(tmp_path, SHAPE, "n_Shape")
    assert "n_Shape_director_create" in header_of(asked)
    assert "string_sink" in asked.files["c/mod_runtime.h"]
    # The same header, without the file: nothing changes for who does not ask.
    plain = bridgefex.generate([tmp_path / "api.h"], bridgefex.Options("mod"))
    assert "director" not in plain.files["c/api_c.h"]
    assert "string_sink" not in plain.files["c/mod_runtime.h"]


def test_the_director_api(tmp_path: Path) -> None:
    text = header_of(generate(tmp_path, SHAPE, "n_Shape"))
    assert "typedef struct n_Shape_director n_Shape_director;" in text
    assert re.search(
        r"typedef struct n_Shape_vtable \{\n    void\* user_data;\n    void \(\*release\)", text
    )
    assert "void (*sides)(void*, int32_t*);" in text
    assert "n_Shape_director_create_void" not in text  # one constructor: no suffix
    create = "mod_status n_Shape_director_create(n_Shape_director** out_director, "
    assert create + "const n_Shape_vtable* vtable);" in text
    assert "n_Shape* n_Shape_director_object(n_Shape_director* director);" in text
    assert "n_Shape_director_base_sides(n_Shape_director* director, int32_t* out_result);" in text


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "namespace n { class Shape final { public: Shape(); virtual ~Shape();"
            " virtual int f(); }; }",
            "cannot have a director: it is final",
        ),
        (
            "namespace n { class Shape { public: Shape(); virtual ~Shape(); }; }",
            "no public or protected virtual method to override",
        ),
        (
            "namespace n { class Shape { public: Shape(); virtual ~Shape();"
            " virtual void f(const std::vector<int>& v) = 0; }; }",
            "the pure virtual method 'Shape::f' cannot be overridden from another language",
        ),
        (
            "namespace n { class Shape { public: Shape(); virtual ~Shape();"
            " virtual const std::string& f() const = 0; }; }",
            "the result is a reference: an override cannot return one",
        ),
        (
            "namespace n { class Shape { public: Shape(); virtual ~Shape();"
            " virtual std::string_view f() const = 0; }; }",
            "return a std::string",
        ),
        (
            "namespace n { class Shape { public: virtual ~Shape(); virtual void f() = 0;"
            " protected: explicit Shape(const std::vector<int>& v); }; }",
            "",
        ),
    ],
)
def test_what_a_class_needs_for_a_director(tmp_path: Path, code: str, message: str) -> None:
    if message:
        text = error_of(tmp_path, code, "n_Shape")
        assert message in text
        assert "annotations.toml:1" in text  # where the file asked for it
    else:
        # Only a constructor that cannot cross: the class still has the ones that can.
        generate(
            tmp_path,
            code.replace("protected: explicit", "public: Shape(); protected: explicit"),
            "n_Shape",
        )


def test_a_class_that_is_not_wrapped(tmp_path: Path) -> None:
    text = error_of(tmp_path, SHAPE, "n_Shpe")
    assert "directors: 'n_Shpe' is not the C type of a wrapped class; did you mean n_Shape?" in text


def test_a_method_that_cannot_be_overridden_keeps_the_class(tmp_path: Path) -> None:
    header = header_of(
        generate(
            tmp_path,
            """
            namespace n { class Shape { public: Shape(); virtual ~Shape();
              virtual std::int32_t a() const;
              virtual std::int32_t b(const std::vector<int>& v) const;
              virtual std::int32_t c() const final; }; }
            """,
            "n_Shape",
        )
    )
    vtable = header.split("n_Shape_vtable {")[1].split("} n_Shape_vtable")[0]
    assert "(*a)" in vtable
    assert "(*b)" not in vtable
    assert "(*c)" not in vtable


def test_protected_virtual_methods_are_overridden_but_not_wrapped(tmp_path: Path) -> None:
    header = header_of(
        generate(
            tmp_path,
            """
            namespace n { class Shape { public: Shape(); virtual ~Shape();
              void run(); protected: virtual void step(std::int32_t n); }; }
            """,
            "n_Shape",
        )
    )
    assert "(*step)(void*, int32_t)" in header
    assert "n_Shape_step(" not in header
    assert "n_Shape_director_base_step(" in header


def test_the_constructors_of_the_class_are_the_ones_of_the_director(tmp_path: Path) -> None:
    header = header_of(
        generate(
            tmp_path,
            """
            namespace n { class Shape { public: Shape(); explicit Shape(std::int32_t n);
              virtual ~Shape(); virtual void f(); protected: Shape(const std::string& s); }; }
            """,
            "n_Shape",
        )
    )
    for suffix in ("void", "int32", "string"):
        assert f"n_Shape_director_create_{suffix}(" in header


def test_an_abstract_class_with_a_protected_constructor(tmp_path: Path) -> None:
    header = header_of(
        generate(
            tmp_path,
            """
            namespace n { class Shape { public: virtual ~Shape(); virtual double area() const = 0;
              protected: Shape(); }; }
            """,
            "n_Shape",
        )
    )
    assert "n_Shape_director_create(" in header
    assert "n_Shape_create(" not in header  # the class itself still has no constructor
    assert "n_Shape_director_base_area" not in header  # a pure method has no implementation


def test_overloaded_virtual_methods_get_a_member_each(tmp_path: Path) -> None:
    header = header_of(
        generate(
            tmp_path,
            """
            namespace n { class Shape { public: Shape(); virtual ~Shape();
              virtual std::int32_t add(std::int32_t a) const;
              virtual double add(double a) const;
              virtual void add(); }; }
            """,
            "n_Shape",
        )
    )
    for member in ("add_int32", "add_double", "add_void"):
        assert f"(*{member})" in header


def test_a_member_called_like_the_fixed_ones_of_the_vtable(tmp_path: Path) -> None:
    header = header_of(
        generate(
            tmp_path,
            """
            namespace n { class Shape { public: Shape(); virtual ~Shape();
              virtual void release(); virtual void user_data(); }; }
            """,
            "n_Shape",
        )
    )
    vtable = header.split("n_Shape_vtable {")[1].split("} n_Shape_vtable")[0]
    assert vtable.count("(*release)") == 1
    assert "(*release_)" in vtable
    assert "(*user_data_)" in vtable


def test_a_derived_director_has_the_inherited_methods(tmp_path: Path) -> None:
    header = header_of(
        generate(
            tmp_path,
            """
            namespace n {
            class A { public: A(); virtual ~A(); virtual std::int32_t a() const; };
            class B : public A { public: B(); std::int32_t a() const override;
              virtual std::int32_t b() const; };
            class C : public B { public: C(); virtual std::int32_t c() const; };
            }
            """,
            "n_C",
        )
    )
    vtable = header.split("n_C_vtable {")[1].split("} n_C_vtable")[0]
    assert [m for m in ("(*a)", "(*b)", "(*c)") if m in vtable] == ["(*a)", "(*b)", "(*c)"]
    assert vtable.count("(*a)") == 1  # the most derived declaration only


def test_the_c_layer_with_directors_does_not_depend_on_the_languages(tmp_path: Path) -> None:
    every = generate(tmp_path, SHAPE, "n_Shape")
    header = tmp_path / "api.h"
    only_c = bridgefex.generate(
        [header],
        bridgefex.Options("mod", annotations=(tmp_path / "annotations.toml",), languages=()),
    )
    assert {p: t for p, t in every.files.items() if p.startswith("c/")} == only_c.files
