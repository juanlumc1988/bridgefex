# SPDX-License-Identifier: Apache-2.0
"""A small writer of Rust source: indentation and blocks."""

from __future__ import annotations


class Source:
    """The lines of a Rust file, with the indentation of the blocks that are open."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._depth = 0

    def line(self, text: str = "") -> None:
        self.lines.append(f"{'    ' * self._depth}{text}" if text else "")

    def lines_of(self, texts: list[str] | tuple[str, ...]) -> None:
        for text in texts:
            self.line(text)

    def open(self, header: str) -> None:
        """``header`` and an opening brace; everything until :meth:`close` is inside."""
        self.line(f"{header} {{")
        self._depth += 1

    def close(self) -> None:
        self._depth -= 1
        self.line("}")

    def doc(self, text: str, *remarks: str) -> None:
        """A documentation comment: ``text`` in code font (it is a C++ declaration), and what
        the signature does not say."""
        self.line(f"/// `{_one_line(text)}`")
        for remark in remarks:
            self.line("///")
            self.line(f"/// {_one_line(remark)}")

    def prose(self, text: str) -> None:
        """A documentation comment that is plain text (what the generator says, or what the header
        says), not a declaration."""
        self.line(f"/// {_one_line(text)}")

    def text(self) -> str:
        return "\n".join(self.lines).rstrip("\n") + "\n"


def _one_line(text: str) -> str:
    return " ".join(text.split())
