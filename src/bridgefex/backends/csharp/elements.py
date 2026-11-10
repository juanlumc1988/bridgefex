# SPDX-License-Identifier: Apache-2.0
"""The elements of the containers (``std::vector``, ``std::array``, ``std::span``): how one is
shown in C#, how it crosses the boundary, and how it is converted from one to the other."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...model import EnumRef, ScalarType, StringType, StructRef
from ...plan import ElementSpec
from .mapping import Unsupported, scalar

if TYPE_CHECKING:
    from .lower import Names


@dataclass(frozen=True, slots=True)
class Element:
    """An element type of a container."""

    public: str
    """The C# type of an element (``int``, ``Color``, ``Point``, ``string``)."""

    native: str
    """What the P/Invoke declaration points to (``int``, ``byte``, ``Native.vec_Point``)."""

    kind: str
    """``plain`` (a number: the same bits in C# and in C), ``bool``, ``enum``, ``converted``
    (a C ``long``, which has 32 bits where C# has 64), ``struct`` or ``string``."""

    to_native: str = "{}"
    """The expression (``{}`` is the C# value) that gives the native value, for the kinds that
    are converted one by one."""

    from_native: str = "{}"
    """The expression (``{}`` is the native value) that gives the C# value."""

    check: tuple[int, int] | None = None
    """The values that an enum without a fixed type holds."""

    @property
    def pinnable(self) -> bool:
        """True if a span of them can be given to C++ as it is, without a copy."""
        return self.kind in ("plain", "bool", "enum")

    @property
    def result_native(self) -> str:
        """The type that a result's declaration points to: a string comes back as ``StringOut``."""
        return "Native.StringOut" if self.kind == "string" else self.native


def element_of(spec: ElementSpec | None, names: Names) -> Element:
    """The element type of a container of the plan."""
    if spec is None:
        raise Unsupported("a container without its element type")
    type_ = spec.type
    if isinstance(type_, ScalarType):
        info = scalar(type_.c_name)
        if type_.c_name == "bool":
            return Element("bool", "byte", "bool", info.to_native, info.from_native)
        if info.converts:
            return Element(info.public, info.native, "converted", info.to_native, info.from_native)
        return Element(info.public, info.native, "plain")
    if isinstance(type_, EnumRef):
        if spec.enum is None:
            raise Unsupported(f"the enum '{type_.cpp_spelling}' of a container")
        plan = spec.enum
        underlying = scalar(plan.model.underlying.c_name)
        public = names.enum(plan)
        if underlying.converts:
            return Element(
                public,
                underlying.native,
                "converted",
                underlying.to_native.replace("{}", f"({underlying.public}){{}}"),
                f"({public}){underlying.from_native}",
                plan.model.valid_range,
            )
        return Element(
            public,
            underlying.native,
            "enum",
            "{}",
            f"({public}){{}}",
            plan.model.valid_range,
        )
    if isinstance(type_, StructRef):
        if spec.struct is None:
            raise Unsupported(f"the struct '{type_.cpp_spelling}' of a container")
        return Element(names.struct(spec.struct), f"Native.{spec.struct.c_type}", "struct")
    if isinstance(type_, StringType):
        return Element("string", "Native.StringIn", "string")
    raise Unsupported(f"the type '{type_.cpp_spelling}' of a container's element")
