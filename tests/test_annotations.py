# SPDX-License-Identifier: Apache-2.0
"""The annotations file: loading it, resolving the policy of a function, unknown names."""

from __future__ import annotations

from pathlib import Path

import pytest

from bridgefex.annotations import (
    FunctionAnnotations,
    Kind,
    Param,
    Resolved,
    load,
    resolve,
    unknown_functions,
)
from bridgefex.errors import ConfigurationError

GOOD = """\
# Who owns what.
[own_Tree_find]
return = "borrowed"

[own_Tree_detach]
return = "owned"

[own_Tree_adoptRaw]
item = "transfer"

[own_Tree_setCursor]
1 = ["retain"]

[own_Tree_contains]
node = "nullable"
"""


def write(directory: Path, text: str, name: str = "annotations.toml") -> Path:
    path = directory / name
    # Bytes: write_text() would turn every \n into \r\n on Windows.
    path.write_bytes(text.encode("utf-8"))
    return path


def config_error(directory: Path, *texts: str) -> str:
    paths = [write(directory, text, f"file{index}.toml") for index, text in enumerate(texts)]
    with pytest.raises(ConfigurationError) as raised:
        load(paths)
    return str(raised.value)


def test_load(tmp_path: Path) -> None:
    table = load([write(tmp_path, GOOD)])
    assert set(table) == {
        "own_Tree_find",
        "own_Tree_detach",
        "own_Tree_adoptRaw",
        "own_Tree_setCursor",
        "own_Tree_contains",
    }
    assert table["own_Tree_find"].result == frozenset({"borrowed"})
    assert table["own_Tree_adoptRaw"].parameters == {"item": (frozenset({"transfer"}), 9)}
    assert table["own_Tree_setCursor"].parameters == {"1": (frozenset({"retain"}), 12)}
    # Where each table starts, for the messages.
    assert table["own_Tree_find"].line == 2
    assert table["own_Tree_find"].result_line == 3
    assert table["own_Tree_find"].where() == f"{tmp_path / 'annotations.toml'}:2"


def test_several_files(tmp_path: Path) -> None:
    first = write(tmp_path, '[a_f]\nreturn = "owned"\n', "first.toml")
    second = write(tmp_path, '[a_g]\nx = "nullable"\n', "second.toml")
    assert set(load([first, second])) == {"a_f", "a_g"}
    assert load([]) == {}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[f]\nreturn = "own"\n', r"file0\.toml:2: \[f\] return: unknown annotation 'own'"),
        ('[f]\nx = "nulable"\n', r"unknown annotation 'nulable' \(expected: call, nullable,"),
        ("[f]\nx = []\n", "expected a word or a non-empty list of words"),
        ("[f]\nx = 3\n", "expected a word or a non-empty list of words"),
        ('[f]\nx = ["nullable", "nullable"]\n', "a word is repeated"),
        ('[f]\nx = ["nullable", 3]\n', "expected a word or a non-empty list of words"),
        ('f = "owned"\n', r"file0\.toml:1: expected a table, e.g. \[f\] followed by return"),
        ('[f]\nreturn = ["owned", "bytes", "bytes"]\n', "a word is repeated"),
        ('[f]\nreturn = "nullable"\n', "unknown annotation 'nullable'"),
        ('[f]\nx = "owned"\n', "unknown annotation 'owned'"),
    ],
)
def test_load_rejects(tmp_path: Path, text: str, message: str) -> None:
    assert __import__("re").search(message, config_error(tmp_path, text)), config_error(
        tmp_path, text
    )


def test_every_problem_is_reported(tmp_path: Path) -> None:
    text = config_error(tmp_path, '[f]\nreturn = "own"\nx = "nulable"\n[g]\ny = []\n')
    assert len(text.splitlines()) == 3
    assert ":2:" not in text.splitlines()[0] or "return" in text.splitlines()[0]


def test_the_same_function_in_two_places(tmp_path: Path) -> None:
    message = config_error(tmp_path, '[f]\nreturn = "owned"\n', '[f]\nreturn = "owned"\n')
    assert "file1.toml:1: [f] is also annotated in" in message
    assert "file0.toml:1" in message


def test_toml_errors_have_their_position(tmp_path: Path) -> None:
    for text, line in (("[f\nreturn = 1\n", 1), ('[f]\nreturn = "a\n', 2), ("[f]\n[f]\n", 2)):
        message = config_error(tmp_path, text)
        assert f"file0.toml:{line}:" in message, message
        assert "invalid TOML" in message, message


