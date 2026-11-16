# SPDX-License-Identifier: Apache-2.0
"""The directors in the Java bindings: what the generator writes for the classes that can be
derived from (both flavors). What the code does is tests/test_jvm_roundtrip.py's (the "directors"
case) and tests/test_jvm_directors_run.py's."""

from __future__ import annotations

from pathlib import Path

import pytest

import bridgefex
from bridgefex.errors import GenerationError
from tests.conformance import runner_jvm
from tests.test_jvm_backend import PRELUDE

pytestmark = pytest.mark.usefixtures("libclang_loaded")


@pytest.fixture
def java() -> None:
    runner_jvm.skip_without_java()


BASE = """
#include <string>
class Base {
public:
    Base();
    explicit Base(int start);
    virtual ~Base();
    virtual int value() const;
    virtual std::string name() const = 0;
    int twice() const;
    static int count();
protected:
    virtual void changed(int reason);
};
class Derived : public Base {
public:
    Derived();
    int value() const override;
    std::string name() const override;
    int extra() const;
};
class Leaf final : public Derived {
public:
    Leaf();
    int value() const override;
    int more() const;
};
class Apart { public: Apart(); virtual ~Apart(); virtual int value() const; };
"""

DIRECTORS = ("Base", "Derived")


def generate(
    tmp_path: Path,
    code: str,
    classes: tuple[str, ...] = DIRECTORS,
    *,
    language: str = "jvm",
    verify: bool = False,
    module: str = "mod",
) -> dict[str, str]:
    annotations = tmp_path / "directors.toml"
    annotations.write_text(
        "directors = [" + ", ".join(f'"{module}_{name}"' for name in classes) + "]\n",
        encoding="utf-8",
    )
    header = tmp_path / "api.h"
    header.write_text(PRELUDE + code, encoding="utf-8")
    options = bridgefex.Options(
        module, std="c++17", languages=(language,), verify=verify, annotations=(annotations,)
    )
    return bridgefex.generate([header], options).files


def source(files: dict[str, str], language: str, name: str) -> str:
    return files[f"{language}/mod/src/main/java/mod/api/{name}.java"]


