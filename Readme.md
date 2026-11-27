# Live Task Monitor — UI Streams Test Project

Separate testing project. It does **not** use RTSP/IP-camera inputs. It plays streams in the browser and sends screenshots back to the same vision-model pipeline.

Original project is unchanged in `../live_task_monitor/`. This directory is independent.

## What changed

- No `CAM1_URL` / `CAM2_URL` / RTSP configuration.
- `config/config.yaml` uses `type: push` streams:
  - `cam1`
  - `cam2`
  - `ui-screen`
- `tasks.source: static` by default for no-HyperReality testing. Can be switched back to `hyperreality`.
- `vision.provider: openai_compatible` by default. Can be `mock` for no-model testing.
- New test page:

```text
http://localhost:8080/ui-streams.html
```

- Two local videos are served by FastAPI:
  - `/streams/cam_a.mp4`
  - `/streams/cam_b.mp4`
- Browser page sends screenshots to:
  - `POST /api/ingest/cam1`
  - `POST /api/ingest/cam2`
  - `POST /api/ingest/ui-screen`
- Model inputs come from push workers, not RTSP workers.

## Run

### With a real vision model

```bash
cd live_task_monitor_ui_streams

# optional: copy .env.example to .env and fill VLM_*
cp .env.example .env

python3 -m venv .venv
source .venv/bin/activate                 # Linux/macOS
# .venv\Scripts\Activate.ps1             # Windows PowerShell

python -m pip install --upgrade pip
python -m pip install -r requirement.txt
python -m pip install -e .

# set in .env:
# VLM_BASE_URL=http://localhost:8000/v1
# VLM_API_KEY=local
# VLM_MODEL=Qwen/Qwen3-VL-2B-Instruct

python -m ltm run
```

Windows Command Prompt activation:

```bat
.venv\Scripts\activate.bat
```

Open:

```text
http://localhost:8080/ui-streams.html
```

### No-model stream test

To test stream wiring without a VLM, set in `config/config.yaml`:

```yaml
vision:
  provider: mock
  mock_complete_after: 3
```

Then run `python -m ltm run`. The mock model does not actually inspect pixels; it tests that UI screenshots reach `/api/ingest` and flow through the engine.

## UI test page

`/ui-streams.html` provides:

- two `<video>` players served locally;
- `Send UI streams cam1/cam2 to model` checkbox;
- `Share screen to ui-screen` using `navigator.mediaDevices.getDisplayMedia`;
- optional ingest token input;
- link back to main dashboard `/`.

Requirements for screen share:

- open via `localhost` or HTTPS;
- browser permission;
- `navigator.mediaDevices.getDisplayMedia` support;
- share this tab/window/screen in the browser prompt.

Frames are JPEG screenshots posted every 500 ms while enabled.

## Backend behavior is the same

Same `TaskEngine`, `StreamManager`, `PushStreamWorker`, HyperReality/static task source, OpenAI-compatible vision client, completion voting, evidence, dashboard, SSE.

Push stream frame format:

```bash
curl -X POST --data-binary @frame.jpg \
  -H "Content-Type: image/jpeg" \
  http://localhost:8080/api/ingest/cam1
```

If `INGEST_TOKEN` is set, include `X-Ingest-Token` or `?token=`.

## Requirements

See `requirement.txt`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`. No RTSP/FFmpeg camera setup is required for this testing project, though `opencv-python-headless` is still used for video decoding/JPEG encoding.

Install runtime:

```bash
python -m pip install -r requirement.txt
python -m pip install -e .
```

## Notes

- `.env`, `.venv`, `data/*`, caches are ignored.
- No secrets are committed.
- `ui-screen` requires a producer; no frames means it is ignored by snapshots.
- `tasks.source: static` uses `examples/tasks.json`.
