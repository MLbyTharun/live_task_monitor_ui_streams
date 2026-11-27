from __future__ import annotations

from pathlib import Path

import pytest

from ltm.config import Config, ConfigError


def write_config(tmp_path: Path, body: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "prompts").mkdir(exist_ok=True)
    (config_dir / "prompts" / "system.md").write_text("system", encoding="utf-8")
    (config_dir / "prompts" / "verify.md").write_text("verify", encoding="utf-8")
    path = config_dir / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_env_expansion_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VLM_BASE_URL", "http://gpu:8000/v1")
    monkeypatch.delenv("VLM_MODEL", raising=False)
    path = write_config(
        tmp_path,
        """
app: {data_dir: data}
vision:
  provider: mock
  base_url: ${VLM_BASE_URL}
  model: ${VLM_MODEL:-qwen3-vl-2b}
streams:
  - id: cam1
    type: push
""",
    )

    config = Config.load(path)

    assert config.vision.base_url == "http://gpu:8000/v1"
    assert config.vision.model == "qwen3-vl-2b"
    assert config.root == tmp_path
    assert config.app.data_dir == tmp_path / "data"
    assert "VLM_BASE_URL" not in config.missing_env
    assert "VLM_MODEL" not in config.missing_env


def test_missing_env_is_recorded(tmp_path, monkeypatch):
    monkeypatch.delenv("HR_TOKEN", raising=False)
    path = write_config(
        tmp_path,
        """
vision: {provider: mock}
tasks:
  source: hyperreality
  hyperreality:
    base_url: https://example.test
    token: ${HR_TOKEN}
streams:
  - id: cam1
    type: push
""",
    )

    config = Config.load(path)

    assert "HR_TOKEN" in config.missing_env
    problems = config.validate_runtime()
    assert any("HR_TOKEN" in problem for problem in problems)


def test_duplicate_stream_ids_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
vision: {provider: mock}
streams:
  - {id: cam1, type: push}
  - {id: cam1, type: push}
""",
    )
    with pytest.raises(ConfigError, match="duplicate stream ids"):
        Config.load(path)


def test_video_stream_without_url_is_left_to_the_dashboard(tmp_path):
    """Cameras are managed in the UI now, so an empty seed URL must not block boot."""
    path = write_config(
        tmp_path,
        """
vision: {provider: mock}
streams:
  - {id: cam1, type: video}
""",
    )
    config = Config.load(path)
    problems = config.validate_runtime()
    assert not any("cam1" in problem for problem in problems)


def test_unknown_key_is_rejected(tmp_path):
    path = write_config(
        tmp_path,
        """
vision: {provider: mock}
anaylsis: {interval_seconds: 5}
streams:
  - {id: cam1, type: push}
""",
    )
    with pytest.raises(ConfigError, match="anaylsis"):
        Config.load(path)


def test_demo_config_is_runnable():
    root = Path(__file__).resolve().parents[1]
    config = Config.load(root / "config" / "config.demo.yaml")
    assert config.validate_runtime() == []
    assert config.tasks.source == "static"
    assert config.vision.provider == "mock"
    assert len([stream for stream in config.streams if stream.enabled]) == 2
