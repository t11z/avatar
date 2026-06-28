from __future__ import annotations

from avatar.config import load_config, load_config_from_dict


def test_env_interpolation(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret-value")
    cfg = load_config_from_dict(
        {
            "providers": {"anthropic": {"api_key": "${MY_TOKEN}"}},
            "platforms": [{"id": "bsky", "type": "bluesky", "handle": "bot.bsky.social"}],
        }
    )
    assert cfg.providers.model_dump()["anthropic"]["api_key"] == "secret-value"


def test_env_interpolation_default():
    cfg = load_config_from_dict({"store": {"type": "sqlite", "path": "${AVATAR_DB:-/tmp/x.db}"}})
    assert cfg.store.path == "/tmp/x.db"


def test_defaults_are_safe():
    cfg = load_config_from_dict({})
    assert cfg.dry_run is True
    assert cfg.security.enabled is False
    assert cfg.store.type == "sqlite"


def test_loads_example(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("dry_run: false\nplatforms:\n  - id: a\n    type: bluesky\n")
    cfg = load_config(p)
    assert cfg.dry_run is False
    assert cfg.platforms[0].id == "a"
