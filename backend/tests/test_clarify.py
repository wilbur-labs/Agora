from __future__ import annotations

import pytest

from agora.tasks.clarify import ClarifyError, _parse_questions


def test_parse_plain_json_array():
    out = _parse_questions('[{"id":"a","question":"Q1?","why":"w","suggestions":["x","y"]}]')
    assert len(out) == 1
    assert out[0]["id"] == "a"
    assert out[0]["question"] == "Q1?"
    assert out[0]["suggestions"] == ["x", "y"]


def test_parse_strips_prose_and_fence():
    text = "Here you go:\n```json\n[{\"question\":\"Q?\"}]\n```\nDone."
    out = _parse_questions(text)
    assert len(out) == 1
    assert out[0]["question"] == "Q?"
    assert out[0]["id"] == "q1"  # defaulted
    assert out[0]["suggestions"] == []


def test_parse_skips_malformed_items_and_caps():
    text = '[{"question":"keep"}, {"no_question":true}, 42]'
    out = _parse_questions(text)
    assert [q["question"] for q in out] == ["keep"]


def test_parse_rejects_non_array():
    with pytest.raises(ClarifyError):
        _parse_questions('{"question": "not an array"}')


def test_parse_rejects_no_json():
    with pytest.raises(ClarifyError):
        _parse_questions("the agent refused to answer")


def test_parse_rejects_empty_usable():
    with pytest.raises(ClarifyError):
        _parse_questions("[]")
