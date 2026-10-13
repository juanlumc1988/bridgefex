# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the binding plan, built from hand-written models (no libclang needed)."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from bridgefex.errors import ConfigurationError, GenerationError, Location
from bridgefex.generator import render
from bridgefex.model import (
    Class,
    Constructor,
    Enumeration,
    Enumerator,
    EnumRef,
    Function,
    Header,
    Method,
    Module,
    Parameter,
    StringKind,
    StringType,
    VisibleNames,
)
from bridgefex.plan import (
    C_API_CONVENTIONS,
    ClassPlan,
    FunctionPlan,
    ModulePlan,
    Role,
    api_fingerprint,
    build_plan,
    c_literal,
)
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
    # Outputs that the function allocates are checked and reset first.
    assert [c.condition for c in create.owned_checks] == ["out_self == nullptr"]
    assert create.resets == ("*out_self = nullptr;",)
    assert create.checks == ()
    assert (destroy.owned_checks, destroy.resets, destroy.checks) == ((), (), ())
    assert not destroy.returns_status
    assert [c.message for c in value.checks] == ["'self' is NULL", "'out_result' is NULL"]
    assert {c.helper for c in value.checks} == {"null_argument"}
    assert [c.condition for c in count.checks] == ["out_result == nullptr"]
    assert [p.role for p in value.params] == [Role.SELF, Role.OUT_RESULT]
    assert item.constructors[0].name == "__init__"
    assert [m.is_static for m in item.members] == [False, False, True]


def test_void_function_has_no_out_parameter() -> None:
    model = Function("reset", ("ns",), (), VOID, HERE)
    item = plan_for(header(model)).headers[0].items[0]
    assert isinstance(item, FunctionPlan)
    assert item.c_function.signature == "ns_reset(void)"
    assert item.c_function.statement == "::ns::reset();"
    assert item.py.result.value is None
    assert item.py.result.setup == ()


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


def test_private_python_names_of_classes_do_not_clash() -> None:
    """The pointer type of X must not be the mirror of a class named X_p."""
    plan = plan_for(header(klass("Circle"), klass("Circle_p")))
    circle, circle_p = plan.headers[0].classes
    names = [circle.struct_name, circle.pointer_name, circle_p.struct_name, circle_p.pointer_name]
    assert names == ["_ns_Circle", "_ns_Circle__p", "_ns_Circle_p", "_ns_Circle_p__p"]


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
    assert [arg.conversions for arg in item.py.args] == [
        ('a = _runtime.to_int(a, ctypes.c_int32, "a")',),
        ('b = _runtime.to_float(b, ctypes.c_double, "b")',),
        ('c = _runtime.to_bool(c, "c")',),
    ]
    assert item.c_function.ctypes_argtypes == (
        "[ctypes.c_int32, ctypes.c_double, ctypes.c_bool, ctypes.POINTER(ctypes.c_int32)]"
    )


def fingerprint(plan: ModulePlan) -> str:
    files = render(plan, ())
    return api_fingerprint(files[f"c/{name}"] for name in plan.public_headers)


def test_fingerprint_is_stable_and_tracks_declarations() -> None:
    base = fingerprint(plan_for(header(function("f", Parameter("a", INT32)))))
    again = fingerprint(plan_for(header(function("f", Parameter("a", INT32)))))
    changed = fingerprint(plan_for(header(function("f", Parameter("a", DOUBLE)))))
    renamed = fingerprint(plan_for(header(function("f", Parameter("b", INT32)))))
    assert len(base) == 32
    assert base == again
    assert base != changed
    # Parameter names are part of the declarations too.
    assert base != renamed


def test_fingerprint_is_in_the_runtime_files() -> None:
    plan = plan_for(header(function("f", Parameter("a", INT32))), module="m")
    files = render(plan)
    expected = fingerprint(plan)
    assert f'return "{expected}";' in files["c/m_runtime.cpp"]
    assert f'API_FINGERPRINT = "{expected}"' in files["python/m/_runtime.py"]


def test_fingerprint_hashes_the_conventions_and_each_header_separately() -> None:
    assert api_fingerprint(["ab", "c"]) != api_fingerprint(["a", "bc"])
    assert api_fingerprint([]) == api_fingerprint([])
    assert len(api_fingerprint(["x"])) == 32


# Conventions that the generated headers do not show (C_API_CONVENTIONS), and a
# hash of the runtime files that implement them. A change to the runtime
# templates fails this test: if it changes a convention (the out-parameter
# contract, how memory is returned...), bump C_API_CONVENTIONS; then update the
# hash here.
PINNED_CONVENTIONS = 2
PINNED_RUNTIME_SHA256 = "b3d70dad1a90cff8ac1eedc4f572bed64fa0f5d348f61edf90454d5ccec1cdf5"


