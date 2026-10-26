from pathlib import Path

from meld.models import ModelRegistry, size_fits

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


def test_the_catalogue_carries_the_current_families():
    """Plan 22: the Claude 5.5 family and Fable 5.1 (Anthropic's list of 2026-10-06) and Codex's gpt-6 family (its own
    `codex debug models` on 2026-10-26), with the efforts each CLI accepts; the older entries stay for configs that name them."""
    from meld.models import ModelRegistry
    reg = ModelRegistry.load()
    for adapter, model, tier in (("claude", "claude-fable-5-1", "large"), ("claude", "claude-opus-5-5", "large"), ("claude", "claude-sonnet-5-5", "medium"),
                                 ("claude", "claude-haiku-5-5", "small"), ("codex", "gpt-6.1-sol", "large"), ("codex", "gpt-6-sol", "large"),
                                 ("codex", "gpt-6-astra", "medium"), ("codex", "gpt-6-luna", "small"), ("claude", "claude-opus-5", "large"), ("codex", "gpt-5.6-sol", "large")):
        spec = reg.get(adapter, model)
        assert spec is not None and spec.tier == tier, (adapter, model)
    assert "xhigh" in reg.get("claude", "claude-opus-5-5").efforts and "ultra" in reg.get("codex", "gpt-6-astra").efforts
    assert "ultra" not in reg.get("codex", "gpt-6-luna").efforts and reg.validate("claude", "claude-sonnet-5-5", "xhigh") == []


def test_the_products_own_catalogue_lays_over_the_packaged_one(tmp_path: Path):
    """Plan 28: `.meld/models.toml` adds a local model (or redefines a packaged one) without touching the package."""
    from meld.models import write_product_model
    extra = tmp_path / ".meld" / "models.toml"
    write_product_model(extra, "codex", "qwen3-coder:30b", tier="large", context_class="large", efforts=["low", "high"])
    write_product_model(extra, "claude", "gpt-oss:20b", tier="medium", context_class="medium", efforts=["medium"])
    write_product_model(extra, "codex", "qwen3-coder:30b", tier="medium", context_class="large", efforts=["low", "high"])   # replaced, not duplicated
    text = extra.read_text()
    assert text.count('[codex."qwen3-coder:30b"]') == 1 and '[claude."gpt-oss:20b"]' in text
    reg = ModelRegistry.load(extra=extra)
    assert reg.get("codex", "qwen3-coder:30b").tier == "medium" and reg.get("claude", "gpt-oss:20b").efforts == ("medium",)
    assert reg.get("claude", "claude-sonnet-5") is not None                  # the packaged entries are still there
    assert ModelRegistry.load(extra=tmp_path / "absent.toml").get("codex", "qwen3-coder:30b") is None
