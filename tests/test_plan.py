# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the binding plan, built from hand-written models (no libclang needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridgefex.errors import ConfigurationError, GenerationError, Location
from bridgefex.model import (
    Class,
    Constructor,
    Function,
    Header,
    Method,
    Module,
    Parameter,
    VisibleNames,
)
from bridgefex.plan import ClassPlan, FunctionPlan, Role, build_plan
from bridgefex.typemap import BUILTIN_TYPES, STANDARD_TYPEDEFS, VOID

INT32 = STANDARD_TYPEDEFS["int32_t"].type
DOUBLE = BUILTIN_TYPES["DOUBLE"]
BOOL = BUILTIN_TYPES["BOOL"]
HERE = Location("test.h", 1, 1)


def function(name: str, *params: Parameter, namespace: tuple[str, ...] = ("ns",)) -> Function:
    return Function(name, namespace, tuple(params), INT32, HERE)


def method(
    name: str, *params: Parameter, is_const: bool = False, is_static: bool = False
) -> Method:
    return Method(name, tuple(params), INT32, is_const, is_static, HERE)


def klass(
    name: str,
    constructors: tuple[Constructor, ...] = (Constructor((), HERE),),
    methods: tuple[Method, ...] = (),
    namespace: tuple[str, ...] = ("ns",),
) -> Class:
    return Class(name, namespace, "class", constructors, methods, HERE)


def header(*declarations: Class | Function, name: str = "test.h") -> Header:
    return Header(Path(name), name, tuple(declarations))


def plan_for(*headers: Header, module: str = "mod"):  # type: ignore[no-untyped-def]
    return build_plan(Module(module, tuple(headers)))


def test_class_functions_and_signatures() -> None:
    model = klass(
        "Counter",
        constructors=(Constructor((Parameter("start", INT32),), HERE),),
        methods=(
            method("value", is_const=True),
            method("add", Parameter("delta", INT32)),
            method("count", is_static=True),
        ),
    )
    item = plan_for(header(model)).headers[0].items[0]
    assert isinstance(item, ClassPlan)
    assert item.c_type == "ns_Counter"
    signatures = [f.signature for f in item.c_functions]
    assert signatures == [
        "ns_Counter_create(ns_Counter** out_self, int32_t start)",
        "ns_Counter_destroy(ns_Counter* self)",
        "ns_Counter_value(const ns_Counter* self, int32_t* out_result)",
        "ns_Counter_add(ns_Counter* self, int32_t delta, int32_t* out_result)",
        "ns_Counter_count(int32_t* out_result)",
    ]
    statements = [f.statement for f in item.c_functions]
    assert statements == [
        "*out_self = reinterpret_cast<ns_Counter*>(new ::ns::Counter(start));",
        "delete reinterpret_cast<::ns::Counter*>(self);",
        "*out_result = reinterpret_cast<const ::ns::Counter*>(self)->value();",
        "*out_result = reinterpret_cast<::ns::Counter*>(self)->add(delta);",
        "*out_result = ::ns::Counter::count();",
    ]
    create, destroy, value, _, count = item.c_functions
    assert [p.name for p in create.null_checked] == ["out_self"]
    assert destroy.null_checked == ()
    assert not destroy.returns_status
    assert [p.role for p in value.null_checked] == [Role.SELF, Role.OUT_RESULT]
    assert [p.name for p in count.null_checked] == ["out_result"]
    assert item.constructors[0].name == "__init__"
    assert [m.is_static for m in item.members] == [False, False, True]


def test_void_function_has_no_out_parameter() -> None:
    model = Function("reset", ("ns",), (), VOID, HERE)
    item = plan_for(header(model)).headers[0].items[0]
    assert isinstance(item, FunctionPlan)
    assert item.c_function.signature == "ns_reset(void)"
    assert item.c_function.statement == "::ns::reset();"
    assert item.py.result_ctype is None


