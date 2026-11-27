"""Generate two short synthetic camera videos for the zero-credential demo."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

_WIDTH, _HEIGHT, _FPS, _SECONDS = 640, 360, 15.0, 20.0


def generate_demo_videos(folder: Path) -> list[Path]:
    """Create ``cam_a.mp4`` and ``cam_b.mp4`` if they do not exist yet."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    videos: list[Path] = []
    for name, accent in (("cam_a", (40, 40, 220)), ("cam_b", (220, 150, 40))):
        path = folder / f"{name}.mp4"
        if path.is_file() and path.stat().st_size > 0:
            videos.append(path)
            continue

        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"mp4v"), _FPS, (_WIDTH, _HEIGHT)
        )
        if not writer.isOpened():
            raise RuntimeError(
                "OpenCV could not create an mp4 writer (codec 'mp4v' unavailable). "
                "Install the full opencv package or provide your own demo videos."
            )

        total = int(_FPS * _SECONDS)
        for index in range(total):
            frame = np.full((_HEIGHT, _WIDTH, 3), (26, 28, 32), dtype=np.uint8)
            phase = index / total

            # Workbench
            cv2.rectangle(frame, (30, 230), (610, 340), (55, 58, 64), -1)
            cv2.line(frame, (30, 230), (610, 230), (90, 94, 100), 2)

            # Blue bin on the right
            cv2.rectangle(frame, (470, 180), (600, 300), (180, 110, 40), -1)
            cv2.rectangle(frame, (470, 180), (600, 195), (210, 140, 60), -1)

            # Red block slides left -> right over the run
            x = int(80 + (470 - 80) * min(1.0, phase * 1.25))
            y = int(190 + 12 * math.sin(index / 12.0))
            cv2.rectangle(frame, (x, y), (x + 70, y + 70), accent, -1)
            cv2.rectangle(frame, (x, y), (x + 70, y + 12), (255, 255, 255), 2)

            # Overhead grid lines make motion obvious in the second view
            for gx in range(0, _WIDTH, 80):
                cv2.line(frame, (gx, 0), (gx, _HEIGHT), (36, 39, 44), 1)

            cv2.putText(
                frame,
                f"{name.upper()}  t={index / _FPS:05.1f}s",
                (24, 44),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (235, 235, 235),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                "DEMO FEED - synthetic",
                (24, 78),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (150, 155, 165),
                1,
                cv2.LINE_AA,
            )

            # Progress bar along the bottom
            cv2.rectangle(frame, (0, _HEIGHT - 6), (_WIDTH, _HEIGHT), (50, 53, 58), -1)
            cv2.rectangle(
                frame,
                (0, _HEIGHT - 6),
                (int(_WIDTH * phase), _HEIGHT),
                (80, 200, 120),
                -1,
            )
            writer.write(frame)

        writer.release()
        videos.append(path)

    return videos
