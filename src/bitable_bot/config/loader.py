"""Load YAML config and expand ${ENV} references. Credentials never live in YAML."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .models import AppConfig

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(Exception):
    pass


def expand_env(obj: Any, env: dict[str, str] | None = None) -> Any:
    env = os.environ if env is None else env
    if isinstance(obj, str):
        return _ENV.sub(lambda m: env.get(m.group(1), m.group(2) or ""), obj)
    if isinstance(obj, list):
        return [expand_env(x, env) for x in obj]
    if isinstance(obj, dict):
        return {k: expand_env(v, env) for k, v in obj.items()}
    return obj


def load_config(path: str | Path, env: dict[str, str] | None = None) -> AppConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    try:
        return AppConfig.model_validate(expand_env(raw, env))
    except Exception as e:  # pydantic ValidationError
        raise ConfigError(str(e)) from e