def test_overloads_get_suffixes_only_when_overloaded() -> None:
    model = klass(
        "Counter",
        constructors=(Constructor((), HERE), Constructor((Parameter("v", INT32),), HERE)),
        methods=(
            method("add", Parameter("v", INT32)),
            method("add", Parameter("v", DOUBLE)),
            method("value"),
        ),
    )
    item = plan_for(header(model)).headers[0].items[0]
    assert isinstance(item, ClassPlan)
    names = [f.name for f in item.c_functions]
    assert names == [
        "ns_Counter_create_void",
        "ns_Counter_create_int32",
        "ns_Counter_destroy",
        "ns_Counter_add_int32",
        "ns_Counter_add_double",
        "ns_Counter_value",
    ]
    assert [c.name for c in item.constructors] == ["create_void", "create_int32"]
    assert [m.name for m in item.members] == ["add_int32", "add_double", "value"]


def test_overload_set_across_headers() -> None:
    first = header(function("scale", Parameter("v", INT32)), name="a.h")
    second = header(function("scale", Parameter("v", DOUBLE)), name="b.h")
    plan = plan_for(first, second)
    names = [h.c_functions[0].name for h in plan.headers]
    assert names == ["ns_scale_int32", "ns_scale_double"]


def test_global_namespace_uses_module_prefix() -> None:
    plan = plan_for(header(klass("Box", namespace=()), function("f", namespace=())), module="lib")
    assert [f.name for f in plan.headers[0].c_functions] == [
        "lib_Box_create",
        "lib_Box_destroy",
        "lib_f",
    ]


def test_c_name_collisions_are_reported() -> None:
    clash = header(function("b_c", namespace=("a",)), function("c", namespace=("a", "b")))
    with pytest.raises(GenerationError, match=r"'a_b_c'.*clashes"):
        plan_for(clash)


def test_collision_with_runtime_names() -> None:
    with pytest.raises(GenerationError, match="'mod_last_error'"):
        plan_for(header(function("last_error", namespace=("mod",))))


def test_collision_between_class_function_and_free_function() -> None:
    clash = header(klass("Box"), function("Box_create"))
    with pytest.raises(GenerationError, match="'ns_Box_create'"):
        plan_for(clash)


def test_python_name_collisions_are_reported() -> None:
    clash = header(klass("Box", namespace=("a",)), klass("Box", namespace=("b",)))
    with pytest.raises(GenerationError, match="Python name 'Box'"):
        plan_for(clash)


def test_python_reserved_member_names_are_renamed() -> None:
    model = klass("Box", methods=(method("close"), method("from")))
    item = plan_for(header(model)).headers[0].items[0]
    assert isinstance(item, ClassPlan)
    assert [m.name for m in item.members] == ["close_", "from_"]


def test_python_member_name_clash_after_renaming() -> None:
    model = klass("Box", methods=(method("close"), method("close_")))
    with pytest.raises(GenerationError, match="Python name 'close_'"):
        plan_for(header(model))


def test_module_level_python_names_avoid_generated_globals() -> None:
    plan = plan_for(
        header(function("ctypes"), function("threading"), function("weakref"), klass("Box"))
    )
    assert plan.headers[0].py_exports == ("ctypes_", "threading_", "weakref_", "Box")


def test_names_starting_with_underscore_give_reserved_c_names() -> None:
    with pytest.raises(GenerationError, match=r"'a__bind'.*reserved identifier"):
        plan_for(header(function("_bind", namespace=("a",))))


def test_python_name_clash_after_renaming_is_reported() -> None:
    with pytest.raises(GenerationError, match="Python name 'ctypes_'"):
        plan_for(header(function("ctypes"), function("ctypes_")))


def test_duplicate_header_names() -> None:
    with pytest.raises(GenerationError, match="same name"):
        plan_for(header(function("f"), name="x/a.h"), header(function("g"), name="y/a.h"))


def test_invalid_header_name() -> None:
    with pytest.raises(ConfigurationError, match="valid identifier"):
        plan_for(header(function("f"), name="my-header.h"))


