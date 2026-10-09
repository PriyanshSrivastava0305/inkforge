"""Load and validate InkForge configuration (TOML).

Enforces two non-negotiables at load time:
- the model runtime is reached over loopback only (D2/D18);
- profiles only reference models on the allow-list (D16).
"""

from __future__ import annotations

import ipaddress
import os
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


class ConfigError(ValueError):
    pass


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: Literal["ollama", "llamacpp", "openai-local", "fake"] = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    max_loaded_models: int = Field(default=1, ge=1)
    keep_alive: str = "15m"
    allowed_models: list[str] = Field(min_length=1)
    min_version: str | None = None  # lowest tested runtime version; `doctor` warns below it
    tokenizer_family: dict[str, str] = Field(default_factory=dict)  # model -> cached tokenizer name

    @field_validator("base_url")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"base_url must be an http(s) URL, got {v!r}")
        if not is_loopback_host(parsed.hostname):
            raise ValueError(f"base_url must be loopback (strictly local), got host {parsed.hostname!r}")
        return v


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    enabled: bool = True
    num_ctx: int = Field(default=4096, ge=512)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    repeat_penalty: float | None = Field(default=None, gt=0.0)
    output_fraction: float = Field(default=0.3, gt=0.0, lt=1.0)
    min_output_tokens: int = Field(default=512, ge=1)
    seed: int | None = None
    # CPU inference: reading a cold prompt can take minutes before the first token (docs/08).
    first_token_timeout_s: float = Field(default=900, gt=0)
    idle_timeout_s: float = Field(default=180, gt=0)  # max gap between streamed chunks
    timeout_s: float = Field(default=1800, gt=0)  # whole call


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: RuntimeConfig
    profiles: dict[str, ModelProfile]

    @model_validator(mode="after")
    def _models_allowed(self) -> ModelConfig:
        allowed = set(self.runtime.allowed_models)
        bad = {name: p.model for name, p in self.profiles.items() if p.model not in allowed}
        if bad:
            raise ValueError(f"profiles reference models outside allowed_models: {bad}")
        for required in ("planner", "prose", "critic"):
            if required not in self.profiles:
                raise ValueError(f"missing required profile [profiles.{required}]")
        return self


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_root: Path
    cache_root: Path
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    approval_gates: dict[str, bool] = Field(default_factory=dict)

    @field_validator("api_host")
    @classmethod
    def _loopback_api(cls, v: str) -> str:
        if not is_loopback_host(v):
            raise ValueError(f"api.host must be loopback (strictly local), got {v!r}")
        return v


def load_app_config(config_dir: Path = CONFIG_DIR, workspace: Path | str | None = None) -> AppConfig:
    """defaults.toml < defaults.local.toml < INKFORGE_WORKSPACE env < explicit `workspace` argument."""
    data = _read_toml(config_dir / "defaults.toml")
    local = config_dir / "defaults.local.toml"
    if local.is_file():
        data = _deep_merge(data, _read_toml(local))
    ws = workspace or os.environ.get("INKFORGE_WORKSPACE") or data["workspace"]["root"]
    return AppConfig(
        workspace_root=Path(ws).expanduser(),
        cache_root=Path(data["cache"]["root"]).expanduser(),
        api_host=data["api"]["host"],
        api_port=data["api"]["port"],
        approval_gates=data.get("approval_gates", {}),
    )


def load_model_config(profile_set: str | None = None, config_dir: Path = CONFIG_DIR) -> ModelConfig:
    """Load config/model-profiles.toml, then overlay model-profiles.<profile_set>.toml if given."""
    data = _read_toml(config_dir / "model-profiles.toml")
    if profile_set:
        overlay = config_dir / f"model-profiles.{profile_set}.toml"
        if not overlay.is_file():
            raise ConfigError(f"unknown profile set {profile_set!r} (no {overlay.name})")
        data = _deep_merge(data, _read_toml(overlay))
    local = config_dir / "model-profiles.local.toml"
    if local.is_file():
        data = _deep_merge(data, _read_toml(local))
    return ModelConfig.model_validate(data)
