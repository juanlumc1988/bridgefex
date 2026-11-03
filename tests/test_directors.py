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


# -- the findings of the review of the first version -------------------------------------


def generate_files(
    directory: Path, files: dict[str, str], *directors: str, std: str = "c++17"
) -> bridgefex.Result:
    headers = []
    for name, code in files.items():
        header = directory / name
        header.write_text(PRELUDE + code + "\n", encoding="utf-8")
        headers.append(header)
    annotations = directory / "annotations.toml"
    names = ", ".join(f'"{name}"' for name in directors)
    annotations.write_bytes(f"directors = [{names}]\n".encode())
    options = bridgefex.Options("mod", std=std, annotations=(annotations,))
    return bridgefex.generate(headers, options)


def vtable_of(result: bridgefex.Result, c_type: str, header: str = "api_c.h") -> str:
    return (
        result.files[f"c/{header}"].split(f"{c_type}_vtable {{")[1].split(f"}} {c_type}_vtable")[0]
    )


@pytest.mark.parametrize(
    ("base", "derived"),
    [
        ("int x", "std::int32_t x"),
        ("unsigned x", "std::uint32_t x"),
        ("short x", "std::int16_t x"),
        ("signed char x", "std::int8_t x"),
        ("unsigned char x", "std::uint8_t x"),
        ("std::size_t x", "unsigned long x"),
        ("const int x", "int y"),
        ("const std::int32_t& x", "const int& y"),
    ],
)
def test_an_override_that_spells_a_type_differently_is_the_same_method(
    tmp_path: Path, base: str, derived: str
) -> None:
    code = f"""
    namespace n {{
    class A {{ public: A(); virtual ~A(); virtual void f({base}); }};
    class B : public A {{ public: B(); void f({derived}) override; }};
    }}
    """
    result = generate(tmp_path, code, "n_B")
    assert vtable_of(result, "n_B").count("(*f)") == 1


def test_what_differs_by_const_is_another_method(tmp_path: Path) -> None:
    # Protected, so that they are not part of the API (which refuses such overloads).
    result = generate(
        tmp_path,
        """
        namespace n { class A { public: A(); virtual ~A(); protected:
          virtual void g(); virtual void g() const; }; }
        """,
        "n_A",
    )
    assert vtable_of(result, "n_A").count("(*g") == 2


def test_a_struct_of_another_header_in_a_constructor(tmp_path: Path) -> None:
    config = "namespace hw { struct Config { std::int32_t baud; std::int32_t parity; }; }"
    driver = (
        '#include "config.h"\nnamespace hw { class Driver { public: explicit Driver(const Config&);'
        " virtual ~Driver(); virtual std::int32_t write(std::int32_t b) = 0; }; }"
    )
    result = generate_files(tmp_path, {"config.h": config, "driver.h": driver}, "hw_Driver")
    assert "hw_Driver_director_create(" in result.files["c/driver_c.h"]
    assert '#include "config_c_internal.hpp"' in result.files["c/driver_c.cpp"]


def test_a_virtual_method_named_like_the_member_that_calls_the_base(tmp_path: Path) -> None:
    result = generate(
        tmp_path,
        """
        namespace n { class A { public: A(); virtual ~A();
          virtual std::int32_t x(); virtual std::int32_t base_x(); }; }
        """,
        "n_A",
    )
    source = result.files["c/api_c.cpp"]
    assert "bridgefex_base_x(Args&&... args)" in source
    assert "bridgefex_base_base_x(Args&&... args)" in source
    assert " base_x(Args&&" not in source


def test_a_private_override_of_a_public_virtual_method_is_left_to_the_class(
    tmp_path: Path,
) -> None:
    result = generate(
        tmp_path,
        """
        namespace n {
        class A { public: A(); virtual ~A(); virtual std::int32_t f() const;
                                              virtual std::int32_t g() const;
                                              virtual std::int32_t h() const; };
        class B : public A { public: B(); private: std::int32_t f() const override;
                                         std::int32_t g() const final; };
        }
        """,
        "n_B",
    )
    vtable = vtable_of(result, "n_B")
    assert "(*f)" not in vtable
    assert "(*g)" not in vtable
    assert "(*h)" in vtable


def test_a_private_pure_virtual_method_has_to_be_implemented(tmp_path: Path) -> None:
    result = generate(
        tmp_path,
        """
        namespace n { class A { public: A(); virtual ~A(); virtual std::int32_t f() const;
                                private: virtual std::int32_t g(std::int32_t x) const = 0; }; }
        """,
        "n_A",
    )
    assert "(*g)(void*, int32_t, int32_t*)" in vtable_of(result, "n_A")
    assert "vtable->g == nullptr" in result.files["c/api_c.cpp"]
    assert "n_A_director_base_g" not in result.files["c/api_c.h"]  # nothing to call


@pytest.mark.parametrize(
    "declaration",
    ["virtual int v() volatile;", "virtual const std::string v() const;"],
)
def test_a_virtual_method_that_a_generated_class_cannot_override_keeps_the_class(
    tmp_path: Path, declaration: str
) -> None:
    result = generate(
        tmp_path,
        "namespace n { class A { public: A(); virtual ~A(); virtual int ok(); "
        + declaration
        + " }; }",
        "n_A",
    )
    vtable = vtable_of(result, "n_A")
    assert "(*ok)" in vtable
    assert "(*v)" not in vtable


def test_a_dynamic_exception_specification_keeps_the_class(tmp_path: Path) -> None:
    result = generate_files(
        tmp_path,
        {
            "api.h": "namespace n { class A { public: A(); virtual ~A(); virtual int ok();"
            " virtual int e() const throw(int); }; }"
        },
        "n_A",
        std="c++14",
    )
    vtable = vtable_of(result, "n_A")
    assert "(*ok)" in vtable
    assert "(*e)" not in vtable


