"""
Configuration utilities for agent package.

Provides settings and get_agent_params without external src.* dependency.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class _RetryConfig:
    max_retries: int = 2


@dataclass
class Settings:
    retry: _RetryConfig = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.retry is None:
            self.retry = _RetryConfig()


# Module-level singleton
settings = Settings()


# ── Agent params from agents.yaml ──────────────────────────────────────────

_AGENT_PARAMS_CACHE: dict[str, dict] = {}
_DEFAULT_PARAMS = {"temperature": 0.1, "max_tokens": 4096}

_CONFIG_DIR = Path(__file__).resolve().parent.parent  # agent/


def get_agent_params(module_name: str) -> dict:
    """Load agent parameters from agents.yaml (cached)."""
    if module_name in _AGENT_PARAMS_CACHE:
        return _AGENT_PARAMS_CACHE[module_name]

    config_path = _CONFIG_DIR / module_name / "agents.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            params = {**_DEFAULT_PARAMS, **data.get("defaults", {})}
            _AGENT_PARAMS_CACHE[module_name] = params
            return params
        except Exception:
            pass

    _AGENT_PARAMS_CACHE[module_name] = _DEFAULT_PARAMS
    return _DEFAULT_PARAMS
