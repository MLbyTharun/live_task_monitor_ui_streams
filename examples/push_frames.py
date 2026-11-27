#!/usr/bin/env python3
"""Feed a push stream from a video file, RTSP camera, or webcam.

This simulates a phone / call / WebRTC bridge that sends frames to the monitor:

    python examples/push_frames.py 0 --url http://127.0.0.1:8080/api/ingest/phone

Use it with a push stream in config/config.yaml:

    streams:
      - id: phone
        name: Phone / call feed
        type: push
        enabled: true
"""

from __future__ import annotations

import argparse
import sys
import time

import cv2
import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Push frames to /api/ingest/{stream}")
    parser.add_argument("source", help="video file, RTSP URL, or webcam index (e.g. 0)")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080/api/ingest/phone",
        help="ingest endpoint",
    )
    parser.add_argument("--fps", type=float, default=2.0, help="frames per second to send")
    parser.add_argument("--loop", action="store_true", help="loop a local video file")
    parser.add_argument("--token", default=None, help="value for X-Ingest-Token")
    args = parser.parse_args()

    source: str | int = int(args.source) if args.source.isdigit() else args.source
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        print(f"cannot open {args.source}", file=sys.stderr)
        return 1

    headers = {"Content-Type": "image/jpeg"}
    if args.token:
        headers["X-Ingest-Token"] = args.token

    sent = 0
    try:
        with httpx.Client(timeout=10) as client:
            while True:
                ok, frame = capture.read()
                if not ok:
                    if args.loop:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    break
                ok, buffer = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
                )
                if not ok:
                    continue
                response = client.post(
                    args.url, content=buffer.tobytes(), headers=headers
                )
                if response.status_code >= 400:
                    print(
                        f"ingest failed ({response.status_code}): {response.text[:300]}",
                        file=sys.stderr,
                    )
                    return 2
                sent += 1
                if sent % 20 == 0:
                    print(f"sent {sent} frames")
                time.sleep(1.0 / max(args.fps, 0.1))
    except KeyboardInterrupt:
        pass
    finally:
        capture.release()
    print(f"done — sent {sent} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
