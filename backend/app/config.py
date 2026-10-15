"""配置加载：config.local.yaml（本机，gitignore）优先，回退 config.example.yaml。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent.parent  # novel-studio/


@dataclass
class TraeConfig:
    binary: str | None = None
    default_model: str = "GLM-5.3"
    exec_timeout_sec: int = 300


@dataclass
class MiniMaxConfig:
    api_key: str = ""
    base_url: str | None = None
    default_model: str = "MiniMax-M3.1-Flash-Preview"


@dataclass
class AppConfig:
    trae: TraeConfig = field(default_factory=TraeConfig)
    minimax: MiniMaxConfig = field(default_factory=MiniMaxConfig)
    routing: dict[str, dict] = field(default_factory=dict)
    vault_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def load_config() -> AppConfig:
    local = REPO / "config.local.yaml"
    source = local if local.exists() else REPO / "config.example.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    p = data.get("providers", {})
    cfg = AppConfig(
        trae=TraeConfig(**(p.get("trae") or {})),
        minimax=MiniMaxConfig(**(p.get("minimax") or {})),
        routing=data.get("routing", {}),
        vault_path=data.get("vault", {}).get("path", ""),
        raw=data,
    )
    routing_file = Path(__file__).resolve().parent.parent / "routing.yaml"
    if routing_file.exists() and not cfg.routing:
        cfg.routing = yaml.safe_load(routing_file.read_text(encoding="utf-8")) \
            .get("routing", {})
    return cfg
