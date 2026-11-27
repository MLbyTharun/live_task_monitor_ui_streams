"""Typed configuration loading with ``${ENV_VAR}`` expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    """Raised when the configuration file is missing or invalid."""


def _expand_env(value: Any, missing: set[str]) -> Any:
    """Recursively replace ``${VAR}`` / ``${VAR:-default}`` in strings."""

    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            # An empty variable (e.g. VLM_MODEL= in .env) counts as unset so
            # `${VAR:-default}` still works and `ltm check` reports it.
            if os.environ.get(name):
                return os.environ[name]
            if default is not None:
                return default
            missing.add(name)
            return ""

        return _ENV_RE.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item, missing) for item in value]
    return value


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppConfig(_Section):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "info"
    data_dir: Path = Path("data")
    settings_file: str = "settings.json"
    ingest_token: str = ""
    cors_origins: list[str] = Field(default_factory=list)


class HyperRealityConfig(_Section):
    base_url: str = ""
    organization_id: str = ""
    project_id: str = ""
    action_block_id: str = ""
    token: str = ""
    page_size: int = Field(default=100, ge=1, le=500)
    include_wip: bool = False
    timeout_seconds: float = Field(default=30, gt=0)


class TasksConfig(_Section):
    source: Literal["hyperreality", "static"] = "hyperreality"
    refresh_seconds: int = Field(default=60, ge=1)
    static_file: Path = Path("examples/tasks.json")
    hyperreality: HyperRealityConfig = Field(default_factory=HyperRealityConfig)


class VisionConfig(_Section):
    provider: Literal["openai_compatible", "mock"] = "openai_compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=500, ge=16)
    timeout_seconds: float = Field(default=120, gt=0)
    json_mode: bool = True
    image_detail: Literal["low", "high", "auto"] = "auto"
    max_image_side: int = Field(default=768, ge=64, le=4096)
    jpeg_quality: int = Field(default=80, ge=20, le=100)
    mock_complete_after: int = Field(default=3, ge=1)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class AnalysisConfig(_Section):
    interval_seconds: float = Field(default=10, gt=0)
    settle_seconds: float = Field(default=3, ge=0)
    startup_grace_seconds: float = Field(default=5, ge=0)
    frames_per_stream: int = Field(default=1, ge=1, le=10)
    confirmation_votes: int = Field(default=2, ge=1, le=20)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    cooldown_seconds: float = Field(default=5, ge=0)
    max_frames_per_request: int = Field(default=8, ge=1, le=32)
    save_evidence: bool = True
    auto_start: bool = True
    history_size: int = Field(default=50, ge=1, le=1000)


class PromptConfig(_Section):
    system_file: Path = Path("config/prompts/system.md")
    verify_file: Path = Path("config/prompts/verify_task.md")


class StreamConfig(_Section):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = ""
    type: Literal["video", "push"] = "video"
    url: str = ""
    enabled: bool = True
    fps_limit: float = Field(default=2, gt=0, le=60)
    loop_file: bool = True
    reconnect_seconds: float = Field(default=3, gt=0)
    push_timeout_seconds: float = Field(default=10, gt=0)


class Config(_Section):
    root: Path = Field(default=Path("."), exclude=True)
    config_path: Path = Field(default=Path("config.yaml"), exclude=True)
    missing_env: list[str] = Field(default_factory=list, exclude=True)

    app: AppConfig = Field(default_factory=AppConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)
    streams: list[StreamConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_stream_ids(self) -> Config:
        ids = [stream.id for stream in self.streams]
        duplicates = {stream_id for stream_id in ids if ids.count(stream_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate stream ids: {', '.join(sorted(duplicates))}")
        return self

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        """Load a YAML config, apply ``.env`` and ``${VAR}`` expansion."""
        raw_path = path or os.environ.get("LTM_CONFIG") or "config/config.yaml"
        config_path = Path(raw_path).expanduser().resolve()
        if not config_path.is_file():
            raise ConfigError(f"config file not found: {config_path}")

        root = config_path.parent.parent if config_path.parent.name == "config" else config_path.parent

        # .env next to the project root wins; a .env in the cwd is a fallback.
        load_dotenv(root / ".env", override=False)
        load_dotenv(override=False)

        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"config root must be a mapping in {config_path}")

        missing: set[str] = set()
        expanded = _expand_env(raw, missing)

        try:
            config = cls.model_validate(expanded)
        except ValidationError as exc:
            raise ConfigError(f"invalid config {config_path}:\n{exc}") from exc

        config.root = root
        config.config_path = config_path
        config.missing_env = sorted(missing)
        config._resolve_paths()
        return config

    def _resolve_paths(self) -> None:
        def resolve(path: Path) -> Path:
            path = Path(path).expanduser()
            return path if path.is_absolute() else (self.root / path).resolve()

        self.app.data_dir = resolve(self.app.data_dir)
        self.tasks.static_file = resolve(self.tasks.static_file)
        self.prompts.system_file = resolve(self.prompts.system_file)
        self.prompts.verify_file = resolve(self.prompts.verify_file)
        for stream in self.streams:
            url = stream.url.strip()
            if url and "://" not in url and not url.isdigit():
                stream.url = str(resolve(Path(url)))

    # -------------------------------------------------------------- validate
    def validate_runtime(self) -> list[str]:
        """Return human-readable problems that would stop a real run."""
        problems: list[str] = []

        if not self.prompts.system_file.is_file():
            problems.append(f"system prompt not found: {self.prompts.system_file}")
        if not self.prompts.verify_file.is_file():
            problems.append(f"verification prompt not found: {self.prompts.verify_file}")

        if self.tasks.source == "hyperreality":
            hr = self.tasks.hyperreality
            for label, value in (
                ("HR_BASE_URL", hr.base_url),
                ("HR_TOKEN", hr.token),
                ("HR_ORG_ID", hr.organization_id),
            ):
                if not str(value).strip():
                    problems.append(f"{label} is empty (needed by tasks.source=hyperreality)")
        elif not self.tasks.static_file.is_file():
            problems.append(f"static task file not found: {self.tasks.static_file}")

        if self.vision.provider == "openai_compatible":
            if not self.vision.base_url.strip():
                problems.append("VLM_BASE_URL is empty (needed by vision.provider=openai_compatible)")
            if not self.vision.model.strip():
                problems.append("VLM_MODEL is empty (needed by vision.provider=openai_compatible)")

        return problems

    # ---------------------------------------------------------------- public
    def public_dict(self) -> dict[str, Any]:
        """Config safe to expose to the dashboard (no secrets)."""
        return {
            "config_path": str(self.config_path),
            "task_source": self.tasks.source,
            "vision": {
                "provider": self.vision.provider,
                "model": self.vision.model or "mock",
                "base_url": self.vision.base_url,
                "image_detail": self.vision.image_detail,
                "max_image_side": self.vision.max_image_side,
                "jpeg_quality": self.vision.jpeg_quality,
            },
            "analysis": self.analysis.model_dump(mode="json"),
            "streams": [
                {
                    "id": stream.id,
                    "name": stream.name or stream.id,
                    "type": stream.type,
                    "enabled": stream.enabled,
                }
                for stream in self.streams
            ],
            "prompts": {
                "system": str(self.prompts.system_file),
                "verify": str(self.prompts.verify_file),
            },
            "ingest_requires_token": bool(self.app.ingest_token),
            "missing_env": self.missing_env,
        }
