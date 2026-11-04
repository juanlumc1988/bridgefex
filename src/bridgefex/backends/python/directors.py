# SPDX-License-Identifier: Apache-2.0
"""The Python view of the directors of a module: a Python class that derives from a wrapped
class and overrides some of its virtual methods makes objects whose C++ object is the
director of the class (see :class:`bridgefex.plan.DirectorPlan`)."""

from __future__ import annotations

from dataclasses import dataclass

from ...plan import DirectorEntry, DirectorPlan
from .model import PyCallable
from .thunks import PyDirectorThunk


@dataclass(frozen=True, slots=True)
class PySlot:
    """A virtual method that a Python subclass may override."""

    entry: DirectorEntry
    name: str
    """The name of the method that overrides it: the name of its wrapper if it is public."""

    ctypes_name: str
    """The member of the ctypes mirror of the vtable."""

    thunk: PyDirectorThunk
    base: PyCallable | None
    """The call of the implementation of the class (``_base_<name>``): None if it is pure."""

    wrapper: str | None
    """How the class wraps the method for an object that is a director: ``own`` (the method
    that the class declares, which calls the base on a director), ``inherited`` (a public
    method of a base class, which the class repeats so that its base call reaches its own
    director), ``protected`` (not part of the API: only an override calls it) or None."""

    @property
    def private_name(self) -> str:
        return f"_base_{self.name}"

    @property
    def parameters(self) -> str:
        return self.base.parameter_list if self.base is not None else ""

    @property
    def signature(self) -> str:
        return f"self, {self.parameters}" if self.parameters else "self"

    @property
    def is_pure(self) -> bool:
        return self.entry.is_pure

    @property
    def thunk_name(self) -> str:
        return self.thunk.name


@dataclass(frozen=True, slots=True)
class PyAlias:
    """A method that a base class has for a virtual method, under the name that the base
    gives it (a class that declares one overload hides the others, so its wrapper loses the
    suffix): the class repeats it, calling the method it has for it."""

    name: str
    target: PySlot

    @property
    def signature(self) -> str:
        return self.target.signature

    @property
    def arguments(self) -> str:
        return self.target.parameters


@dataclass(frozen=True, slots=True)
class PyDirectorConstructor:
    """The creation of a director: ``callable`` calls the create function."""

    name: str
    """What the Python class calls it: ``__init__``, or ``create<suffix>``."""

    callable: PyCallable
    plain: str | None
    """The name of the constructor of the class with the same parameters, if it has one: the
    creation then makes a director only if the class that is instantiated overrides something."""

    @property
    def private_name(self) -> str:
        return "_init_director" if self.name == "__init__" else f"_director_{self.name}"


@dataclass(frozen=True, slots=True)
class PyDirector:
    plan: DirectorPlan
    slots: tuple[PySlot, ...]
    constructors: tuple[PyDirectorConstructor, ...]
    """What creates a director: the constructors of the class if it has ordinary ones (each
    makes a director when the class instantiated overrides something), else those of the
    director, which are the only way to create an object of the class."""

    own_constructors: bool
    """The class has ordinary constructors."""

    aliases: tuple[PyAlias, ...] = ()

    def creation(self, plain: str) -> PyDirectorConstructor | None:
        """The creation of the director for the constructor of the class called ``plain``."""
        return next((item for item in self.constructors if item.plain == plain), None)

    @property
    def class_name(self) -> str:
        return self.plan.class_name

    @property
    def struct_name(self) -> str:
        """The opaque handle of the director, in ``_types.py``."""
        return f"_{self.plan.director_type}"

    @property
    def pointer_name(self) -> str:
        return f"_{self.plan.director_type}__p"

    @property
    def vtable_name(self) -> str:
        return f"_{self.plan.vtable_type}"

    @property
    def wrappers(self) -> tuple[PySlot, ...]:
        """The methods that the class has to add (see :attr:`PySlot.wrapper`)."""
        return tuple(slot for slot in self.slots if slot.wrapper in ("inherited", "protected"))

    @property
    def bases(self) -> tuple[PySlot, ...]:
        return tuple(slot for slot in self.slots if slot.base is not None)
