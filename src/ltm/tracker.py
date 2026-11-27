"""Consecutive-vote logic that turns noisy per-frame verdicts into a completion."""

from __future__ import annotations

from .schemas import Verdict


class CompletionTracker:
    """Require N consecutive confident positives before declaring a task done.

    A negative verdict, or a positive one below ``min_confidence``, resets the
    streak. This is the main defence against a single hallucinated frame.
    """

    def __init__(self, votes_required: int = 2, min_confidence: float = 0.6) -> None:
        if votes_required < 1:
            raise ValueError("votes_required must be >= 1")
        self.votes_required = votes_required
        self.min_confidence = min_confidence
        self.votes = 0

    def add(self, verdict: Verdict) -> bool:
        """Record a verdict; return True when the task should be completed."""
        if not verdict.task_completed or verdict.confidence < self.min_confidence:
            self.votes = 0
            return False
        self.votes += 1
        return self.votes >= self.votes_required

    def reset(self) -> None:
        self.votes = 0