def test_a_pure_virtual_method_that_cannot_be_overridden_says_why(tmp_path: Path) -> None:
    text = error_of(
        tmp_path,
        "namespace n { class A { public: A(); virtual ~A(); virtual int v() volatile = 0; }; }",
        "n_A",
    )
    assert "the pure virtual method 'A::v' cannot be overridden from another language" in text
    assert "volatile" in text


def test_a_pure_virtual_overload_that_the_model_cannot_read_is_reported(tmp_path: Path) -> None:
    text = error_of(
        tmp_path,
        """
        #include <map>
        namespace n { class A { public: A(); virtual ~A(); protected:
          virtual void f(const std::map<int, int>& m);
          virtual void f(const std::map<int, long>& m) = 0; }; }
        """,
        "n_A",
    )
    assert "the pure virtual method 'A::f' cannot be overridden from another language" in text
    assert "std::map" in text


def test_the_same_virtual_method_in_two_unrelated_bases_is_left_to_the_class(
    tmp_path: Path,
) -> None:
    result = generate(
        tmp_path,
        """
        namespace n {
        class X { public: X(); virtual ~X(); virtual std::int32_t f() const; };
        class Y { public: Y(); virtual ~Y(); virtual std::int32_t f() const;
                                              virtual std::int32_t k() const; };
        class Z : public X, public Y { public: Z();
          std::int32_t k() const override; };
        }
        """,
        "n_Z",
    )
    vtable = vtable_of(result, "n_Z")
    assert "(*k)" in vtable
    assert "(*f)" not in vtable  # ambiguous: Z::f() cannot be named


def test_the_names_of_the_runtime_of_a_module_with_directors_are_reserved(tmp_path: Path) -> None:
    text = error_of(
        tmp_path,
        """
        namespace mod { class string_sink { public: string_sink(); int f() const; }; }
        namespace n { class A { public: A(); virtual ~A(); virtual int f(); }; }
        """,
        "n_A",
    )
    assert "mod_string_sink" in text
    assert "clashes" in text


def test_the_parameters_of_the_functions_of_a_director_avoid_its_own(tmp_path: Path) -> None:
    result = generate(
        tmp_path,
        """
        namespace n { class A { public: A(int vtable, int out_director, int director);
          virtual ~A(); virtual int f(int director, int vtable); }; }
        """,
        "n_A",
    )
    header = result.files["c/api_c.h"]
    assert "int vtable_, int out_director_, int director_" in header
    assert "int director_, int vtable_" in header


def test_a_pointer_to_a_class_that_is_not_wrapped_is_an_error_not_a_crash(
    tmp_path: Path,
) -> None:
    text = error_of(
        tmp_path,
        """
        class K;
        namespace n { class A { public: A(); virtual ~A(); virtual int f() const;
          protected: explicit A(K* k); }; }
        """,
        "n_A",
    )
    assert "class 'K' is not a class of this module" in text


def test_a_constructor_that_takes_a_unique_ptr_receives_it(tmp_path: Path) -> None:
    result = generate_files(
        tmp_path,
        {
            "api.h": "#include <memory>\nnamespace n { class Item { public: Item(); ~Item(); };"
            " class A { public: explicit A(std::unique_ptr<Item> item); virtual ~A();"
            " virtual int f(); }; }"
        },
        "n_A",
    )
    assert "n_A_director_create(" in result.files["c/api_c.h"]


def test_the_director_is_final_and_its_vtable_is_copied(tmp_path: Path) -> None:
    source = generate(tmp_path, SHAPE, "n_Shape").files["c/api_c.cpp"]
    assert "class n_Shape_director_impl final : public ::n::Shape" in source
    assert "n_Shape_vtable vt_;" in source  # a copy: the vtable of the caller may go away


def test_a_struct_result_with_a_value_that_is_not_valid_gives_the_zero_value(
    tmp_path: Path,
) -> None:
    source = generate(
        tmp_path,
        """
        namespace n { enum Color { Red, Green = 5 }; struct P { std::int32_t x; Color c; };
          class A { public: A(); virtual ~A(); virtual P p() const noexcept; }; }
        """,
        "n_A",
    ).files["c/api_c.cpp"]
    assert "catch (const mod_detail::argument_error&)" in source
    assert "return ::n::P();" in source


def test_a_class_without_a_constructor_that_a_derived_class_can_call(tmp_path: Path) -> None:
    text = error_of(
        tmp_path,
        """
        #include <map>
        namespace n { class A { public: virtual ~A(); virtual int f() = 0;
          protected: explicit A(const std::map<int, int>& m); }; }
        """,
        "n_A",
    )
    assert "no constructor that a derived class can call crosses the C boundary" in text
    assert "std::map" in text


def test_a_virtual_method_that_returns_a_reference_keeps_the_class(tmp_path: Path) -> None:
    result = generate(
        tmp_path,
        """
        namespace n { class A { public: A(); virtual ~A(); virtual int ok();
          virtual const int& r() const; virtual const std::string& s() const; }; }
        """,
        "n_A",
    )
    vtable = vtable_of(result, "n_A")
    assert "(*ok)" in vtable
    assert "(*r)" not in vtable
    assert "(*s)" not in vtable


def test_the_implicitly_deleted_default_constructor_of_an_abstract_class(tmp_path: Path) -> None:
    text = error_of(
        tmp_path,
        "namespace n { class A { public: virtual ~A(); virtual int f() = 0; private: int& r_; }; }",
        "n_A",
    )
    assert "implicitly-deleted default constructor" in text
    assert "annotations.toml:1: the class asks for a director" in text
    assert "api.h:" in text  # and where the class is