def test_runtime_changes_are_pinned_to_the_conventions() -> None:
    files = render(build_plan(Module("m", ())))
    digest = hashlib.sha256()
    for path in sorted(files):
        if "_runtime" in path:
            digest.update(path.encode() + b"\0" + files[path].encode() + b"\0")
    assert (C_API_CONVENTIONS, digest.hexdigest()) == (PINNED_CONVENTIONS, PINNED_RUNTIME_SHA256)


def test_long_python_calls_are_wrapped() -> None:
    params = tuple(Parameter(f"argument_{index}", INT32) for index in range(6))
    item = plan_for(header(function("f", *params))).headers[0].items[0]
    assert isinstance(item, FunctionPlan)
    lines = item.py.body(4).splitlines()
    # The conversions come first, then the call, which does not fit in one line.
    conversion = '    {0} = _runtime.to_int({0}, ctypes.c_int32, "{0}")'
    assert lines[1:7] == [conversion.format(f"argument_{index}") for index in range(6)]
    assert lines[7] == "    _runtime.check(_bind().ns_f("
    assert all(len(line) <= 99 for line in lines)


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


def test_tags_of_the_c_library_only_clash_with_handle_types() -> None:
    """A C tag lives in its own namespace: only 'typedef struct X X;' declares one."""
    system = VisibleNames(tag_names=frozenset({"tcp_info", "link_map"}))
    accepted = Module("mod", (header(function("info", namespace=("tcp",))),), system)
    assert [f.name for f in build_plan(accepted).headers[0].c_functions] == ["tcp_info"]
    rejected = Module("mod", (header(klass("map", namespace=("link",))),), system)
    with pytest.raises(GenerationError) as raised:
        build_plan(rejected)
    assert (
        "the C name 'link_map' of the handle type of 'link::map' clashes with a struct, "
        "union or enum tag of the system headers" in str(raised.value)
    )


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


STRING = StringType(StringKind.STRING)
STRING_REF = StringType(StringKind.STRING, by_reference=True)
VIEW = StringType(StringKind.STRING_VIEW)
C_STRING = StringType(StringKind.C_STRING)


def only_function(plan: ModulePlan) -> FunctionPlan:
    (item,) = plan.headers[0].items
    assert isinstance(item, FunctionPlan)
    return item


def test_string_parameters() -> None:
    model = Function(
        "f",
        ("ns",),
        (Parameter("a", STRING_REF), Parameter("b", VIEW), Parameter("c", C_STRING)),
        VOID,
        HERE,
    )
    item = only_function(plan_for(header(model)))
    function = item.c_function
    assert function.signature == (
        "ns_f(const char* a, size_t a_size, const char* b, size_t b_size, const char* c)"
    )
    assert [p.role for p in function.params] == [
        Role.INPUT,
        Role.SIZE,
        Role.INPUT,
        Role.SIZE,
        Role.INPUT,
    ]
    assert function.ctypes_argtypes == (
        "[ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]"
    )
    assert function.statement == (
        "::ns::f(std::string(mod_detail::string_data(a), a_size), "
        "std::string_view(mod_detail::string_data(b), b_size), c);"
    )
    assert [(c.condition, c.message) for c in function.checks] == [
        ("a == nullptr && a_size != 0", "'a' is NULL and 'a_size' is not 0"),
        ("b == nullptr && b_size != 0", "'b' is NULL and 'b_size' is not 0"),
        ("c == nullptr", "'c' is NULL"),
    ]
    assert function.owned_checks == ()
    assert function.resets == ()
    assert function.description == (
        "void ns::f(const std::string& a, std::string_view b, const char* c)"
    )
    # The sizes are computed with the conversions, before the call.
    assert [arg.conversions for arg in item.py.args] == [
        ('a = _runtime.to_bytes(a, "a")', "a_size = _builtins.len(a)"),
        ('b = _runtime.to_bytes(b, "b")', "b_size = _builtins.len(b)"),
        ('c = _runtime.to_c_string(c, "c")',),
    ]
    assert item.py.call_args == ("a", "a_size", "b", "b_size", "c")


