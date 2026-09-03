from pathlib import Path

from moot.models import ModelRegistry, size_fits

SAMPLE = """
[claude.claude-opus-5]
context_class = "large"
efforts = ["low", "medium", "high", "max"]
tier = "large"

[claude.claude-haiku-4-5-20251001]
context_class = "medium"
efforts = ["low", "medium", "high"]
tier = "small"
"""


def test_load_and_get(tmp_path: Path):
    p = tmp_path / "models.toml"
    p.write_text(SAMPLE)
    reg = ModelRegistry.load(p)
    spec = reg.get("claude", "claude-opus-5")
    assert spec is not None
    assert spec.context_class == "large"
    assert spec.efforts == ("low", "medium", "high", "max")
    assert spec.tier == "large"
    assert reg.get("claude", "nope") is None
    assert reg.get("codex", "claude-opus-5") is None
    assert [m.id for m in reg.for_adapter("claude")] == ["claude-opus-5", "claude-haiku-4-5-20251001"]


def test_validate_reports_each_problem(tmp_path: Path):
    p = tmp_path / "models.toml"
    p.write_text(SAMPLE)
    reg = ModelRegistry.load(p)
    assert reg.validate("claude", "claude-opus-5", "high") == []
    assert reg.validate("claude", "claude-opus-5", "turbo") == ["effort 'turbo' not valid for claude/claude-opus-5 (valid: low, medium, high, max)"]
    assert reg.validate("claude", "gpt-9", "high") == ["model 'gpt-9' unknown for adapter 'claude'"]
    assert reg.tier_of("claude", "claude-haiku-4-5-20251001") == "small"
    assert reg.tier_of("claude", "gpt-9") is None


def test_size_fits_context_class():
    assert size_fits("S", "small")
    assert size_fits("S", "large")
    assert not size_fits("M", "small")
    assert size_fits("M", "medium")
    assert not size_fits("L", "medium")
    assert size_fits("L", "large")


def test_packaged_registry_loads_and_has_claude_models():
    reg = ModelRegistry.load()
    ids = [m.id for m in reg.for_adapter("claude")]
    assert "claude-opus-5" in ids
    assert "claude-sonnet-5" in ids
    assert reg.for_adapter("codex"), "codex adapter must have at least one model"
