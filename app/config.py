"""Persistent application settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


APP_NAME = "AIWebSiteBuilder"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    context_limit: int = 128_000
    timeout: int = 300
    compaction_threshold: int = 80
    max_tool_rounds: int = 25
    vision_max_px: int = 1568
    recent_projects: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.recent_projects is None:
            self.recent_projects = []

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls) -> "Settings":
        path = settings_path()
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        obj = cls(**filtered)
        # Guard against nonsense values.
        obj.context_limit = max(1_000, int(obj.context_limit or 128_000))
        obj.timeout = max(10, int(obj.timeout or 300))
        obj.compaction_threshold = min(95, max(30, int(obj.compaction_threshold or 80)))
        obj.max_tool_rounds = min(100, max(1, int(obj.max_tool_rounds or 25)))
        obj.vision_max_px = min(4096, max(512, int(obj.vision_max_px or 1568)))
        if not isinstance(obj.recent_projects, list):
            obj.recent_projects = []
        return obj

    def save(self) -> None:
        path = settings_path()
        tmp = path.with_suffix(".tmp")
        data = asdict(self)
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------- helpers
    def add_recent(self, project_path: str) -> None:
        path = str(Path(project_path).resolve())
        self.recent_projects = [p for p in self.recent_projects if p != path]
        self.recent_projects.insert(0, path)
        del self.recent_projects[12:]
        self.save()

    def remove_recent(self, project_path: str) -> None:
        path = str(Path(project_path).resolve())
        self.recent_projects = [p for p in self.recent_projects if p != path]
        self.save()

    def api_url(self) -> str:
        """Normalise the configured endpoint into a chat-completions URL."""
        base = (self.api_base or "").strip().rstrip("/")
        if not base:
            return ""
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def is_configured(self) -> bool:
        return bool(self.api_url() and self.model.strip())

    def masked_key(self) -> str:
        key = self.api_key or ""
        if len(key) <= 8:
            return "•" * len(key)
        return key[:4] + "…" + key[-4:]