@pytest.mark.parametrize(
    ("result", "helper"), [(STRING, "return_string"), (C_STRING, "return_c_string")]
)
def test_string_results(result: StringType, helper: str) -> None:
    get = dataclasses.replace(method("get", Parameter("s", STRING), is_const=True), result=result)
    model = klass("Text", methods=(get,))
    (item,) = plan_for(header(model)).headers[0].items
    assert isinstance(item, ClassPlan)
    py = item.members[0]
    function = py.function
    assert function.signature == (
        "ns_Text_get(const ns_Text* self, const char* s, size_t s_size, "
        "char** out_result, size_t* out_result_size)"
    )
    # The outputs that the function allocates are checked and reset first, so
    # they are NULL on every failure.
    assert [c.condition for c in function.owned_checks] == [
        "out_result == nullptr",
        "out_result_size == nullptr",
    ]
    assert function.resets == ("*out_result = nullptr;", "*out_result_size = 0;")
    assert [c.condition for c in function.checks] == [
        "self == nullptr",
        "s == nullptr && s_size != 0",
    ]
    # The result is copied in the same full-expression as the call.
    assert function.statement == (
        f"mod_detail::{helper}(reinterpret_cast<const ::ns::Text*>(self)->get("
        "std::string(mod_detail::string_data(s), s_size)), out_result, out_result_size);"
    )
    assert function.ctypes_argtypes.endswith(
        "ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t)]"
    )
    body = py.body(8).splitlines()
    assert body[:4] == [
        "        _out = ctypes.c_void_p()",
        "        _out_size = ctypes.c_size_t()",
        '        s = _runtime.to_bytes(s, "s")',
        "        s_size = _builtins.len(s)",
    ]
    assert body[4:10] == [
        "        try:",
        "            _runtime.check(_bind().ns_Text_get(self._ptr(), s, s_size, _out, _out_size))",
        body[6],
        "        finally:",
        "            if _out:",
        "                _lib.mod_free(_out)",
    ]
    if result is C_STRING:
        assert body[6].endswith("_runtime.string_at(_out, _out_size.value) if _out else None")
        assert (
            body[10] == "        return None if _out_data is None else _runtime.decode(_out_data)"
        )
    else:
        assert body[6] == "            _out_data = _runtime.string_at(_out, _out_size.value)"
        assert body[10] == "        return _runtime.decode(_out_data)"
    assert len(body) == 11


def test_string_overload_tokens() -> None:
    plan = plan_for(
        header(
            function("f", Parameter("s", STRING_REF)),
            function("f", Parameter("s", C_STRING)),
            function("f", Parameter("s", VIEW)),
        )
    )
    names = [f.name for f in plan.headers[0].c_functions]
    assert names == ["ns_f_string", "ns_f_cstr", "ns_f_string_view"]
    # By value and by reference are the same in C: a clash, not a silent choice.
    with pytest.raises(GenerationError, match=r"'ns_f_string'.*clashes"):
        plan_for(
            header(function("f", Parameter("s", STRING)), function("f", Parameter("s", STRING_REF)))
        )


def test_size_parameters_avoid_other_names() -> None:
    names = VisibleNames(object_macro_names=frozenset({"s_size"}))
    model = function(
        "f", Parameter("s", STRING), Parameter("t_", STRING), Parameter("t_size", INT32)
    )
    plan = plan_for(Header(Path("a.h"), "a.h", (model,), names))
    params = [p.name for p in plan.headers[0].c_functions[0].params]
    assert params == ["s", "s_size_", "t_", "t_size_", "t_size", "out_result"]


@pytest.mark.parametrize("name", ["free", "string"])
def test_runtime_names_of_strings_are_reserved(name: str) -> None:
    with pytest.raises(GenerationError, match=f"'mod_{name}'"):
        plan_for(header(function(name, namespace=("mod",))))


def test_header_std_includes() -> None:
    plan = plan_for(
        header(function("f", Parameter("s", STRING_REF)), name="a.h"),
        header(function("g", Parameter("s", C_STRING)), name="b.h"),
        header(klass("K", methods=(method("v", Parameter("s", VIEW)),)), name="c.h"),
    )
    assert [h.std_includes for h in plan.headers] == [("string",), (), ("string_view",)]


@pytest.mark.parametrize("names", [("mod_types.h",), ("a.h", "a_c_internal.hpp")])
def test_headers_named_like_future_generated_files(names: tuple[str, ...]) -> None:
    headers = [header(function(f"f{i}"), name=name) for i, name in enumerate(names)]
    with pytest.raises(GenerationError, match="has the name of a generated file"):
        plan_for(*headers)


