"""Command-line entry point: run, check, list-tasks, reset, demo."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import Config, ConfigError
from .logging_setup import setup_logging
from .state import StateStore
from .tasks import TaskSourceError, build_task_source
from .vision import VisionError, build_vision

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(path: str | None) -> Config:
    return Config.load(path)


def _summary(config: Config) -> None:
    streams = [s for s in config.streams if s.enabled]
    print(f"config:  {config.config_path}")
    print(f"root:    {config.root}")
    print(f"tasks:   {config.tasks.source}")
    print(f"model:   {config.vision.provider} / {config.vision.model or '(not set)'}")
    print(f"streams: {', '.join(s.id for s in streams) or '(none enabled)'}")
    print(f"data:    {config.app.data_dir}")


def _problems(config: Config) -> int:
    problems = config.validate_runtime()
    if not problems:
        print("✓ static configuration looks good")
        return 0
    print("\nProblems:")
    for problem in problems:
        print(f"  ✗ {problem}")
    print("\nFill in .env (copy .env.example) and run `ltm check` again.")
    return 1


async def _ping(vision) -> str:
    try:
        return await vision.ping()
    finally:
        await vision.close()


def cmd_check(args: argparse.Namespace) -> int:
    config = _load(args.config)
    _summary(config)
    print()
    if _problems(config):
        return 1
    if args.offline:
        return 0

    source = build_task_source(config)
    try:
        tasks = source.fetch_tasks()
    except TaskSourceError as exc:
        print(f"\n✗ task source: {exc}")
        return 1
    print(f"\n✓ task source: {len(tasks)} task(s)")
    for index, task in enumerate(tasks, start=1):
        print(f"   {index:2}. [{task.id}] {task.title}  (order={task.order:g})")
    if not tasks:
        print("   (the API returned no tasks for this project/action block)")

    try:
        vision = build_vision(config)
    except VisionError as exc:
        print(f"\n✗ vision model: {exc}")
        return 1
    try:
        status = asyncio.run(_ping(vision))
    except VisionError as exc:
        print(f"\n✗ vision model: {exc}")
        return 1
    print(f"\n✓ vision model: {status}")

    if args.streams:
        _check_streams(config)
    return 0


def _check_streams(config: Config) -> None:
    import cv2

    from .streams.video import safe_url

    print("\nStream probe (up to 10s each):")
    for stream in config.streams:
        if not stream.enabled or stream.type != "video":
            continue
        url = stream.url
        source = int(url) if url.isdigit() else url
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000]
        try:
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
        except TypeError:
            cap = cv2.VideoCapture(source)
        try:
            ok, frame = cap.read() if cap.isOpened() else (False, None)
        finally:
            cap.release()
        if ok and frame is not None:
            height, width = frame.shape[:2]
            print(f"  ✓ {stream.id}: {safe_url(url)} ({width}x{height})")
        else:
            print(f"  ✗ {stream.id}: no frame from {safe_url(url)}")


def cmd_list_tasks(args: argparse.Namespace) -> int:
    config = _load(args.config)
    source = build_task_source(config)
    tasks = source.fetch_tasks()
    completed = StateStore(config.app.data_dir / "state.json").completed_ids
    print(f"{len(tasks)} task(s) from {source.name}:\n")
    for task in tasks:
        mark = "✓" if task.id in completed else " "
        print(f" [{mark}] order={task.order:<6g} {task.title}  [{task.id}]")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    config = _load(args.config)
    path = config.app.data_dir / "state.json"
    StateStore(path).clear()
    print(f"cleared completed-task state in {path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args.config)
    setup_logging(config.app.log_level, config.app.data_dir / "monitor.log")
    if _problems(config):
        return 2

    from .api import create_app

    app = create_app(config)
    host = args.host or config.app.host
    port = args.port or config.app.port
    print(f"\nLive Task Monitor → http://{host}:{port}\n")

    import uvicorn

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=config.app.log_level.lower(),
        log_config=None,
        access_log=False,
    )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    demo_config = (
        Path(args.config)
        if args.config
        else (
            Path("config/config.demo.yaml")
            if Path("config/config.demo.yaml").is_file()
            else PROJECT_ROOT / "config/config.demo.yaml"
        )
    )
    config = _load(str(demo_config))
    setup_logging(config.app.log_level, config.app.data_dir / "monitor.log")

    from .demo import generate_demo_videos

    videos = generate_demo_videos(config.root / "data/demo")
    print("demo videos: " + ", ".join(str(path) for path in videos))
    print("model: mock (no credentials needed)")

    from .api import create_app

    app = create_app(config)
    host = args.host or config.app.host
    port = args.port or config.app.port
    print(f"\nDemo dashboard → http://{host}:{port}\n")

    import uvicorn

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=config.app.log_level.lower(),
        log_config=None,
        access_log=False,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ltm",
        description="Monitor live cameras and detect when each task is completed.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_config(argument_parser: argparse.ArgumentParser) -> None:
        argument_parser.add_argument(
            "-c",
            "--config",
            default=None,
            help="path to config.yaml (default: config/config.yaml or $LTM_CONFIG)",
        )

    run = subparsers.add_parser("run", help="start the monitor + dashboard")
    add_config(run)
    run.add_argument("--host", default=None)
    run.add_argument("--port", type=int, default=None)
    run.set_defaults(func=cmd_run)

    demo = subparsers.add_parser("demo", help="run with mock model and demo videos")
    add_config(demo)
    demo.add_argument("--host", default=None)
    demo.add_argument("--port", type=int, default=None)
    demo.set_defaults(func=cmd_demo)

    check = subparsers.add_parser("check", help="validate config, tasks and model")
    add_config(check)
    check.add_argument("--offline", action="store_true", help="skip network checks")
    check.add_argument("--streams", action="store_true", help="probe every video stream")
    check.set_defaults(func=cmd_check)

    tasks = subparsers.add_parser("list-tasks", help="print the ordered task queue")
    add_config(tasks)
    tasks.set_defaults(func=cmd_list_tasks)

    reset = subparsers.add_parser("reset", help="clear completed-task progress")
    add_config(reset)
    reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, TaskSourceError, VisionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
