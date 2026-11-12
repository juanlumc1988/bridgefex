# SPDX-License-Identifier: Apache-2.0
"""A Java class for an enum of C++: a final class with a constant for each enumerator."""

from __future__ import annotations

from ...errors import Diagnostic, GenerationError
from ...plan import EnumPlan
from .mapping import RANGES, Unsupported, scalar
from .naming import pascal, upper_snake
from .source import Source, escape_doc

MAX_ENUMERATORS = 2000
"""A class initialiser of Java is at most 64 KB: the enumerators of an enum (a constant and a place
in the table of its values each) take about 25 bytes each, which is 2600 of them."""


def _literal(value: int, unsigned_64: bool) -> str:
    """A Java ``long`` literal for an enumerator (a 64-bit unsigned one is its bits)."""
    if unsigned_64 and value >= 2**63:
        value -= 2**64
    return f"{value}L"


def enum_class(src: Source, enum: EnumPlan, *, nested: bool) -> None:
    """A final class with a constant for each enumerator: any value of the underlying type can be
    one (a flag, a strong integer type without enumerators), where a Java ``enum`` could not."""
    name = pascal(enum.model.name)
    underlying = enum.model.underlying.c_name
    info = scalar(underlying)
    constants = _constants(enum)
    if len(constants) > MAX_ENUMERATORS:
        raise Unsupported(
            f"the enum '{enum.model.qualified_name}' has {len(constants)} enumerators, and the "
            f"Java bindings take {MAX_ENUMERATORS} at most (a class of Java cannot initialise more "
            "constants)",
            enum.model.location,
        )
    unsigned_64 = info.unsigned
    src.doc(
        enum.comment,
        "A final class with a constant for each enumerator, and an object for any other value of "
        "the underlying type (see of(long)).",
    )
    header = f"public {'static ' if nested else ''}final class {name}"
    src.open(f"{header} implements java.lang.Comparable<{name}>")
    for constant, cpp_name, value in constants:
        src.line(f"/** {escape_doc(cpp_name)}. */")
        literal = _literal(value, unsigned_64)
        src.line(f'public static final {name} {constant} = new {name}("{constant}", {literal});')
    src.line()
    values = ", ".join(constant for constant, _, _ in constants)
    src.line(f"private static final {name}[] bridgefexValues = {{{values}}};")
    src.line()
    src.line("private final java.lang.String name;")
    src.line("private final long value;")
    src.line()
    src.open(f"private {name}(java.lang.String name, long value)")
    src.line("this.name = name;")
    src.line("this.value = value;")
    src.close()
    src.line()
    _factory(src, name, underlying, unsigned_64)
    _accessors(src, name, unsigned_64)
    _object_methods(src, name, unsigned_64)
    src.close()


def _constants(enum: EnumPlan) -> list[tuple[str, str, int]]:
    constants: list[tuple[str, str, int]] = []
    seen: dict[str, str] = {}
    for item in enum.enumerators:
        constant = upper_snake(item.model_name)
        if constant in seen:
            raise GenerationError(
                [
                    Diagnostic(
                        f"the enumerators '{seen[constant]}' and '{item.model_name}' of "
                        f"'{enum.model.qualified_name}' get the Java name '{constant}'"
                    )
                ]
            )
        seen[constant] = item.model_name
        constants.append((constant, item.model_name, item.value))
    return constants


def _factory(src: Source, name: str, underlying: str, unsigned_64: bool) -> None:
    info = scalar(underlying)
    bounds = RANGES.get(underlying)
    src.doc(
        "The enumerator with this value, or an object for a value that no enumerator has "
        f"(its name() is null). The value is that of {underlying}.",
        *(["A 64-bit unsigned value is given by its bits."] if unsigned_64 else []),
    )
    src.open(f"public static {name} of({info.annotation}long value)")
    if bounds is not None:
        src.line(f'BridgefexRuntime.inRange(value, {bounds[0]}L, {bounds[1]}L, "{underlying}");')
    src.open(f"for ({name} item : bridgefexValues)")
    src.open("if (item.value == value)")
    src.line("return item;")
    src.close()
    src.close()
    src.line(f"return new {name}(null, value);")
    src.close()
    src.line()


def _accessors(src: Source, name: str, unsigned_64: bool) -> None:
    src.doc("The enumerators, in the order of their declaration.")
    src.open(f"public static {name}[] values()")
    src.line("return bridgefexValues.clone();")
    src.close()
    src.line()
    src.doc("The value in the underlying type.")
    if unsigned_64:
        src.line("@BridgefexUnsigned")
    src.open("public long value()")
    src.line("return value;")
    src.close()
    src.line()
    src.doc("The name of the enumerator, or null for a value that no enumerator has.")
    src.open("public java.lang.String name()")
    src.line("return name;")
    src.close()


def _object_methods(src: Source, name: str, unsigned_64: bool) -> None:
    src.line()
    src.line("@java.lang.Override")
    src.open("public boolean equals(java.lang.Object other)")
    src.line(f"return other instanceof {name} that && that.value == value;")
    src.close()
    src.line()
    src.line("@java.lang.Override")
    src.open("public int hashCode()")
    src.line("return java.lang.Long.hashCode(value);")
    src.close()
    src.line()
    src.line("@java.lang.Override")
    src.open(f"public int compareTo({name} other)")
    compare = "compareUnsigned" if unsigned_64 else "compare"
    src.line(f"return java.lang.Long.{compare}(value, other.value);")
    src.close()
    src.line()
    src.line("@java.lang.Override")
    src.open("public java.lang.String toString()")
    shown = "java.lang.Long.toUnsignedString(value)" if unsigned_64 else "value"
    src.line(f'return name != null ? name : "{name}(" + {shown} + ")";')
    src.close()