@pytest.mark.parametrize(
    ("value", "c_type", "literal"),
    [
        (0, "int", "0"),
        (-1, "int", "(-1)"),
        (-(2**31), "int", "(-2147483647 - 1)"),
        (2**31 - 1, "int", "2147483647"),
        (255, "unsigned char", "255"),
        (-128, "int8_t", "(-128)"),
        (65535, "uint16_t", "65535"),
        (2**32 - 1, "unsigned int", "4294967295U"),
        (-(2**31), "int32_t", "(-INT32_C(2147483647) - 1)"),
        (2**32 - 1, "uint32_t", "UINT32_C(4294967295)"),
        (-(2**63), "int64_t", "(-INT64_C(9223372036854775807) - 1)"),
        (2**31, "uint64_t", "UINT64_C(2147483648)"),
        (2**64 - 1, "uint64_t", "UINT64_C(18446744073709551615)"),
        (-(2**63), "long", "(-9223372036854775807L - 1)"),
        (-(2**31), "long", "(-2147483647L - 1)"),
        (-5, "long", "(-5L)"),
        (7, "unsigned long", "7UL"),
        (-(2**63), "long long", "(-9223372036854775807LL - 1)"),
        (2**64 - 1, "unsigned long long", "18446744073709551615ULL"),
    ],
)
def test_c_literal(value: int, c_type: str, literal: str) -> None:
    assert c_literal(value, c_type) == literal


def enumeration(*values: int, fixed: bool = False) -> Enumeration:
    return Enumeration(
        name="E",
        namespace=("ns",),
        scope=(),
        usr="c:@N@ns@E@E",
        scoped=False,
        underlying=BUILTIN_TYPES["INT"],
        fixed=fixed,
        enumerators=tuple(Enumerator(f"v{i}", value) for i, value in enumerate(values)),
        location=HERE,
    )


@pytest.mark.parametrize(
    ("values", "valid"),
    [
        ((0,), (0, 1)),
        ((), (0, 1)),
        ((0, 5), (0, 7)),
        ((-3, 2), (-4, 3)),
        ((-1,), (-1, 0)),
        ((1, 2, 4), (0, 7)),
        ((-(2**31), 0), (-(2**31), 2**31 - 1)),
        ((2**31 - 1,), (0, 2**31 - 1)),
        ((100, 3000), (0, 4095)),
    ],
)
def test_valid_range_of_enums_without_a_fixed_type(
    values: tuple[int, ...], valid: tuple[int, int]
) -> None:
    assert enumeration(*values).valid_range == valid
    assert enumeration(*values, fixed=True).valid_range is None


def enum_header(*declarations: Class | Function | Enumeration, name: str = "test.h") -> Header:
    return Header(Path(name), name, tuple(declarations))


def color(fixed: bool = True) -> Enumeration:
    return Enumeration(
        name="Color",
        namespace=("ns",),
        scope=(),
        usr="c:@N@ns@E@Color",
        scoped=True,
        underlying=STANDARD_TYPEDEFS["uint8_t"].type,
        fixed=fixed,
        enumerators=(Enumerator("Red", 0), Enumerator("Green", 200)),
        location=HERE,
    )


COLOR = EnumRef("c:@N@ns@E@Color", "ns::Color", HERE)


def test_enum_parameters_and_results() -> None:
    model = Function("f", ("ns",), (Parameter("c", COLOR),), COLOR, HERE)
    plan = plan_for(enum_header(color(), model))
    item = plan.headers[0].items[1]
    assert isinstance(item, FunctionPlan)
    function = item.c_function
    assert function.signature == "ns_f(ns_Color c, ns_Color* out_result)"
    assert function.statement == (
        "*out_result = static_cast<ns_Color>(::ns::f(static_cast<::ns::Color>(c)));"
    )
    assert [c.condition for c in function.checks] == ["out_result == nullptr"]
    assert item.py.args[0].conversions == (
        'c = _runtime.to_enum(c, _types.ns_Color, ctypes.c_uint8, "c")',
    )
    assert item.py.result.value == "_runtime.enum_result(_types.ns_Color, _out.value)"
    assert function.description == "ns::Color ns::f(ns::Color c)"