@pytest.fixture(params=["jvm", "jni"])
def language(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def test_the_methods_that_c_plus_plus_calls_through_the_director_can_be_overridden(
    tmp_path: Path, language: str
) -> None:
    base = source(generate(tmp_path, BASE, language=language), language, "Base")
    # The virtual methods are the ones of the class (Java methods are virtual), also the pure one.
    assert "    public int value() {" in base
    assert "    public java.lang.String name() {" in base
    # Everything else is final: C++ would not call an override of it, and Java says so.
    assert "    public final int twice() {" in base
    assert "public static int count()" in base
    assert "final" not in base.split("public static int count()")[1].split("{")[0]


def test_a_virtual_method_that_is_not_part_of_the_api_is_protected_with_a_bridge_for_the_thunk(
    tmp_path: Path, language: str
) -> None:
    base = source(generate(tmp_path, BASE, language=language), language, "Base")
    assert "    protected void changed(int reason) {" in base
    # The thunks are in another package: they cannot call a protected method.
    assert "    public final void bridgefex_changed(int reason) {" in base
    assert "not part of the API: only an override of it can call it" in base


def test_the_constructors_of_an_abstract_class_are_protected(tmp_path: Path, language: str) -> None:
    base = source(generate(tmp_path, BASE, language=language), language, "Base")
    assert "    protected Base() {" in base
    assert "    protected Base(int start) {" in base
    assert "public Base(" not in base
    # They make a director whatever the class is (an abstract class has no plain object).
    assert "DirectorOverrides.of(getClass(), true)" in base


def test_a_class_that_is_not_abstract_makes_a_plain_object_unless_a_subclass_overrides(
    tmp_path: Path, language: str
) -> None:
    derived = source(generate(tmp_path, BASE, language=language), language, "Derived")
    assert "    public Derived() {" in derived
    assert "boolean[] __over = DirectorOverrides.of(getClass(), false);" in derived
    assert "if (__over == null)" in derived
    # What a subclass has to override, and what C++ has: its own director, with the base's methods.
    assert "BridgefexDirectors.overrides(type, GENERATED," in derived
    assert "    public final int extra() {" in derived


def test_the_classes_below_one_that_can_be_derived_from_are_final_in_their_methods(
    tmp_path: Path, language: str
) -> None:
    leaf = source(generate(tmp_path, BASE, language=language), language, "Leaf")
    # C++ never calls the overrides of a class of the user that derives from a final class.
    assert "    public final int value() {" in leaf
    assert "    public final int more() {" in leaf
    assert "DirectorOverrides" not in leaf


def test_a_class_that_is_unrelated_to_the_directors_is_as_before(
    tmp_path: Path, language: str
) -> None:
    apart = source(generate(tmp_path, BASE, language=language), language, "Apart")
    assert "    public int value() {" in apart
    assert "final" not in apart


def test_a_module_without_directors_has_no_final_methods_nor_the_runtime_of_directors(
    tmp_path: Path, language: str
) -> None:
    header = tmp_path / "api.h"
    header.write_text(PRELUDE + BASE, encoding="utf-8")
    files = bridgefex.generate(
        [header], bridgefex.Options("mod", std="c++17", languages=(language,), verify=False)
    ).files
    base = source(files, language, "Base")
    assert "    public int twice() {" in base
    assert "DirectorOverrides" not in base
    assert not any(path.endswith("BridgefexDirectors.java") for path in files)
    assert "DirectorBox" not in "".join(files.values())


def test_a_virtual_method_with_types_that_an_override_cannot_have_keeps_the_implementation(
    tmp_path: Path, language: str
) -> None:
    code = """
class Item { public: Item(); ~Item(); int size() const; };
class Mixed {
public:
    Mixed();
    virtual ~Mixed();
    virtual std::string name() const;
    virtual int total(Item item) const;
};
"""
    mixed = source(generate(tmp_path, code, ("Mixed",), language=language), language, "Mixed")
    assert "    public java.lang.String name() {" in mixed
    # An object by value is a copy: C++ keeps its implementation.
    assert "    public final int total(" in mixed


def test_the_member_names_of_the_directors_are_reserved(tmp_path: Path, language: str) -> None:
    code = """
class Taken {
public:
    Taken();
    virtual ~Taken();
    virtual int holdDirector() const;
    virtual int setDirector() const;
};
class Other {
public:
    Other();
    ~Other();
    int director() const;
};
"""
    files = generate(tmp_path, code, ("Taken",), language=language)
    taken = source(files, language, "Taken")
    for name in ("holdDirector", "setDirector"):
        assert f"public int {name}_()" in taken
    # The C layer names the handle of a director after the class: a method of that name clashes
    # there, but one of a class that has no director only needs another name in Java.
    assert "public final int director_()" not in source(files, language, "Other")
    assert "public int director_()" in source(files, language, "Other")


@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_the_bindings_of_the_directors_compile(
    tmp_path: Path,
    java: None,
    language: str,
) -> None:
    assert generate(tmp_path, BASE, language=language, verify=True)


@pytest.mark.parametrize("module", ["box", "scope", "director", "vt", "over", "value", "d"])
@pytest.mark.parametrize("language", ["jvm", "jni"])
def test_a_module_named_like_a_local_of_the_directors_compiles(
    tmp_path: Path,
    java: None,
    language: str,
    module: str,
) -> None:
    assert generate(tmp_path, BASE, language=language, verify=True, module=module)


def test_overloads_of_a_virtual_method_are_told_apart_as_in_the_c_api(
    tmp_path: Path, java: None, language: str
) -> None:
    code = """
class Twin {
public:
    Twin();
    virtual ~Twin();
    virtual int value() const;
    virtual int value(int) const;
    virtual double value(double) const;
protected:
    virtual int value(const std::string&) const;
};
"""
    files = generate(tmp_path, code, ("Twin",), language=language, verify=True)
    twin = source(files, language, "Twin")
    assert "    public int valueVoid() {" in twin
    assert "    public int valueInt(int arg1) {" in twin
    assert "    public double valueDouble(double arg1) {" in twin
    assert "    protected int valueString(java.lang.String arg1) {" in twin
    assert "    public final int bridgefex_valueString(java.lang.String arg1) {" in twin


# What the reviews of the directors found ------------------------------------------------------


def test_an_override_that_names_its_parameters_differently_compiles(
    tmp_path: Path, java: None, language: str
) -> None:
    """The base call of each director is lowered with the names that its own class gives the
    parameters, not the ones of the method that contains it."""
    code = """
class Base { public: Base(); virtual ~Base();
    virtual int f(int alpha, int beta) const; virtual int g(int, int) const; };
class Der : public Base { public: Der();
    int f(int x, int y) const override; int g(int a, int b) const override; };
"""
    files = generate(tmp_path, code, ("Base", "Der"), language=language, verify=True)
    base = source(files, language, "Base")
    assert "    public int f(int alpha, int beta) {" in base
    assert "private int __base1_f(int x, int y) {" in base


def test_a_method_named_differently_from_the_one_that_declares_it_is_final(
    tmp_path: Path, java: None, language: str
) -> None:
    """The director looks for the override by the name of the class that declares the method: the
    method of another name (an overload that the first class does not have) does nothing."""
    code = """
class Shape { public: Shape(); virtual ~Shape();
    virtual int add(int a, int b) const; virtual double add(double a, double b) const; };
class Circle : public Shape { public: Circle(); int add(int a, int b) const override; };
"""
    files = generate(tmp_path, code, ("Shape", "Circle"), language=language, verify=True)
    assert "    public int addIntInt(int a, int b) {" in source(files, language, "Shape")
    circle = source(files, language, "Circle")
    assert "    public final int add(int a, int b) {" in circle
    assert "addIntInt" not in circle.split("public final int add(int a, int b)")[0]


def test_what_a_class_below_inherits_is_final_in_it(
    tmp_path: Path, java: None, language: str
) -> None:
    """C++ does not call an override of a class that has no director: Java refuses it."""
    code = """
class Shape { public: Shape(); virtual ~Shape(); virtual int area() const;
    virtual std::string name() const; };
class Dot final : public Shape { public: Dot(); };
"""
    files = generate(tmp_path, code, ("Shape",), language=language, verify=True)
    dot = source(files, language, "Dot")
    assert "    public final int area() {" in dot
    assert "    public final java.lang.String name() {" in dot
    assert "return super.area();" in dot


def test_a_chain_of_classes_that_cannot_be_derived_from_compiles(
    tmp_path: Path, java: None, language: str
) -> None:
    """A class below one that Java cannot derive from declares the virtual method again in C++:
    the one above is final and does the same."""
    code = """
class D { public: D(); virtual ~D(); virtual int v() const; int w() const; };
class G1 : public D { public: G1(); int v() const override; };
class G2 : public G1 { public: G2(); int v() const override; virtual int z() const; };
class G3 : public G2 { public: G3(); int v() const override; int z() const override; };
"""
    files = generate(tmp_path, code, ("D",), language=language, verify=True)
    assert "    public final int v() {" in source(files, language, "G1")
    assert " v()" not in source(files, language, "G2").replace("super.v()", "")
    assert " v()" not in source(files, language, "G3").replace("super.v()", "")


def test_a_class_with_a_director_that_java_cannot_derive_from_has_final_methods(
    tmp_path: Path, java: None, language: str
) -> None:
    """The annotations give the class a director, but its base class has none (or it has two
    bases): an override would be ignored, so Java refuses it as it does for the other classes."""
    code = """
class Base { public: Base(); virtual ~Base(); virtual int f() const; };
class Other { public: Other(); virtual ~Other(); virtual int h() const; };
class Plain : public Base { public: Plain(); int f() const override; virtual int g() const; };
class Multi : public Other, public Base { public: Multi(); int g() const; };
"""
    files = generate(tmp_path, code, ("Plain", "Multi"), language=language, verify=True)
    plain = source(files, language, "Plain")
    assert "    public final int f() {" in plain
    assert "    public final int g() {" in plain
    assert "BridgefexDirectors" not in plain
    multi = source(files, language, "Multi")
    assert "    public final int g() {" in multi
    assert "    public final int f() {" in multi  # the copy of a method of the second base
    assert " h() {" not in multi.replace("final int h() {", "")  # the copy of the other base


def test_an_intermediate_class_that_drops_a_virtual_method_has_a_derivable_subclass(
    tmp_path: Path, java: None, language: str
) -> None:
    """What a class makes final (C++ implements it where it cannot be overridden) is not made
    final again by the classes below it."""
    code = """
class Base { public: Base(); virtual ~Base(); virtual int p() const; virtual int q() const; };
class Mid : public Base { public: Mid(); int p() const final; };
class Leaf : public Mid { public: Leaf(); int q() const override; };
"""
    files = generate(tmp_path, code, ("Base", "Mid", "Leaf"), language=language, verify=True)
    assert "    public final int p() {" in source(files, language, "Mid")
    assert "final int p()" not in source(files, language, "Leaf")


def test_a_module_whose_directors_have_nothing_to_override_compiles(
    tmp_path: Path, java: None, language: str
) -> None:
    """The virtual methods use types that an override cannot have, and the module has no callback:
    the class still has its director (and the runtime that C++ calls into)."""
    code = """
class Item { public: Item(); ~Item(); int size() const; };
class Lone { public: Lone(); virtual ~Lone(); virtual int total(Item item) const; };
"""
    files = generate(tmp_path, code, ("Lone",), language=language, verify=True)
    assert any(path.endswith("BridgefexCallbacks.java") for path in files)


def test_a_nested_enum_cannot_be_named_like_the_class_of_the_overrides(
    tmp_path: Path, language: str
) -> None:
    code = """
class Base { public: enum class DirectorOverrides { A }; Base(); virtual ~Base();
    virtual int f() const; };
"""
    with pytest.raises(GenerationError) as raised:
        generate(tmp_path, code, ("Base",), language=language)
    assert "'Base::DirectorOverrides' gets the Java name 'DirectorOverrides'" in str(raised.value)
