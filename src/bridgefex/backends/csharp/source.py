# SPDX-License-Identifier: Apache-2.0
"""A small writer of C# source: indentation and blocks."""

from __future__ import annotations


class Source:
    """The lines of a C# file, with the indentation of the blocks that are open."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._depth = 0

    def line(self, text: str = "") -> None:
        self.lines.append(f"{'    ' * self._depth}{text}" if text else "")

    def lines_of(self, texts: list[str] | tuple[str, ...]) -> None:
        for text in texts:
            self.line(text)

    def open(self, header: str = "") -> None:
        """``header`` and an opening brace; everything until :meth:`close` is inside."""
        if header:
            self.line(header)
        self.line("{")
        self._depth += 1

    def close(self, suffix: str = "") -> None:
        self._depth -= 1
        self.line("}" + suffix)

    def doc(self, text: str) -> None:
        """A documentation comment (XML-escaped)."""
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.line("/// <summary>")
        self.line(f"/// {escaped}")
        self.line("/// </summary>")

    def remarks(self, texts: list[str]) -> None:
        """A documentation comment (XML-escaped) with what the signature does not say."""
        if not texts:
            return
        self.line("/// <remarks>")
        for text in texts:
            escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            self.line(f"/// {escaped}")
        self.line("/// </remarks>")

    def text(self) -> str:
        return "\n".join(self.lines).rstrip("\n") + "\n"