def test_enums_without_a_fixed_type_are_range_checked() -> None:
    unfixed = dataclasses.replace(
        color(fixed=False),
        underlying=BUILTIN_TYPES["INT"],
        enumerators=(Enumerator("A", -3), Enumerator("B", 2)),
    )
    model = Function("f", ("ns",), (Parameter("c", COLOR),), VOID, HERE)
    item = plan_for(enum_header(unfixed, model)).headers[0].items[1]
    assert isinstance(item, FunctionPlan)
    (check,) = item.c_function.checks
    assert check.condition == "c < -4 || c > 3"
    assert check.helper == "invalid_argument"
    assert check.message == "'c' is not a value of ns::Color (-4..3)"
    assert item.py.args[0].conversions == (
        'c = _runtime.to_enum(c, _types.ns_Color, ctypes.c_int, "c", (-4, 3))',
    )
    # No check at the limits of int itself.
    full = dataclasses.replace(unfixed, enumerators=(Enumerator("A", -(2**31)), Enumerator("B", 0)))
    item = plan_for(enum_header(full, model)).headers[0].items[1]
    assert isinstance(item, FunctionPlan)
    assert item.c_function.checks == ()


def test_enum_constants_and_names() -> None:
    plan = plan_for(enum_header(color()))
    (enum,) = plan.headers[0].enums
    assert enum.c_type == "ns_Color"
    assert [(e.macro, e.literal) for e in enum.enumerators] == [
        ("ns_Color_Red", "0"),
        ("ns_Color_Green", "200"),
    ]
    assert enum.py_name == "Color"
    assert enum.py_module == "mod.test"
    assert plan.headers[0].py_exports == ("Color",)


def test_enum_overload_tokens_are_their_c_type() -> None:
    plan = plan_for(
        enum_header(
            color(),
            function("f", Parameter("c", COLOR)),
            function("f", Parameter("i", INT32)),
        )
    )
    assert [f.name for f in plan.headers[0].c_functions] == ["ns_f_ns_Color", "ns_f_int32"]


def test_enums_of_other_headers() -> None:
    user = function("f", Parameter("c", COLOR))
    plan = plan_for(enum_header(user, name="a.h"), enum_header(color(), name="b.h"))
    assert plan.headers[0].foreign_enums[0].c_type == "ns_Color"
    assert plan.headers[1].foreign_enums == ()
    assert plan.headers[1].enums[0].py_module == "mod.b"
    # Without a fixed underlying type, the user sees the definition: nothing to check.
    plan = plan_for(enum_header(user, name="a.h"), enum_header(color(False), name="b.h"))
    assert plan.headers[0].foreign_enums == ()


def test_enums_that_no_wrapped_header_defines() -> None:
    elsewhere = EnumRef("c:@N@ns@E@Color", "ns::Color", Location("other.h", 3, 1))
    with pytest.raises(
        GenerationError, match=r"defined in other\.h, which is not one of the wrapped"
    ):
        plan_for(enum_header(function("f", Parameter("c", elsewhere))))
    opaque = EnumRef("c:@N@ns@E@Color", "ns::Color", None)
    with pytest.raises(GenerationError, match="opaque enums are not supported yet"):
        plan_for(enum_header(function("f", Parameter("c", opaque))))


def test_enum_names_are_reserved_and_clash_checked() -> None:
    with pytest.raises(GenerationError, match="'ns_Color_Red' of function"):
        plan_for(enum_header(color(), function("Color_Red")))
    with pytest.raises(GenerationError, match="'ns_Color' of function"):
        plan_for(enum_header(color(), function("Color")))
    # A parameter named like an enumerator constant would be replaced by it.
    item = plan_for(enum_header(color(), function("f", Parameter("ns_Color_Red", INT32))))
    assert item.headers[0].c_functions[0].params[0].name == "ns_Color_Red_"
    same = dataclasses.replace(color(), enumerators=(Enumerator("None", 0), Enumerator("None_", 1)))
    with pytest.raises(GenerationError, match="get the same Python name 'None_'"):
        plan_for(enum_header(same))


def test_nested_enums() -> None:
    nested = dataclasses.replace(
        color(), scope=("Lamp",), usr="c:@N@ns@S@Lamp@E@Color", name="close"
    )
    lamp = dataclasses.replace(klass("Lamp", methods=(method("close_"),)), enums=(nested,))
    with pytest.raises(GenerationError, match="two members of '::ns::Lamp' get the Python name"):
        plan_for(enum_header(lamp))
    lamp = dataclasses.replace(klass("Lamp"), enums=(dataclasses.replace(nested, name="Mode"),))
    plan = plan_for(enum_header(lamp))
    (item,) = plan.headers[0].items
    assert isinstance(item, ClassPlan)
    (enum,) = item.enums
    assert enum.c_type == "ns_Lamp_Mode"
    assert enum.py_qualname == "Lamp.Mode"
    assert [t.c_type for t in plan.headers[0].types] == ["ns_Lamp", "ns_Lamp_Mode"]
