# SPDX-License-Identifier: Apache-2.0
"""A small writer of Java source: indentation, blocks and Javadoc."""

from __future__ import annotations


def escape_doc(text: str) -> str:
    """Text for a Javadoc comment: no HTML, no tag, no end of comment."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("@", "&#64;")
        .replace("*/", "*&#47;")
        .replace("\\u", "\\&#117;")
    )


class Source:
    """The lines of a Java file, with the indentation of the blocks that are open."""

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
        self.line(f"{header} {{" if header else "{")
        self._depth += 1

    def close(self, suffix: str = "") -> None:
        self._depth -= 1
        self.line("}" + suffix)

    def doc(self, text: str, *remarks: str) -> None:
        """A Javadoc comment: ``text`` and what the signature does not say."""
        self.line("/**")
        self.line(f" * {escape_doc(text)}")
        for remark in remarks:
            self.line(" * <p>")
            self.line(f" * {escape_doc(remark)}")
        self.line(" */")

    def text(self) -> str:
        return "\n".join(self.lines).rstrip("\n") + "\n"
