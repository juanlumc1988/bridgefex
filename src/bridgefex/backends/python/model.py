# SPDX-License-Identifier: Apache-2.0
"""The Python side of a wrapped function: what the generated Python function does
before and after the call of the C function."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ... import naming
from ...plan import CFunction

# Longest line written in one piece in the generated Python code.
MAX_LINE_LENGTH = 99


def python_call(head: str, arguments: Sequence[str], indent: int) -> str:
    """``head(arguments)`` indented by ``indent`` spaces: on one line if it fits,
    else with the arguments on the next line, else one per line."""
    pad = " " * indent
    one_line = f"{pad}{head}({', '.join(arguments)})"
    if len(one_line) <= MAX_LINE_LENGTH:
        return one_line
    inner = f"{pad}    {', '.join(arguments)}"
    if len(inner) <= MAX_LINE_LENGTH:
        return f"{pad}{head}(\n{inner}\n{pad})"
    lines = [f"{pad}{head}("]
    lines += [f"{pad}    {argument}," for argument in arguments]
    lines.append(f"{pad})")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class PyArg:
    name: str
    conversions: tuple[str, ...]
    """Statements that check and convert the argument, rebinding its name (and
    computing derived locals), before the call."""

    registration: tuple[str, tuple[str, ...]] | None = None
    """For a callback: the targets and the arguments of the statement that
    registers it in the call's scope, after every conversion (which may fail)
    and right before the call."""

    imports: tuple[str, ...] = ()
    """The header modules (stems) of other headers whose classes the conversions
    name, as ``_<stem>``: imported when the function runs, so that header modules
    never import each other at module level."""

    retain: bool = False
    """The C++ object keeps a pointer to this argument (a wrapped object): the
    Python object that owns the C++ one keeps the argument alive."""

    borrowable: bool = False
    """A reference or a pointer to a wrapped object: what a function without
    ``self`` returns by reference is borrowed from its arguments."""

    transfer: bool = False
    """The C++ code takes the object (a std::unique_ptr): the Python object
    gives it up, last before the call (see :func:`_runtime.take`)."""

    view: str | None = None
    """For a transferred object of a class that others derive from: the method that
    gives its handle as that class, which is not the handle of its own class."""


@dataclass(frozen=True, slots=True)
class PyResult:
    """How a Python function gets the result of its C function."""

    setup: tuple[str, ...] = ()
    """Statements that create the output locals, before the conversions."""

    value: str | None = None
    """Expression returned after the call, or None for ``void``."""

    read: str | None = None
    """For outputs that the C function allocates: statement that copies them
    into Python objects (``_out_data``) right after the call. The call and the
    copy then run in a ``try`` whose ``finally`` runs ``cleanup``."""

    cleanup: tuple[str, ...] = ()
    """Statements that release the allocated outputs, inline: a Python function
    call before the release would let a second pending signal (Ctrl-C) raise
    first and leak them."""

    imports: tuple[str, ...] = ()
    """Like :attr:`PyArg.imports`, for the class of a result."""

    finish: tuple[str, ...] = ()
    """Statements after the call, before the value is returned: they hand the
    object that the C function created to the Python object that owns it."""


NO_RESULT = PyResult()


@dataclass(frozen=True, slots=True)
class PyCallable:
    name: str
    function: CFunction
    args: tuple[PyArg, ...]
    call_args: tuple[str, ...]
    """Arguments of the C call, in order."""

    result: PyResult = NO_RESULT
    is_static: bool = False

    prepare: tuple[str, ...] = ()
    """With transfers: the statements that read the handles into locals, before
    the objects are given up (a closed or read-only object must fail first)."""

    transfers: tuple[tuple[str, str, str | None], ...] = ()
    """The (parameter, local, view) of each object that the call takes."""

    retained: str = "_runtime.forever"
    """The list that keeps alive what the objects taken retained (``{owner}``:
    self, or _self in a factory of a constructor)."""

    keep: str | None = None
    """The object that must not be one of those taken."""

    @property
    def parameter_list(self) -> str:
        return ", ".join(arg.name for arg in self.args)

    @property
    def registrations(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple(arg.registration for arg in self.args if arg.registration is not None)

    @property
    def imports(self) -> tuple[str, ...]:
        """The ``from . import`` statements of the modules of other headers."""
        stems = dict.fromkeys(stem for arg in self.args for stem in arg.imports)
        stems.update(dict.fromkeys(self.result.imports))
        return tuple(f"from . import {stem} as {naming.module_alias(stem)}" for stem in stems)

    def retain_statements(self, owner: str = "self") -> tuple[str, ...]:
        """The statements that keep alive the arguments that the C++ code keeps a
        pointer to (in the list of ``owner``, ``self`` or ``_self``, or ``forever``),
        before the call: on failure the C++ code may already have stored them."""
        retained = self.retained.format(owner=owner)
        return tuple(f"_runtime.retain({retained}, {arg.name})" for arg in self.args if arg.retain)

    def check_call(
        self,
        indent: int,
        before_call: Sequence[str] = (),
        *,
        with_imports: bool = True,
        owner: str = "self",
    ) -> str:
        """The imports (``with_imports``), the argument conversions, the
        ``before_call`` statements, then the C call wrapped in
        ``_runtime.check``, indented by ``indent`` spaces.

        The conversions run Python code (``__index__``, ``__float__``), which
        could close the object: they come before ``self._ptr()``, after which
        only local variables are read before the call (ctypes passes ``_out``
        by reference itself, without a call to ``ctypes.byref``).
        """
        pad = " " * indent
        lines = [f"{pad}{line}" for line in self.imports if with_imports]
        lines += [f"{pad}{line}" for arg in self.args for line in arg.conversions]
        if self.registrations:
            lines.append(self._scoped_call(indent, before_call, owner=owner))
            return "\n".join(lines)
        lines += [pad + statement for statement in before_call]
        lines += self._take(indent, owner)
        lines.append(self._call(indent))
        return "\n".join(lines)

    def _take(self, indent: int, owner: str) -> list[str]:
        """With transfers, the last statements before the call: bind the C
        function, read the handles, then give up the objects that the call takes.
        Nothing after that may fail, except a signal (which leaks the objects)."""
        if not self.transfers:
            return []
        pad = " " * indent
        lines = [f"{pad}_c = _bind().{self.function.name}"]
        lines += [pad + statement for statement in self.prepare]
        targets = ", ".join(local for _, local, _ in self.transfers)
        arguments = [
            f'({name}, "{name}", "{view}")' if view else f'({name}, "{name}")'
            for name, _, view in self.transfers
        ]
        if self.keep is not None:
            arguments.append(f"keep={self.keep.format(owner=owner)}")
        lines.append(
            python_call(
                f"[{targets}] = _runtime.take",
                [self.retained.format(owner=owner), *arguments],
                indent,
            )
        )
        return lines

    @property
    def _callee(self) -> str:
        return "_c" if self.transfers else f"_bind().{self.function.name}"

    def _scoped_call(
        self,
        indent: int,
        before_call: Sequence[str] = (),
        read: str | None = None,
        cleanup: Sequence[str] = (),
        owner: str = "self",
    ) -> str:
        """The call of a function with callbacks, in a scope that keeps the
        errors of its callbacks (see _runtime.CallScope).

        The callbacks are registered last, right before the call: if anything
        before the call fails, the scope drops them, since C++ will never
        release them (_scope.call marks the call). The release of allocated
        outputs comes first in the ``finally``, inline (see PyResult.cleanup).
        """
        pad = " " * indent
        inner = " " * (indent + 4)
        lines = [f"{pad}_scope = _runtime.CallScope()", f"{pad}try:", f"{inner}_scope.begin()"]
        lines += [
            python_call(f"{targets} = _scope.callback", arguments, indent + 4)
            for targets, arguments in self.registrations
        ]
        lines += [inner + statement for statement in before_call]
        lines += self._take(indent + 4, owner)
        lines.append(
            python_call("_status = _scope.call", (self._callee, *self.call_args), indent + 4)
        )
        lines.append(f"{inner}_scope.close()")
        lines.append(f"{inner}_scope.check(_status)")
        if read is not None:
            lines.append(inner + read)
        lines.append(f"{pad}finally:")
        lines += [inner + line for line in cleanup]
        lines.append(f"{inner}_scope.close()")
        return "\n".join(lines)

    def _call(self, indent: int) -> str:
        pad = " " * indent
        head = f"_runtime.check({self._callee}("
        one_line = f"{pad}{head}{', '.join(self.call_args)}))"
        if len(one_line) <= MAX_LINE_LENGTH:
            return one_line
        lines = [pad + head]
        lines += [f"{pad}    {argument}," for argument in self.call_args]
        lines.append(f"{pad}))")
        return "\n".join(lines)

    def body(self, indent: int) -> str:
        """Statements of a method or function that calls the C function."""
        pad = " " * indent
        result = self.result
        lines = [f"{pad}{line}" for line in (*self.imports, *result.setup)]
        retains = self.retain_statements()
        if self.registrations:
            lines += [f"{pad}{line}" for arg in self.args for line in arg.conversions]
            lines.append(
                self._scoped_call(indent, retains, read=result.read, cleanup=result.cleanup)
            )
        elif result.read is None:
            lines.append(self.check_call(indent, retains, with_imports=False))
        else:
            # The conversions stay out of the 'try': nothing is allocated yet.
            inner = " " * (indent + 4)
            lines += [f"{pad}{line}" for arg in self.args for line in arg.conversions]
            lines.append(f"{pad}try:")
            lines += [f"{inner}{statement}" for statement in retains]
            lines += self._take(indent + 4, "self")
            lines.append(self._call(indent + 4))
            lines.append(f"{inner}{result.read}")
            lines.append(f"{pad}finally:")
            lines += [f"{inner}{line}" for line in result.cleanup]
        lines += [f"{pad}{line}" for line in result.finish]
        if result.value is not None:
            lines.append(f"{pad}return {result.value}")
        return "\n".join(lines)
