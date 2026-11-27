from __future__ import annotations

import pytest

from ltm.schemas import Verdict
from ltm.tracker import CompletionTracker
from ltm.vision.parsing import parse_verdict


def test_plain_json():
    verdict = parse_verdict(
        '{"task_completed": true, "confidence": 0.82, "reasoning": "block is in bin", "evidence": ["bin full"]}'
    )
    assert verdict.task_completed is True
    assert verdict.confidence == pytest.approx(0.82)
    assert verdict.reasoning == "block is in bin"
    assert verdict.evidence == ["bin full"]
    assert verdict.parse_error is False


def test_fenced_json_with_prose():
    text = """Here is my answer:
```json
{"task_completed": "yes", "confidence": "85%", "reasoning": "visible"}
```
Thanks."""
    verdict = parse_verdict(text)
    assert verdict.task_completed is True
    assert verdict.confidence == pytest.approx(0.85)
    assert verdict.parse_error is False


def test_json_embedded_in_text():
    text = 'The result is {"task_completed": false, "confidence": 0.2, "reasoning": "not visible"} as shown.'
    verdict = parse_verdict(text)
    assert verdict.task_completed is False
    assert verdict.confidence == pytest.approx(0.2)


def test_alias_fields_and_string_confidence():
    verdict = parse_verdict('{"completed": true, "score": 82, "explanation": "ok", "observations": "one"}')
    assert verdict.task_completed is True
    assert verdict.confidence == pytest.approx(0.82)
    assert verdict.evidence == ["one"]


def test_invalid_json_is_safe():
    verdict = parse_verdict("I could not see the task.")
    assert verdict.task_completed is False
    assert verdict.parse_error is True
    assert verdict.confidence == 0.0


def test_missing_field_is_flagged():
    verdict = parse_verdict('{"confidence": 0.9, "reasoning": "maybe"}')
    assert verdict.parse_error is True
    assert verdict.task_completed is False


def test_confidence_is_clamped():
    assert parse_verdict('{"task_completed": true, "confidence": 150}').confidence == 1.0
    assert parse_verdict('{"task_completed": true, "confidence": -2}').confidence == 0.0


def test_tracker_requires_consecutive_confident_votes():
    tracker = CompletionTracker(votes_required=2, min_confidence=0.6)
    assert tracker.add(Verdict(task_completed=True, confidence=0.9)) is False
    assert tracker.votes == 1
    assert tracker.add(Verdict(task_completed=False, confidence=0.9)) is False
    assert tracker.votes == 0
    assert tracker.add(Verdict(task_completed=True, confidence=0.5)) is False
    assert tracker.votes == 0
    assert tracker.add(Verdict(task_completed=True, confidence=0.7)) is False
    assert tracker.add(Verdict(task_completed=True, confidence=0.7)) is True
