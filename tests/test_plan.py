# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the binding plan, built from hand-written models (no libclang needed)."""

from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path

import pytest

from bridgefex.errors import ConfigurationError, GenerationError, Location
from bridgefex.generator import render
from bridgefex.model import (
    CallbackType,
    Class,
    ClassForm,
    ClassRef,
    Constructor,
    ContainerKind,
    ContainerType,
    Enumeration,
    Enumerator,
    EnumRef,
    Field,
    Function,
    Header,
    Method,
    Module,
    Parameter,
    StringKind,
    StringType,
    Struct,
    StructRef,
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
    usr = "c:" + "".join(f"@N@{part}" for part in namespace) + f"@S@{name}"
    return Class(name, namespace, "class", constructors, methods, HERE, usr=usr)


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
PINNED_CONVENTIONS = 6
PINNED_RUNTIME_SHA256 = "4e734cbb2a6b9159dffedd0dc792530609c9f918cf651412d24c32f8de2de69b"


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
    # A const reference gets an lvalue: overloads for temporaries of headers
    # that are not wrapped do not take the call...
    assert function.statement == (
        "::ns::f(static_cast<const std::string&>(std::string(mod_detail::string_data(a), "
        "a_size)), std::string_view(mod_detail::string_data(b), b_size), c);"
    )
    # ... and a deleted overload for temporaries is still a compile error.
    (check,) = function.compile_checks
    assert check.startswith(
        "static_assert(sizeof(decltype(static_cast<void>(::ns::f(std::string(), "
        "std::string_view(mod_detail::string_data(b), b_size), c)))*) != 0,"
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


def test_runtime_names_of_strings_are_reserved() -> None:
    with pytest.raises(GenerationError, match="'mod_free'"):
        plan_for(header(function("free", namespace=("mod",))))


def test_outputs_are_reset_right_after_their_own_check() -> None:
    """With out_result valid and out_result_size NULL, out_result is reset too."""
    model = Function("f", ("ns",), (), STRING, HERE)
    item = only_function(plan_for(header(model)))
    pairs = [(c.condition, reset) for c, reset in item.c_function.owned_outputs]
    assert pairs == [
        ("out_result == nullptr", "*out_result = nullptr;"),
        ("out_result_size == nullptr", "*out_result_size = 0;"),
    ]


def test_parameters_named_like_generated_types_are_renamed() -> None:
    model = function(
        "f",
        Parameter("ns_Color", COLOR),
        Parameter("ns_K", INT32),
        Parameter("mod_status", INT32),
        Parameter("mod_string", INT32),
    )
    plan = plan_for(enum_header(color(), klass("K"), model))
    names = [p.name for p in plan.headers[0].c_functions[-1].params]
    assert names == ["ns_Color_", "ns_K_", "mod_status_", "mod_string_", "out_result"]


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


DOUBLE_T = BUILTIN_TYPES["DOUBLE"]


def struct(name: str, *fields: Field, namespace: tuple[str, ...] = ("ns",)) -> Struct:
    return Struct(name, namespace, f"c:@N@ns@S@{name}", tuple(fields), HERE)


def field(name: str, type_: object, length: int | None = None) -> Field:
    return Field(name, type_, length, HERE)  # type: ignore[arg-type]


def ref(name: str, by_reference: bool = False) -> StructRef:
    return StructRef(f"c:@N@ns@S@{name}", f"ns::{name}", HERE, by_reference)


POINT = struct("Point", field("x", DOUBLE_T), field("y", DOUBLE_T))


def test_struct_parameters_and_results() -> None:
    model = Function("mid", ("ns",), (Parameter("a", ref("Point", True)),), ref("Point"), HERE)
    plan = plan_for(enum_header(POINT, model))
    item = plan.headers[0].items[1]
    assert isinstance(item, FunctionPlan)
    function = item.c_function
    assert function.signature == "ns_mid(const ns_Point* a, ns_Point* out_result)"
    assert function.statement == (
        "*out_result = mod_detail::to_c(::ns::mid(static_cast<const ::ns::Point&>("
        "mod_detail::from_c(*a))));"
    )
    # Value outputs are checked with the inputs, never reset: they may alias one.
    assert [c.condition for c in function.checks] == ["a == nullptr", "out_result == nullptr"]
    assert function.owned_checks == ()
    assert function.ctypes_argtypes == (
        "[ctypes.POINTER(_types._ns_Point), ctypes.POINTER(_types._ns_Point)]"
    )
    assert item.py.args[0].conversions == ('a = _types._ns_Point__to_c(a, "a")',)
    assert item.py.result.setup == ("_out = _types._ns_Point()",)
    assert item.py.result.value == "_types._ns_Point__from_c(_out)"
    assert plan.headers[0].c_functions[0].name == "ns_Point_init"


def test_struct_conversions_are_flattened() -> None:
    msg = struct(
        "Msg",
        field("tag", BUILTIN_TYPES["UCHAR"]),
        field("where", ref("Point")),
        field("path", ref("Point"), 2),
    )
    plan = plan_for(enum_header(POINT, msg))
    (msg_plan,) = [s for s in plan.structs if s.c_type == "ns_Msg"]
    assert msg_plan.to_c == (
        "    _out.tag = _in.tag;",
        "    _out.where.x = _in.where.x;",
        "    _out.where.y = _in.where.y;",
        "    for (size_t _i0 = 0; _i0 < 2; ++_i0) {",
        "        _out.path[_i0].x = _in.path[_i0].x;",
        "        _out.path[_i0].y = _in.path[_i0].y;",
        "    }",
    )
    assert msg_plan.extents == (("::ns::Msg::path", 2),)
    # Each struct comes after those it contains.
    assert [s.c_type for s in plan_for(enum_header(msg, POINT)).structs] == ["ns_Point", "ns_Msg"]


def test_struct_member_names() -> None:
    names = struct(
        "Names",
        field("from", INT32),
        field("restrict", INT32),
        field("errno", INT32),
        field("errno_", INT32),
        field("_fields_", INT32),
        field("self", INT32),
    )
    (plan,) = plan_for(enum_header(names)).structs
    # A renamed member never takes the name of another one.
    assert [f.name for f in plan.fields] == [
        "from_",
        "restrict_",
        "errno2",
        "errno_",
        "_fields_",
        "self",
    ]
    assert plan.fields[4].ctypes_name == "_fields__"


def test_struct_members_named_like_generated_macros() -> None:
    bad = struct("S", field("ns_Color_Red", INT32))
    with pytest.raises(GenerationError, match="has the name of a macro of the generated C"):
        plan_for(enum_header(color(), bad))


def test_struct_names_are_reserved() -> None:
    with pytest.raises(GenerationError, match="'ns_Point_init'"):
        plan_for(enum_header(POINT, function("Point_init")))
    system = VisibleNames(tag_names=frozenset({"ns_Point"}))
    with pytest.raises(GenerationError, match=r"'ns_Point'.*struct, union or enum tag"):
        build_plan(Module("mod", (Header(Path("t.h"), "t.h", (POINT,)),), system))


def test_struct_overload_tokens() -> None:
    plan = plan_for(
        enum_header(
            POINT,
            function("f", Parameter("p", ref("Point"))),
            function("f", Parameter("i", INT32)),
        )
    )
    assert [f.name for f in plan.headers[0].c_functions][1:] == ["ns_f_ns_Point", "ns_f_int32"]


def test_structs_that_no_wrapped_header_defines() -> None:
    elsewhere = StructRef("c:@N@ns@S@P", "ns::P", Location("other.h", 1, 1))
    with pytest.raises(GenerationError, match=r"defined in other\.h, which is not one of"):
        plan_for(enum_header(function("f", Parameter("p", elsewhere))))
    # A class that the header only declares is a class, not an undefined struct.
    handle = StructRef("c:@N@ns@S@K", "ns::K", None)
    plan = plan_for(enum_header(klass("K"), function("f", Parameter("p", handle))))
    assert plan.headers[0].c_functions[-1].signature == ("ns_f(const ns_K* p, int32_t* out_result)")
    unknown = StructRef("c:@N@ns@S@U", "ns::U", None)
    with pytest.raises(GenerationError, match="struct 'ns::U' has no definition"):
        plan_for(enum_header(klass("K"), function("f", Parameter("p", unknown))))


def test_nested_enum_of_a_struct_against_its_members() -> None:
    mode = dataclasses.replace(color(), name="x", scope=("S",), usr="c:@N@ns@S@S@E@x")
    bad = dataclasses.replace(struct("S", field("x", INT32)), enums=(mode,))
    with pytest.raises(GenerationError, match="get the Python name 'x'"):
        plan_for(enum_header(bad))


def test_sources_include_the_headers_of_the_structs_they_use() -> None:
    user = function("f", Parameter("p", ref("Point")))
    plan = plan_for(enum_header(user, name="a.h"), enum_header(POINT, name="b.h"))
    a, b = plan.headers
    assert [h.stem for h in a.foreign_headers] == ["b"]
    assert a.internal_includes == ("b_c_internal.hpp",)
    assert b.internal_includes == ("b_c_internal.hpp",)


def vector(element: object, by_reference: bool = False) -> ContainerType:
    return ContainerType(ContainerKind.VECTOR, element, by_reference=by_reference)  # type: ignore[arg-type]


def test_vector_parameters_and_results() -> None:
    model = Function("scale", ("ns",), (Parameter("v", vector(INT32, True)),), vector(INT32), HERE)
    item = only_function(plan_for(header(model)))
    function = item.c_function
    assert function.signature == (
        "ns_scale(const int32_t* v, size_t v_size, int32_t** out_result, size_t* out_result_size)"
    )
    assert function.statement == (
        "mod_detail::copy_out(::ns::scale(static_cast<const std::vector<int32_t>&>("
        "mod_detail::vector_in<std::vector<int32_t>>(v, v_size))), out_result, out_result_size);"
    )
    assert [c.condition for c in function.owned_checks] == [
        "out_result == nullptr",
        "out_result_size == nullptr",
    ]
    assert [c.condition for c in function.checks] == ["v == nullptr && v_size != 0"]
    assert item.py.args[0].conversions == (
        'v = _runtime.to_array(v, ctypes.c_int32, "v", _runtime.check_int)',
        "v_size = _builtins.len(v)",
    )
    assert item.py.result.read == "_out_data = _runtime.array_items(_out, _out_size.value)"
    assert item.py.result.cleanup == ("if _out:", "    _lib.mod_free(_out)")


def test_vector_elements() -> None:
    strings = only_function(
        plan_for(header(Function("f", ("ns",), (Parameter("w", vector(STRING)),), VOID, HERE)))
    ).c_function
    assert strings.signature == "ns_f(const mod_string* w, size_t w_size)"
    assert strings.checks[1].condition == "mod_detail::has_null_string(w, w_size)"
    assert "strings_in<std::vector<std::string>>(w, w_size)" in strings.statement
    unfixed = dataclasses.replace(
        color(fixed=False), underlying=BUILTIN_TYPES["INT"], enumerators=(Enumerator("A", 0),)
    )
    model = Function("g", ("ns",), (Parameter("c", vector(COLOR)),), vector(COLOR), HERE)
    item = plan_for(enum_header(unfixed, model)).headers[0].items[1]
    assert isinstance(item, FunctionPlan)
    assert (
        """mod_detail::enum_in<::ns::Color>{0, 1, "an element of 'c' is not a value of """
        """ns::Color (0..1)"}""" in item.c_function.statement
    )
    assert "mod_detail::cast_to<ns_Color>()" in item.c_function.statement
    assert item.py.result.value == "_runtime.enum_results(_types.ns_Color, _out_data)"
    model = Function("h", ("ns",), (Parameter("p", vector(ref("Point"))),), VOID, HERE)
    item = plan_for(enum_header(POINT, model)).headers[0].items[1]
    assert isinstance(item, FunctionPlan)
    assert "[](const ns_Point& _item) { return mod_detail::from_c(_item); }" in (
        item.c_function.statement
    )


def test_vector_overload_tokens() -> None:
    plan = plan_for(
        header(
            function("f", Parameter("v", vector(INT32))),
            function("f", Parameter("v", vector(STRING))),
        )
    )
    assert [f.name for f in plan.headers[0].c_functions] == [
        "ns_f_vector_int32",
        "ns_f_vector_string",
    ]
    # const& and && (a value) are the same in C: a clash, not a silent choice.
    with pytest.raises(GenerationError, match="'ns_f_vector_int32'"):
        plan_for(
            header(
                function("f", Parameter("v", vector(INT32, True))),
                function(
                    "f",
                    Parameter(
                        "v", ContainerType(ContainerKind.VECTOR, INT32, rvalue_reference=True)
                    ),
                ),
            )
        )


def test_array_and_span_parameters_and_results() -> None:
    array = ContainerType(ContainerKind.ARRAY, DOUBLE, length=4, by_reference=True)
    span = ContainerType(ContainerKind.SPAN, INT32)
    model = Function(
        "f",
        ("ns",),
        (Parameter("a", array), Parameter("s", span)),
        dataclasses.replace(array, by_reference=False),
        HERE,
    )
    function = only_function(plan_for(header(model))).c_function
    assert function.signature == (
        "ns_f(const double a[4], const int32_t* s, size_t s_size, double out_result[4])"
    )
    # The span of a scalar is a view of the caller's memory, not a copy.
    assert "std::span<const int32_t>(s, s_size)" in function.statement
    assert [c.condition for c in function.owned_checks] == []
    assert [c.condition for c in function.checks] == [
        "a == nullptr",
        "s == nullptr && s_size != 0",
        "out_result == nullptr",
    ]


def test_array_and_span_overload_tokens() -> None:
    plan = plan_for(
        header(
            function("f", Parameter("a", ContainerType(ContainerKind.ARRAY, INT32, length=3))),
            function("f", Parameter("s", ContainerType(ContainerKind.SPAN, INT32))),
            function("f", Parameter("v", vector(INT32))),
        )
    )
    assert [f.name for f in plan.headers[0].c_functions] == [
        "ns_f_array3_int32",
        "ns_f_span_int32",
        "ns_f_vector_int32",
    ]


def callback(result: object, *arguments: object, references: tuple[bool, ...] = ()) -> CallbackType:
    refs = references or (False,) * len(arguments)
    return CallbackType(result, arguments, refs)  # type: ignore[arg-type]


def test_callback_parameters() -> None:
    f = callback(INT32, INT32, references=(True,))
    model = Function("sum", ("ns",), (Parameter("f", f),), INT32, HERE)
    item = only_function(plan_for(header(model)))
    function = item.c_function
    assert function.signature == (
        "ns_sum(void (*f)(void*, int32_t, int32_t*), void* f_user_data, "
        "void (*f_release)(void*), int32_t* out_result)"
    )
    # The owner comes first: it releases the callback on every return.
    assert function.prologue == ("mod_detail::callback_owner _owner1(f_release, f_user_data);",)
    assert (
        "mod_detail::make_callback<::std::function<int32_t (const int32_t&)>, int32_t>(f, _owner1, "
        "[](decltype(f) _callback, void* _user_data, int32_t _arg1) -> int32_t "
        "{ int32_t _result{}; _callback(_user_data, _arg1, &_result); return _result; })"
    ) in function.statement
    assert function.ctypes_argtypes == (
        "[_types._callback__int32__int32__type, ctypes.c_void_p, _runtime.Release, "
        "ctypes.POINTER(ctypes.c_int32)]"
    )
    assert item.py.args[0].conversions == ('f = _runtime.to_callback(f, "f")',)
    assert item.py.registrations == (
        ("f, f_user_data, f_release", ("f", "_types._callback__int32__int32", '"f"')),
    )
    body = item.py.body(4)
    # Registered last, right before the call; the scope is closed in any case.
    assert body.index("_scope.callback(") < body.index("_status = _scope.call(_bind().ns_sum,")
    assert "    finally:\n        _scope.close()" in body


def test_callback_arguments_and_thunks() -> None:
    sink = callback(VOID, COLOR, ref("Point", True), STRING_REF, VIEW, C_STRING)
    model = Function("each", ("ns",), (Parameter("f", sink),), VOID, HERE)
    plan = plan_for(enum_header(color(), POINT, model))
    function = plan.headers[0].c_functions[-1]
    assert function.signature.startswith(
        "ns_each(void (*f)(void*, ns_Color, const ns_Point*, const char*, size_t, "
        "const char*, size_t, const char*), "
    )
    assert (
        "::std::function<void (::ns::Color, const ::ns::Point&, const ::std::string&, "
        "::std::string_view, const char*)>"
    ) in function.statement
    assert "mod_detail::address_of(mod_detail::to_c(_arg2))" in function.statement
    assert "_arg3.data(), _arg3.size()" in function.statement
    (thunk,) = plan.thunks
    assert thunk.name == "_callback__void__ns_Color__ns_Point__string__string__cstr"
    assert thunk.params == (
        "_user_data",
        "_arg1",
        "_arg2",
        "_arg3",
        "_arg3_size",
        "_arg4",
        "_arg4_size",
        "_arg5",
    )
    assert thunk.arguments == (
        "_runtime.enum_result(ns_Color, _arg1)",
        "_ns_Point__from_c(_arg2.contents)",
        "_runtime.callback_string(_arg3, _arg3_size)",
        "_runtime.callback_string(_arg4, _arg4_size)",
        "_runtime.callback_c_string(_arg5)",
    )
    assert thunk.check is None


def test_callback_results() -> None:
    unfixed = dataclasses.replace(
        color(fixed=False), underlying=BUILTIN_TYPES["INT"], enumerators=(Enumerator("A", 0),)
    )
    model = Function("g", ("ns",), (Parameter("f", callback(COLOR)),), VOID, HERE)
    plan = plan_for(enum_header(unfixed, model))
    statement = plan.headers[0].c_functions[-1].statement
    # 0 is always a value: an invalid result becomes the default.
    assert (
        "return _result < 0 || _result > 1 ? ::ns::Color() : static_cast<::ns::Color>(_result);"
        in (statement)
    )
    (thunk,) = plan.thunks
    assert thunk.check == (
        "_runtime.to_enum",
        "_value",
        "ns_Color",
        "ctypes.c_int",
        "_name",
        "(0, 1)",
        'what="the result of callback"',
    )
    assert "_out_result[0] = _runtime.to_enum(" in thunk.body


def test_callback_names() -> None:
    model = function(
        "f",
        Parameter("g", callback(VOID, INT32, INT32)),
        Parameter("g_user_data", INT32),
        Parameter("_arg2", INT32),
        Parameter("_owner1", INT32),
        Parameter("h", callback(VOID)),
    )
    names = [p.name for p in only_function(plan_for(header(model))).c_function.params]
    assert names == [
        "g",
        "g_user_data_",
        "g_release",
        "g_user_data",
        "_arg2_",
        "_owner1_",
        "h",
        "h_user_data",
        "h_release",
        "out_result",
    ]


def test_callback_overload_tokens() -> None:
    plan = plan_for(
        header(
            function("f", Parameter("g", callback(VOID, INT32))),
            function("f", Parameter("g", callback(INT32))),
        )
    )
    assert [f.name for f in plan.headers[0].c_functions] == [
        "ns_f_function_void_int32",
        "ns_f_function_int32",
    ]


def test_thunks_are_shared_by_the_headers() -> None:
    first = header(function("f", Parameter("g", callback(VOID, INT32))), name="a.h")
    second = header(function("h", Parameter("g", callback(VOID, INT32))), name="b.h")
    plan = plan_for(first, second)
    assert [thunk.name for thunk in plan.thunks] == ["_callback__void__int32"]


# Wrapped classes ---------------------------------------------------------------------


def cref(name: str, form: ClassForm = ClassForm.CONST_REFERENCE, namespace: str = "ns") -> ClassRef:
    return ClassRef(f"c:@N@{namespace}@S@{name}", f"{namespace}::{name}", HERE, form)


def two_headers(tree: Class) -> tuple[Header, Header]:
    node = klass("Node", methods=(method("value", is_const=True),))
    return header(node, name="node.h"), header(tree, name="tree.h")


def test_class_parameters() -> None:
    forms = ClassForm
    tree = klass(
        "Tree",
        methods=(
            method(
                "f",
                Parameter("a", cref("Node", forms.CONST_REFERENCE)),
                Parameter("b", cref("Node", forms.REFERENCE)),
                Parameter("c", cref("Node", forms.CONST_POINTER)),
                Parameter("d", cref("Node", forms.POINTER)),
                Parameter("e", cref("Node", forms.VALUE)),
                is_const=True,
            ),
        ),
    )
    plan = plan_for(*two_headers(tree))
    (item,) = [i for i in plan.headers[1].items if isinstance(i, ClassPlan)]
    (member,) = item.members
    function = member.function
    assert function.signature == (
        "ns_Tree_f(const ns_Tree* self, const ns_Node* a, ns_Node* b, const ns_Node* c, "
        "ns_Node* d, const ns_Node* e, int32_t* out_result)"
    )
    assert function.statement.startswith(
        "*out_result = reinterpret_cast<const ::ns::Tree*>(self)->f("
        "*reinterpret_cast<const ::ns::Node*>(a), *reinterpret_cast<::ns::Node*>(b), "
        "reinterpret_cast<const ::ns::Node*>(c), reinterpret_cast<::ns::Node*>(d), "
        "*reinterpret_cast<const ::ns::Node*>(e))"
    )
    assert [c.message for c in function.checks][:6] == [
        "'self' is NULL",
        "'a' is NULL",
        "'b' is NULL",
        "'c' is NULL",
        "'d' is NULL",
        "'e' is NULL",
    ]
    assert [p.ctypes_type for p in function.params[1:6]] == ["_types._ns_Node__p"] * 5
    # Only a copy needs the class to be copyable.
    (copyable,) = function.compile_checks
    assert "std::is_copy_constructible<::ns::Node>" in copyable
    assert "parameter 'e' takes ns::Node by value" in copyable
    # Handles are read last, in the call; a non-const use needs a writable object.
    assert member.call_args[:6] == (
        "self._ptr()",
        "a._ptr()",
        "b._mptr()",
        "c._ptr()",
        "d._mptr()",
        "e._ptr()",
    )
    assert [conversion for arg in member.args for conversion in arg.conversions] == [
        f'_runtime.check_object({name}, _node.Node, "{name}")' for name in "abcde"
    ]
    # The class is that of another header, which is imported when the function runs.
    assert member.imports == ("from . import node as _node",)
    assert "from . import node as _node" in member.body(8)


def test_classes_of_the_same_header_are_named_directly() -> None:
    node = klass("Node")
    tree = klass("Tree", methods=(method("f", Parameter("a", cref("Node", ClassForm.REFERENCE))),))
    plan = plan_for(header(node, tree))
    member = plan.headers[0].classes[1].members[0]
    assert member.imports == ()
    assert member.args[0].conversions == ('_runtime.check_object(a, Node, "a")',)
    assert plan.headers[0].included_headers == ()


def test_the_headers_of_the_classes_it_uses_are_included() -> None:
    tree = klass("Tree", methods=(method("f", Parameter("a", cref("Node"))),))
    node_header, tree_header = two_headers(tree)
    plan = plan_for(node_header, tree_header)
    assert [h.stem for h in plan.headers[1].foreign_class_headers] == ["node"]
    assert [h.stem for h in plan.headers[1].included_headers] == ["node"]
    assert plan.headers[0].included_headers == ()
    files = render(plan)
    assert '#include "node.h"' in files["c/tree_c.cpp"]
    assert '#include "node.h"' not in files["c/node_c.cpp"].partition('#include "node_c.h"')[2]


def test_a_class_declared_in_one_header_is_seen_as_a_struct_in_another() -> None:
    """A header that only forward-declares a class sees it by value or by const
    reference as an (undefined) struct."""
    declared = StructRef("c:@N@ns@S@Node", "ns::Node", None, True)
    by_value = StructRef("c:@N@ns@S@Node", "ns::Node", None, False)
    tree = klass("Tree", methods=(method("f", Parameter("a", declared), Parameter("b", by_value)),))
    plan = plan_for(*two_headers(tree))
    (item,) = [i for i in plan.headers[1].items if isinstance(i, ClassPlan)]
    function = item.members[0].function
    assert function.signature.startswith(
        "ns_Tree_f(ns_Tree* self, const ns_Node* a, const ns_Node* b, int32_t* out_result)"
    )
    assert len(function.compile_checks) == 1  # only b is a copy


def test_class_overloads_get_distinct_names() -> None:
    forms = [
        ClassForm.VALUE,
        ClassForm.REFERENCE,
        ClassForm.CONST_POINTER,
        ClassForm.POINTER,
    ]
    tree = klass("Tree", methods=tuple(method("f", Parameter("a", cref("Node", f))) for f in forms))
    plan = plan_for(*two_headers(tree))
    (item,) = [i for i in plan.headers[1].items if isinstance(i, ClassPlan)]
    assert [m.function.name for m in item.members] == [
        "ns_Tree_f_ns_Node",
        "ns_Tree_f_ns_Node_ref",
        "ns_Tree_f_ns_Node_cptr",
        "ns_Tree_f_ns_Node_ptr",
    ]


def test_constructors_keep_the_references_and_pointers_they_get() -> None:
    forms = ClassForm
    tree = klass(
        "Tree",
        constructors=(
            Constructor(
                (
                    Parameter("a", cref("Node", forms.REFERENCE)),
                    Parameter("b", cref("Node", forms.CONST_POINTER)),
                    Parameter("c", cref("Node", forms.VALUE)),
                    Parameter("d", INT32),
                ),
                HERE,
            ),
        ),
    )
    plan = plan_for(*two_headers(tree))
    (item,) = [i for i in plan.headers[1].items if isinstance(i, ClassPlan)]
    (constructor,) = item.constructors
    assert constructor.retain_statements("self") == (
        "_runtime.retain(self._retained, a)",
        "_runtime.retain(self._retained, b)",
    )
    # Methods keep nothing by default.
    method_plan = plan_for(
        *two_headers(
            klass("Tree", methods=(method("f", Parameter("a", cref("Node", forms.REFERENCE))),))
        )
    )
    (tree_item,) = [i for i in method_plan.headers[1].items if isinstance(i, ClassPlan)]
    assert tree_item.members[0].retain_statements("self") == ()


def test_non_const_methods_need_a_writable_object() -> None:
    node = klass("Node", methods=(method("get", is_const=True), method("set")))
    item = plan_for(header(node)).headers[0].items[0]
    assert isinstance(item, ClassPlan)
    assert [m.call_args[0] for m in item.members] == ["self._ptr()", "self._mptr()"]


@pytest.mark.parametrize(
    ("type_", "message"),
    [
        (
            ClassRef(
                "c:@N@other@S@X", "other::X", Location("elsewhere.h", 3, 1), ClassForm.POINTER
            ),
            r"class 'other::X' is not a class of this module: it is defined in elsewhere\.h, "
            "which is not one of the wrapped headers",
        ),
        (
            ClassRef("c:@N@other@S@X", "other::X", None, ClassForm.POINTER),
            "class 'other::X' is not a class of this module: no wrapped header defines it",
        ),
        (
            ClassRef("c:@N@other@S@X", "other::X", HERE, ClassForm.POINTER, system=True),
            "class 'other::X' of a system header is not a class of this module",
        ),
        (cref("Node", ClassForm.UNIQUE), "std::unique_ptr parameters are not supported yet"),
    ],
)
def test_classes_that_the_module_does_not_wrap(type_: ClassRef, message: str) -> None:
    model = function("f", Parameter("x", type_))
    with pytest.raises(GenerationError, match=message):
        plan_for(header(klass("Node"), model))


def test_pointers_to_value_structs_that_a_header_only_declares() -> None:
    pointer = ClassRef("c:@N@ns@S@Point", "ns::Point", None, ClassForm.POINTER)
    with pytest.raises(GenerationError, match="'ns::Point' is a value struct"):
        plan_for(header(POINT, function("f", Parameter("p", pointer))))  # type: ignore[arg-type]


def class_results(*results: ClassRef, static: bool = False, params: tuple[Parameter, ...] = ()):  # type: ignore[no-untyped-def]
    methods = tuple(
        Method(f"m{index}", params, result, False, static, HERE)
        for index, result in enumerate(results)
    )
    tree = klass("Tree", methods=methods)
    plan = plan_for(*two_headers(tree))
    (item,) = [i for i in plan.headers[1].items if isinstance(i, ClassPlan)]
    return plan, item


def test_results_by_value_and_unique_ptr_are_owned() -> None:
    plan, item = class_results(cref("Node", ClassForm.VALUE), cref("Node", ClassForm.UNIQUE))
    value, unique = item.members
    assert value.function.signature == "ns_Tree_m0(ns_Tree* self, ns_Node** out_result)"
    assert value.function.statement == (
        "*out_result = reinterpret_cast<ns_Node*>(new ::ns::Node("
        "reinterpret_cast<::ns::Tree*>(self)->m0()));"
    )
    assert unique.function.statement == (
        "*out_result = reinterpret_cast<ns_Node*>("
        "reinterpret_cast<::ns::Tree*>(self)->m1().release());"
    )
    for member in (value, unique):
        function = member.function
        # The output is NULL on every failure.
        assert [c.condition for c in function.owned_checks] == ["out_result == nullptr"]
        assert function.resets == ("*out_result = nullptr;",)
        assert "owned by the caller" in function.notes[0]
        assert "destroy it with ns_Node_destroy" in function.notes[0]
        # The object exists before the call, and is destroyed by its collection.
        assert member.result.setup == (
            "_result = _node.Node.__new__(_node.Node)",
            "_out = _result._watch()",
        )
        assert member.call_args[-1] == "_out"
        assert member.imports == ("from . import node as _node",)
    assert value.result.finish == ("_result._adopt(_out)",)
    assert value.result.value == "_result"
    assert "(NULL if empty)" in unique.function.notes[0]
    assert unique.result.finish == ("if _out:", "    _result._adopt(_out)")
    assert unique.result.value == "_result if _out else None"
    assert plan.headers[1].value_results == (("::ns::Node", "ns::Node"),)


def test_results_by_reference_are_borrowed() -> None:
    plan, item = class_results(
        cref("Node", ClassForm.REFERENCE), cref("Node", ClassForm.CONST_REFERENCE)
    )
    mutable, constant = item.members
    assert mutable.function.signature == "ns_Tree_m0(ns_Tree* self, ns_Node** out_result)"
    assert constant.function.signature == "ns_Tree_m1(ns_Tree* self, const ns_Node** out_result)"
    assert mutable.function.statement == (
        "*out_result = reinterpret_cast<ns_Node*>("
        "mod_detail::object_address(reinterpret_cast<::ns::Tree*>(self)->m0()));"
    )
    assert constant.function.statement == (
        "*out_result = reinterpret_cast<const ns_Node*>("
        "mod_detail::object_address(reinterpret_cast<::ns::Tree*>(self)->m1()));"
    )
    for member in (mutable, constant):
        # Nothing is allocated: a plain output.
        assert member.function.owned_checks == ()
        assert [c.condition for c in member.function.checks][-1] == "out_result == nullptr"
        assert member.function.notes == (
            "*out_result: borrowed from self, never NULL; do not destroy it.",
        )
    assert mutable.result.value == "_runtime.borrow(_node.Node, _out, (self,), False)"
    assert constant.result.value == "_runtime.borrow(_node.Node, _out, (self,), True)"
    assert plan.headers[1].value_results == ()


def test_what_a_function_returns_by_reference_is_borrowed_from_its_arguments() -> None:
    forms = ClassForm
    result = cref("Node", forms.REFERENCE)
    arguments = (
        Parameter("a", cref("Node", forms.REFERENCE)),
        Parameter("n", INT32),
        Parameter("b", cref("Node", forms.CONST_POINTER)),
        Parameter("c", cref("Node", forms.VALUE)),
    )
    _, item = class_results(result, static=True, params=arguments)
    assert item.members[0].result.value == "_runtime.borrow(_node.Node, _out, (a, b), False)"
    _, only = class_results(result, static=True, params=arguments[:1])
    assert only.members[0].result.value == "_runtime.borrow(_node.Node, _out, (a,), False)"
    _, none = class_results(result, static=True)
    assert none.members[0].result.value == "_runtime.borrow(_node.Node, _out, (), False)"
    assert none.members[0].function.notes == (
        "*out_result: borrowed, never NULL; do not destroy it.",
    )


def test_the_classes_a_header_creates_are_checked_before_cxx17() -> None:
    plan, _ = class_results(cref("Node", ClassForm.VALUE))
    tree_header = plan.headers[1]
    # Its own class, and the class of another header that it returns by value.
    assert tree_header.created_classes == (("::ns::Tree", "ns::Tree"), ("::ns::Node", "ns::Node"))
    source = render(plan)["c/tree_c.cpp"]
    assert "static_assert(alignof(::ns::Node) <= mod_detail::new_alignment" in source
    assert "static_assert(std::is_move_constructible<::ns::Node>::value," in source
    assert "_MSVC_LANG >= 201703L" in source
    # No result by value, no check.
    other, _ = class_results(cref("Node", ClassForm.REFERENCE))
    assert "is_move_constructible" not in render(other)["c/tree_c.cpp"]


def test_the_header_says_who_owns_what() -> None:
    plan, _ = class_results(cref("Node", ClassForm.VALUE), cref("Node", ClassForm.REFERENCE))
    header_text = render(plan)["c/tree_c.h"]
    assert (
        "/* int32_t ns::Tree::m0()\n"
        " * *out_result: owned by the caller; destroy it with ns_Node_destroy. */"
    ) not in header_text  # the description spells the result type
    assert " * *out_result: owned by the caller; destroy it with ns_Node_destroy. */" in header_text
    assert " * *out_result: borrowed from self, never NULL; do not destroy it. */" in header_text


def test_the_ownership_of_a_result_is_part_of_the_api_fingerprint() -> None:
    """The C signatures are the same, but who destroys the object is not."""

    def fingerprint(form: ClassForm) -> str:
        files = render(class_results(cref("Node", form))[0])
        match = re.search(r'API_FINGERPRINT = "(\w+)"', files["python/mod/_runtime.py"])
        assert match is not None
        return match.group(1)

    owned = fingerprint(ClassForm.UNIQUE)
    assert owned != fingerprint(ClassForm.REFERENCE)
    assert owned == fingerprint(ClassForm.UNIQUE)


@pytest.mark.parametrize("form", [ClassForm.POINTER, ClassForm.CONST_POINTER])
def test_raw_pointer_results_need_an_annotation(form: ClassForm) -> None:
    model = Function("f", ("ns",), (), cref("Node", form), HERE)
    with pytest.raises(GenerationError, match="returns a raw pointer to the wrapped class"):
        plan_for(header(klass("Node"), model))


def test_a_parameter_cannot_take_the_name_of_a_module_alias() -> None:
    tree = klass("Tree", methods=(method("f", Parameter("_node", cref("Node"))),))
    plan = plan_for(*two_headers(tree))
    (item,) = [i for i in plan.headers[1].items if isinstance(i, ClassPlan)]
    assert item.members[0].function.params[1].name == "_node_"
