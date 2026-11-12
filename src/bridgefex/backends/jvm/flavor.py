# SPDX-License-Identifier: Apache-2.0
"""The two ways that the Java bindings call the C layer, and what the generated code says
differently for each.

The ``ffm`` flavor uses the foreign function and memory API of Java 22 (``java.lang.foreign``),
the ``jni`` one JNI (Java 8 or later, Android, 32-bit ARM). Both write the same Java API. The
generated code of the headers is the same too: the runtime of each flavor has the classes that
it uses (``BridgefexRuntime``, ``BridgefexNative``...) with the same names and signatures, so
that only the types of memory and the syntax that the language level allows are told here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Flavor:
    name: str
    segment: str
    """The Java type of a piece of memory or a pointer."""

    arena: str
    """The type of the memory of a call."""

    struct_layout: str
    """The type of the layout of a struct."""

    modern: bool
    """``var`` and the patterns of ``instanceof`` (Java 16) are available."""

    release: int
    """The version of Java that the code is written for (``javac --release``)."""

    directory: str
    """The directory of the project below the output directory."""

    segment_name: str
    """The simple name of the type of memory, which the files of the runtime import."""

    cleaner: str
    """The class of the runtime that runs an action when an object is collected."""

    def declare(self, type_: str) -> str:
        """The type of a local variable: ``var`` where the language has it."""
        return "var" if self.modern else type_

    @property
    def root_types(self) -> tuple[str, ...]:
        """The classes of the runtime that a flavor has besides the common ones."""
        if self.modern:
            return ()
        # The types that ``var`` leaves out of the code, and those of the memory.
        return ("BridgefexSegment", "BridgefexArena", "BridgefexStruct", "BridgefexLease")


FFM = Flavor(
    "ffm",
    "java.lang.foreign.MemorySegment",
    "java.lang.foreign.Arena",
    "java.lang.foreign.StructLayout",
    modern=True,
    release=22,
    directory="jvm",
    segment_name="MemorySegment",
    cleaner="Cleaner",
)

JNI = Flavor(
    "jni",
    "BridgefexSegment",
    "BridgefexArena",
    "BridgefexStruct",
    modern=False,
    release=8,
    directory="jni",
    segment_name="BridgefexSegment",
    cleaner="BridgefexCleaner",
)

FLAVORS = {flavor.name: flavor for flavor in (FFM, JNI)}