def test_files_that_cannot_be_read(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot read the annotations file"):
        load([tmp_path / "missing.toml"])
    path = tmp_path / "binary.toml"
    path.write_bytes(b"\xff\xfe[f]\n")
    with pytest.raises(ConfigurationError, match="not UTF-8 text"):
        load([path])


# Policies ---------------------------------------------------------------------------


def policy(
    text: str,
    directory: Path,
    params: list[Param],
    result: Kind | None = None,
    *,
    constructor: bool = False,
    name: str = "f",
) -> tuple[Resolved, list[str]]:
    table = load([write(directory, text)]) if text else {}
    resolved, errors = resolve(
        name,
        f"function '{name}'",
        params,
        result,
        constructor=constructor,
        annotations=table,
        location=None,
    )
    return resolved, [str(error) for error in errors]


def test_what_the_types_imply(tmp_path: Path) -> None:
    for kind, owner in (
        (Kind.OBJECT_REFERENCE, "borrowed"),
        (Kind.OBJECT_VALUE, "owned"),
        (Kind.UNIQUE_PTR, "owned"),
    ):
        resolved, errors = policy("", tmp_path, [], kind)
        assert (errors, resolved.result) == ([], owner)
    resolved, errors = policy("", tmp_path, [Param("item", Kind.UNIQUE_PTR)])
    assert (errors, resolved.parameters) == ([], (frozenset({"transfer"}),))
    resolved, errors = policy("", tmp_path, [Param("n", Kind.OTHER)], Kind.OTHER)
    assert (errors, resolved) == ([], Resolved(None, False, (frozenset(),)))


def test_raw_pointer_results_say_who_owns_them(tmp_path: Path) -> None:
    for word in ("owned", "borrowed"):
        resolved, errors = policy(f'[f]\nreturn = "{word}"\n', tmp_path, [], Kind.OBJECT_POINTER)
        assert (errors, resolved.result) == ([], word)
    resolved, errors = policy('[f]\nreturn = "borrowed"\n', tmp_path, [], Kind.OBJECT_CONST_POINTER)
    assert (errors, resolved.result) == ([], "borrowed")
    (error,) = policy("", tmp_path, [], Kind.OBJECT_POINTER)[1]
    assert (
        'returns a raw pointer to a wrapped class: say who owns it with [f] return = "owned"'
        in error
    )
    assert policy("", tmp_path, [], Kind.OBJECT_POINTER)[0].result is None


@pytest.mark.parametrize(
    ("text", "result", "message"),
    [
        (
            '[f]\nreturn = "owned"\n',
            Kind.OBJECT_CONST_POINTER,
            "an owned result cannot point to const",
        ),
        ('[f]\nreturn = "owned"\n', Kind.OBJECT_REFERENCE, "the result is always borrowed: remove"),
        ('[f]\nreturn = "borrowed"\n', Kind.OBJECT_VALUE, "the result is always owned: remove"),
        ('[f]\nreturn = "owned"\n', Kind.UNIQUE_PTR, "the result is always owned: remove"),
        (
            '[f]\nreturn = "borrowed"\n',
            Kind.OTHER,
            "only applies to a result that is a raw pointer",
        ),
        ('[f]\nreturn = ["owned", "borrowed"]\n', Kind.OBJECT_POINTER, "exclude each other"),
        (
            '[f]\nreturn = "bytes"\n',
            Kind.OBJECT_REFERENCE,
            "'bytes' only applies to a string result",
        ),
        ('[f]\nreturn = "bytes"\n', None, "'bytes' only applies to a string result"),
    ],
)
def test_results_that_cannot_be_annotated_so(
    tmp_path: Path, text: str, result: Kind | None, message: str
) -> None:
    (error,) = policy(text, tmp_path, [], result)[1]
    assert message in error
    assert error.startswith("function 'f': ")
    assert "annotations.toml:" in error  # where the annotation is


def test_bytes(tmp_path: Path) -> None:
    for kind in (Kind.STRING, Kind.C_STRING):
        resolved, errors = policy('[f]\nreturn = "bytes"\n', tmp_path, [], kind)
        assert errors == []
        assert resolved.result_bytes


@pytest.mark.parametrize(
    ("kind", "word", "ok"),
    [
        (Kind.OBJECT_POINTER, "nullable", True),
        (Kind.OBJECT_CONST_POINTER, "nullable", True),
        (Kind.UNIQUE_PTR, "nullable", True),
        (Kind.C_STRING, "nullable", True),
        (Kind.OBJECT_REFERENCE, "nullable", False),
        (Kind.OBJECT_VALUE, "nullable", False),
        (Kind.STRING, "nullable", False),
        (Kind.CALLBACK, "nullable", False),
        (Kind.OTHER, "nullable", False),
        (Kind.OBJECT_POINTER, "transfer", True),
        (Kind.OBJECT_CONST_POINTER, "transfer", False),
        (Kind.UNIQUE_PTR, "transfer", False),
        (Kind.OBJECT_REFERENCE, "transfer", False),
        (Kind.OBJECT_REFERENCE, "retain", True),
        (Kind.OBJECT_POINTER, "retain", True),
        (Kind.OBJECT_CONST_POINTER, "retain", True),
        (Kind.OBJECT_VALUE, "retain", False),
        (Kind.UNIQUE_PTR, "retain", False),
        (Kind.CALLBACK, "retain", False),
        (Kind.OBJECT_REFERENCE, "call", True),
        (Kind.OBJECT_VALUE, "call", False),
        (Kind.C_STRING, "call", False),
    ],
)
def test_where_each_word_applies(tmp_path: Path, kind: Kind, word: str, ok: bool) -> None:
    resolved, errors = policy(f'[f]\nx = "{word}"\n', tmp_path, [Param("x", kind)])
    if ok:
        assert errors == []
        assert word in resolved.parameters[0]
    else:
        (error,) = errors
        assert f"parameter 'x': '{word}' does not apply to a {kind.value}" in error


def test_hints(tmp_path: Path) -> None:
    (error,) = policy('[f]\nx = "transfer"\n', tmp_path, [Param("x", Kind.UNIQUE_PTR)])[1]
    assert "a std::unique_ptr always transfers the ownership" in error
    (error,) = policy('[f]\nx = "nullable"\n', tmp_path, [Param("x", Kind.OBJECT_REFERENCE)])[1]
    assert "a reference is never NULL" in error
    (error,) = policy('[f]\nx = "nullable"\n', tmp_path, [Param("x", Kind.CALLBACK)])[1]
    assert "a callback may always be None" in error


@pytest.mark.parametrize("words", ["transfer", "retain", "call"])
def test_words_that_exclude_each_other(tmp_path: Path, words: str) -> None:
    others = [word for word in ("transfer", "retain", "call") if word != words]
    for other in others:
        errors = policy(
            f'[f]\nx = ["{words}", "{other}"]\n', tmp_path, [Param("x", Kind.OBJECT_POINTER)]
        )[1]
        messages = " ".join(errors)
        # (transfer, retain, call apply to different kinds: a pointer takes all three)
        assert "exclude each other" in messages


def test_the_constructor_keeps_what_it_gets_unless_told_not_to(tmp_path: Path) -> None:
    params = [
        Param("reference", Kind.OBJECT_REFERENCE),
        Param("pointer", Kind.OBJECT_POINTER),
        Param("const_pointer", Kind.OBJECT_CONST_POINTER),
        Param("value", Kind.OBJECT_VALUE),
        Param("number", Kind.OTHER),
        Param("unique", Kind.UNIQUE_PTR),
    ]
    resolved, errors = policy("", tmp_path, params, constructor=True)
    assert errors == []
    assert resolved.parameters == (
        frozenset({"retain"}),
        frozenset({"retain"}),
        frozenset({"retain"}),
        frozenset(),
        frozenset(),
        frozenset({"transfer"}),
    )
    # A method keeps nothing by default.
    resolved, _ = policy("", tmp_path, params)
    assert all("retain" not in words for words in resolved.parameters)
    # 'call' turns it off, and so does 'transfer'.
    resolved, errors = policy(
        '[f]\nreference = "call"\npointer = "transfer"\n', tmp_path, params, constructor=True
    )
    assert errors == []
    assert resolved.parameters[:3] == (
        frozenset({"call"}),
        frozenset({"transfer"}),
        frozenset({"retain"}),
    )


def test_parameters_by_name_or_by_position(tmp_path: Path) -> None:
    params = [Param("first", Kind.OBJECT_POINTER), Param("", Kind.OBJECT_POINTER)]
    resolved, errors = policy('[f]\nfirst = "nullable"\n2 = "nullable"\n', tmp_path, params)
    assert errors == []
    assert resolved.parameters == (frozenset({"nullable"}), frozenset({"nullable"}))
    # A named parameter can be given by its position too.
    resolved, errors = policy('[f]\n1 = "nullable"\n', tmp_path, params)
    assert errors == []
    assert resolved.parameters[0] == frozenset({"nullable"})


def test_parameters_that_do_not_exist(tmp_path: Path) -> None:
    params = [Param("item", Kind.OBJECT_POINTER), Param("", Kind.OTHER)]
    (error,) = policy('[f]\nitme = "nullable"\n', tmp_path, params)[1]
    assert "there is no parameter 'itme' (parameters: item, #2)" in error
    (error,) = policy('[f]\n3 = "nullable"\n', tmp_path, params)[1]
    assert "there is no parameter '3'" in error
    (error,) = policy('[f]\n0 = "nullable"\n', tmp_path, params)[1]
    assert "there is no parameter '0'" in error
    (error,) = policy('[f]\nitem = "nullable"\n1 = "nullable"\n', tmp_path, params)[1]
    assert "parameter '1' is annotated twice (by name and by position)" in error
    (error,) = policy('[f]\nx = "nullable"\n', tmp_path, [])[1]
    assert "(parameters: none)" in error


def test_the_location_of_the_function_is_reported(tmp_path: Path) -> None:
    table = load([write(tmp_path, '[f]\nx = "nullable"\n')])
    _, errors = resolve(
        "f",
        "function 'f'",
        [Param("x", Kind.OTHER)],
        None,
        constructor=False,
        annotations=table,
        location=None,
    )
    assert str(errors[0]).startswith("function 'f': parameter 'x': 'nullable' does not apply")


# Functions that do not exist --------------------------------------------------------


def test_unknown_functions(tmp_path: Path) -> None:
    table = load([write(tmp_path, '[own_Tree_fnd]\nreturn = "borrowed"\n[zzz]\nx = "nullable"\n')])
    generated = ["own_Tree_find", "own_Tree_detach", "own_Tree_adoptRaw"]
    first, second = (str(problem) for problem in unknown_functions(table, generated))
    assert "[own_Tree_fnd] is not a generated C function that can be annotated" in first
    assert "did you mean own_Tree_find" in first
    assert "adding an overload renames the C functions of the others" in first
    assert "annotations.toml:1" in first
    assert "[zzz]" in second
    assert "did you mean" not in second
    assert unknown_functions(table, ["own_Tree_fnd", "zzz"]) == []


def test_function_annotations_are_values() -> None:
    entry = FunctionAnnotations("a.toml", 4)
    assert entry.where() == "a.toml:4"
    assert entry.where(9) == "a.toml:9"
    assert FunctionAnnotations("a.toml").where() == "a.toml"


def test_a_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "bom.toml"
    path.write_bytes(b'\xef\xbb\xbf[f]\nx = "nullable"\n')
    assert set(load([path])) == {"f"}


def test_lines_are_counted_as_toml_does(tmp_path: Path) -> None:
    # U+2028 is no line break in TOML; str.splitlines() would split there.
    separator = chr(0x2028)
    message = config_error(tmp_path, f'# a note {separator} here\n[f]\nx = "nulable"\n')
    assert "file0.toml:3:" in message
    # CRLF is.
    assert "file0.toml:3:" in config_error(tmp_path, '# note\r\n[f]\r\nx = "nulable"\r\n')


def test_quoted_names_have_their_line(tmp_path: Path) -> None:
    assert "file0.toml:2:" in config_error(tmp_path, "['f']\n'x' = \"nulable\"\n")
    assert "file0.toml:2:" in config_error(tmp_path, '["f"]\n"x" = "nulable"\n')


def test_a_table_without_a_header_has_no_line_zero(tmp_path: Path) -> None:
    message = config_error(tmp_path, 'f.x = "nulable"\n')
    assert "file0.toml" in message
    assert ":0:" not in message


# The directors: classes that other languages may derive from.
def test_directors_are_read_before_the_first_table(tmp_path: Path) -> None:
    text = (
        "# the classes\n"
        'directors = [\n  "inh_Shape",\n  "inh_Widget",\n]\n\n'
        '[own_Tree_find]\nreturn = "borrowed"\n'
    )
    path = write(tmp_path, text)
    annotations = load([path])
    assert list(annotations.directors) == ["inh_Shape", "inh_Widget"]
    assert [request.line for request in annotations.directors.values()] == [3, 4]
    assert annotations.directors["inh_Shape"].where() == f"{path}:3"
    assert set(annotations) == {"own_Tree_find"}  # the list is not a function


def test_a_file_without_directors_has_none(tmp_path: Path) -> None:
    assert load([write(tmp_path, GOOD)]).directors == {}


def test_directors_come_from_every_file(tmp_path: Path) -> None:
    paths = [
        write(tmp_path, 'directors = ["a_One"]\n', "one.toml"),
        write(tmp_path, 'directors = ["a_Two"]\n', "two.toml"),
    ]
    assert list(load(paths).directors) == ["a_One", "a_Two"]


@pytest.mark.parametrize(
    "text",
    [
        'directors = "inh_Shape"\n',
        "directors = [1]\n",
        'directors = [""]\n',
        '[directors]\nname = "x"\n',
    ],
)
def test_directors_must_be_a_list_of_names(tmp_path: Path, text: str) -> None:
    message = config_error(tmp_path, text)
    assert "directors: expected a list of class names" in message


def test_a_director_asked_for_twice_is_an_error(tmp_path: Path) -> None:
    message = config_error(tmp_path, 'directors = ["a_One", "a_Two", "a_One"]\n')
    assert "directors: 'a_One' is also asked for in" in message
    message = config_error(tmp_path, 'directors = ["a_One"]\n', 'directors = ["a_One"]\n')
    assert "file1.toml:1: directors: 'a_One' is also asked for in" in message
    assert "file0.toml:1" in message
