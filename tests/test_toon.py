from crew.toon import encode


def test_uniform_object_arrays_encode_as_compact_tables() -> None:
    assert encode({"tasks": [
        {"id": 1, "title": "Fix auth", "status": "open"},
        {"id": 2, "title": "Release", "status": "done"},
    ]}) == "tasks[2]{id,title,status}:\n  1,Fix auth,open\n  2,Release,done"


def test_ambiguous_strings_are_quoted_and_empty_arrays_are_explicit() -> None:
    assert encode({"true_word": "true", "empty": "", "items": []}) == (
        'true_word: "true"\nempty: ""\nitems: []'
    )


def test_nonuniform_arrays_use_length_declared_list_form() -> None:
    assert encode({"items": [1, {"name": "Ada"}]}) == (
        "items[2]:\n  - 1\n  - name: Ada"
    )


def test_nested_uniform_columns_and_keyed_objects_keep_shape() -> None:
    assert encode({
        "people": [
            {"id": 1, "profile": {"name": "Ada", "active": True}},
            {"id": 2, "profile": {"name": "Lin", "active": False}},
        ],
        "limits": {"api": {"max": 3}, "worker": {"max": 5}},
    }) == (
        "people[2]{id,profile{name,active}}:\n"
        "  1,Ada,true\n"
        "  2,Lin,false\n"
        "limits[2:]{max}:\n"
        "  api: 3\n"
        "  worker: 5"
    )


def test_root_keyed_object_rows_are_indented_under_the_header() -> None:
    assert encode({"a": {"x": 1}, "b": {"x": 2}}) == (
        "[2:]{x}:\n"
        "  a: 1\n"
        "  b: 2"
    )


def test_toon_escapes_required_controls_and_normalizes_nonfinite_numbers() -> None:
    assert encode({"text": "a\n\tb", "not_finite": float("inf"), "number": 1e6}) == (
        'text: "a\\n\\tb"\nnot_finite: null\nnumber: 1000000'
    )


def test_empty_root_values_and_numeric_string_are_unambiguous() -> None:
    assert encode({}) == ""
    assert encode([]) == "[]"
    assert encode({"codes": ["05", "#tag", "safe value"]}) == (
        'codes[3]: "05","#tag",safe value'
    )


def test_mixed_arrays_indent_object_fields_and_keep_empty_lists() -> None:
    assert encode({"items": [[], {"name": "Ada", "score": 9}, 7]}) == (
        "items[3]:\n"
        "  - []\n"
        "  - name: Ada\n"
        "    score: 9\n"
        "  - 7"
    )


def test_nested_mixed_array_retains_list_prefix() -> None:
    assert encode({"items": [[1, {"a": 2}]]}) == (
        "items[1]:\n"
        "  - [2]:\n"
        "    - 1\n"
        "    - a: 2"
    )


def test_nested_uniform_object_array_uses_list_form() -> None:
    assert encode({"items": [[{"x": 1}, {"x": 2}]]}) == (
        "items[1]:\n"
        "  - [2]:\n"
        "    - x: 1\n"
        "    - x: 2"
    )


def test_nested_object_in_list_item_indents_below_its_field() -> None:
    assert encode({"items": [{"profile": {"name": "Ada"}, "score": 9}, 7]}) == (
        "items[2]:\n"
        "  - profile:\n"
        "      name: Ada\n"
        "    score: 9\n"
        "  - 7"
    )
