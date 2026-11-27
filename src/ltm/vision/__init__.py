"""Vision model factory — swap providers here or via ``vision.provider``."""

from __future__ import annotations

from ..config import Config
from .base import VisionError, VisionModel
from .mock import MockVisionModel
from .openai_compat import OpenAICompatibleVision


def build_vision(config: Config) -> VisionModel:
    if config.vision.provider == "mock":
        return MockVisionModel(config.vision)
    return OpenAICompatibleVision(config.vision)


__all__ = [
    "MockVisionModel",
    "OpenAICompatibleVision",
    "VisionError",
    "VisionModel",
    "build_vision",
]
