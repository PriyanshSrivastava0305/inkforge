from pathlib import Path

import pytest
from pydantic import ValidationError

from inkforge.config import ConfigError, ModelConfig, is_loopback_host, load_model_config

D16_MODELS = {"llama3.1:8b-instruct-q4_K_M", "llama3.2:1b"}


def test_default_profiles_use_8b():
    cfg = load_model_config()
    for role in ("planner", "prose", "critic"):
        assert cfg.profiles[role].model == "llama3.1:8b-instruct-q4_K_M"


def test_fast_profile_set_uses_1b_and_keeps_other_fields():
    cfg = load_model_config("fast")
    assert {cfg.profiles[r].model for r in ("planner", "prose", "critic")} == {"llama3.2:1b"}
    assert cfg.profiles["prose"].temperature == 0.85  # inherited from defaults


def test_generation_models_are_d16_only():
    for profile_set in (None, "fast"):
        cfg = load_model_config(profile_set)
        gen = {p.model for name, p in cfg.profiles.items() if name != "embedding"}
        assert gen <= D16_MODELS


def test_unknown_profile_set():
    with pytest.raises(ConfigError):
        load_model_config("nope")


@pytest.mark.parametrize(
    "host,ok",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("127.0.0.2", True),
        ("0.0.0.0", False),
        ("192.168.1.5", False),
        ("api.example.com", False),
        ("10.0.0.1", False),
    ],
)
def test_is_loopback_host(host: str, ok: bool):
    assert is_loopback_host(host) is ok


def _cfg(base_url: str, model: str = "llama3.2:1b") -> dict:
    prof = {"model": model}
    return {
        "runtime": {"base_url": base_url, "allowed_models": ["llama3.2:1b"]},
        "profiles": {"planner": prof, "prose": prof, "critic": prof},
    }


def test_rejects_remote_base_url():
    with pytest.raises(ValidationError, match="loopback"):
        ModelConfig.model_validate(_cfg("https://api.example.com"))


def test_rejects_model_not_on_allow_list():
    with pytest.raises(ValidationError, match="allowed_models"):
        ModelConfig.model_validate(_cfg("http://127.0.0.1:11434", model="qwen2.5:14b"))


def test_local_override_is_applied(tmp_path: Path):
    for name in ("model-profiles.toml",):
        (tmp_path / name).write_text(
            (Path(__file__).parents[2] / "config" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "model-profiles.local.toml").write_text(
        "[profiles.prose]\nnum_ctx = 8192\n", encoding="utf-8"
    )
    assert load_model_config(config_dir=tmp_path).profiles["prose"].num_ctx == 8192