def test_python_conversions() -> None:
    model = function("f", Parameter("a", INT32), Parameter("b", DOUBLE), Parameter("c", BOOL))
    item = plan_for(header(model)).headers[0].items[0]
    assert isinstance(item, FunctionPlan)
    assert [arg.conversion for arg in item.py.args] == [
        '_runtime.to_int(a, ctypes.c_int32, "a")',
        '_runtime.to_float(b, ctypes.c_double, "b")',
        '_runtime.to_bool(c, "c")',
    ]
    assert item.c_function.ctypes_argtypes == (
        "[ctypes.c_int32, ctypes.c_double, ctypes.c_bool, ctypes.POINTER(ctypes.c_int32)]"
    )


def test_fingerprint_is_stable_and_tracks_declarations() -> None:
    base = plan_for(header(function("f", Parameter("a", INT32))))
    again = plan_for(header(function("f", Parameter("a", INT32))))
    changed = plan_for(header(function("f", Parameter("a", DOUBLE))))
    renamed = plan_for(header(function("f", Parameter("b", INT32))))
    assert len(base.api_fingerprint) == 32
    assert base.api_fingerprint == again.api_fingerprint
    assert base.api_fingerprint != changed.api_fingerprint
    # Parameter names are part of the declarations too.
    assert base.api_fingerprint != renamed.api_fingerprint


def test_long_python_calls_are_wrapped() -> None:
    params = tuple(Parameter(f"argument_{index}", INT32) for index in range(4))
    item = plan_for(header(function("f", *params))).headers[0].items[0]
    assert isinstance(item, FunctionPlan)
    body = item.py.body(4)
    assert body.splitlines()[1] == "    _runtime.check(_bind().ns_f("
    assert all(len(line) <= 99 for line in body.splitlines())


def test_parameters_named_like_macros_without_arguments_are_renamed() -> None:
    names = VisibleNames(macro_names=frozenset({"X", "max"}), object_macro_names=frozenset({"X"}))
    model = function(
        "f", Parameter("X", INT32), Parameter("max", INT32), Parameter("MOD_A_C_H", INT32)
    )
    plan = plan_for(Header(Path("a.h"), "a.h", (model,), names))
    item = plan.headers[0].items[0]
    assert isinstance(item, FunctionPlan)
    # A function-like macro cannot replace a name that is not followed by '('.
    assert [param.name for param in item.c_function.params] == [
        "X_",
        "max",
        "MOD_A_C_H_",
        "out_result",
    ]


def test_names_of_the_c_library_are_reserved() -> None:
    system = VisibleNames(global_names=frozenset({"sched_yield"}), macro_names=frozenset({"ns_M"}))
    module = Module(
        "mod", (header(function("yield", namespace=("sched",)), function("M")),), system
    )
    with pytest.raises(GenerationError) as raised:
        build_plan(module)
    message = str(raised.value)
    assert (
        "'sched_yield' of function 'sched::yield()' clashes with a global declaration "
        "of the system headers" in message
    )
    assert "'ns_M' of function 'ns::M()' clashes with a macro of the system headers" in message


@pytest.mark.parametrize(
    "names",
    [
        ("mod_runtime.h",),
        ("MOD_RUNTIME_INTERNAL.HPP",),
        ("mod_runtime.cpp",),
        ("a.h", "a_c.h"),
        ("b_c.cpp", "b.h"),
    ],
)
def test_headers_named_like_generated_files(names: tuple[str, ...]) -> None:
    headers = [header(function(f"f{i}"), name=name) for i, name in enumerate(names)]
    with pytest.raises(GenerationError, match="has the name of a generated file"):
        plan_for(*headers)


def test_headers_in_a_directory_do_not_hide_generated_files() -> None:
    model = Header(Path("lib/mod_runtime.h"), "lib/mod_runtime.h", (function("f"),))
    assert plan_for(model).headers[0].stem == "mod_runtime"
