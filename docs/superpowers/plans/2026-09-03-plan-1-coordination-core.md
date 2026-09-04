# Meld Plan 1: Coordination Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `meld` Python package's coordination core: config, GitHub access, the coordination-comment grammar and agreement protocol, the typed board, the rules engine that computes legal actions, and the `status`, `wait`, `claim`, `release`, and `post` verbs.

**Architecture:** "LLM decides, Python enforces." `meld status` reads GitHub into a typed `Board`, `rules.py` computes which verbs each party may call, and every mutating verb re-checks legality and refuses with the violated rule. All GitHub access goes through `github.py`, a thin wrapper over the `gh` CLI with an ETag cache and injectable `run` function so every test runs without network. Everything durable lives in GitHub; `~/.meld` holds only disposable state.

**Tech Stack:** Python 3.12, `uv`, `click` (only runtime dependency), `pytest`, `gh` CLI (system dependency), stdlib `tomllib`, `dataclasses`, `subprocess`.

**Spec:** `docs/superpowers/specs/2026-09-03-meld-design.md` — sections 4 to 8, 12, 17 to 19 are implemented here. Read the spec first; this plan argues from it.

## Global Constraints

- Python `>=3.12`; managed by `uv`; run everything as `uv run ...`.
- Only runtime dependency is `click>=8.1`. Dev dependency: `pytest>=8`. No others without a spec change.
- Every GitHub call goes through `meld.github.GitHub`. Nothing else shells out to `gh`.
- Every invariant lives in `meld/rules.py` and has a test. Prompts never enforce.
- No secrets in the repo, in issues, in briefs, or in `$MELD_HOME`. Tokens come from environment variables named in `.meld.toml`.
- Coordination comments start with `[<party>] <VERB> [<ref>] [<qualifier>]`. Only `meld` writes `[joint]`.
- Effective review quorum is `max(1, min(review.quorum, N-1))`.
- Commit messages are plain conventional commits (`feat:`, `test:`, `chore:`). No AI attribution, no `Co-Authored-By` lines.
- Work on branch `plan-1-coordination-core` in `~/Documents/meld`; never commit to `main` directly after Task 1's branch step.
- Deviations from the spec introduced by this plan, all small: `models.toml` ships inside the package at `src/meld/data/models.toml` (spec drew it at repo root) so an installed package finds it; `.meld.toml` gains `[repo] name = "owner/name"`; the grammar gains a `[joint] WORKER` verb for agreed worker types and a `[human] RELEASE` verb for forced releases. Update the spec in Task 16.

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/meld/__init__.py`
- Create: `src/meld/cli.py`
- Create: `tests/__init__.py`
- Create: `tests/test_cli.py`
- Create: `Taskfile.yml`
- Modify: `README.md` (create if absent)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `meld.cli.main` (a `click.Group`), `meld.__version__ == "0.1.0"`.

- [ ] **Step 1: Create the branch**

```bash
cd ~/Documents/meld && git checkout -b plan-1-coordination-core
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "meld"
version = "0.1.0"
description = "Multi-AI coordination: N AI coding systems, one GitHub repo, one human."
requires-python = ">=3.12"
dependencies = ["click>=8.1"]

[project.scripts]
meld = "meld.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/meld"]

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Write the package init and the failing CLI test**

`src/meld/__init__.py`:

```python
__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

`tests/test_cli.py`:

```python
from click.testing import CliRunner

from meld.cli import main


def test_version_flag_prints_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd ~/Documents/meld && uv sync && uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.cli'`

- [ ] **Step 5: Write the minimal CLI**

`src/meld/cli.py`:

```python
"""meld command-line entry point. Verbs are added in later tasks."""
from __future__ import annotations

import click

from meld import __version__


@click.group()
@click.version_option(__version__, prog_name="meld")
def main() -> None:
    """Meld: multi-AI coordination over a GitHub repo."""


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 7: Add Taskfile, README, .gitignore**

`Taskfile.yml`:

```yaml
version: "3"

tasks:
  test:
    desc: Run the unit test suite
    cmds:
      - uv run pytest -q

  test:smoke:
    desc: Run opt-in smoke tests against real CLIs (Plan 2)
    cmds:
      - MELD_SMOKE=1 uv run pytest tests/smoke -q

  status:
    desc: Print the board for the current party (MELD_GROUP / MELD_ROLE from env)
    cmds:
      - uv run meld status
```

`README.md`:

```markdown
# Meld

Multi-AI coordination: N independent AI coding systems (Claude Code, Codex CLI, ...) work on one GitHub repo as separate groups, negotiate on a coordination issue, claim work via issue assignment, review and modify each other's PRs, and answer to one human.

Design: `docs/superpowers/specs/2026-09-03-meld-design.md`.

## Develop

    uv sync
    uv run pytest
    uv run meld --help
```

`.gitignore` (replace contents):

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
dist/
.meld-home/
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/meld/__init__.py src/meld/cli.py tests/__init__.py tests/test_cli.py Taskfile.yml README.md .gitignore
git commit -m "chore: scaffold meld package with click CLI and pytest"
```

---

### Task 2: Config loading and Party

**Files:**
- Create: `src/meld/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `class ConfigError(ValueError)`
  - `@dataclass(frozen=True) RoleConfig(model: str, effort: str)`
  - `@dataclass(frozen=True) GroupConfig(name, cli, bot, token_env, concurrency, review_concurrency, orchestrator: RoleConfig, pm: RoleConfig)`
  - `@dataclass(frozen=True) Config(...)` with `Config.from_dict(data: dict) -> Config`, `Config.effective_quorum -> int`, `Config.group_for_bot(login) -> GroupConfig | None`, `Config.is_human(login) -> bool`, `Config.group_names -> tuple[str, ...]`
  - `load_config(path: Path) -> Config`
  - `@dataclass(frozen=True) Party(kind: Literal["group","pm","human","joint"], group: str | None = None)` with `.label -> str` (e.g. `[claude]`, `[claude:pm]`, `[human]`, `[joint]`) and `Party.parse(text, cfg) -> Party | None`
  - `party_from_env(cfg, env: Mapping[str, str]) -> Party`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from meld.config import Config, ConfigError, Party, load_config, party_from_env

MINIMAL = """
[repo]
name = "acme/widgets"

[humans]
accounts = ["empowerScott"]

[groups.claude]
cli = "claude"
bot = "meld-claude-bot"
token_env = "MELD_TOKEN_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"

[groups.codex]
cli = "codex"
bot = "meld-codex-bot"
token_env = "MELD_TOKEN_CODEX"
[groups.codex.orchestrator]
model = "gpt-x"
effort = "high"
[groups.codex.pm]
model = "gpt-x-mini"
effort = "medium"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".meld.toml"
    p.write_text(text)
    return p


def test_loads_groups_and_defaults(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.repo_name == "acme/widgets"
    assert cfg.default_branch == "main"
    assert cfg.private is True
    assert cfg.humans == ("empowerScott",)
    assert cfg.group_names == ("claude", "codex")
    assert cfg.groups["claude"].bot == "meld-claude-bot"
    assert cfg.groups["claude"].concurrency == 8
    assert cfg.groups["claude"].review_concurrency == 4
    assert cfg.groups["claude"].orchestrator.model == "claude-opus-5"
    assert cfg.review_quorum == 2
    assert cfg.pingpong_cap == 3
    assert cfg.amend_cap == 3
    assert cfg.poll_interval_seconds == 30
    assert cfg.wait_timeout_seconds == 300
    assert cfg.timeouts == {"S": "20m", "M": "45m", "L": "90m"}
    assert cfg.budget == {"small": 200, "medium": 80, "large": 20}
    assert cfg.budget_wallclock_hours == 72


def test_effective_quorum_is_min_of_quorum_and_n_minus_one(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.effective_quorum == 1  # two groups
    three = MINIMAL + """
[groups.gemini]
cli = "gemini"
bot = "meld-gemini-bot"
token_env = "MELD_TOKEN_GEMINI"
[groups.gemini.orchestrator]
model = "g"
effort = "high"
[groups.gemini.pm]
model = "g"
effort = "medium"
"""
    cfg3 = load_config(write(tmp_path, three))
    assert cfg3.effective_quorum == 2


def test_group_for_bot_and_is_human(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.group_for_bot("meld-codex-bot").name == "codex"
    assert cfg.group_for_bot("nobody") is None
    assert cfg.is_human("empowerScott")
    assert not cfg.is_human("meld-claude-bot")


def test_missing_repo_name_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="repo.name"):
        load_config(write(tmp_path, MINIMAL.replace('name = "acme/widgets"', "")))


def test_group_missing_bot_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="groups.codex.bot"):
        load_config(write(tmp_path, MINIMAL.replace('bot = "meld-codex-bot"', "")))


def test_party_labels_and_parse(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert Party("group", "claude").label == "[claude]"
    assert Party("pm", "claude").label == "[claude:pm]"
    assert Party("human").label == "[human]"
    assert Party("joint").label == "[joint]"
    assert Party.parse("codex:pm", cfg) == Party("pm", "codex")
    assert Party.parse("codex", cfg) == Party("group", "codex")
    assert Party.parse("human", cfg) == Party("human")
    assert Party.parse("mystery", cfg) is None
    assert Party.parse("codex:qa", cfg) is None


def test_party_from_env(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert party_from_env(cfg, {}) == Party("human")
    assert party_from_env(cfg, {"MELD_GROUP": "claude"}) == Party("group", "claude")
    assert party_from_env(cfg, {"MELD_GROUP": "claude", "MELD_ROLE": "pm"}) == Party("pm", "claude")
    with pytest.raises(ConfigError):
        party_from_env(cfg, {"MELD_GROUP": "nope"})
    with pytest.raises(ConfigError):
        party_from_env(cfg, {"MELD_GROUP": "claude", "MELD_ROLE": "worker"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.config'`

- [ ] **Step 3: Write `src/meld/config.py`**

```python
"""Load .meld.toml into a typed Config, and identify the calling Party."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping


class ConfigError(ValueError):
    """Raised for a missing or invalid configuration value."""


@dataclass(frozen=True)
class RoleConfig:
    model: str
    effort: str


@dataclass(frozen=True)
class GroupConfig:
    name: str
    cli: str
    bot: str
    token_env: str
    concurrency: int
    review_concurrency: int
    orchestrator: RoleConfig
    pm: RoleConfig


def _require(data: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise ConfigError(f"missing required setting {path}.{key}")
    return data[key]


def _role(data: Mapping[str, Any], path: str) -> RoleConfig:
    return RoleConfig(model=str(_require(data, "model", path)), effort=str(_require(data, "effort", path)))


@dataclass(frozen=True)
class Config:
    repo_name: str
    default_branch: str
    private: bool
    humans: tuple[str, ...]
    groups: dict[str, GroupConfig]
    review_quorum: int
    pingpong_cap: int
    security_paths: tuple[str, ...]
    amend_cap: int
    poll_interval_seconds: int
    wait_timeout_seconds: int
    restart_hours: int
    roster_refresh_minutes: int
    silence_grace_minutes: int
    summary_cadence_minutes: int
    timeouts: dict[str, str]
    budget: dict[str, int]
    budget_wallclock_hours: int
    notify_webhook_env: str | None
    notify_repeat_after_minutes: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Config":
        repo = data.get("repo", {})
        humans = data.get("humans", {})
        groups_raw = data.get("groups", {})
        if not groups_raw:
            raise ConfigError("missing required table groups (at least one group)")
        groups: dict[str, GroupConfig] = {}
        for name, g in groups_raw.items():
            path = f"groups.{name}"
            groups[name] = GroupConfig(
                name=name,
                cli=str(_require(g, "cli", path)),
                bot=str(_require(g, "bot", path)),
                token_env=str(_require(g, "token_env", path)),
                concurrency=int(g.get("concurrency", 8)),
                review_concurrency=int(g.get("review_concurrency", 4)),
                orchestrator=_role(_require(g, "orchestrator", path), f"{path}.orchestrator"),
                pm=_role(_require(g, "pm", path), f"{path}.pm"),
            )
        review = data.get("review", {})
        agreement = data.get("agreement", {})
        polling = data.get("polling", {})
        runners = data.get("runners", {})
        timeouts = data.get("timeouts", {})
        budget = data.get("budget", {})
        notify = data.get("notify", {})
        return cls(
            repo_name=str(_require(repo, "name", "repo")),
            default_branch=str(repo.get("default_branch", "main")),
            private=bool(repo.get("private", True)),
            humans=tuple(str(h) for h in humans.get("accounts", [])),
            groups=groups,
            review_quorum=int(review.get("quorum", 2)),
            pingpong_cap=int(review.get("pingpong_cap", 3)),
            security_paths=tuple(str(p) for p in review.get("security_paths", [])),
            amend_cap=int(agreement.get("amend_cap", 3)),
            poll_interval_seconds=int(polling.get("interval_seconds", 30)),
            wait_timeout_seconds=int(polling.get("wait_timeout_seconds", 300)),
            restart_hours=int(runners.get("restart_hours", 4)),
            roster_refresh_minutes=int(runners.get("roster_refresh_minutes", 60)),
            silence_grace_minutes=int(runners.get("silence_grace_minutes", 30)),
            summary_cadence_minutes=int(runners.get("summary_cadence_minutes", 60)),
            timeouts={"S": str(timeouts.get("S", "20m")), "M": str(timeouts.get("M", "45m")), "L": str(timeouts.get("L", "90m"))},
            budget={
                "small": int(budget.get("small", 200)),
                "medium": int(budget.get("medium", 80)),
                "large": int(budget.get("large", 20)),
            },
            budget_wallclock_hours=int(budget.get("wallclock_hours", 72)),
            notify_webhook_env=notify.get("slack_dm_webhook_env"),
            notify_repeat_after_minutes=int(notify.get("repeat_after_minutes", 120)),
        )

    @property
    def group_names(self) -> tuple[str, ...]:
        return tuple(self.groups.keys())

    @property
    def effective_quorum(self) -> int:
        return max(1, min(self.review_quorum, len(self.groups) - 1))

    def group_for_bot(self, login: str) -> GroupConfig | None:
        for g in self.groups.values():
            if g.bot == login:
                return g
        return None

    def is_human(self, login: str) -> bool:
        return login in self.humans


def load_config(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e
    return Config.from_dict(data)


PartyKind = Literal["group", "pm", "human", "joint"]


@dataclass(frozen=True)
class Party:
    kind: PartyKind
    group: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "group":
            return f"[{self.group}]"
        if self.kind == "pm":
            return f"[{self.group}:pm]"
        return f"[{self.kind}]"

    @classmethod
    def parse(cls, text: str, cfg: Config) -> "Party | None":
        if text == "human":
            return cls("human")
        if text == "joint":
            return cls("joint")
        name, _, role = text.partition(":")
        if name not in cfg.groups:
            return None
        if role == "":
            return cls("group", name)
        if role == "pm":
            return cls("pm", name)
        return None


def party_from_env(cfg: Config, env: Mapping[str, str]) -> Party:
    group = env.get("MELD_GROUP")
    if not group:
        return Party("human")
    if group not in cfg.groups:
        raise ConfigError(f"MELD_GROUP={group!r} is not a configured group")
    role = env.get("MELD_ROLE", "orchestrator")
    if role == "orchestrator":
        return Party("group", group)
    if role == "pm":
        return Party("pm", group)
    raise ConfigError(f"MELD_ROLE={role!r} must be 'orchestrator' or 'pm'")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/meld/config.py tests/test_config.py
git commit -m "feat: load .meld.toml into Config and identify the calling Party"
```

---

### Task 3: Model registry and size compatibility

**Files:**
- Create: `src/meld/data/models.toml`
- Create: `src/meld/data/__init__.py` (empty, so hatch packages the data directory)
- Create: `src/meld/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ModelSpec(adapter: str, id: str, context_class: str, efforts: tuple[str, ...], tier: str)`
  - `class ModelRegistry` with `ModelRegistry.load(path: Path | None = None) -> ModelRegistry` (None loads the packaged file), `.get(adapter, model_id) -> ModelSpec | None`, `.validate(adapter, model_id, effort) -> list[str]` (empty list means valid), `.for_adapter(adapter) -> list[ModelSpec]`, `.tier_of(adapter, model_id) -> str | None`
  - `size_fits(size: str, context_class: str) -> bool`
  - `SIZES = ("S", "M", "L")`

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.models'`

- [ ] **Step 3: Write the packaged `models.toml`**

Create `src/meld/data/__init__.py` (empty). Then `src/meld/data/models.toml`. The Claude entries below are current model IDs. For Codex, **do not invent identifiers**: run `codex --help` and `codex exec --help` on this machine, check `~/.codex/config.toml` for a configured `model`, and consult the Codex docs for the current model list and reasoning-effort values. Enter exactly what the installed CLI accepts, at least one model, and mark tiers by cost.

```toml
# Per adapter, per model. context_class and tier are small|medium|large.
# efforts is the ordered list the adapter accepts, cheapest first.

[claude.claude-opus-5]
context_class = "large"
efforts = ["low", "medium", "high", "max"]
tier = "large"

[claude.claude-sonnet-5]
context_class = "large"
efforts = ["low", "medium", "high", "max"]
tier = "medium"

[claude.claude-haiku-4-5-20251001]
context_class = "medium"
efforts = ["low", "medium", "high"]
tier = "small"

# Codex entries: fill from the installed CLI's model list. Example shape only;
# replace the key and values with real ones before committing.
# [codex.<model-id>]
# context_class = "large"
# efforts = ["low", "medium", "high", "xhigh"]
# tier = "medium"
```

- [ ] **Step 4: Write `src/meld/models.py`**

```python
"""Model registry: which models and efforts each adapter accepts, and size fit."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

SIZES = ("S", "M", "L")
_SIZE_MIN_CLASS = {"S": "small", "M": "medium", "L": "large"}
_CLASS_RANK = {"small": 0, "medium": 1, "large": 2}


@dataclass(frozen=True)
class ModelSpec:
    adapter: str
    id: str
    context_class: str
    efforts: tuple[str, ...]
    tier: str


def size_fits(size: str, context_class: str) -> bool:
    """True if an issue of `size` can run on a model of `context_class`."""
    return _CLASS_RANK[context_class] >= _CLASS_RANK[_SIZE_MIN_CLASS[size]]


class ModelRegistry:
    def __init__(self, specs: dict[str, dict[str, ModelSpec]]):
        self._specs = specs

    @classmethod
    def load(cls, path: Path | None = None) -> "ModelRegistry":
        if path is None:
            text = resources.files("meld.data").joinpath("models.toml").read_text()
        else:
            text = path.read_text()
        raw = tomllib.loads(text)
        specs: dict[str, dict[str, ModelSpec]] = {}
        for adapter, models in raw.items():
            specs[adapter] = {}
            for model_id, m in models.items():
                specs[adapter][model_id] = ModelSpec(
                    adapter=adapter,
                    id=model_id,
                    context_class=str(m["context_class"]),
                    efforts=tuple(str(e) for e in m["efforts"]),
                    tier=str(m["tier"]),
                )
        return cls(specs)

    def get(self, adapter: str, model_id: str) -> ModelSpec | None:
        return self._specs.get(adapter, {}).get(model_id)

    def for_adapter(self, adapter: str) -> list[ModelSpec]:
        return list(self._specs.get(adapter, {}).values())

    def tier_of(self, adapter: str, model_id: str) -> str | None:
        spec = self.get(adapter, model_id)
        return spec.tier if spec else None

    def validate(self, adapter: str, model_id: str, effort: str) -> list[str]:
        spec = self.get(adapter, model_id)
        if spec is None:
            return [f"model '{model_id}' unknown for adapter '{adapter}'"]
        if effort not in spec.efforts:
            return [f"effort '{effort}' not valid for {adapter}/{model_id} (valid: {', '.join(spec.efforts)})"]
        return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (4 tests). If the last test fails on the codex assertion, you have not filled the Codex entries; do that now from the installed CLI.

- [ ] **Step 6: Commit**

```bash
git add src/meld/data/__init__.py src/meld/data/models.toml src/meld/models.py tests/test_models.py
git commit -m "feat: model registry with per-adapter efforts, tiers, and size fit"
```

---

### Task 4: GitHub wrapper over `gh api`

**Files:**
- Create: `src/meld/github.py`
- Create: `tests/fakegh.py`
- Create: `tests/test_github.py`

**Interfaces:**
- Produces:
  - `class GitHubError(RuntimeError)`, `class RateLimited(GitHubError)` with `.reset_at: int | None`
  - `class GitHub(repo: str, token: str | None = None, run: Callable = subprocess.run, cache_dir: Path | None = None)`
  - Low level: `.get(endpoint: str, params: dict | None = None) -> Any`, `.get_paginated(endpoint, params=None) -> list`, `.post(endpoint, body: dict) -> Any`, `.patch(endpoint, body) -> Any`, `.delete(endpoint, body: dict | None = None) -> Any`
  - Helpers (all endpoints relative to `repos/{repo}`): `.viewer_login() -> str`, `.list_open_milestones() -> list[dict]`, `.list_issues(milestone: int) -> list[dict]` (excludes PR entries), `.get_issue(number) -> dict`, `.list_comments(issue: int, since: str | None = None) -> list[dict]`, `.list_pulls(state: str = "open") -> list[dict]`, `.pull_reviews(number) -> list[dict]`, `.pull_commits(number) -> list[dict]`, `.check_runs(sha) -> dict`, `.add_assignees(number, logins: list[str]) -> dict`, `.remove_assignees(number, logins) -> dict`, `.add_labels(number, labels: list[str]) -> list`, `.remove_label(number, label) -> None` (404 is not an error), `.create_comment(number, body) -> dict`
  - `tests/fakegh.py`: `class FakeGh` callable usable as `run=`, with `.route(method, endpoint, body, status=200, etag=None)`, `.calls: list[Call]` where `Call(method, endpoint, query: dict, body: dict | None, headers: dict)`.

- [ ] **Step 1: Write the fake `gh` runner**

`tests/fakegh.py`:

```python
"""A fake `gh api` for tests: routes (method, path) to canned responses."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit


@dataclass
class Call:
    method: str
    endpoint: str
    query: dict[str, str]
    body: dict | None
    headers: dict[str, str]


@dataclass
class FakeGh:
    routes: dict[tuple[str, str], tuple[int, object, str | None]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    rate_limit_next: bool = False

    def route(self, method: str, endpoint: str, body: object, status: int = 200, etag: str | None = None) -> None:
        self.routes[(method.upper(), endpoint)] = (status, body, etag)

    def __call__(self, cmd, input=None, capture_output=True, text=True, env=None, **_):
        assert cmd[:3] == ["gh", "api", "--include"], cmd
        method, endpoint, headers = "GET", None, {}
        it = iter(cmd[3:])
        for tok in it:
            if tok == "-X":
                method = next(it).upper()
            elif tok == "-H":
                k, _, v = next(it).partition(": ")
                headers[k.lower()] = v
            elif tok == "--input":
                next(it)
            elif tok.startswith("-"):
                continue
            else:
                endpoint = tok
        assert endpoint is not None, cmd
        parts = urlsplit(endpoint)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        body = json.loads(input) if input else None
        self.calls.append(Call(method, parts.path, query, body, headers))

        if self.rate_limit_next:
            self.rate_limit_next = False
            head = "HTTP/2.0 403 Forbidden\r\nX-Ratelimit-Remaining: 0\r\nX-Ratelimit-Reset: 1700000000\r\n\r\n"
            return subprocess.CompletedProcess(cmd, 1, stdout=head + '{"message":"API rate limit exceeded"}', stderr="")

        if (method, parts.path) not in self.routes:
            head = "HTTP/2.0 404 Not Found\r\n\r\n"
            return subprocess.CompletedProcess(cmd, 1, stdout=head + '{"message":"Not Found"}', stderr="")
        status, resp, etag = self.routes[(method, parts.path)]
        if etag and headers.get("if-none-match") == etag:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"HTTP/2.0 304 Not Modified\r\nETag: {etag}\r\n\r\n", stderr="")
        head = f"HTTP/2.0 {status} OK\r\n" + (f"ETag: {etag}\r\n" if etag else "") + "\r\n"
        out = head + (json.dumps(resp) if resp is not None else "")
        return subprocess.CompletedProcess(cmd, 0 if status < 400 else 1, stdout=out, stderr="")
```

- [ ] **Step 2: Write the failing tests**

`tests/test_github.py`:

```python
import pytest

from meld.github import GitHub, GitHubError, RateLimited
from tests.fakegh import FakeGh


def make(tmp_path, token="tok"):
    fake = FakeGh()
    gh = GitHub("acme/widgets", token=token, run=fake, cache_dir=tmp_path / "cache")
    return fake, gh


def test_get_sets_token_and_parses_json(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7, "title": "x"})
    assert gh.get_issue(7) == {"number": 7, "title": "x"}
    assert fake.calls[0].method == "GET"


def test_etag_cache_returns_cached_body_on_304(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7}, etag='W/"abc"')
    assert gh.get_issue(7) == {"number": 7}
    assert gh.get_issue(7) == {"number": 7}
    assert fake.calls[1].headers["if-none-match"] == 'W/"abc"'


def test_paginated_follows_pages_until_short_page(tmp_path):
    fake, gh = make(tmp_path)
    page = [{"number": i} for i in range(100)]
    fake.route("GET", "repos/acme/widgets/pulls", page)
    # FakeGh serves the same body for every page; a full page followed by another full page
    # would loop forever, so the implementation must stop when a page repeats its first item.
    items = gh.get_paginated("repos/acme/widgets/pulls", {"state": "open"})
    assert len(items) == 100
    assert fake.calls[0].query["per_page"] == "100"
    assert fake.calls[0].query["page"] == "1"


def test_list_issues_filters_out_pull_requests(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues", [{"number": 1}, {"number": 2, "pull_request": {"url": "u"}}])
    assert gh.list_issues(3) == [{"number": 1}]
    assert fake.calls[0].query == {"milestone": "3", "state": "all", "per_page": "100", "page": "1"}


def test_list_comments_passes_since(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/10/comments", [])
    gh.list_comments(10, since="2026-09-03T00:00:00Z")
    assert fake.calls[0].query["since"] == "2026-09-03T00:00:00Z"


def test_mutations_send_json_bodies(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/issues/7/assignees", {"assignees": [{"login": "b"}]})
    fake.route("DELETE", "repos/acme/widgets/issues/7/assignees", {"assignees": []})
    fake.route("POST", "repos/acme/widgets/issues/7/labels", [{"name": "meld:ready"}])
    fake.route("POST", "repos/acme/widgets/issues/7/comments", {"id": 99, "html_url": "u"})
    gh.add_assignees(7, ["b"])
    gh.remove_assignees(7, ["b"])
    gh.add_labels(7, ["meld:ready"])
    assert gh.create_comment(7, "hi")["id"] == 99
    assert fake.calls[0].body == {"assignees": ["b"]}
    assert fake.calls[1].method == "DELETE"
    assert fake.calls[2].body == {"labels": ["meld:ready"]}
    assert fake.calls[3].body == {"body": "hi"}


def test_remove_label_ignores_404(tmp_path):
    fake, gh = make(tmp_path)
    gh.remove_label(7, "meld:ready")  # no route -> 404 -> swallowed


def test_http_error_raises(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"message": "boom"}, status=500)
    with pytest.raises(GitHubError, match="500"):
        gh.get_issue(7)


def test_rate_limit_raises_rate_limited(tmp_path):
    fake, gh = make(tmp_path)
    fake.rate_limit_next = True
    with pytest.raises(RateLimited) as ei:
        gh.get_issue(7)
    assert ei.value.reset_at == 1700000000


def test_viewer_login(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "user", {"login": "empowerScott"})
    assert gh.viewer_login() == "empowerScott"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_github.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.github'`

- [ ] **Step 4: Write `src/meld/github.py`**

```python
"""Thin wrapper over `gh api`. The only module that talks to GitHub."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

RunFn = Callable[..., subprocess.CompletedProcess]


class GitHubError(RuntimeError):
    pass


class RateLimited(GitHubError):
    def __init__(self, reset_at: int | None):
        super().__init__(f"GitHub rate limit exceeded (resets at {reset_at})")
        self.reset_at = reset_at


class GitHub:
    def __init__(self, repo: str, token: str | None = None, run: RunFn = subprocess.run, cache_dir: Path | None = None):
        self.repo = repo
        self.token = token
        self._run = run
        self._cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- low level -------------------------------------------------------

    def _cache_path(self, endpoint: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / (hashlib.sha1(endpoint.encode()).hexdigest() + ".json")

    def _call(self, method: str, endpoint: str, body: dict | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
        cmd = ["gh", "api", "--include", "-X", method]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        if body is not None:
            cmd += ["--input", "-"]
        cmd.append(endpoint)
        env = dict(os.environ)
        if self.token:
            env["GH_TOKEN"] = self.token
        proc = self._run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True, text=True, env=env)
        raw = (proc.stdout or "").replace("\r\n", "\n")
        head, _, text = raw.partition("\n\n")
        lines = head.split("\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError) as e:
            raise GitHubError(f"could not parse gh response for {method} {endpoint}: {proc.stderr.strip() or raw[:200]}") from e
        resp_headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(": ")
            if k:
                resp_headers[k.lower()] = v
        if status in (403, 429) and resp_headers.get("x-ratelimit-remaining") == "0":
            reset = resp_headers.get("x-ratelimit-reset")
            raise RateLimited(int(reset) if reset else None)
        parsed: Any = None
        if text.strip():
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
        return status, resp_headers, parsed

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = endpoint + ("?" + urlencode(params) if params else "")
        cache = self._cache_path(url)
        headers: dict[str, str] = {}
        cached: dict | None = None
        if cache is not None and cache.exists():
            cached = json.loads(cache.read_text())
            headers["If-None-Match"] = cached["etag"]
        status, resp_headers, body = self._call("GET", url, headers=headers)
        if status == 304 and cached is not None:
            return cached["body"]
        if status >= 400:
            raise GitHubError(f"GET {url} failed with HTTP {status}: {body}")
        if cache is not None and "etag" in resp_headers:
            cache.write_text(json.dumps({"etag": resp_headers["etag"], "body": body}))
        return body

    def get_paginated(self, endpoint: str, params: dict[str, Any] | None = None) -> list:
        items: list = []
        page = 1
        seen_first: Any = object()
        while True:
            q = dict(params or {})
            q.update({"per_page": 100, "page": page})
            batch = self.get(endpoint, q)
            if not isinstance(batch, list) or not batch:
                break
            if batch[0] == seen_first:
                break
            seen_first = batch[0]
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

    def _mutate(self, method: str, endpoint: str, body: dict | None) -> Any:
        status, _, resp = self._call(method, endpoint, body=body)
        if status >= 400:
            raise GitHubError(f"{method} {endpoint} failed with HTTP {status}: {resp}")
        return resp

    def post(self, endpoint: str, body: dict) -> Any:
        return self._mutate("POST", endpoint, body)

    def patch(self, endpoint: str, body: dict) -> Any:
        return self._mutate("PATCH", endpoint, body)

    def delete(self, endpoint: str, body: dict | None = None) -> Any:
        return self._mutate("DELETE", endpoint, body)

    # ---- helpers ---------------------------------------------------------

    def _r(self, path: str) -> str:
        return f"repos/{self.repo}/{path}"

    def viewer_login(self) -> str:
        return str(self.get("user")["login"])

    def list_open_milestones(self) -> list[dict]:
        return self.get_paginated(self._r("milestones"), {"state": "open"})

    def list_issues(self, milestone: int) -> list[dict]:
        raw = self.get_paginated(self._r("issues"), {"milestone": milestone, "state": "all"})
        return [i for i in raw if "pull_request" not in i]

    def get_issue(self, number: int) -> dict:
        return self.get(self._r(f"issues/{number}"))

    def list_comments(self, issue: int, since: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if since:
            params["since"] = since
        return self.get_paginated(self._r(f"issues/{issue}/comments"), params)

    def list_pulls(self, state: str = "open") -> list[dict]:
        return self.get_paginated(self._r("pulls"), {"state": state})

    def pull_reviews(self, number: int) -> list[dict]:
        return self.get_paginated(self._r(f"pulls/{number}/reviews"))

    def pull_commits(self, number: int) -> list[dict]:
        return self.get_paginated(self._r(f"pulls/{number}/commits"))

    def check_runs(self, sha: str) -> dict:
        return self.get(self._r(f"commits/{sha}/check-runs"))

    def add_assignees(self, number: int, logins: list[str]) -> dict:
        return self.post(self._r(f"issues/{number}/assignees"), {"assignees": logins})

    def remove_assignees(self, number: int, logins: list[str]) -> dict:
        return self.delete(self._r(f"issues/{number}/assignees"), {"assignees": logins})

    def add_labels(self, number: int, labels: list[str]) -> list:
        return self.post(self._r(f"issues/{number}/labels"), {"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        status, _, resp = self._call("DELETE", self._r(f"issues/{number}/labels/{label}"))
        if status >= 400 and status != 404:
            raise GitHubError(f"DELETE label {label} on #{number} failed with HTTP {status}: {resp}")

    def create_comment(self, number: int, body: str) -> dict:
        return self.post(self._r(f"issues/{number}/comments"), {"body": body})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_github.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Commit**

```bash
git add src/meld/github.py tests/fakegh.py tests/test_github.py
git commit -m "feat: gh api wrapper with etag cache, pagination, and rate-limit detection"
```

---

### Task 5: Grammar — headers, authorization, validation, compose

**Files:**
- Create: `src/meld/grammar.py`
- Create: `tests/test_grammar.py`

**Interfaces:**
- Consumes: `Config`, `Party` from `meld.config`.
- Produces:
  - `@dataclass(frozen=True) Header(party: Party, verb: str, ref: str | None, qualifier: str | None)`
  - `@dataclass(frozen=True) ParsedComment(id: int, author: str, created_at: str, body: str, header: Header | None, authorized: bool, errors: tuple[str, ...])` with `.text -> str` (body minus the header line, stripped) and `.ok -> bool` (`header is not None and authorized and not errors`)
  - `parse_header(line: str, cfg: Config) -> Header | None`
  - `allowed_verbs(party: Party) -> frozenset[str]`
  - `validate(header: Header, body: str) -> list[str]`
  - `is_authorized(author: str, header: Header, cfg: Config) -> bool`
  - `parse_comment(raw: dict, cfg: Config) -> ParsedComment` (raw is a GitHub comment JSON: `id`, `user.login`, `created_at`, `body`)
  - `compose(header: Header, text: str) -> str`
  - `has_verified_by(body: str) -> bool`
  - Constants: `ORCHESTRATOR_VERBS`, `PM_VERBS`, `HUMAN_VERBS`, `JOINT_VERBS`, `REQUIRES_REF`, `REQUIRES_VERIFIED_BY`, `AGREEMENT_VERBS = {"PROPOSE", "DRAFT", "AMEND", "AGREE", "DISSENT"}`

- [ ] **Step 1: Write the failing tests**

`tests/test_grammar.py`:

```python
from meld.config import Config, Party
from meld.grammar import (
    Header,
    compose,
    has_verified_by,
    is_authorized,
    parse_comment,
    parse_header,
    validate,
)

CFG = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "groups": {
        "claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "codex": {"cli": "codex", "bot": "meld-codex-bot", "token_env": "T2",
                  "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
    },
})


def test_parse_header_variants():
    assert parse_header("[claude] ROSTER", CFG) == Header(Party("group", "claude"), "ROSTER", None, None)
    assert parse_header("[codex:pm] REVIEW-REQUEST #42", CFG) == Header(Party("pm", "codex"), "REVIEW-REQUEST", "#42", None)
    assert parse_header("[human] ASK ask-7", CFG) == Header(Party("human"), "ASK", "ask-7", None)
    assert parse_header("[joint] ANSWER ask-7 DISAGREE", CFG) == Header(Party("joint"), "ANSWER", "ask-7", "DISAGREE")
    assert parse_header("  [claude] DRIFT  ", CFG) == Header(Party("group", "claude"), "DRIFT", None, None)


def test_parse_header_rejects_garbage():
    assert parse_header("hello there", CFG) is None
    assert parse_header("[mystery] ROSTER", CFG) is None
    assert parse_header("[claude] roster", CFG) is None  # verbs are upper case
    assert parse_header("[claude] MADE-UP-VERB", CFG) is None
    assert parse_header("[claude] REVIEW-REQUEST #1", CFG) is None  # PM-only verb


def test_validate_required_fields():
    assert validate(Header(Party("group", "claude"), "CHALLENGE", None, None), "x") == ["CHALLENGE requires a ref"]
    assert validate(Header(Party("joint"), "PLAN", None, None), "no evidence") == ["PLAN requires a verified-by: line"]
    assert validate(Header(Party("joint"), "PLAN", None, None), "text\n\nverified-by: pytest -q (3 passed)") == []
    assert validate(Header(Party("group", "claude"), "EVIDENCE", "#4", None), "x") == ["EVIDENCE requires a verified-by: line"]


def test_has_verified_by_is_case_insensitive_and_needs_content():
    assert has_verified_by("a\nVerified-By: ran make test\n")
    assert not has_verified_by("verified-by:\n")
    assert not has_verified_by("nothing")


def test_authorization():
    assert is_authorized("meld-claude-bot", Header(Party("group", "claude"), "ROSTER", None, None), CFG)
    assert is_authorized("meld-claude-bot", Header(Party("pm", "claude"), "STATUS", None, None), CFG)
    assert not is_authorized("meld-codex-bot", Header(Party("group", "claude"), "ROSTER", None, None), CFG)
    assert is_authorized("empowerScott", Header(Party("human"), "HALT", None, None), CFG)
    assert not is_authorized("randouser", Header(Party("human"), "HALT", None, None), CFG)
    assert is_authorized("meld-codex-bot", Header(Party("joint"), "PLAN", None, None), CFG)
    assert not is_authorized("empowerScott", Header(Party("joint"), "PLAN", None, None), CFG)


def test_parse_comment_end_to_end():
    raw = {"id": 5, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T10:00:00Z",
           "body": "[claude] PROPOSE plan-1\n\nSplit into 4 issues."}
    c = parse_comment(raw, CFG)
    assert c.ok
    assert c.header.verb == "PROPOSE" and c.header.ref == "plan-1"
    assert c.text == "Split into 4 issues."

    imposter = parse_comment({**raw, "user": {"login": "randouser"}}, CFG)
    assert imposter.header is not None and not imposter.authorized and not imposter.ok

    plain = parse_comment({**raw, "body": "just chatting"}, CFG)
    assert plain.header is None and not plain.ok and plain.text == "just chatting"


def test_compose_round_trips():
    h = Header(Party("pm", "codex"), "REVIEW-REQUEST", "#42", None)
    body = compose(h, "Please review.\n\nverified-by: pytest")
    assert body.startswith("[codex:pm] REVIEW-REQUEST #42\n\n")
    assert parse_header(body.splitlines()[0], CFG) == h
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_grammar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.grammar'`

- [ ] **Step 3: Write `src/meld/grammar.py`**

```python
"""Coordination-comment grammar: `[<party>] <VERB> [<ref>] [<qualifier>]`."""
from __future__ import annotations

import re
from dataclasses import dataclass

from meld.config import Config, Party

ORCHESTRATOR_VERBS = frozenset({
    "ROSTER", "ROSTER-CHANGE", "CONFIG", "PROPOSE", "AMEND", "AGREE", "DISSENT", "DRAFT", "ACK",
    "NEEDS-HUMAN", "BUDGET", "CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED", "AUDIT",
    "DRIFT", "PROPOSE-WORKER", "PING", "SOLO", "RESUMED", "PROTOCOL-VIOLATION",
})
PM_VERBS = frozenset({
    "REVIEW-REQUEST", "STATUS", "NEEDS-HUMAN", "BUDGET", "PROPOSE-WORKER", "AUDIT",
    "PROTOCOL-VIOLATION", "IGNORED-COMMENT", "PING", "RESUMED",
})
HUMAN_VERBS = frozenset({
    "ASK", "DIRECTIVE", "AMEND", "APPROVE", "ACK", "PROVIDED", "RESOLVED", "HALT", "RESUME", "REVERT",
    "RELEASE", "CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED",
})
JOINT_VERBS = frozenset({"PLAN", "PLAN-ADD", "ANSWER", "DECISION", "WORKER"})
AGREEMENT_VERBS = frozenset({"PROPOSE", "DRAFT", "AMEND", "AGREE", "DISSENT"})

REQUIRES_REF = frozenset({
    "PROPOSE", "AMEND", "AGREE", "DISSENT", "DRAFT", "ANSWER", "ASK", "DIRECTIVE", "REVIEW-REQUEST",
    "NEEDS-HUMAN", "CHALLENGE", "EVIDENCE", "RETRACT", "UPHELD", "DISMISSED", "AUDIT", "DECISION",
    "PROPOSE-WORKER", "PROVIDED", "RESOLVED", "REVERT", "RELEASE", "PLAN-ADD", "ACK", "WORKER",
})
REQUIRES_VERIFIED_BY = frozenset({"PLAN", "DECISION", "ANSWER", "EVIDENCE"})

_HEADER_RE = re.compile(
    r"^\s*\[(?P<party>[A-Za-z0-9_-]+(?::pm)?)\]\s+(?P<verb>[A-Z][A-Z-]*)"
    r"(?:\s+(?P<ref>(?![A-Z][A-Z-]*\s*$)\S+))?(?:\s+(?P<qualifier>[A-Z][A-Z-]*))?\s*$"
)
_VERIFIED_RE = re.compile(r"^\s*verified-by:\s*\S.*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Header:
    party: Party
    verb: str
    ref: str | None
    qualifier: str | None


@dataclass(frozen=True)
class ParsedComment:
    id: int
    author: str
    created_at: str
    body: str
    header: Header | None
    authorized: bool
    errors: tuple[str, ...]

    @property
    def text(self) -> str:
        if self.header is None:
            return self.body.strip()
        first_nl = self.body.find("\n")
        return "" if first_nl == -1 else self.body[first_nl + 1:].strip()

    @property
    def ok(self) -> bool:
        return self.header is not None and self.authorized and not self.errors


def allowed_verbs(party: Party) -> frozenset[str]:
    return {"group": ORCHESTRATOR_VERBS, "pm": PM_VERBS, "human": HUMAN_VERBS, "joint": JOINT_VERBS}[party.kind]


def parse_header(line: str, cfg: Config) -> Header | None:
    m = _HEADER_RE.match(line)
    if not m:
        return None
    party = Party.parse(m.group("party"), cfg)
    if party is None:
        return None
    verb = m.group("verb")
    if verb not in allowed_verbs(party):
        return None
    return Header(party, verb, m.group("ref"), m.group("qualifier"))


def has_verified_by(body: str) -> bool:
    return _VERIFIED_RE.search(body) is not None


def validate(header: Header, body: str) -> list[str]:
    errors: list[str] = []
    if header.verb in REQUIRES_REF and not header.ref:
        errors.append(f"{header.verb} requires a ref")
    if header.verb in REQUIRES_VERIFIED_BY and not has_verified_by(body):
        errors.append(f"{header.verb} requires a verified-by: line")
    return errors


def is_authorized(author: str, header: Header, cfg: Config) -> bool:
    p = header.party
    if p.kind == "human":
        return cfg.is_human(author)
    if p.kind == "joint":
        return cfg.group_for_bot(author) is not None
    group = cfg.groups.get(p.group or "")
    return group is not None and group.bot == author


def parse_comment(raw: dict, cfg: Config) -> ParsedComment:
    body = raw.get("body") or ""
    author = (raw.get("user") or {}).get("login", "")
    first_line = body.split("\n", 1)[0]
    header = parse_header(first_line, cfg)
    authorized = header is not None and is_authorized(author, header, cfg)
    errors = tuple(validate(header, body)) if header is not None else ()
    return ParsedComment(
        id=int(raw["id"]), author=author, created_at=str(raw.get("created_at", "")),
        body=body, header=header, authorized=authorized, errors=errors,
    )


def compose(header: Header, text: str) -> str:
    parts = [header.party.label, header.verb]
    if header.ref:
        parts.append(header.ref)
    if header.qualifier:
        parts.append(header.qualifier)
    return " ".join(parts) + "\n\n" + text.strip() + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_grammar.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/meld/grammar.py tests/test_grammar.py
git commit -m "feat: coordination header grammar with authorization and validation"
```

---

### Task 6: Grammar — agreement protocol state

**Files:**
- Modify: `src/meld/grammar.py` (append)
- Create: `tests/test_agreement.py`

**Interfaces:**
- Produces (in `meld.grammar`):
  - `@dataclass(frozen=True) Agreement(ref: str, text: str | None, proposer: str | None, agreed: frozenset[str], dissent: dict[str, str], rounds: int, status: str, joint_posted: bool)` where `status in {"none", "open", "agreed", "majority", "disagree"}` and `.can_amend(cfg) -> bool`
  - `agreement_for(comments: Sequence[ParsedComment], ref: str, cfg: Config) -> Agreement`

Semantics (from spec section 7): `PROPOSE`/`DRAFT` by group G sets the text, proposer G, agreed `{G}`; `AMEND` by G sets new text, proposer G, agreed `{G}`, `rounds += 1`; `AGREE` by G adds G; `DISSENT` by G records its position. Status: `none` if no text; `agreed` when every group agreed; else `open` until every group has agreed or dissented; then `majority` if agreed count is a strict majority of groups, else `disagree`. `can_amend` is `rounds < cfg.amend_cap`. A `[joint]` comment with the same ref sets `joint_posted`. Only `ok` comments count.

- [ ] **Step 1: Write the failing tests**

`tests/test_agreement.py`:

```python
from meld.config import Config
from meld.grammar import agreement_for, parse_comment

CFG2 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "agreement": {"amend_cap": 2},
    "groups": {
        "claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "codex": {"cli": "codex", "bot": "meld-codex-bot", "token_env": "T2",
                  "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
    },
})
CFG3 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "agreement": {"amend_cap": 2},
    "groups": {
        "claude": {"cli": "claude", "bot": "meld-claude-bot", "token_env": "T1",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "codex": {"cli": "codex", "bot": "meld-codex-bot", "token_env": "T2",
                  "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
        "gemini": {"cli": "gemini", "bot": "meld-gemini-bot", "token_env": "T3",
                   "orchestrator": {"model": "m", "effort": "high"}, "pm": {"model": "m", "effort": "medium"}},
    },
})

BOTS = {"claude": "meld-claude-bot", "codex": "meld-codex-bot", "gemini": "meld-gemini-bot"}


def c(cfg, i, group, verb, ref, text="body", joint=False, author=None):
    party = "joint" if joint else group
    login = author or (BOTS[group] if group in BOTS else group)
    return parse_comment({"id": i, "user": {"login": login}, "created_at": f"2026-09-03T10:{i:02d}:00Z",
                          "body": f"[{party}] {verb} {ref}\n\n{text}"}, cfg)


def test_none_when_no_proposal():
    a = agreement_for([], "plan-1", CFG2)
    assert a.status == "none" and a.text is None


def test_propose_then_agree_is_agreed():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG2, 2, "codex", "AGREE", "plan-1")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "agreed" and a.text == "v1" and a.proposer == "claude" and a.agreed == {"claude", "codex"}


def test_amend_resets_agreement_and_counts_round():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG2, 2, "codex", "AMEND", "plan-1", "v2")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "open" and a.text == "v2" and a.proposer == "codex" and a.agreed == {"codex"} and a.rounds == 1
    assert a.can_amend(CFG2)
    cs.append(c(CFG2, 3, "claude", "AMEND", "plan-1", "v3"))
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.rounds == 2 and not a.can_amend(CFG2) and a.status == "open"


def test_two_groups_split_is_disagree_after_all_vote():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG2, 2, "codex", "DISSENT", "plan-1", "I object")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "disagree" and a.dissent == {"codex": "I object"}


def test_three_groups_majority():
    cs = [c(CFG3, 1, "claude", "PROPOSE", "plan-1", "v1"), c(CFG3, 2, "codex", "AGREE", "plan-1"),
          c(CFG3, 3, "gemini", "DISSENT", "plan-1", "no")]
    a = agreement_for(cs, "plan-1", CFG3)
    assert a.status == "majority" and a.agreed == {"claude", "codex"} and a.dissent == {"gemini": "no"}


def test_unauthorized_and_other_ref_comments_are_ignored():
    cs = [c(CFG2, 1, "claude", "PROPOSE", "plan-1", "v1"),
          c(CFG2, 2, "codex", "AGREE", "plan-1", author="randouser"),
          c(CFG2, 3, "codex", "AGREE", "plan-2")]
    a = agreement_for(cs, "plan-1", CFG2)
    assert a.status == "open" and a.agreed == {"claude"}


def test_draft_behaves_like_propose_and_joint_marks_posted():
    cs = [c(CFG2, 1, "codex", "DRAFT", "ask-1", "answer"), c(CFG2, 2, "claude", "AGREE", "ask-1"),
          c(CFG2, 3, "codex", "ANSWER", "ask-1", "answer\n\nverified-by: meld status", joint=True)]
    a = agreement_for(cs, "ask-1", CFG2)
    assert a.status == "agreed" and a.joint_posted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agreement.py -v`
Expected: FAIL with `ImportError: cannot import name 'agreement_for'`

- [ ] **Step 3: Append to `src/meld/grammar.py`**

```python
from typing import Sequence  # add to the imports at the top of the file


@dataclass(frozen=True)
class Agreement:
    ref: str
    text: str | None
    proposer: str | None
    agreed: frozenset[str]
    dissent: dict[str, str]
    rounds: int
    status: str
    joint_posted: bool

    def can_amend(self, cfg: Config) -> bool:
        return self.rounds < cfg.amend_cap


def agreement_for(comments: Sequence[ParsedComment], ref: str, cfg: Config) -> Agreement:
    text: str | None = None
    proposer: str | None = None
    agreed: set[str] = set()
    dissent: dict[str, str] = {}
    rounds = 0
    joint_posted = False
    for cmt in sorted(comments, key=lambda x: (x.created_at, x.id)):
        if not cmt.ok or cmt.header is None or cmt.header.ref != ref:
            continue
        h = cmt.header
        if h.party.kind == "joint":
            joint_posted = True
            continue
        if h.party.kind not in ("group", "pm") or h.party.group is None:
            continue
        g = h.party.group
        if h.verb in ("PROPOSE", "DRAFT"):
            text, proposer, agreed, dissent = cmt.text, g, {g}, {}
        elif h.verb == "AMEND":
            text, proposer, agreed, dissent = cmt.text, g, {g}, {}
            rounds += 1
        elif h.verb == "AGREE" and text is not None:
            agreed.add(g)
            dissent.pop(g, None)
        elif h.verb == "DISSENT" and text is not None:
            dissent[g] = cmt.text
            agreed.discard(g)
    groups = set(cfg.group_names)
    if text is None:
        status = "none"
    elif agreed >= groups:
        status = "agreed"
    elif (agreed | set(dissent)) < groups:
        status = "open"
    elif len(agreed) * 2 > len(groups):
        status = "majority"
    else:
        status = "disagree"
    return Agreement(ref, text, proposer, frozenset(agreed), dissent, rounds, status, joint_posted)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agreement.py tests/test_grammar.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/meld/grammar.py tests/test_agreement.py
git commit -m "feat: agreement protocol state (propose/amend/agree/dissent) with quorum"
```

---

### Task 7: Board model (pure data)

**Files:**
- Create: `src/meld/board.py`
- Create: `tests/factories.py`
- Create: `tests/test_board.py`

**Interfaces:**
- Consumes: `Config` (`meld.config`), `ParsedComment` (`meld.grammar`).
- Produces (in `meld.board`):
  - `STATUS_LABELS: dict[str, str]` mapping `meld:*` labels to status names `planned|ready|in_progress|in_review|blocked|needs_human`
  - `@dataclass(frozen=True) Issue(number, title, body, labels: frozenset[str], assignees: tuple[str, ...], state, milestone: int | None, created_at: str = "", updated_at: str = "")` with `.status -> str` (adds `closed`, `unlabeled`), `.label_value(prefix) -> str | None`, `.worker`, `.model`, `.effort`, `.size`, `.challenged`
  - `@dataclass(frozen=True) Review(author, state, commit_id, submitted_at: str = "")`
  - `@dataclass(frozen=True) PullRequest(number, title, body, head_ref, head_sha, author, head_committer, issue_number: int | None, reviews: tuple[Review, ...], checks: str, labels: frozenset[str], state, merged: bool)` with `.latest_reviews() -> dict[str, Review]`, `.approvals_on_head() -> frozenset[str]`, `.changes_requested_on_head() -> frozenset[str]`, `.changes_requested_total() -> int`, `.challenged`
  - `@dataclass(frozen=True) RunRecord(group, issue, kind, pr: int | None, pid, started_at, worktree, log_path, model, effort, tier, milestone, alive: bool, collected: bool)` with `.finished -> bool` (exited and not yet collected)
  - `@dataclass(frozen=True) Board(repo, milestone, coordination_issue, phase, halted, issues: dict[int, Issue], pulls: dict[int, PullRequest], comments: tuple[ParsedComment, ...], runs: dict[str, dict[int, RunRecord]], spend: dict[str, dict[str, int]], open_challenges: frozenset[str], ignored_comments: int)` with `.in_progress_count(bot) -> int`, `.pr_for_issue(n) -> PullRequest | None`, `.run_for(group, issue) -> RunRecord | None`, `.is_challenged(ref) -> bool`
  - `derive_phase(coordination_labels: frozenset[str]) -> str`, `derive_halted(comments) -> bool`, `derive_open_challenges(comments) -> frozenset[str]`
- `tests/factories.py` produces: `CFG2` (two groups, bots `meld-claude-bot`/`meld-codex-bot`, human `empowerScott`), `CFG3` (adds `gemini`/`meld-gemini-bot`), `mk_issue(...)`, `mk_pr(...)`, `mk_comment(cfg, id, author, body, created_at=...)`, `mk_board(cfg, ...)`, `mk_run(...)`.

- [ ] **Step 1: Write the factories**

`tests/factories.py`:

```python
"""Builders for pure board objects used across rules/status tests."""
from __future__ import annotations

from meld.board import Board, Issue, PullRequest, Review, RunRecord
from meld.config import Config
from meld.grammar import ParsedComment, parse_comment


def _group(cli, bot, token):
    return {"cli": cli, "bot": bot, "token_env": token,
            "orchestrator": {"model": "claude-opus-5", "effort": "high"},
            "pm": {"model": "claude-sonnet-5", "effort": "medium"}}


CFG2 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "groups": {"claude": _group("claude", "meld-claude-bot", "T1"), "codex": _group("codex", "meld-codex-bot", "T2")},
})

CFG3 = Config.from_dict({
    "repo": {"name": "acme/widgets"},
    "humans": {"accounts": ["empowerScott"]},
    "groups": {"claude": _group("claude", "meld-claude-bot", "T1"), "codex": _group("codex", "meld-codex-bot", "T2"),
               "gemini": _group("gemini", "meld-gemini-bot", "T3")},
})

STATUS_TO_LABEL = {"planned": "meld:planned", "ready": "meld:ready", "in_progress": "meld:in-progress",
                   "in_review": "meld:in-review", "blocked": "meld:blocked", "needs_human": "meld:needs-human"}


def mk_issue(number: int, status: str = "ready", worker: str | None = "backend", model: str | None = "claude-sonnet-5",
             effort: str | None = "medium", size: str | None = "S", assignees: tuple[str, ...] = (),
             extra_labels: tuple[str, ...] = (), state: str = "open", title: str | None = None,
             updated_at: str = "2026-09-03T10:00:00Z") -> Issue:
    labels = set(extra_labels)
    if status in STATUS_TO_LABEL:
        labels.add(STATUS_TO_LABEL[status])
    for prefix, val in (("worker:", worker), ("model:", model), ("effort:", effort), ("size:", size)):
        if val is not None:
            labels.add(prefix + val)
    return Issue(number=number, title=title or f"Issue {number}", body="", labels=frozenset(labels),
                 assignees=tuple(assignees), state=state, milestone=3, created_at="2026-09-03T09:00:00Z",
                 updated_at=updated_at)


def mk_pr(number: int, issue: int, head_sha: str = "abc123", committer: str = "meld-claude-bot",
          author: str | None = None, approvals: tuple[str, ...] = (), stale_approvals: tuple[str, ...] = (),
          changes_requested: tuple[str, ...] = (), extra_changes_requested_history: int = 0,
          checks: str = "green", state: str = "open", merged: bool = False,
          extra_labels: tuple[str, ...] = ()) -> PullRequest:
    reviews: list[Review] = []
    t = 0
    for a in stale_approvals:
        t += 1
        reviews.append(Review(a, "APPROVED", "old000", f"2026-09-03T10:{t:02d}:00Z"))
    for _ in range(extra_changes_requested_history):
        t += 1
        reviews.append(Review(committer + "-other", "CHANGES_REQUESTED", "old000", f"2026-09-03T10:{t:02d}:00Z"))
    for a in changes_requested:
        t += 1
        reviews.append(Review(a, "CHANGES_REQUESTED", head_sha, f"2026-09-03T10:{t:02d}:00Z"))
    for a in approvals:
        t += 1
        reviews.append(Review(a, "APPROVED", head_sha, f"2026-09-03T10:{t:02d}:00Z"))
    return PullRequest(number=number, title=f"PR {number}", body=f"Closes #{issue}\n\nverified-by: pytest",
                       head_ref=f"meld/{issue}-slug", head_sha=head_sha, author=author or committer,
                       head_committer=committer, issue_number=issue, reviews=tuple(reviews), checks=checks,
                       labels=frozenset(extra_labels), state=state, merged=merged)


def mk_comment(cfg: Config, id: int, author: str, body: str, created_at: str | None = None) -> ParsedComment:
    return parse_comment({"id": id, "user": {"login": author}, "body": body,
                          "created_at": created_at or f"2026-09-03T10:{id % 60:02d}:00Z"}, cfg)


def mk_run(group: str, issue: int, alive: bool = True, collected: bool = False, kind: str = "build",
           tier: str = "medium", pr: int | None = None) -> RunRecord:
    return RunRecord(group=group, issue=issue, kind=kind, pr=pr, pid=4242, started_at="2026-09-03T10:00:00Z",
                     worktree=f"/tmp/wt/{group}/{issue}", log_path=f"/tmp/wt/{group}/{issue}.log",
                     model="claude-sonnet-5", effort="medium", tier=tier, milestone=3, alive=alive, collected=collected)


def mk_board(cfg: Config, issues=(), pulls=(), comments=(), phase: str = "executing", halted: bool = False,
             runs: dict[str, dict[int, RunRecord]] | None = None, spend: dict[str, dict[str, int]] | None = None,
             open_challenges: frozenset[str] = frozenset()) -> Board:
    return Board(repo=cfg.repo_name, milestone=3, coordination_issue=10, phase=phase, halted=halted,
                 issues={i.number: i for i in issues}, pulls={p.number: p for p in pulls}, comments=tuple(comments),
                 runs=runs or {g: {} for g in cfg.group_names}, spend=spend or {g: {} for g in cfg.group_names},
                 open_challenges=open_challenges, ignored_comments=0)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_board.py`:

```python
from meld.board import Issue, derive_halted, derive_open_challenges, derive_phase
from tests.factories import CFG2, mk_board, mk_comment, mk_issue, mk_pr, mk_run


def test_issue_status_precedence_and_labels():
    i = mk_issue(1, status="ready")
    assert i.status == "ready"
    assert i.worker == "backend" and i.model == "claude-sonnet-5" and i.effort == "medium" and i.size == "S"
    both = Issue(2, "t", "", frozenset({"meld:blocked", "meld:needs-human", "meld:in-progress"}), (), "open", 3)
    assert both.status == "needs_human"
    assert Issue(3, "t", "", frozenset(), (), "open", 3).status == "unlabeled"
    assert Issue(4, "t", "", frozenset({"meld:ready"}), (), "closed", 3).status == "closed"
    assert mk_issue(5, extra_labels=("meld:challenged",)).challenged
    assert mk_issue(6, worker=None).worker is None


def test_pr_review_math_uses_latest_review_per_author_on_head():
    pr = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), stale_approvals=("empowerScott",))
    assert pr.approvals_on_head() == {"meld-claude-bot"}
    assert pr.changes_requested_on_head() == frozenset()
    pr2 = mk_pr(21, 14, changes_requested=("meld-claude-bot",), extra_changes_requested_history=2)
    assert pr2.changes_requested_on_head() == {"meld-claude-bot"}
    assert pr2.changes_requested_total() == 3
    assert pr2.latest_reviews()["meld-claude-bot"].state == "CHANGES_REQUESTED"


def test_derive_phase_and_halted():
    assert derive_phase(frozenset({"meld:coordination"})) == "planning"
    assert derive_phase(frozenset({"meld:coordination", "meld:plan-approved"})) == "executing"
    cs = [mk_comment(CFG2, 1, "empowerScott", "[human] HALT\n\nstop"),
          mk_comment(CFG2, 2, "randouser", "[human] RESUME\n\ngo")]
    assert derive_halted(cs) is True  # imposter RESUME ignored
    cs.append(mk_comment(CFG2, 3, "empowerScott", "[human] RESUME\n\ngo"))
    assert derive_halted(cs) is False


def test_derive_open_challenges_closes_on_verdict_or_retract():
    cs = [mk_comment(CFG2, 1, "meld-codex-bot", "[codex] CHALLENGE #12\n\ndoubt"),
          mk_comment(CFG2, 2, "meld-codex-bot", "[codex] CHALLENGE #20\n\ndoubt"),
          mk_comment(CFG2, 3, "meld-claude-bot", "[claude] RETRACT #20\n\nfair"),
          mk_comment(CFG2, 4, "meld-codex-bot", "[codex] CHALLENGE plan-1\n\ndoubt"),
          mk_comment(CFG2, 5, "empowerScott", "[human] DISMISSED plan-1\n\nfine")]
    assert derive_open_challenges(cs) == {"#12"}


def test_board_helpers():
    b = mk_board(CFG2,
                 issues=[mk_issue(11), mk_issue(12, status="in_progress", assignees=("meld-claude-bot",)),
                         mk_issue(13, status="in_review", assignees=("meld-claude-bot",))],
                 pulls=[mk_pr(20, 13)],
                 runs={"claude": {12: mk_run("claude", 12)}, "codex": {}},
                 open_challenges=frozenset({"#12"}))
    assert b.in_progress_count("meld-claude-bot") == 1
    assert b.pr_for_issue(13).number == 20 and b.pr_for_issue(11) is None
    assert b.run_for("claude", 12).alive and b.run_for("codex", 12) is None
    assert b.is_challenged("#12") and not b.is_challenged("#11")
    assert not mk_run("claude", 1, alive=True).finished
    assert mk_run("claude", 1, alive=False).finished
    assert not mk_run("claude", 1, alive=False, collected=True).finished
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_board.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.board'`

- [ ] **Step 4: Write `src/meld/board.py`**

```python
"""Typed board model. Pure data and derivations; no GitHub access here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from meld.grammar import ParsedComment

STATUS_LABELS = {
    "meld:planned": "planned",
    "meld:ready": "ready",
    "meld:in-progress": "in_progress",
    "meld:in-review": "in_review",
    "meld:blocked": "blocked",
    "meld:needs-human": "needs_human",
}
_STATUS_PRECEDENCE = ("needs_human", "blocked", "in_review", "in_progress", "ready", "planned")
CHALLENGED_LABEL = "meld:challenged"
COORDINATION_LABEL = "meld:coordination"
PLAN_APPROVED_LABEL = "meld:plan-approved"


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    labels: frozenset[str]
    assignees: tuple[str, ...]
    state: str
    milestone: int | None
    created_at: str = ""
    updated_at: str = ""

    @property
    def status(self) -> str:
        if self.state == "closed":
            return "closed"
        present = {STATUS_LABELS[l] for l in self.labels if l in STATUS_LABELS}
        for s in _STATUS_PRECEDENCE:
            if s in present:
                return s
        return "unlabeled"

    def label_value(self, prefix: str) -> str | None:
        for l in sorted(self.labels):
            if l.startswith(prefix):
                return l[len(prefix):]
        return None

    @property
    def worker(self) -> str | None:
        return self.label_value("worker:")

    @property
    def model(self) -> str | None:
        return self.label_value("model:")

    @property
    def effort(self) -> str | None:
        return self.label_value("effort:")

    @property
    def size(self) -> str | None:
        return self.label_value("size:")

    @property
    def challenged(self) -> bool:
        return CHALLENGED_LABEL in self.labels


@dataclass(frozen=True)
class Review:
    author: str
    state: str
    commit_id: str
    submitted_at: str = ""


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    head_ref: str
    head_sha: str
    author: str
    head_committer: str
    issue_number: int | None
    reviews: tuple[Review, ...]
    checks: str
    labels: frozenset[str]
    state: str
    merged: bool

    def latest_reviews(self) -> dict[str, Review]:
        latest: dict[str, Review] = {}
        for r in sorted(self.reviews, key=lambda r: r.submitted_at):
            if r.state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latest[r.author] = r
        return latest

    def approvals_on_head(self) -> frozenset[str]:
        return frozenset(a for a, r in self.latest_reviews().items() if r.state == "APPROVED" and r.commit_id == self.head_sha)

    def changes_requested_on_head(self) -> frozenset[str]:
        return frozenset(a for a, r in self.latest_reviews().items() if r.state == "CHANGES_REQUESTED" and r.commit_id == self.head_sha)

    def changes_requested_total(self) -> int:
        return sum(1 for r in self.reviews if r.state == "CHANGES_REQUESTED")

    @property
    def challenged(self) -> bool:
        return CHALLENGED_LABEL in self.labels


@dataclass(frozen=True)
class RunRecord:
    group: str
    issue: int
    kind: str
    pr: int | None
    pid: int
    started_at: str
    worktree: str
    log_path: str
    model: str
    effort: str
    tier: str
    milestone: int
    alive: bool
    collected: bool

    @property
    def finished(self) -> bool:
        return not self.alive and not self.collected


@dataclass(frozen=True)
class Board:
    repo: str
    milestone: int
    coordination_issue: int
    phase: str
    halted: bool
    issues: dict[int, Issue]
    pulls: dict[int, PullRequest]
    comments: tuple[ParsedComment, ...]
    runs: dict[str, dict[int, RunRecord]]
    spend: dict[str, dict[str, int]]
    open_challenges: frozenset[str]
    ignored_comments: int

    def in_progress_count(self, bot: str) -> int:
        return sum(1 for i in self.issues.values() if i.status == "in_progress" and bot in i.assignees)

    def pr_for_issue(self, issue: int) -> PullRequest | None:
        candidates = [p for p in self.pulls.values() if p.issue_number == issue]
        candidates.sort(key=lambda p: (p.state != "open", -p.number))
        return candidates[0] if candidates else None

    def run_for(self, group: str, issue: int) -> RunRecord | None:
        return self.runs.get(group, {}).get(issue)

    def is_challenged(self, ref: str) -> bool:
        if ref in self.open_challenges:
            return True
        if ref.startswith("#") and ref[1:].isdigit():
            n = int(ref[1:])
            if n in self.issues and self.issues[n].challenged:
                return True
            if n in self.pulls and self.pulls[n].challenged:
                return True
        return False


def derive_phase(coordination_labels: frozenset[str]) -> str:
    return "executing" if PLAN_APPROVED_LABEL in coordination_labels else "planning"


def _ordered(comments: Iterable[ParsedComment]) -> list[ParsedComment]:
    return sorted(comments, key=lambda c: (c.created_at, c.id))


def derive_halted(comments: Sequence[ParsedComment]) -> bool:
    halted = False
    for c in _ordered(comments):
        if not c.ok or c.header is None or c.header.party.kind != "human":
            continue
        if c.header.verb == "HALT":
            halted = True
        elif c.header.verb == "RESUME":
            halted = False
    return halted


def derive_open_challenges(comments: Sequence[ParsedComment]) -> frozenset[str]:
    open_refs: set[str] = set()
    for c in _ordered(comments):
        if not c.ok or c.header is None or not c.header.ref:
            continue
        if c.header.verb == "CHALLENGE":
            open_refs.add(c.header.ref)
        elif c.header.verb in ("UPHELD", "DISMISSED", "RETRACT"):
            open_refs.discard(c.header.ref)
    return frozenset(open_refs)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_board.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add src/meld/board.py tests/factories.py tests/test_board.py
git commit -m "feat: typed board model with status, review math, halt and challenge derivations"
```

---

### Task 8: State store — MELD_HOME, run records, spend, snapshot digest

**Files:**
- Create: `src/meld/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: `Board`, `RunRecord` from `meld.board`.
- Produces (in `meld.state`):
  - `meld_home(env: Mapping[str, str]) -> Path` (`MELD_HOME`, default `~/.meld`)
  - `pid_alive(pid: int) -> bool`
  - `class StateStore(home: Path, group: str | None)` with attributes `cache_dir`, `state_dir`, `runs_root`, and methods `load_snapshot() -> dict | None`, `save_snapshot(d: dict) -> None`, `load_runs(alive=pid_alive) -> dict[str, dict[int, RunRecord]]` (every group's records under `runs/<group>/*.json`), `load_spend(milestone: int) -> dict[str, dict[str, int]]` (counts records in `runs/<group>/*.json` and `runs/<group>/archive/*.json` with that milestone, by tier), `write_run(record: RunRecord) -> Path` (used by Plan 2's dispatch; written here so the on-disk shape has one owner)
  - `board_digest(board: Board) -> dict`, `diff_digest(old: dict | None, new: dict) -> list[str]`
- Run record file shape (JSON): `{"group","issue","kind","pr","pid","started_at","worktree","log_path","model","effort","tier","milestone","collected"}`; `alive` is computed at load.

- [ ] **Step 1: Write the failing tests**

`tests/test_state.py`:

```python
import json

from meld.state import StateStore, board_digest, diff_digest, meld_home
from tests.factories import CFG2, mk_board, mk_issue, mk_pr, mk_run


def test_meld_home_default_and_override(tmp_path):
    assert meld_home({}).name == ".meld"
    assert meld_home({"MELD_HOME": str(tmp_path)}) == tmp_path


def test_write_and_load_runs_with_alive_probe(tmp_path):
    store = StateStore(tmp_path, "claude")
    rec = mk_run("claude", 12)
    path = store.write_run(rec)
    assert path == tmp_path / "runs" / "claude" / "12.json"
    data = json.loads(path.read_text())
    assert data["pid"] == 4242 and "alive" not in data
    runs = store.load_runs(alive=lambda pid: False)
    assert runs["claude"][12].alive is False and runs["claude"][12].finished
    runs = store.load_runs(alive=lambda pid: True)
    assert runs["claude"][12].alive is True


def test_load_spend_counts_active_and_archived_by_tier(tmp_path):
    store = StateStore(tmp_path, "claude")
    store.write_run(mk_run("claude", 1, tier="small"))
    store.write_run(mk_run("claude", 2, tier="medium"))
    archive = tmp_path / "runs" / "claude" / "archive"
    archive.mkdir(parents=True)
    (archive / "3.json").write_text(json.dumps({**json.loads((tmp_path / "runs/claude/1.json").read_text()), "issue": 3}))
    (archive / "4.json").write_text(json.dumps({**json.loads((tmp_path / "runs/claude/1.json").read_text()), "issue": 4, "milestone": 99}))
    assert store.load_spend(3) == {"claude": {"small": 2, "medium": 1}}


def test_snapshot_round_trip(tmp_path):
    store = StateStore(tmp_path, "claude")
    assert store.load_snapshot() is None
    store.save_snapshot({"a": 1})
    assert store.load_snapshot() == {"a": 1}


def test_digest_and_diff_name_what_changed():
    b1 = mk_board(CFG2, issues=[mk_issue(11)], pulls=[mk_pr(20, 13)], runs={"claude": {12: mk_run("claude", 12)}, "codex": {}})
    b2 = mk_board(CFG2, issues=[mk_issue(11, status="in_progress", assignees=("meld-claude-bot",))],
                  pulls=[mk_pr(20, 13, approvals=("meld-codex-bot",))],
                  runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}}, halted=True)
    d1, d2 = board_digest(b1), board_digest(b2)
    assert diff_digest(d1, d1) == []
    changes = diff_digest(d1, d2)
    assert "issue #11: ready -> in_progress" in changes
    assert "pr #20: approvals [] -> ['meld-codex-bot']" in changes
    assert "run claude/#12: running -> finished" in changes
    assert "halted: False -> True" in changes
    assert diff_digest(None, d2) == ["initial snapshot"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.state'`

- [ ] **Step 3: Write `src/meld/state.py`**

```python
"""Disposable local state under $MELD_HOME: run records, snapshots, cursors, caches."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Mapping

from meld.board import Board, RunRecord


def meld_home(env: Mapping[str, str]) -> Path:
    return Path(env.get("MELD_HOME", "~/.meld")).expanduser()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class StateStore:
    def __init__(self, home: Path, group: str | None):
        self.home = home
        self.group = group
        who = group or "human"
        self.cache_dir = home / "cache" / who
        self.state_dir = home / "state" / who
        self.runs_root = home / "runs"

    # ---- snapshot ----------------------------------------------------------

    @property
    def snapshot_path(self) -> Path:
        return self.state_dir / "snapshot.json"

    def load_snapshot(self) -> dict | None:
        if not self.snapshot_path.exists():
            return None
        return json.loads(self.snapshot_path.read_text())

    def save_snapshot(self, digest: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(digest, sort_keys=True))

    # ---- run records -------------------------------------------------------

    def write_run(self, record: RunRecord) -> Path:
        d = self.runs_root / record.group
        d.mkdir(parents=True, exist_ok=True)
        data = asdict(record)
        data.pop("alive", None)
        path = d / f"{record.issue}.json"
        path.write_text(json.dumps(data, sort_keys=True))
        return path

    def _read_record(self, path: Path, alive: Callable[[int], bool]) -> RunRecord:
        data = json.loads(path.read_text())
        return RunRecord(
            group=data["group"], issue=int(data["issue"]), kind=data.get("kind", "build"), pr=data.get("pr"),
            pid=int(data["pid"]), started_at=data["started_at"], worktree=data["worktree"], log_path=data["log_path"],
            model=data["model"], effort=data["effort"], tier=data["tier"], milestone=int(data["milestone"]),
            alive=alive(int(data["pid"])), collected=bool(data.get("collected", False)),
        )

    def load_runs(self, alive: Callable[[int], bool] = pid_alive) -> dict[str, dict[int, RunRecord]]:
        out: dict[str, dict[int, RunRecord]] = {}
        if not self.runs_root.exists():
            return out
        for gdir in sorted(p for p in self.runs_root.iterdir() if p.is_dir()):
            out[gdir.name] = {}
            for f in sorted(gdir.glob("*.json")):
                rec = self._read_record(f, alive)
                out[gdir.name][rec.issue] = rec
        return out

    def load_spend(self, milestone: int) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        if not self.runs_root.exists():
            return out
        for gdir in sorted(p for p in self.runs_root.iterdir() if p.is_dir()):
            counts: dict[str, int] = {}
            files = list(gdir.glob("*.json")) + list((gdir / "archive").glob("*.json"))
            for f in files:
                data = json.loads(f.read_text())
                if int(data.get("milestone", -1)) != milestone:
                    continue
                counts[data["tier"]] = counts.get(data["tier"], 0) + 1
            out[gdir.name] = counts
        return out


# ---- digests ---------------------------------------------------------------

def board_digest(board: Board) -> dict:
    return {
        "phase": board.phase,
        "halted": board.halted,
        "issues": {str(n): {"status": i.status, "assignees": list(i.assignees), "labels": sorted(i.labels)}
                   for n, i in sorted(board.issues.items())},
        "pulls": {str(n): {"head": p.head_sha, "approvals": sorted(p.approvals_on_head()),
                           "changes_requested": sorted(p.changes_requested_on_head()), "checks": p.checks, "state": p.state}
                  for n, p in sorted(board.pulls.items())},
        "runs": {f"{g}/#{n}": ("running" if r.alive else "collected" if r.collected else "finished")
                 for g, recs in sorted(board.runs.items()) for n, r in sorted(recs.items())},
        "last_comment_id": max((c.id for c in board.comments), default=0),
        "open_challenges": sorted(board.open_challenges),
    }


def diff_digest(old: dict | None, new: dict) -> list[str]:
    if old is None:
        return ["initial snapshot"]
    changes: list[str] = []
    for key in ("phase", "halted"):
        if old.get(key) != new.get(key):
            changes.append(f"{key}: {old.get(key)} -> {new.get(key)}")
    for n in sorted(set(old.get("issues", {})) | set(new.get("issues", {})), key=int):
        a, b = old.get("issues", {}).get(n), new.get("issues", {}).get(n)
        if a is None:
            changes.append(f"issue #{n}: new ({b['status']})")
        elif b is None:
            changes.append(f"issue #{n}: gone")
        elif a["status"] != b["status"]:
            changes.append(f"issue #{n}: {a['status']} -> {b['status']}")
        elif a != b:
            changes.append(f"issue #{n}: labels or assignees changed")
    for n in sorted(set(old.get("pulls", {})) | set(new.get("pulls", {})), key=int):
        a, b = old.get("pulls", {}).get(n), new.get("pulls", {}).get(n)
        if a is None:
            changes.append(f"pr #{n}: new")
        elif b is None:
            changes.append(f"pr #{n}: gone")
        else:
            if a["head"] != b["head"]:
                changes.append(f"pr #{n}: new head {b['head']}")
            if a["approvals"] != b["approvals"]:
                changes.append(f"pr #{n}: approvals {a['approvals']} -> {b['approvals']}")
            if a["changes_requested"] != b["changes_requested"]:
                changes.append(f"pr #{n}: changes_requested {a['changes_requested']} -> {b['changes_requested']}")
            if a["checks"] != b["checks"]:
                changes.append(f"pr #{n}: checks {a['checks']} -> {b['checks']}")
            if a["state"] != b["state"]:
                changes.append(f"pr #{n}: {a['state']} -> {b['state']}")
    for k in sorted(set(old.get("runs", {})) | set(new.get("runs", {}))):
        a, b = old.get("runs", {}).get(k), new.get("runs", {}).get(k)
        if a != b:
            changes.append(f"run {k}: {a or 'none'} -> {b or 'none'}")
    if old.get("last_comment_id") != new.get("last_comment_id"):
        changes.append(f"comments: new up to #{new.get('last_comment_id')}")
    if old.get("open_challenges") != new.get("open_challenges"):
        changes.append(f"open_challenges: {old.get('open_challenges')} -> {new.get('open_challenges')}")
    return changes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/meld/state.py tests/test_state.py
git commit -m "feat: state store for run records, spend, and board snapshot digests"
```

---

### Task 9: Board assembly from GitHub

**Files:**
- Create: `src/meld/assemble.py`
- Create: `tests/fixtures/board1/milestones.json`
- Create: `tests/fixtures/board1/issues.json`
- Create: `tests/fixtures/board1/comments_10.json`
- Create: `tests/fixtures/board1/pulls.json`
- Create: `tests/fixtures/board1/reviews_20.json`
- Create: `tests/fixtures/board1/commits_20.json`
- Create: `tests/fixtures/board1/checkruns_abc123.json`
- Create: `tests/conftest.py`
- Create: `tests/test_assemble.py`

**Interfaces:**
- Consumes: `GitHub` (`meld.github`), `Config`, `parse_comment`, board dataclasses and `derive_*` helpers.
- Produces (in `meld.assemble`):
  - `class BoardError(RuntimeError)`
  - `find_active_milestone(gh: GitHub) -> tuple[int, int]` returning `(milestone_number, coordination_issue_number)`; raises `BoardError` when no open milestone has a `meld:coordination` issue
  - `build_board(gh, cfg, runs, spend, milestone: int | None = None) -> Board`
  - `checks_state(check_runs_json: dict) -> str` (`none|pending|green|red`)
  - `issue_number_from_pr(body: str, head_ref: str) -> int | None`
- `tests/conftest.py` produces fixture `board1_gh(tmp_path) -> tuple[FakeGh, GitHub]` with all board1 routes loaded.

- [ ] **Step 1: Write the fixture JSON files**

`tests/fixtures/board1/milestones.json`:

```json
[{"number": 3, "title": "M1: Skeleton", "state": "open", "description": "approved plan text"}]
```

`tests/fixtures/board1/issues.json`:

```json
[
  {"number": 10, "title": "Coordination: M1", "body": "Goal: build the skeleton", "state": "open",
   "labels": [{"name": "meld:coordination"}, {"name": "meld:plan-approved"}], "assignees": [],
   "milestone": {"number": 3}, "created_at": "2026-09-03T08:00:00Z", "updated_at": "2026-09-03T09:05:00Z"},
  {"number": 11, "title": "Add health endpoint", "body": "GET /healthz returns 200", "state": "open",
   "labels": [{"name": "meld:ready"}, {"name": "worker:backend"}, {"name": "model:claude-sonnet-5"}, {"name": "effort:medium"}, {"name": "size:S"}],
   "assignees": [], "milestone": {"number": 3}, "created_at": "2026-09-03T08:10:00Z", "updated_at": "2026-09-03T08:10:00Z"},
  {"number": 12, "title": "Login form", "body": "OAuth login form", "state": "open",
   "labels": [{"name": "meld:in-progress"}, {"name": "worker:frontend"}, {"name": "model:claude-sonnet-5"}, {"name": "effort:medium"}, {"name": "size:M"}],
   "assignees": [{"login": "meld-claude-bot"}], "milestone": {"number": 3}, "created_at": "2026-09-03T08:11:00Z", "updated_at": "2026-09-03T10:00:00Z"},
  {"number": 13, "title": "CI pipeline", "body": "GitHub Actions running pytest", "state": "open",
   "labels": [{"name": "meld:in-review"}, {"name": "worker:backend"}, {"name": "model:claude-sonnet-5"}, {"name": "effort:high"}, {"name": "size:M"}],
   "assignees": [{"login": "meld-codex-bot"}], "milestone": {"number": 3}, "created_at": "2026-09-03T08:12:00Z", "updated_at": "2026-09-03T10:30:00Z"},
  {"number": 14, "title": "Needs API key", "body": "Stripe sandbox key required", "state": "open",
   "labels": [{"name": "meld:needs-human"}, {"name": "worker:backend"}, {"name": "model:claude-sonnet-5"}, {"name": "effort:medium"}, {"name": "size:S"}],
   "assignees": [{"login": "meld-codex-bot"}], "milestone": {"number": 3}, "created_at": "2026-09-03T08:13:00Z", "updated_at": "2026-09-03T09:00:00Z"},
  {"number": 20, "title": "CI pipeline", "body": "Closes #13", "state": "open", "labels": [], "assignees": [],
   "milestone": {"number": 3}, "pull_request": {"url": "https://api.github.com/repos/acme/widgets/pulls/20"},
   "created_at": "2026-09-03T10:20:00Z", "updated_at": "2026-09-03T11:00:00Z"}
]
```

`tests/fixtures/board1/comments_10.json`:

```json
[
  {"id": 101, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z",
   "body": "[claude] ROSTER\n\nbackend, frontend, security, qa, reviewer, auditor, docs"},
  {"id": 102, "user": {"login": "empowerScott"}, "created_at": "2026-09-03T09:05:00Z",
   "body": "[human] APPROVE\n\nLooks good."},
  {"id": 103, "user": {"login": "randouser"}, "created_at": "2026-09-03T09:06:00Z",
   "body": "[human] HALT\n\nignore me"},
  {"id": 104, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:10:00Z",
   "body": "[codex] CHALLENGE #12\n\nThe login form claims OAuth works; no test shows it."}
]
```

`tests/fixtures/board1/pulls.json`:

```json
[
  {"number": 20, "title": "CI pipeline", "body": "Closes #13\n\nverified-by: act -j ci (passed)", "state": "open",
   "merged_at": null, "user": {"login": "meld-codex-bot"}, "head": {"ref": "meld/13-ci-pipeline", "sha": "abc123"}, "labels": []},
  {"number": 19, "title": "Old merged", "body": "Closes #9", "state": "closed",
   "merged_at": "2026-09-02T10:00:00Z", "user": {"login": "meld-claude-bot"}, "head": {"ref": "meld/9-old", "sha": "000999"}, "labels": []},
  {"number": 18, "title": "Not ours", "body": "", "state": "open", "merged_at": null,
   "user": {"login": "empowerScott"}, "head": {"ref": "feature/unrelated", "sha": "111222"}, "labels": []}
]
```

`tests/fixtures/board1/reviews_20.json`:

```json
[{"user": {"login": "meld-claude-bot"}, "state": "APPROVED", "commit_id": "abc123", "submitted_at": "2026-09-03T11:00:00Z"}]
```

`tests/fixtures/board1/commits_20.json`:

```json
[
  {"sha": "abc122", "author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}},
  {"sha": "abc123", "author": {"login": "meld-codex-bot"}, "committer": {"login": "meld-codex-bot"}}
]
```

`tests/fixtures/board1/checkruns_abc123.json`:

```json
{"total_count": 1, "check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
import json
from pathlib import Path

import pytest

from meld.github import GitHub
from tests.fakegh import FakeGh

FIXTURES = Path(__file__).parent / "fixtures"


def load_board1_routes(fake: FakeGh, repo: str = "acme/widgets") -> None:
    d = FIXTURES / "board1"
    r = lambda p: f"repos/{repo}/{p}"  # noqa: E731
    fake.route("GET", r("milestones"), json.loads((d / "milestones.json").read_text()))
    fake.route("GET", r("issues"), json.loads((d / "issues.json").read_text()))
    fake.route("GET", r("issues/10/comments"), json.loads((d / "comments_10.json").read_text()))
    fake.route("GET", r("pulls"), json.loads((d / "pulls.json").read_text()))
    fake.route("GET", r("pulls/20/reviews"), json.loads((d / "reviews_20.json").read_text()))
    fake.route("GET", r("pulls/20/commits"), json.loads((d / "commits_20.json").read_text()))
    fake.route("GET", r("commits/abc123/check-runs"), json.loads((d / "checkruns_abc123.json").read_text()))
    fake.route("GET", "user", {"login": "empowerScott"})


@pytest.fixture
def board1_gh(tmp_path):
    fake = FakeGh()
    load_board1_routes(fake)
    gh = GitHub("acme/widgets", token="tok", run=fake, cache_dir=tmp_path / "cache")
    return fake, gh
```

- [ ] **Step 3: Write the failing tests**

`tests/test_assemble.py`:

```python
import pytest

from meld.assemble import BoardError, build_board, checks_state, find_active_milestone, issue_number_from_pr
from tests.factories import CFG2, mk_run


def test_find_active_milestone(board1_gh):
    _, gh = board1_gh
    assert find_active_milestone(gh) == (3, 10)


def test_find_active_milestone_raises_without_coordination_issue(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/issues", [])
    with pytest.raises(BoardError, match="coordination"):
        find_active_milestone(gh)


def test_build_board_end_to_end(board1_gh):
    _, gh = board1_gh
    runs = {"claude": {12: mk_run("claude", 12)}, "codex": {}}
    spend = {"claude": {"medium": 1}, "codex": {}}
    b = build_board(gh, CFG2, runs, spend)
    assert (b.milestone, b.coordination_issue, b.phase, b.halted) == (3, 10, "executing", False)
    assert sorted(b.issues) == [11, 12, 13, 14]          # coordination issue and PR entry excluded
    assert b.issues[11].status == "ready" and b.issues[12].assignees == ("meld-claude-bot",)
    assert b.issues[14].status == "needs_human"
    assert sorted(b.pulls) == [19, 20]                    # non-meld branch excluded
    pr = b.pulls[20]
    assert pr.issue_number == 13 and pr.head_committer == "meld-codex-bot" and pr.checks == "green"
    assert pr.approvals_on_head() == {"meld-claude-bot"}
    assert b.pulls[19].merged and b.pulls[19].reviews == ()
    assert b.open_challenges == {"#12"} and b.is_challenged("#12")
    assert b.ignored_comments == 1
    assert b.run_for("claude", 12).alive
    assert b.spend == spend
    assert [c.header.verb for c in b.comments if c.ok] == ["ROSTER", "APPROVE", "CHALLENGE"]


def test_checks_state():
    assert checks_state({"total_count": 0, "check_runs": []}) == "none"
    assert checks_state({"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}) == "pending"
    assert checks_state({"total_count": 2, "check_runs": [{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "skipped"}]}) == "green"
    assert checks_state({"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}) == "red"


def test_issue_number_from_pr():
    assert issue_number_from_pr("Closes #13", "meld/13-x") == 13
    assert issue_number_from_pr("fixes   #7 and more", "whatever") == 7
    assert issue_number_from_pr("no keyword", "meld/42-slug") == 42
    assert issue_number_from_pr("nothing", "feature/x") is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.assemble'`

- [ ] **Step 5: Write `src/meld/assemble.py`**

```python
"""Assemble a Board from GitHub. The only reader of GitHub in the coordination core."""
from __future__ import annotations

import re

from meld.board import (
    COORDINATION_LABEL,
    Board,
    Issue,
    PullRequest,
    Review,
    RunRecord,
    derive_halted,
    derive_open_challenges,
    derive_phase,
)
from meld.config import Config
from meld.github import GitHub
from meld.grammar import parse_comment

BRANCH_PREFIX = "meld/"
_CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
_BRANCH_RE = re.compile(r"^meld/(\d+)-")


class BoardError(RuntimeError):
    pass


def _labels(raw: dict) -> frozenset[str]:
    return frozenset(l["name"] for l in raw.get("labels", []))


def find_active_milestone(gh: GitHub) -> tuple[int, int]:
    for m in sorted(gh.list_open_milestones(), key=lambda m: m["number"]):
        for issue in gh.list_issues(m["number"]):
            if COORDINATION_LABEL in _labels(issue):
                return m["number"], issue["number"]
    raise BoardError("no open milestone has an issue labelled meld:coordination; run `meld setup` first")


def checks_state(check_runs: dict) -> str:
    runs = check_runs.get("check_runs", [])
    if not runs:
        return "none"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    if all(r.get("conclusion") in ("success", "neutral", "skipped") for r in runs):
        return "green"
    return "red"


def issue_number_from_pr(body: str, head_ref: str) -> int | None:
    m = _CLOSES_RE.search(body or "")
    if m:
        return int(m.group(1))
    b = _BRANCH_RE.match(head_ref or "")
    return int(b.group(1)) if b else None


def _issue(raw: dict) -> Issue:
    return Issue(
        number=raw["number"], title=raw.get("title", ""), body=raw.get("body") or "", labels=_labels(raw),
        assignees=tuple(a["login"] for a in raw.get("assignees", [])), state=raw.get("state", "open"),
        milestone=(raw.get("milestone") or {}).get("number"), created_at=raw.get("created_at", ""),
        updated_at=raw.get("updated_at", ""),
    )


def _pull(gh: GitHub, raw: dict) -> PullRequest:
    number = raw["number"]
    head_sha = raw["head"]["sha"]
    author = (raw.get("user") or {}).get("login", "")
    merged = raw.get("merged_at") is not None
    state = raw.get("state", "open")
    reviews: tuple[Review, ...] = ()
    head_committer = author
    checks = "none"
    if state == "open":
        reviews = tuple(
            Review(author=(r.get("user") or {}).get("login", ""), state=r.get("state", ""), commit_id=r.get("commit_id", ""),
                   submitted_at=r.get("submitted_at", ""))
            for r in gh.pull_reviews(number)
        )
        commits = gh.pull_commits(number)
        if commits:
            last = commits[-1]
            head_committer = ((last.get("author") or {}).get("login") or (last.get("committer") or {}).get("login") or author)
        checks = checks_state(gh.check_runs(head_sha))
    return PullRequest(
        number=number, title=raw.get("title", ""), body=raw.get("body") or "", head_ref=raw["head"]["ref"], head_sha=head_sha,
        author=author, head_committer=head_committer, issue_number=issue_number_from_pr(raw.get("body") or "", raw["head"]["ref"]),
        reviews=reviews, checks=checks, labels=_labels(raw), state=state, merged=merged,
    )


def build_board(gh: GitHub, cfg: Config, runs: dict[str, dict[int, RunRecord]], spend: dict[str, dict[str, int]],
                milestone: int | None = None) -> Board:
    if milestone is None:
        milestone, coordination = find_active_milestone(gh)
        raw_issues = gh.list_issues(milestone)
    else:
        raw_issues = gh.list_issues(milestone)
        coord = [i for i in raw_issues if COORDINATION_LABEL in _labels(i)]
        if not coord:
            raise BoardError(f"milestone {milestone} has no issue labelled meld:coordination")
        coordination = coord[0]["number"]
    coord_raw = next(i for i in raw_issues if i["number"] == coordination)
    comments = tuple(parse_comment(c, cfg) for c in gh.list_comments(coordination))
    known = {g.bot for g in cfg.groups.values()} | set(cfg.humans)
    ignored = sum(1 for c in comments if c.author not in known)
    issues = {i["number"]: _issue(i) for i in raw_issues if i["number"] != coordination}
    pulls: dict[int, PullRequest] = {}
    for raw in gh.list_pulls(state="all"):
        if not raw["head"]["ref"].startswith(BRANCH_PREFIX):
            continue
        if raw.get("state") == "closed" and raw.get("merged_at") is None:
            continue
        pulls[raw["number"]] = _pull(gh, raw)
    return Board(
        repo=gh.repo, milestone=milestone, coordination_issue=coordination, phase=derive_phase(_labels(coord_raw)),
        halted=derive_halted(comments), issues=issues, pulls=pulls, comments=comments, runs=runs, spend=spend,
        open_challenges=derive_open_challenges(comments), ignored_comments=ignored,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add src/meld/assemble.py tests/conftest.py tests/fixtures tests/test_assemble.py
git commit -m "feat: assemble the board from GitHub milestones, issues, PRs, and comments"
```

---

### Task 10: Rules — issue verbs (claim, release, dispatch, collect, reopen)

**Files:**
- Create: `src/meld/rules.py`
- Modify: `tests/factories.py` (add `REG`, a test `ModelRegistry`)
- Create: `tests/test_rules_issues.py`

**Interfaces:**
- Consumes: `Board`, `Issue`, `PullRequest` (`meld.board`); `Config`, `Party` (`meld.config`); `ModelRegistry`, `SIZES`, `size_fits` (`meld.models`).
- Produces (in `meld.rules`):
  - `class Verb(str, Enum)` with `CLAIM="claim"`, `RELEASE="release"`, `DISPATCH="dispatch"`, `COLLECT="collect"`, `REOPEN="reopen"`, `APPROVE="approve"`, `REQUEST_CHANGES="request-changes"`, `PUSH_FIX="push-fix"`, `MERGE="merge"`
  - `ISSUE_VERBS`, `PR_VERBS` tuples
  - `@dataclass(frozen=True) Actor(party: Party, login: str, roster: frozenset[str] = frozenset())` with `.is_human`, `.is_pm`, `.group -> str | None`, `.name -> str` (group name, or `human:<login>`)
  - `@dataclass(frozen=True) Refusal(verb: Verb, target: str, rule: str)` with `__str__` → `"<verb> <target> refused: <rule>"`
  - `party_of_login(cfg, login) -> str | None` (group name, `human:<login>`, or None)
  - `issue_refusal(board, cfg, actor, verb, issue, models) -> Refusal | None`
  - `legal_issue_verbs(board, cfg, actor, issue, models) -> list[str]`
- `tests/factories.py` gains `REG = ModelRegistry({...})` with claude models `claude-sonnet-5` (large/medium tier), `claude-haiku-4-5-20251001` (medium/small tier) and a codex model `gpt-test` (large/medium tier), all with efforts `("low", "medium", "high")`.

- [ ] **Step 1: Add the test registry to `tests/factories.py`**

Append:

```python
from meld.models import ModelRegistry, ModelSpec  # add near the other imports at the top


def _spec(adapter: str, model_id: str, context_class: str, tier: str) -> ModelSpec:
    return ModelSpec(adapter, model_id, context_class, ("low", "medium", "high"), tier)


REG = ModelRegistry({
    "claude": {
        "claude-sonnet-5": _spec("claude", "claude-sonnet-5", "large", "medium"),
        "claude-haiku-4-5-20251001": _spec("claude", "claude-haiku-4-5-20251001", "medium", "small"),
    },
    "codex": {"gpt-test": _spec("codex", "gpt-test", "large", "medium")},
})
```

- [ ] **Step 2: Write the failing tests**

`tests/test_rules_issues.py`:

```python
from meld.config import Party
from meld.rules import Actor, Verb, issue_refusal, legal_issue_verbs, party_of_login
from tests.factories import CFG2, REG, mk_board, mk_comment, mk_issue, mk_pr, mk_run

ROSTER = frozenset({"backend", "frontend", "security", "qa", "reviewer", "auditor", "docs"})
CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot", ROSTER)
CLAUDE_ORCH = Actor(Party("group", "claude"), "meld-claude-bot", ROSTER)
CODEX_PM = Actor(Party("pm", "codex"), "meld-codex-bot", ROSTER)
HUMAN = Actor(Party("human"), "empowerScott")


def rule(board, actor, verb, issue):
    r = issue_refusal(board, CFG2, actor, verb, issue, REG)
    return None if r is None else r.rule


# ---- claim -------------------------------------------------------------------

def test_claim_legal_for_pm_on_ready_issue():
    b = mk_board(CFG2, issues=[mk_issue(11)])
    assert rule(b, CLAUDE_PM, Verb.CLAIM, b.issues[11]) is None
    assert rule(b, HUMAN, Verb.CLAIM, b.issues[11]) is None


def test_claim_refusals_in_priority_order():
    i = mk_issue(11)
    assert rule(mk_board(CFG2, issues=[i], halted=True), CLAUDE_PM, Verb.CLAIM, i) == "HALT is in effect"
    assert "planning" in rule(mk_board(CFG2, issues=[i], phase="planning"), CLAUDE_PM, Verb.CLAIM, i)
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_ORCH, Verb.CLAIM, i) == "only a PM or a human may claim"
    blocked = mk_issue(12, status="blocked")
    assert rule(mk_board(CFG2, issues=[blocked]), CLAUDE_PM, Verb.CLAIM, blocked) == "issue is blocked, not ready"
    taken = mk_issue(13, assignees=("meld-codex-bot",))
    assert rule(mk_board(CFG2, issues=[taken]), CLAUDE_PM, Verb.CLAIM, taken) == "already claimed by meld-codex-bot"
    no_worker = mk_issue(14, worker=None)
    assert rule(mk_board(CFG2, issues=[no_worker]), CLAUDE_PM, Verb.CLAIM, no_worker) == "issue has no worker: label"
    marketing = mk_issue(15, worker="marketing")
    assert "not in claude's roster" in rule(mk_board(CFG2, issues=[marketing]), CLAUDE_PM, Verb.CLAIM, marketing)


def test_claim_respects_concurrency_cap():
    busy = [mk_issue(n, status="in_progress", assignees=("meld-claude-bot",)) for n in range(100, 108)]
    fresh = mk_issue(11, model="gpt-test")  # known to codex, unknown to claude; the cap check comes before the model check
    b = mk_board(CFG2, issues=[*busy, fresh])
    assert rule(b, CLAUDE_PM, Verb.CLAIM, fresh) == "claude is at its concurrency cap (8/8)"
    assert rule(b, CODEX_PM, Verb.CLAIM, fresh) is None


def test_claim_requires_known_model_and_budget():
    no_model = mk_issue(11, model=None)
    assert rule(mk_board(CFG2, issues=[no_model]), CLAUDE_PM, Verb.CLAIM, no_model) == "issue has no model: label"
    odd = mk_issue(12, model="gpt-test")
    assert rule(mk_board(CFG2, issues=[odd]), CLAUDE_PM, Verb.CLAIM, odd) == "model 'gpt-test' is unknown for adapter 'claude'"
    assert rule(mk_board(CFG2, issues=[odd]), CODEX_PM, Verb.CLAIM, odd) is None
    i = mk_issue(13)
    spent = mk_board(CFG2, issues=[i], spend={"claude": {"medium": 80}, "codex": {}})
    assert rule(spent, CLAUDE_PM, Verb.CLAIM, i) == "claude has exhausted its medium-tier budget (80/80)"
    assert rule(spent, CODEX_PM, Verb.CLAIM, i) == "model 'claude-sonnet-5' is unknown for adapter 'codex'"


# ---- dispatch ----------------------------------------------------------------

def test_dispatch_legal_for_assignee_pm():
    i = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",), size="M")
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_PM, Verb.DISPATCH, i) is None


def test_dispatch_refusals():
    i = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))
    assert rule(mk_board(CFG2, issues=[i], halted=True), CLAUDE_PM, Verb.DISPATCH, i) == "HALT is in effect"
    assert rule(mk_board(CFG2, issues=[i]), CLAUDE_ORCH, Verb.DISPATCH, i) == "only the PM may dispatch"
    assert rule(mk_board(CFG2, issues=[i]), HUMAN, Verb.DISPATCH, i) == "only the PM may dispatch"
    assert rule(mk_board(CFG2, issues=[i]), CODEX_PM, Verb.DISPATCH, i) == "issue is not assigned to meld-codex-bot"
    ready = mk_issue(13)
    assert rule(mk_board(CFG2, issues=[ready]), CLAUDE_PM, Verb.DISPATCH, ready) == "issue is ready, not in-progress"
    running = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12)}, "codex": {}})
    assert "already exists" in rule(running, CLAUDE_PM, Verb.DISPATCH, i)
    uncollected = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    assert "already exists" in rule(uncollected, CLAUDE_PM, Verb.DISPATCH, i)
    collected = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False, collected=True)}, "codex": {}})
    assert rule(collected, CLAUDE_PM, Verb.DISPATCH, i) is None
    assert rule(mk_board(CFG2, issues=[i], open_challenges=frozenset({"#12"})), CLAUDE_PM, Verb.DISPATCH, i) == "issue has an open CHALLENGE"
    labelled = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",), extra_labels=("meld:challenged",))
    assert rule(mk_board(CFG2, issues=[labelled]), CLAUDE_PM, Verb.DISPATCH, labelled) == "issue has an open CHALLENGE"


def test_dispatch_validates_model_effort_size():
    missing = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",), effort=None, size=None)
    assert rule(mk_board(CFG2, issues=[missing]), CLAUDE_PM, Verb.DISPATCH, missing) == "issue is missing labels: effort, size"
    bad_effort = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",), effort="turbo")
    assert "effort 'turbo' not valid" in rule(mk_board(CFG2, issues=[bad_effort]), CLAUDE_PM, Verb.DISPATCH, bad_effort)
    bad_size = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",), size="XL")
    assert rule(mk_board(CFG2, issues=[bad_size]), CLAUDE_PM, Verb.DISPATCH, bad_size) == "size 'XL' is not one of S, M, L"
    too_big = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",), model="claude-haiku-4-5-20251001", size="L")
    assert rule(mk_board(CFG2, issues=[too_big]), CLAUDE_PM, Verb.DISPATCH, too_big) == \
        "size L does not fit model claude-haiku-4-5-20251001 (context class medium)"


# ---- collect -----------------------------------------------------------------

def test_collect_rules():
    i = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))
    none = mk_board(CFG2, issues=[i])
    assert rule(none, CLAUDE_PM, Verb.COLLECT, i) == "no run record for #12 in claude"
    alive = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12)}, "codex": {}})
    assert rule(alive, CLAUDE_PM, Verb.COLLECT, i) == "worker is still running"
    done = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    assert rule(done, CLAUDE_PM, Verb.COLLECT, i) is None
    assert rule(done, CLAUDE_ORCH, Verb.COLLECT, i) == "only the PM may collect"
    twice = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False, collected=True)}, "codex": {}})
    assert rule(twice, CLAUDE_PM, Verb.COLLECT, i) == "run already collected"


# ---- release -----------------------------------------------------------------

def test_release_rules():
    mine = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))
    b = mk_board(CFG2, issues=[mine])
    assert rule(b, CLAUDE_PM, Verb.RELEASE, mine) is None
    assert rule(b, CLAUDE_ORCH, Verb.RELEASE, mine) is None
    assert rule(b, CODEX_PM, Verb.RELEASE, mine) == "issue is not assigned to meld-codex-bot"
    assert rule(b, HUMAN, Verb.RELEASE, mine) is None
    ready = mk_issue(13)
    assert rule(mk_board(CFG2, issues=[ready]), HUMAN, Verb.RELEASE, ready) == \
        "issue is ready; release is only legal from in-progress or blocked"
    blocked = mk_issue(14, status="blocked", assignees=("meld-claude-bot",))
    assert rule(mk_board(CFG2, issues=[blocked]), CLAUDE_PM, Verb.RELEASE, blocked) is None


# ---- reopen ------------------------------------------------------------------

def test_reopen_requires_fail_audit_from_non_author_group():
    closed = mk_issue(9, state="closed")
    pr = mk_pr(19, 9, committer="meld-claude-bot", state="closed", merged=True)
    audit = mk_comment(CFG2, 7, "meld-codex-bot", "[codex] AUDIT #9 FAIL\n\nacceptance criterion 2 unmet\n\nverified-by: pytest -k healthz")
    b = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[audit])
    assert rule(b, CODEX_PM, Verb.REOPEN, closed) is None
    assert rule(b, CLAUDE_PM, Verb.REOPEN, closed) == "no AUDIT #9 FAIL posted by claude"
    self_audit = mk_comment(CFG2, 8, "meld-claude-bot", "[claude] AUDIT #9 FAIL\n\nnope")
    b2 = mk_board(CFG2, issues=[closed], pulls=[pr], comments=[audit, self_audit])
    assert rule(b2, CLAUDE_PM, Verb.REOPEN, closed) == "claude authored the PR that closed #9"
    assert rule(b, HUMAN, Verb.REOPEN, closed) is None
    open_issue = mk_issue(11)
    assert rule(mk_board(CFG2, issues=[open_issue]), HUMAN, Verb.REOPEN, open_issue) == "issue is not closed"


def test_legal_issue_verbs_and_party_of_login():
    i = mk_issue(12, status="in_progress", assignees=("meld-claude-bot",))
    b = mk_board(CFG2, issues=[i], runs={"claude": {12: mk_run("claude", 12, alive=False)}, "codex": {}})
    assert legal_issue_verbs(b, CFG2, CLAUDE_PM, i, REG) == ["release", "collect"]
    assert party_of_login(CFG2, "meld-codex-bot") == "codex"
    assert party_of_login(CFG2, "empowerScott") == "human:empowerScott"
    assert party_of_login(CFG2, "randouser") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_rules_issues.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.rules'`

- [ ] **Step 4: Write `src/meld/rules.py`**

```python
"""Legal-action computation. Every invariant lives here and has a test."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from meld.board import Board, Issue, PullRequest
from meld.config import Config, Party
from meld.models import SIZES, ModelRegistry, size_fits


class Verb(str, Enum):
    CLAIM = "claim"
    RELEASE = "release"
    DISPATCH = "dispatch"
    COLLECT = "collect"
    REOPEN = "reopen"
    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    PUSH_FIX = "push-fix"
    MERGE = "merge"


ISSUE_VERBS = (Verb.CLAIM, Verb.RELEASE, Verb.DISPATCH, Verb.COLLECT, Verb.REOPEN)
PR_VERBS = (Verb.APPROVE, Verb.REQUEST_CHANGES, Verb.PUSH_FIX, Verb.MERGE)


@dataclass(frozen=True)
class Actor:
    party: Party
    login: str
    roster: frozenset[str] = frozenset()

    @property
    def is_human(self) -> bool:
        return self.party.kind == "human"

    @property
    def is_pm(self) -> bool:
        return self.party.kind == "pm"

    @property
    def group(self) -> str | None:
        return self.party.group

    @property
    def name(self) -> str:
        return self.group or f"human:{self.login}"


@dataclass(frozen=True)
class Refusal:
    verb: Verb
    target: str
    rule: str

    def __str__(self) -> str:
        return f"{self.verb.value} {self.target} refused: {self.rule}"


def party_of_login(cfg: Config, login: str) -> str | None:
    g = cfg.group_for_bot(login)
    if g is not None:
        return g.name
    if cfg.is_human(login):
        return f"human:{login}"
    return None


# ---- issue verbs -------------------------------------------------------------

def _claim(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if board.phase != "executing":
        return "milestone is still in planning (no meld:plan-approved label)"
    if not (actor.is_pm or actor.is_human):
        return "only a PM or a human may claim"
    if issue.status != "ready":
        return f"issue is {issue.status}, not ready"
    if issue.assignees:
        return f"already claimed by {', '.join(issue.assignees)}"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.worker is None:
        return "issue has no worker: label"
    if issue.worker not in actor.roster:
        return f"worker type '{issue.worker}' is not in {group.name}'s roster"
    n = board.in_progress_count(group.bot)
    if n >= group.concurrency:
        return f"{group.name} is at its concurrency cap ({n}/{group.concurrency})"
    if issue.model is None:
        return "issue has no model: label"
    tier = models.tier_of(group.cli, issue.model)
    if tier is None:
        return f"model '{issue.model}' is unknown for adapter '{group.cli}'"
    used = board.spend.get(group.name, {}).get(tier, 0)
    cap = cfg.budget.get(tier, 0)
    if used >= cap:
        return f"{group.name} has exhausted its {tier}-tier budget ({used}/{cap})"
    return None


def _dispatch(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not actor.is_pm:
        return "only the PM may dispatch"
    group = cfg.groups[actor.group]  # type: ignore[index]
    if issue.status != "in_progress":
        return f"issue is {issue.status}, not in-progress"
    if group.bot not in issue.assignees:
        return f"issue is not assigned to {group.bot}"
    run = board.run_for(group.name, issue.number)
    if run is not None and (run.alive or not run.collected):
        return "a worker run already exists for this issue (running or uncollected)"
    if board.is_challenged(f"#{issue.number}"):
        return "issue has an open CHALLENGE"
    missing = [name for name, val in (("model", issue.model), ("effort", issue.effort), ("size", issue.size)) if val is None]
    if missing:
        return f"issue is missing labels: {', '.join(missing)}"
    errors = models.validate(group.cli, issue.model, issue.effort)  # type: ignore[arg-type]
    if errors:
        return errors[0]
    if issue.size not in SIZES:
        return f"size '{issue.size}' is not one of S, M, L"
    spec = models.get(group.cli, issue.model)  # type: ignore[arg-type]
    assert spec is not None
    if not size_fits(issue.size, spec.context_class):  # type: ignore[arg-type]
        return f"size {issue.size} does not fit model {issue.model} (context class {spec.context_class})"
    return None


def _collect(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if not actor.is_pm:
        return "only the PM may collect"
    run = board.run_for(actor.group or "", issue.number)
    if run is None:
        return f"no run record for #{issue.number} in {actor.group}"
    if run.alive:
        return "worker is still running"
    if run.collected:
        return "run already collected"
    return None


def _release(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if issue.status not in ("in_progress", "blocked"):
        return f"issue is {issue.status}; release is only legal from in-progress or blocked"
    if actor.is_human:
        return None
    group = cfg.groups[actor.group]  # type: ignore[index]
    if group.bot not in issue.assignees:
        return f"issue is not assigned to {group.bot}"
    return None


def _reopen(board: Board, cfg: Config, actor: Actor, issue: Issue) -> str | None:
    if issue.state != "closed":
        return "issue is not closed"
    if actor.is_human:
        return None
    ref = f"#{issue.number}"
    audits = [
        c for c in board.comments
        if c.ok and c.header is not None and c.header.verb == "AUDIT" and c.header.ref == ref
        and c.header.qualifier == "FAIL" and c.header.party.group == actor.group
    ]
    if not audits:
        return f"no AUDIT {ref} FAIL posted by {actor.group}"
    pr = board.pr_for_issue(issue.number)
    if pr is not None and party_of_login(cfg, pr.author) == actor.group:
        return f"{actor.group} authored the PR that closed {ref}"
    return None


def issue_refusal(board: Board, cfg: Config, actor: Actor, verb: Verb, issue: Issue, models: ModelRegistry) -> Refusal | None:
    if verb == Verb.CLAIM:
        rule = _claim(board, cfg, actor, issue, models)
    elif verb == Verb.DISPATCH:
        rule = _dispatch(board, cfg, actor, issue, models)
    elif verb == Verb.COLLECT:
        rule = _collect(board, cfg, actor, issue)
    elif verb == Verb.RELEASE:
        rule = _release(board, cfg, actor, issue)
    elif verb == Verb.REOPEN:
        rule = _reopen(board, cfg, actor, issue)
    else:
        rule = f"{verb.value} is not an issue verb"
    return Refusal(verb, f"#{issue.number}", rule) if rule else None


def legal_issue_verbs(board: Board, cfg: Config, actor: Actor, issue: Issue, models: ModelRegistry) -> list[str]:
    return [v.value for v in ISSUE_VERBS if issue_refusal(board, cfg, actor, v, issue, models) is None]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_rules_issues.py -v`
Expected: PASS (12 tests)

- [ ] **Step 6: Commit**

```bash
git add src/meld/rules.py tests/factories.py tests/test_rules_issues.py
git commit -m "feat: rules engine for claim, dispatch, collect, release, and reopen"
```

---

### Task 11: Rules — PR verbs (approve, request-changes, push-fix, merge)

**Files:**
- Modify: `src/meld/rules.py` (append)
- Create: `tests/test_rules_prs.py`

**Interfaces:**
- Produces (in `meld.rules`):
  - `committer_party(cfg, pr) -> str | None`
  - `approving_parties(cfg, pr) -> frozenset[str]` (parties with an APPROVED latest review on the head, excluding the committer's party and unknown logins)
  - `pingpong_exceeded(cfg, pr) -> bool`
  - `pr_refusal(board, cfg, actor, verb, pr) -> Refusal | None`
  - `legal_pr_verbs(board, cfg, actor, pr) -> list[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_rules_prs.py`:

```python
from meld.config import Party
from meld.rules import Actor, Verb, approving_parties, committer_party, legal_pr_verbs, pingpong_exceeded, pr_refusal
from tests.factories import CFG2, CFG3, mk_board, mk_pr

CLAUDE_PM = Actor(Party("pm", "claude"), "meld-claude-bot")
CLAUDE_ORCH = Actor(Party("group", "claude"), "meld-claude-bot")
CODEX_PM = Actor(Party("pm", "codex"), "meld-codex-bot")
GEMINI_PM = Actor(Party("pm", "gemini"), "meld-gemini-bot")
HUMAN = Actor(Party("human"), "empowerScott")


def rule(board, cfg, actor, verb, pr):
    r = pr_refusal(board, cfg, actor, verb, pr)
    return None if r is None else r.rule


def test_committer_and_approving_parties():
    pr = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot", "meld-codex-bot", "empowerScott", "randouser"))
    assert committer_party(CFG2, pr) == "codex"
    assert approving_parties(CFG2, pr) == {"claude", "human:empowerScott"}


def test_approve_rules():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    assert rule(b, CFG2, CLAUDE_PM, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, CLAUDE_ORCH, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, HUMAN, Verb.APPROVE, pr) is None
    assert rule(b, CFG2, CODEX_PM, Verb.APPROVE, pr) == "codex made the last commit on this PR and cannot approve its own head"
    fixed = mk_pr(21, 14, committer="meld-claude-bot", author="meld-codex-bot")  # claude pushed a fix onto codex's PR
    b2 = mk_board(CFG2, pulls=[fixed])
    assert rule(b2, CFG2, CODEX_PM, Verb.APPROVE, fixed) is None
    assert rule(b2, CFG2, CLAUDE_PM, Verb.APPROVE, fixed) == "claude made the last commit on this PR and cannot approve its own head"
    challenged = mk_board(CFG2, pulls=[pr], open_challenges=frozenset({"#20"}))
    assert rule(challenged, CFG2, CLAUDE_PM, Verb.APPROVE, pr) == "PR has an open CHALLENGE"
    closed = mk_pr(22, 15, state="closed", merged=True)
    assert rule(mk_board(CFG2, pulls=[closed]), CFG2, CLAUDE_PM, Verb.APPROVE, closed) == "PR is not open"


def test_request_changes_and_push_fix_need_a_different_committer():
    pr = mk_pr(20, 13, committer="meld-codex-bot")
    b = mk_board(CFG2, pulls=[pr])
    for verb in (Verb.REQUEST_CHANGES, Verb.PUSH_FIX):
        assert rule(b, CFG2, CLAUDE_PM, verb, pr) is None
        assert rule(b, CFG2, HUMAN, verb, pr) is None
        assert rule(b, CFG2, CODEX_PM, verb, pr) == f"codex made the last commit on this PR; only other parties may {verb.value}"


def test_merge_quorum_two_groups():
    unreviewed = mk_pr(20, 13, committer="meld-codex-bot")
    assert rule(mk_board(CFG2, pulls=[unreviewed]), CFG2, CLAUDE_PM, Verb.MERGE, unreviewed) == \
        "0/1 approvals from non-committer parties (have: [])"
    self_approved = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-codex-bot",))
    assert "0/1 approvals" in rule(mk_board(CFG2, pulls=[self_approved]), CFG2, CLAUDE_PM, Verb.MERGE, self_approved)
    approved = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",))
    b = mk_board(CFG2, pulls=[approved])
    assert rule(b, CFG2, CLAUDE_PM, Verb.MERGE, approved) is None
    assert rule(b, CFG2, CODEX_PM, Verb.MERGE, approved) is None  # either PM may merge
    assert rule(b, CFG2, HUMAN, Verb.MERGE, approved) is None
    assert rule(b, CFG2, CLAUDE_ORCH, Verb.MERGE, approved) == "only a PM or a human may merge"
    human_approved = mk_pr(21, 14, committer="meld-codex-bot", approvals=("empowerScott",))
    assert rule(mk_board(CFG2, pulls=[human_approved]), CFG2, CODEX_PM, Verb.MERGE, human_approved) is None


def test_merge_quorum_three_groups():
    one = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",))
    assert rule(mk_board(CFG3, pulls=[one]), CFG3, CLAUDE_PM, Verb.MERGE, one) == \
        "1/2 approvals from non-committer parties (have: ['claude'])"
    two = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot", "meld-gemini-bot"))
    assert rule(mk_board(CFG3, pulls=[two]), CFG3, GEMINI_PM, Verb.MERGE, two) is None
    human_plus_one = mk_pr(21, 14, committer="meld-codex-bot", approvals=("meld-claude-bot", "empowerScott"))
    assert rule(mk_board(CFG3, pulls=[human_plus_one]), CFG3, CLAUDE_PM, Verb.MERGE, human_plus_one) is None


def test_merge_blockers_and_stale_approvals():
    approved = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",))
    assert rule(mk_board(CFG2, pulls=[approved], halted=True), CFG2, CLAUDE_PM, Verb.MERGE, approved) == "HALT is in effect"
    assert rule(mk_board(CFG2, pulls=[approved], open_challenges=frozenset({"#20"})), CFG2, CLAUDE_PM, Verb.MERGE, approved) == "PR has an open CHALLENGE"
    red = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), checks="red")
    assert rule(mk_board(CFG2, pulls=[red]), CFG2, CLAUDE_PM, Verb.MERGE, red) == "required checks are red, not green"
    pending = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), checks="pending")
    assert rule(mk_board(CFG2, pulls=[pending]), CFG2, CLAUDE_PM, Verb.MERGE, pending) == "required checks are pending, not green"
    stale = mk_pr(20, 13, committer="meld-codex-bot", stale_approvals=("meld-claude-bot",))
    assert "0/1 approvals" in rule(mk_board(CFG2, pulls=[stale]), CFG2, CLAUDE_PM, Verb.MERGE, stale)


def test_pingpong_and_legal_pr_verbs():
    pr = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), extra_changes_requested_history=3)
    assert pingpong_exceeded(CFG2, pr)
    assert not pingpong_exceeded(CFG2, mk_pr(21, 14, extra_changes_requested_history=2))
    b = mk_board(CFG2, pulls=[pr])
    assert legal_pr_verbs(b, CFG2, CLAUDE_PM, pr) == ["approve", "request-changes", "push-fix", "merge"]
    assert legal_pr_verbs(b, CFG2, CODEX_PM, pr) == ["merge"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rules_prs.py -v`
Expected: FAIL with `ImportError: cannot import name 'approving_parties'`

- [ ] **Step 3: Append to `src/meld/rules.py`**

```python
# ---- PR verbs ----------------------------------------------------------------

def committer_party(cfg: Config, pr: PullRequest) -> str | None:
    return party_of_login(cfg, pr.head_committer)


def approving_parties(cfg: Config, pr: PullRequest) -> frozenset[str]:
    committer = committer_party(cfg, pr)
    parties = (party_of_login(cfg, login) for login in pr.approvals_on_head())
    return frozenset(p for p in parties if p is not None and p != committer)


def pingpong_exceeded(cfg: Config, pr: PullRequest) -> bool:
    return pr.changes_requested_total() >= cfg.pingpong_cap


def _approve(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if actor.name == committer_party(cfg, pr):
        return f"{actor.name} made the last commit on this PR and cannot approve its own head"
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    return None


def _other_party_only(cfg: Config, actor: Actor, pr: PullRequest, verb: Verb) -> str | None:
    if actor.name == committer_party(cfg, pr):
        return f"{actor.name} made the last commit on this PR; only other parties may {verb.value}"
    return None


def _merge(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> str | None:
    if board.halted:
        return "HALT is in effect"
    if not (actor.is_pm or actor.is_human):
        return "only a PM or a human may merge"
    if board.is_challenged(f"#{pr.number}"):
        return "PR has an open CHALLENGE"
    if pr.checks != "green":
        return f"required checks are {pr.checks}, not green"
    have = approving_parties(cfg, pr)
    need = cfg.effective_quorum
    if len(have) < need:
        return f"{len(have)}/{need} approvals from non-committer parties (have: {sorted(have)})"
    return None


def pr_refusal(board: Board, cfg: Config, actor: Actor, verb: Verb, pr: PullRequest) -> Refusal | None:
    if pr.state != "open":
        rule: str | None = "PR is not open"
    elif verb == Verb.APPROVE:
        rule = _approve(board, cfg, actor, pr)
    elif verb in (Verb.REQUEST_CHANGES, Verb.PUSH_FIX):
        rule = _other_party_only(cfg, actor, pr, verb)
    elif verb == Verb.MERGE:
        rule = _merge(board, cfg, actor, pr)
    else:
        rule = f"{verb.value} is not a PR verb"
    return Refusal(verb, f"#{pr.number}", rule) if rule else None


def legal_pr_verbs(board: Board, cfg: Config, actor: Actor, pr: PullRequest) -> list[str]:
    return [v.value for v in PR_VERBS if pr_refusal(board, cfg, actor, v, pr) is None]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rules_prs.py tests/test_rules_issues.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add src/meld/rules.py tests/test_rules_prs.py
git commit -m "feat: PR rules with review quorum, last-committer exclusion, and merge blockers"
```

---

### Task 12: Roster — built-in and product worker types

**Files:**
- Create: `src/meld/prompts/workers/backend.md`, `frontend.md`, `security.md`, `qa.md`, `reviewer.md`, `auditor.md`, `docs.md`
- Create: `src/meld/roster.py`
- Create: `tests/test_roster.py`

**Interfaces:**
- Produces (in `meld.roster`):
  - `@dataclass(frozen=True) WorkerType(name: str, description: str, source: str, path: Path | None)` where `source in {"builtin", "product", "skill"}`
  - `parse_worker_file(path: Path, source: str) -> WorkerType`
  - `builtin_workers() -> list[WorkerType]`
  - `product_workers(repo_root: Path) -> list[WorkerType]` (reads `<repo_root>/.meld/workers/*.md`)
  - `roster(repo_root: Path | None, extra: Iterable[WorkerType] = ()) -> list[WorkerType]` (builtins overridden by product entries of the same name, then `extra`, sorted by name)
  - `roster_names(repo_root: Path | None, extra=()) -> frozenset[str]`
- Worker prompt file format: line 1 is `# <name>`; the first non-empty line after it is the one-line description. Plan 2 appends the full role brief and the fixed worker contract beneath.

- [ ] **Step 1: Write the seven built-in worker files**

`src/meld/prompts/workers/backend.md`:

```markdown
# backend

Implements server-side code, APIs, data models, and migrations, with tests for everything it builds.
```

`src/meld/prompts/workers/frontend.md`:

```markdown
# frontend

Builds user-facing components, pages, and styling; verifies rendering and accessibility with tests.
```

`src/meld/prompts/workers/security.md`:

```markdown
# security

Reviews or implements authentication, authorization, secrets handling, and input validation; reports findings with evidence.
```

`src/meld/prompts/workers/qa.md`:

```markdown
# qa

Writes and runs end-to-end and regression tests against acceptance criteria; files precise failure reports.
```

`src/meld/prompts/workers/reviewer.md`:

```markdown
# reviewer

Reviews another group's pull request for correctness, security, tests, and conventions; approves, requests changes, or pushes a fix.
```

`src/meld/prompts/workers/auditor.md`:

```markdown
# auditor

Independently re-checks merged work against its acceptance criteria and posted evidence; reports pass or fail per PR.
```

`src/meld/prompts/workers/docs.md`:

```markdown
# docs

Writes and updates documentation, decision records, and worker-type prompt files; keeps them consistent with the code.
```

- [ ] **Step 2: Write the failing tests**

`tests/test_roster.py`:

```python
from pathlib import Path

from meld.roster import WorkerType, builtin_workers, parse_worker_file, product_workers, roster, roster_names


def test_builtin_roster_has_the_seven_day_one_types():
    names = sorted(w.name for w in builtin_workers())
    assert names == ["auditor", "backend", "docs", "frontend", "qa", "reviewer", "security"]
    backend = next(w for w in builtin_workers() if w.name == "backend")
    assert backend.source == "builtin" and backend.description.startswith("Implements server-side")


def test_parse_worker_file_uses_stem_and_first_paragraph(tmp_path: Path):
    f = tmp_path / "marketing.md"
    f.write_text("# marketing\n\nDrafts launch copy and positioning.\n\nMore detail below.\n")
    w = parse_worker_file(f, "product")
    assert w == WorkerType("marketing", "Drafts launch copy and positioning.", "product", f)
    empty = tmp_path / "blank.md"
    empty.write_text("# blank\n")
    assert parse_worker_file(empty, "product").description == ""


def test_product_workers_override_builtins_and_add_new(tmp_path: Path):
    wd = tmp_path / ".meld" / "workers"
    wd.mkdir(parents=True)
    (wd / "marketing.md").write_text("# marketing\n\nDrafts launch copy.\n")
    (wd / "backend.md").write_text("# backend\n\nProduct-specific backend rules.\n")
    assert [w.name for w in product_workers(tmp_path)] == ["backend", "marketing"]
    full = roster(tmp_path)
    by_name = {w.name: w for w in full}
    assert by_name["backend"].source == "product" and by_name["backend"].description == "Product-specific backend rules."
    assert by_name["marketing"].source == "product"
    assert by_name["frontend"].source == "builtin"
    assert len(full) == 8
    assert roster_names(tmp_path) == frozenset({"auditor", "backend", "docs", "frontend", "qa", "reviewer", "security", "marketing"})


def test_roster_without_repo_root_and_with_extra():
    extra = [WorkerType("data", "Notebook and pipeline work.", "skill", None)]
    names = [w.name for w in roster(None, extra)]
    assert names == ["auditor", "backend", "data", "docs", "frontend", "qa", "reviewer", "security"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_roster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.roster'`

- [ ] **Step 4: Write `src/meld/roster.py`**

```python
"""Worker-type inventory: built-in prompts, product overrides, and adapter-provided skills."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class WorkerType:
    name: str
    description: str
    source: str
    path: Path | None


def parse_worker_file(path: Path, source: str) -> WorkerType:
    lines = path.read_text().splitlines()
    description = ""
    for line in lines[1:]:
        if line.strip():
            description = line.strip()
            break
    return WorkerType(name=path.stem, description=description, source=source, path=path)


def builtin_workers() -> list[WorkerType]:
    root = resources.files("meld").joinpath("prompts", "workers")
    out: list[WorkerType] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            with resources.as_file(entry) as p:
                out.append(parse_worker_file(Path(p), "builtin"))
    return out


def product_workers(repo_root: Path) -> list[WorkerType]:
    d = repo_root / ".meld" / "workers"
    if not d.is_dir():
        return []
    return [parse_worker_file(p, "product") for p in sorted(d.glob("*.md"))]


def roster(repo_root: Path | None, extra: Iterable[WorkerType] = ()) -> list[WorkerType]:
    by_name: dict[str, WorkerType] = {w.name: w for w in builtin_workers()}
    if repo_root is not None:
        for w in product_workers(repo_root):
            by_name[w.name] = w
    for w in extra:
        by_name.setdefault(w.name, w)
    return sorted(by_name.values(), key=lambda w: w.name)


def roster_names(repo_root: Path | None, extra: Iterable[WorkerType] = ()) -> frozenset[str]:
    return frozenset(w.name for w in roster(repo_root, extra))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_roster.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/meld/prompts/workers src/meld/roster.py tests/test_roster.py
git commit -m "feat: worker roster from built-in prompts and product overrides"
```

---

### Task 13: CLI context, `meld status`, and `meld models`

**Files:**
- Create: `src/meld/context.py`
- Create: `src/meld/status.py`
- Modify: `src/meld/cli.py` (replace whole file)
- Modify: `tests/conftest.py` (add `PROJECT_TOML` and `env_for`)
- Create: `tests/test_status.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `meld.context.Context` dataclass: `cfg, party, gh, store, models, repo_root: Path, actor_login: str` and `.actor() -> Actor` (roster from `roster_names(repo_root)` for groups, empty for humans)
  - `meld.context.find_config(start: Path) -> Path` (searches `start` and its parents for `.meld.toml`, raises `ConfigError`)
  - `meld.context.load_context(config_path: Path | None = None, env: Mapping | None = None, run=subprocess.run, cwd: Path | None = None, models: ModelRegistry | None = None) -> Context`
  - `meld.status.render_status(board, cfg, actor, models, now: datetime | None = None) -> dict`
  - `meld.status.build_status(ctx, now=None) -> tuple[dict, Board]`
  - `meld.cli.RUN` module attribute (defaults to `subprocess.run`; tests monkeypatch it), `meld.cli.main` group with `--config PATH` and `--pretty/--compact`, commands `status` and `models`.
- Status JSON shape (keys and types are the contract for the prompts in later plans):

```
{
  "repo": str, "milestone": int, "coordination_issue": int, "phase": "planning"|"executing", "halted": bool,
  "party": "[claude:pm]", "actor": "meld-claude-bot", "quorum": int,
  "needs_human": [{"issue": int, "title": str, "age_minutes": int}],
  "budget": {"spent": {group: {tier: int}}, "cap": {tier: int}},
  "issues": [{"number", "title", "status", "assignees", "worker", "model", "effort", "size", "challenged", "legal": [verb]}],
  "pulls": [{"number", "issue", "state", "head_sha", "committer_party", "approvals": [party], "changes_requested": [party],
             "checks", "pingpong_exceeded": bool, "legal": [verb]}],
  "runs": [{"group", "issue", "kind", "pr", "pid", "state": "running"|"finished"|"collected", "started_at"}],
  "coordination": {"comments": int, "last_comment_id": int, "open_challenges": [ref], "ignored_comments": int,
                   "agreements": {ref: status}},
  "digest": {...board_digest...},
  "state_errors": [str]   # one message per unreadable local state file; empty when state is clean
}
```

- [ ] **Step 1: Add shared project helpers to `tests/conftest.py`**

Append:

```python
PROJECT_TOML = """
[repo]
name = "acme/widgets"
[humans]
accounts = ["empowerScott"]
[groups.claude]
cli = "claude"
bot = "meld-claude-bot"
token_env = "MELD_TOKEN_CLAUDE"
[groups.claude.orchestrator]
model = "claude-opus-5"
effort = "high"
[groups.claude.pm]
model = "claude-sonnet-5"
effort = "medium"
[groups.codex]
cli = "codex"
bot = "meld-codex-bot"
token_env = "MELD_TOKEN_CODEX"
[groups.codex.orchestrator]
model = "gpt-x"
effort = "high"
[groups.codex.pm]
model = "gpt-x"
effort = "medium"
"""


def env_for(tmp_path: Path, group: str | None = "claude", role: str = "pm") -> dict[str, str]:
    e = {"MELD_HOME": str(tmp_path / "home"), "MELD_TOKEN_CLAUDE": "tok-c", "MELD_TOKEN_CODEX": "tok-x"}
    if group:
        e["MELD_GROUP"] = group
        e["MELD_ROLE"] = role
    return e
```

- [ ] **Step 2: Write the failing tests**

`tests/test_status.py`:

```python
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.config import ConfigError
from meld.context import find_config, load_context
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh


@pytest.fixture
def project(tmp_path: Path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    (tmp_path / "sub").mkdir()
    return tmp_path


def test_find_config_walks_up(project: Path):
    assert find_config(project / "sub") == project / ".meld.toml"
    with pytest.raises(ConfigError):
        find_config(Path("/"))


def test_load_context_requires_token_for_groups(project: Path):
    fake = FakeGh()
    env = env_for(project)
    del env["MELD_TOKEN_CLAUDE"]
    with pytest.raises(ConfigError, match="MELD_TOKEN_CLAUDE"):
        load_context(project / ".meld.toml", env=env, run=fake)


def test_load_context_human_uses_viewer_login(project: Path):
    fake = FakeGh()
    fake.route("GET", "user", {"login": "empowerScott"})
    ctx = load_context(project / ".meld.toml", env=env_for(project, group=None), run=fake)
    assert ctx.party.kind == "human" and ctx.actor_login == "empowerScott" and ctx.gh.token is None


def test_status_command_reports_board_and_legal_actions(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"], env=env_for(project))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["party"] == "[claude:pm]" and data["actor"] == "meld-claude-bot" and data["phase"] == "executing"
    assert data["quorum"] == 1
    issues = {i["number"]: i for i in data["issues"]}
    assert issues[11]["legal"] == ["claim"]
    assert issues[12]["challenged"] and "dispatch" not in issues[12]["legal"] and "release" in issues[12]["legal"]
    assert issues[14]["status"] == "needs_human"
    assert data["needs_human"][0]["issue"] == 14 and data["needs_human"][0]["age_minutes"] > 0
    pulls = {p["number"]: p for p in data["pulls"]}
    assert pulls[20]["committer_party"] == "codex" and pulls[20]["approvals"] == ["claude"]
    assert pulls[20]["legal"] == ["approve", "request-changes", "push-fix", "merge"]
    assert pulls[19]["state"] == "closed" and pulls[19]["legal"] == []
    assert data["coordination"]["open_challenges"] == ["#12"] and data["coordination"]["ignored_comments"] == 1
    assert data["budget"]["cap"] == {"small": 200, "medium": 80, "large": 20}
    assert "digest" in data


def test_status_as_orchestrator_cannot_claim(project: Path, monkeypatch):
    fake = FakeGh()
    load_board1_routes(fake)
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "status"],
                                env=env_for(project, role="orchestrator"))
    data = json.loads(result.output)
    assert {i["number"]: i["legal"] for i in data["issues"]}[11] == []


def test_models_command_lists_adapter_models(project: Path, monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(project / ".meld.toml"), "--compact", "models"], env=env_for(project))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["adapter"] == "claude"
    assert any(m["id"] == "claude-sonnet-5" for m in data["models"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.context'`

- [ ] **Step 4: Write `src/meld/context.py`**

```python
"""Resolve config, party, GitHub client, and state store for one CLI invocation."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from meld.config import Config, ConfigError, Party, load_config, party_from_env
from meld.github import GitHub
from meld.models import ModelRegistry
from meld.roster import roster_names
from meld.rules import Actor
from meld.state import StateStore, meld_home


@dataclass
class Context:
    cfg: Config
    party: Party
    gh: GitHub
    store: StateStore
    models: ModelRegistry
    repo_root: Path
    actor_login: str

    def actor(self) -> Actor:
        names = roster_names(self.repo_root) if self.party.group else frozenset()
        return Actor(self.party, self.actor_login, names)


def find_config(start: Path) -> Path:
    for d in [start, *start.parents]:
        candidate = d / ".meld.toml"
        if candidate.exists():
            return candidate
    raise ConfigError(f"no .meld.toml found from {start} upward; pass --config")


def load_context(config_path: Path | None = None, env: Mapping[str, str] | None = None, run=subprocess.run,
                 cwd: Path | None = None, models: ModelRegistry | None = None) -> Context:
    env = os.environ if env is None else env
    path = config_path or find_config(cwd or Path.cwd())
    cfg = load_config(path)
    party = party_from_env(cfg, env)
    store = StateStore(meld_home(env), party.group)
    token: str | None = None
    if party.group:
        g = cfg.groups[party.group]
        token = env.get(g.token_env)
        if not token:
            raise ConfigError(f"{g.token_env} is not set in the environment")
    gh = GitHub(cfg.repo_name, token=token, run=run, cache_dir=store.cache_dir)
    login = cfg.groups[party.group].bot if party.group else gh.viewer_login()
    return Context(cfg=cfg, party=party, gh=gh, store=store, models=models or ModelRegistry.load(),
                   repo_root=path.parent, actor_login=login)
```

- [ ] **Step 5: Write `src/meld/status.py`**

```python
"""Render the board plus legal actions for the calling party."""
from __future__ import annotations

from datetime import datetime, timezone

from meld.assemble import build_board, find_active_milestone
from meld.board import Board
from meld.config import Config
from meld.grammar import AGREEMENT_VERBS, agreement_for
from meld.models import ModelRegistry
from meld.rules import (
    Actor,
    approving_parties,
    committer_party,
    legal_issue_verbs,
    legal_pr_verbs,
    party_of_login,
    pingpong_exceeded,
)
from meld.state import board_digest


def _age_minutes(iso: str, now: datetime) -> int:
    if not iso:
        return 0
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return max(0, int((now - then).total_seconds() // 60))


def render_status(board: Board, cfg: Config, actor: Actor, models: ModelRegistry, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    needs_human = sorted(
        ({"issue": i.number, "title": i.title, "age_minutes": _age_minutes(i.updated_at, now)}
         for i in board.issues.values() if i.status == "needs_human"),
        key=lambda d: -d["age_minutes"],
    )
    issues = [
        {"number": i.number, "title": i.title, "status": i.status, "assignees": list(i.assignees), "worker": i.worker,
         "model": i.model, "effort": i.effort, "size": i.size, "challenged": board.is_challenged(f"#{i.number}"),
         "legal": legal_issue_verbs(board, cfg, actor, i, models)}
        for i in sorted(board.issues.values(), key=lambda i: i.number)
    ]
    pulls = [
        {"number": p.number, "issue": p.issue_number, "state": p.state, "head_sha": p.head_sha,
         "committer_party": committer_party(cfg, p), "approvals": sorted(approving_parties(cfg, p)),
         "changes_requested": sorted({q for q in (party_of_login(cfg, a) for a in p.changes_requested_on_head()) if q}),
         "checks": p.checks, "pingpong_exceeded": pingpong_exceeded(cfg, p),
         "legal": legal_pr_verbs(board, cfg, actor, p)}
        for p in sorted(board.pulls.values(), key=lambda p: p.number)
    ]
    runs = [
        {"group": r.group, "issue": r.issue, "kind": r.kind, "pr": r.pr, "pid": r.pid,
         "state": "running" if r.alive else "collected" if r.collected else "finished", "started_at": r.started_at}
        for g in sorted(board.runs) for r in sorted(board.runs[g].values(), key=lambda r: r.issue)
    ]
    refs = sorted({c.header.ref for c in board.comments if c.ok and c.header and c.header.ref and c.header.verb in AGREEMENT_VERBS})
    agreements = {ref: agreement_for(board.comments, ref, cfg).status for ref in refs}
    return {
        "repo": board.repo, "milestone": board.milestone, "coordination_issue": board.coordination_issue,
        "phase": board.phase, "halted": board.halted, "party": actor.party.label, "actor": actor.login,
        "quorum": cfg.effective_quorum, "needs_human": needs_human,
        "budget": {"spent": board.spend, "cap": cfg.budget},
        "issues": issues, "pulls": pulls, "runs": runs,
        "coordination": {"comments": len(board.comments), "last_comment_id": max((c.id for c in board.comments), default=0),
                         "open_challenges": sorted(board.open_challenges), "ignored_comments": board.ignored_comments,
                         "agreements": agreements},
        "digest": board_digest(board),
    }


def build_status(ctx, now: datetime | None = None) -> tuple[dict, Board]:
    runs = ctx.store.load_runs()
    milestone, _ = find_active_milestone(ctx.gh)
    spend = ctx.store.load_spend(milestone)
    board = build_board(ctx.gh, ctx.cfg, runs, spend, milestone=milestone)
    return render_status(board, ctx.cfg, ctx.actor(), ctx.models, now), board
```

- [ ] **Step 6: Replace `src/meld/cli.py`**

```python
"""meld command-line entry point."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click

from meld import __version__
from meld.assemble import BoardError
from meld.config import ConfigError
from meld.context import Context, load_context
from meld.github import GitHubError
from meld.status import build_status

RUN = subprocess.run  # tests monkeypatch this with a FakeGh


def emit(data: object, pretty: bool) -> None:
    click.echo(json.dumps(data, indent=2 if pretty else None))


@click.group()
@click.version_option(__version__, prog_name="meld")
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None,
              help="Path to .meld.toml (default: search upward from the current directory).")
@click.option("--pretty/--compact", default=True, help="Pretty-print JSON output.")
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, pretty: bool) -> None:
    """Meld: multi-AI coordination over a GitHub repo."""
    ctx.obj = {"config_path": config_path, "pretty": pretty}


def _load(ctx: click.Context) -> Context:
    try:
        return load_context(ctx.obj["config_path"], run=RUN)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e


def _guard(fn):
    try:
        return fn()
    except (BoardError, GitHubError, ConfigError) as e:
        raise click.ClickException(str(e)) from e


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Print the board and the legal actions for the calling party."""
    c = _load(ctx)
    data, _ = _guard(lambda: build_status(c))
    emit(data, ctx.obj["pretty"])


@main.command()
@click.option("--adapter", default=None, help="Adapter to list (default: the calling group's CLI).")
@click.pass_context
def models(ctx: click.Context, adapter: str | None) -> None:
    """List the models and efforts an adapter accepts."""
    c = _load(ctx)
    adapter = adapter or (c.cfg.groups[c.party.group].cli if c.party.group else "claude")
    specs = c.models.for_adapter(adapter)
    emit({"adapter": adapter, "models": [
        {"id": s.id, "context_class": s.context_class, "efforts": list(s.efforts), "tier": s.tier} for s in specs
    ]}, ctx.obj["pretty"])


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_status.py tests/test_cli.py -v`
Expected: PASS (7 tests)

- [ ] **Step 8: Commit**

```bash
git add src/meld/context.py src/meld/status.py src/meld/cli.py tests/test_status.py
git commit -m "feat: meld status and meld models with legal actions per party"
```

---

### Task 14: `meld wait`

**Files:**
- Create: `src/meld/waiting.py`
- Modify: `src/meld/cli.py` (add command)
- Create: `tests/test_wait.py`

**Interfaces:**
- Produces:
  - `meld.waiting.wait_for_change(ctx, timeout: float, interval: float, *, sleep=time.sleep, clock=time.monotonic) -> dict` — the status dict plus `changes: list[str]`, `timed_out: bool`, `rate_limited: bool`. Returns immediately when there is no prior snapshot (`changes == ["initial snapshot"]`), when the digest differs from the saved snapshot (and saves the new one), or at the deadline with `changes == []`. On `RateLimited` it backs off (interval, doubling, capped at 300s) and keeps trying until the deadline.
  - CLI: `meld wait [--timeout SECONDS] [--interval SECONDS]`, defaults from `polling.wait_timeout_seconds` and `polling.interval_seconds`.

- [ ] **Step 1: Write the failing tests**

`tests/test_wait.py`:

```python
import json

from meld.context import load_context
from meld.waiting import wait_for_change
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh


def make_ctx(tmp_path):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path), run=fake)
    return fake, ctx


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_first_call_returns_initial_snapshot(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    out = wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    assert out["changes"] == ["initial snapshot"] and out["timed_out"] is False
    assert ctx.store.load_snapshot() == out["digest"]


def test_returns_when_board_changes(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    issues = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures/board1/issues.json").read_text())
    for i in issues:
        if i["number"] == 11:
            i["labels"] = [l for l in i["labels"] if l["name"] != "meld:ready"] + [{"name": "meld:in-progress"}]
            i["assignees"] = [{"login": "meld-codex-bot"}]

    def sleep_then_mutate(s):
        clock.sleep(s)
        fake.route("GET", "repos/acme/widgets/issues", issues)

    out = wait_for_change(ctx, timeout=300, interval=30, sleep=sleep_then_mutate, clock=clock)
    assert out["timed_out"] is False
    assert "issue #11: ready -> in_progress" in out["changes"]
    assert ctx.store.load_snapshot() == out["digest"]


def test_times_out_without_changes(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    out = wait_for_change(ctx, timeout=90, interval=30, sleep=clock.sleep, clock=clock)
    assert out["timed_out"] is True and out["changes"] == [] and out["rate_limited"] is False
    assert clock.t >= 90


def test_rate_limit_backs_off_and_recovers(tmp_path):
    fake, ctx = make_ctx(tmp_path)
    clock = Clock()
    wait_for_change(ctx, timeout=300, interval=30, sleep=clock.sleep, clock=clock)
    fake.rate_limit_next = True
    slept = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)

    out = wait_for_change(ctx, timeout=100, interval=30, sleep=sleep, clock=clock)
    assert out["timed_out"] is True and slept[0] == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_wait.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.waiting'`

- [ ] **Step 3: Write `src/meld/waiting.py`**

```python
"""Block until the board changes or a timeout elapses."""
from __future__ import annotations

import time

from meld.github import RateLimited
from meld.state import diff_digest
from meld.status import build_status

_MAX_BACKOFF = 300.0


def wait_for_change(ctx, timeout: float, interval: float, *, sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline = clock() + timeout
    prev = ctx.store.load_snapshot()
    backoff = float(interval)
    while True:
        try:
            data, _ = build_status(ctx)
        except RateLimited:
            if clock() >= deadline:
                return {"timed_out": True, "rate_limited": True, "changes": []}
            sleep(min(backoff, _MAX_BACKOFF))
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue
        digest = data["digest"]
        changes = diff_digest(prev, digest)
        if changes:
            ctx.store.save_snapshot(digest)
            data.update({"changes": changes, "timed_out": False, "rate_limited": False})
            return data
        if clock() >= deadline:
            data.update({"changes": [], "timed_out": True, "rate_limited": False})
            return data
        sleep(interval)
```

- [ ] **Step 4: Add the command to `src/meld/cli.py`**

Add `from meld.waiting import wait_for_change` to the imports, then append:

```python
@main.command()
@click.option("--timeout", type=float, default=None, help="Seconds to wait before returning unchanged (default: polling.wait_timeout_seconds).")
@click.option("--interval", type=float, default=None, help="Seconds between polls (default: polling.interval_seconds).")
@click.pass_context
def wait(ctx: click.Context, timeout: float | None, interval: float | None) -> None:
    """Block until the board changes, then print status plus a changes list."""
    c = _load(ctx)
    t = timeout if timeout is not None else c.cfg.wait_timeout_seconds
    i = interval if interval is not None else c.cfg.poll_interval_seconds
    data = _guard(lambda: wait_for_change(c, timeout=t, interval=i))
    emit(data, ctx.obj["pretty"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_wait.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/meld/waiting.py src/meld/cli.py tests/test_wait.py
git commit -m "feat: meld wait blocks until the board changes with rate-limit backoff"
```

---

### Task 15: `meld claim` and `meld release`

**Files:**
- Create: `src/meld/actions.py`
- Modify: `src/meld/cli.py` (add commands and refusal handling)
- Create: `tests/test_actions.py`

**Interfaces:**
- Produces (in `meld.actions`):
  - `class Refused(Exception)` with `.refusal: Refusal`
  - `refuse(ctx, board, refusal) -> NoReturn` — for group parties posts `[<party>] PROTOCOL-VIOLATION <target>` on the coordination issue, then raises `Refused`; humans get no comment.
  - `claim(ctx, number: int) -> dict` → `{"claimed": number, "assignee": login}`; performs assign, re-read, race check, label swap `meld:ready` → `meld:in-progress`.
  - `release(ctx, number: int, force: bool = False) -> dict` → `{"released": number, "previous_assignees": [...]}`; removes assignees, swaps `meld:in-progress`/`meld:blocked` → `meld:ready`; `--force` is human-only and posts `[human] RELEASE #n`.
  - CLI: `meld claim N`, `meld release N [--force]`; on `Refused` prints `{"refused": ..., "verb": ..., "target": ..., "rule": ..., "violation_posted": ...}` and exits 2. `violation_posted` is tri-state: `True` when a `PROTOCOL-VIOLATION` comment was posted, `False` when posting one was attempted and failed, `None` when none was attempted (a human's refusal, or a lost claim race, which is not a protocol violation).

- [ ] **Step 1: Write the failing tests**

`tests/test_actions.py`:

```python
import json

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.actions import Refused, claim, release
from meld.context import load_context
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"


def make(tmp_path, group="claude", role="pm"):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake)
    return fake, ctx


def posted_headers(fake):
    return [c.body["body"].splitlines()[0] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def test_claim_happy_path(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/11/assignees", {"assignees": [{"login": "meld-claude-bot"}]})
    fake.route("GET", R + "issues/11", {"number": 11, "assignees": [{"login": "meld-claude-bot"}]})
    fake.route("DELETE", R + "issues/11/labels/meld:ready", [])
    fake.route("POST", R + "issues/11/labels", [{"name": "meld:in-progress"}])
    assert claim(ctx, 11) == {"claimed": 11, "assignee": "meld-claude-bot"}
    mutations = [(c.method, c.endpoint) for c in fake.calls if c.method != "GET"]
    assert mutations == [("POST", R + "issues/11/assignees"), ("DELETE", R + "issues/11/labels/meld:ready"),
                         ("POST", R + "issues/11/labels")]
    assert posted_headers(fake) == []


def test_claim_lost_race_releases_and_refuses(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/11/assignees", {})
    fake.route("GET", R + "issues/11", {"number": 11, "assignees": [{"login": "meld-codex-bot"}, {"login": "meld-claude-bot"}]})
    fake.route("DELETE", R + "issues/11/assignees", {})
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert "lost the claim race to meld-codex-bot" in ei.value.refusal.rule
    assert ("DELETE", R + "issues/11/assignees") in [(c.method, c.endpoint) for c in fake.calls]
    assert posted_headers(fake) == ["[claude:pm] PROTOCOL-VIOLATION #11"]


def test_claim_refusal_posts_protocol_violation_for_groups_only(tmp_path):
    fake, ctx = make(tmp_path, role="orchestrator")
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused) as ei:
        claim(ctx, 11)
    assert ei.value.refusal.rule == "only a PM or a human may claim"
    assert posted_headers(fake) == ["[claude] PROTOCOL-VIOLATION #11"]
    assert not any(c.endpoint.endswith("/assignees") for c in fake.calls)

    fake2, human = make(tmp_path, group=None)
    with pytest.raises(Refused):
        claim(human, 12)  # already in progress
    assert posted_headers(fake2) == []


def test_claim_unknown_issue(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    with pytest.raises(Refused, match="not in the active milestone"):
        claim(ctx, 999)


def test_release_by_owner_and_forced_by_human(tmp_path):
    fake, ctx = make(tmp_path)
    fake.route("DELETE", R + "issues/12/assignees", {})
    fake.route("DELETE", R + "issues/12/labels/meld:in-progress", [])
    fake.route("DELETE", R + "issues/12/labels/meld:blocked", [])
    fake.route("POST", R + "issues/12/labels", [{"name": "meld:ready"}])
    assert release(ctx, 12) == {"released": 12, "previous_assignees": ["meld-claude-bot"]}
    assert posted_headers(fake) == []

    fake2, human = make(tmp_path, group=None)
    fake2.route("DELETE", R + "issues/12/assignees", {})
    fake2.route("DELETE", R + "issues/12/labels/meld:in-progress", [])
    fake2.route("POST", R + "issues/12/labels", [{"name": "meld:ready"}])
    fake2.route("POST", R + "issues/10/comments", {"id": 2})
    release(human, 12, force=True)
    assert posted_headers(fake2) == ["[human] RELEASE #12"]

    fake3, pm = make(tmp_path)
    fake3.route("POST", R + "issues/10/comments", {"id": 3})
    with pytest.raises(Refused, match="human-only"):
        release(pm, 12, force=True)


def test_cli_claim_refusal_exit_code(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/10/comments", {"id": 1})
    monkeypatch.setattr(cli, "RUN", fake)
    result = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "claim", "12"], env=env_for(tmp_path))
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["verb"] == "claim" and data["target"] == "#12" and "not ready" in data["rule"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.actions'`

- [ ] **Step 3: Write `src/meld/actions.py`**

```python
"""Mutating verbs on issues: claim and release. Each re-checks legality before acting."""
from __future__ import annotations

from typing import NoReturn

from meld.board import Board
from meld.config import Party
from meld.grammar import Header, compose
from meld.rules import Refusal, Verb, issue_refusal
from meld.status import build_status

READY, IN_PROGRESS, BLOCKED = "meld:ready", "meld:in-progress", "meld:blocked"


class Refused(Exception):
    def __init__(self, refusal: Refusal):
        super().__init__(str(refusal))
        self.refusal = refusal


def refuse(ctx, board: Board | None, refusal: Refusal) -> NoReturn:
    actor = ctx.actor()
    if board is not None and not actor.is_human:
        header = Header(actor.party, "PROTOCOL-VIOLATION", refusal.target, None)
        ctx.gh.create_comment(board.coordination_issue, compose(header, f"Refused `{refusal.verb.value}`: {refusal.rule}"))
    raise Refused(refusal)


def _issue_or_refuse(ctx, board: Board, verb: Verb, number: int):
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(verb, f"#{number}", "issue is not in the active milestone"))
    return issue


def claim(ctx, number: int) -> dict:
    _, board = build_status(ctx)
    issue = _issue_or_refuse(ctx, board, Verb.CLAIM, number)
    actor = ctx.actor()
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.CLAIM, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    ctx.gh.add_assignees(number, [actor.login])
    fresh = ctx.gh.get_issue(number)
    assignees = [a["login"] for a in fresh.get("assignees", [])]
    if assignees != [actor.login]:
        ctx.gh.remove_assignees(number, [actor.login])
        others = [a for a in assignees if a != actor.login]
        refuse(ctx, board, Refusal(Verb.CLAIM, f"#{number}", f"lost the claim race to {', '.join(others) or 'unknown'}"))
    ctx.gh.remove_label(number, READY)
    ctx.gh.add_labels(number, [IN_PROGRESS])
    return {"claimed": number, "assignee": actor.login}


def release(ctx, number: int, force: bool = False) -> dict:
    _, board = build_status(ctx)
    issue = _issue_or_refuse(ctx, board, Verb.RELEASE, number)
    actor = ctx.actor()
    if force and not actor.is_human:
        refuse(ctx, board, Refusal(Verb.RELEASE, f"#{number}", "--force is human-only"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.RELEASE, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    if issue.assignees:
        ctx.gh.remove_assignees(number, list(issue.assignees))
    for label in (IN_PROGRESS, BLOCKED):
        if label in issue.labels:
            ctx.gh.remove_label(number, label)
    ctx.gh.add_labels(number, [READY])
    if force:
        note = compose(Header(Party("human"), "RELEASE", f"#{number}", None),
                       f"Force-released #{number} from {', '.join(issue.assignees) or 'nobody'}.")
        ctx.gh.create_comment(board.coordination_issue, note)
    return {"released": number, "previous_assignees": list(issue.assignees)}
```

- [ ] **Step 4: Add the commands to `src/meld/cli.py`**

Add `from meld.actions import Refused, claim as do_claim, release as do_release` to the imports, then append:

```python
def _run_action(ctx: click.Context, fn) -> None:
    try:
        emit(_guard(fn), ctx.obj["pretty"])
    except Refused as e:
        emit({"refused": str(e), "verb": e.refusal.verb.value, "target": e.refusal.target, "rule": e.refusal.rule},
             ctx.obj["pretty"])
        ctx.exit(2)


@main.command()
@click.argument("number", type=int)
@click.pass_context
def claim(ctx: click.Context, number: int) -> None:
    """Claim a ready issue by assigning this party (first assignment wins)."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_claim(c, number))


@main.command()
@click.argument("number", type=int)
@click.option("--force", is_flag=True, help="Human-only: release another party's claim and post a note.")
@click.pass_context
def release(ctx: click.Context, number: int, force: bool) -> None:
    """Release a claim, returning the issue to ready."""
    c = _load(ctx)
    _run_action(ctx, lambda: do_release(c, number, force=force))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_actions.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add src/meld/actions.py src/meld/cli.py tests/test_actions.py
git commit -m "feat: meld claim and meld release with race check and protocol-violation posts"
```

---

### Task 16: `meld post`, spec reconciliation, and README

**Files:**
- Create: `src/meld/posting.py`
- Modify: `src/meld/cli.py` (add command)
- Create: `tests/test_posting.py`
- Modify: `docs/superpowers/specs/2026-09-03-meld-design.md` (record the three small deviations)
- Modify: `README.md`

**Interfaces:**
- Produces (in `meld.posting`):
  - `class PostError(ValueError)`
  - `post(ctx, verb: str, ref: str | None = None, qualifier: str | None = None, text: str = "", joint: bool = False) -> dict` → `{"comment_id", "url", "header"}`. Rules: verb must be allowed for the party; grammar `validate` must pass on the final body; `--joint` is refused for humans, requires a ref, requires the agreement on that ref to be `agreed`, `majority`, or `disagree`, requires the caller to be the proposer, renders a `## DISSENT` section for `majority`, requires qualifier `DISAGREE` and renders `## Positions` for `disagree`, and enforces `PLAN` only in `planning` and `PLAN-ADD` only in `executing`; a group `AMEND` is refused once `Agreement.can_amend` is false.
  - CLI: `meld post VERB [REF] [--qualifier Q] [-m TEXT | --body-file PATH] [--joint]`; `PostError` prints `{"error": ...}` and exits 2.

- [ ] **Step 1: Write the failing tests**

`tests/test_posting.py`:

```python
import json

import pytest
from click.testing import CliRunner

import meld.cli as cli
from meld.context import load_context
from meld.posting import PostError, post
from tests.conftest import PROJECT_TOML, env_for, load_board1_routes
from tests.fakegh import FakeGh

R = "repos/acme/widgets/"
BASE_COMMENTS = [
    {"id": 101, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:00:00Z", "body": "[claude] ROSTER\n\nbackend"},
]


def make(tmp_path, group="claude", role="orchestrator", comments=None, approved=True):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    if comments is not None:
        fake.route("GET", R + "issues/10/comments", comments)
    if not approved:
        issues = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures/board1/issues.json").read_text())
        for i in issues:
            if i["number"] == 10:
                i["labels"] = [{"name": "meld:coordination"}]
        fake.route("GET", R + "issues", issues)
    fake.route("POST", R + "issues/10/comments", {"id": 555, "html_url": "https://github.com/acme/widgets/issues/10#issuecomment-555"})
    ctx = load_context(tmp_path / ".meld.toml", env=env_for(tmp_path, group=group, role=role), run=fake)
    return fake, ctx


def posted(fake):
    return [c.body["body"] for c in fake.calls if c.method == "POST" and c.endpoint.endswith("/comments")]


def test_post_simple_verb(tmp_path):
    fake, ctx = make(tmp_path)
    out = post(ctx, "DRIFT", text="Scope moved: #12 now includes SSO.\n\nverified-by: meld status")
    assert out["comment_id"] == 555 and out["header"] == "[claude] DRIFT"
    assert posted(fake)[0].startswith("[claude] DRIFT\n\nScope moved")


def test_verb_not_allowed_for_party(tmp_path):
    _, pm = make(tmp_path, role="pm")
    with pytest.raises(PostError, match="DRIFT is not a verb \\[claude:pm\\] may post"):
        post(pm, "DRIFT", text="x")
    _, human = make(tmp_path, group=None)
    with pytest.raises(PostError, match="humans cannot post \\[joint\\]"):
        post(human, "PLAN", ref="plan-1", joint=True, text="x")


def test_validation_errors_surface(tmp_path):
    _, ctx = make(tmp_path)
    with pytest.raises(PostError, match="CHALLENGE requires a ref"):
        post(ctx, "CHALLENGE", text="doubt")
    with pytest.raises(PostError, match="EVIDENCE requires a verified-by"):
        post(ctx, "EVIDENCE", ref="#12", text="trust me")


def test_joint_plan_when_agreed_during_planning(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE plan-1\n\n1. health endpoint\n2. login form\n\nverified-by: meld roster"},
        {"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE plan-1\n\nok"},
    ]
    fake, ctx = make(tmp_path, comments=comments, approved=False)
    out = post(ctx, "PLAN", ref="plan-1", joint=True)
    assert out["header"] == "[joint] PLAN plan-1"
    body = posted(fake)[0]
    assert "1. health endpoint" in body and "verified-by: meld roster" in body


def test_joint_refused_when_open_or_not_proposer_or_wrong_phase(tmp_path):
    open_comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[claude] PROPOSE plan-1\n\nplan\n\nverified-by: x"},
    ]
    _, ctx = make(tmp_path, comments=open_comments, approved=False)
    with pytest.raises(PostError, match="agreement on plan-1 is open"):
        post(ctx, "PLAN", ref="plan-1", joint=True)
    agreed = open_comments + [{"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AGREE plan-1\n\nok"}]
    _, codex = make(tmp_path, group="codex", comments=agreed, approved=False)
    with pytest.raises(PostError, match="only the proposer \\(claude\\)"):
        post(codex, "PLAN", ref="plan-1", joint=True)
    _, executing = make(tmp_path, comments=agreed, approved=True)
    with pytest.raises(PostError, match="PLAN may only be posted during planning"):
        post(executing, "PLAN", ref="plan-1", joint=True)


def test_joint_disagree_requires_qualifier_and_lists_positions(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:10:00Z",
         "body": "[codex] DRAFT ask-1\n\nUse Postgres.\n\nverified-by: docs"},
        {"id": 103, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:11:00Z",
         "body": "[claude] DISSENT ask-1\n\nSQLite is enough for v1."},
    ]
    fake, codex = make(tmp_path, group="codex", comments=comments)
    with pytest.raises(PostError, match="no majority; post with qualifier DISAGREE"):
        post(codex, "ANSWER", ref="ask-1", joint=True)
    out = post(codex, "ANSWER", ref="ask-1", qualifier="DISAGREE", joint=True, text="verified-by: meld status")
    assert out["header"] == "[joint] ANSWER ask-1 DISAGREE"
    body = posted(fake)[0]
    assert "## Positions" in body and "Use Postgres." in body and "SQLite is enough" in body


def test_amend_cap_enforced(tmp_path):
    comments = BASE_COMMENTS + [
        {"id": 102, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:10:00Z", "body": "[claude] PROPOSE plan-1\n\nv1"},
        {"id": 103, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:11:00Z", "body": "[codex] AMEND plan-1\n\nv2"},
        {"id": 104, "user": {"login": "meld-claude-bot"}, "created_at": "2026-09-03T09:12:00Z", "body": "[claude] AMEND plan-1\n\nv3"},
        {"id": 105, "user": {"login": "meld-codex-bot"}, "created_at": "2026-09-03T09:13:00Z", "body": "[codex] AMEND plan-1\n\nv4"},
    ]
    _, ctx = make(tmp_path, comments=comments)
    with pytest.raises(PostError, match="amend cap \\(3\\) reached"):
        post(ctx, "AMEND", ref="plan-1", text="v5")
    assert post(ctx, "AGREE", ref="plan-1", text="fine")["header"] == "[claude] AGREE plan-1"


def test_cli_post_with_message_and_error_exit(tmp_path, monkeypatch):
    (tmp_path / ".meld.toml").write_text(PROJECT_TOML)
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("POST", R + "issues/10/comments", {"id": 7, "html_url": "u"})
    monkeypatch.setattr(cli, "RUN", fake)
    ok = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "post", "PING", "-m", "anyone there?"],
                            env=env_for(tmp_path, role="orchestrator"))
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["header"] == "[claude] PING"
    bad = CliRunner().invoke(cli.main, ["--config", str(tmp_path / ".meld.toml"), "--compact", "post", "CHALLENGE", "-m", "x"],
                             env=env_for(tmp_path, role="orchestrator"))
    assert bad.exit_code == 2 and "requires a ref" in json.loads(bad.output)["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_posting.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meld.posting'`

- [ ] **Step 3: Write `src/meld/posting.py`**

```python
"""Post a coordination comment in the header grammar, including verified [joint] comments."""
from __future__ import annotations

from meld.config import Party
from meld.grammar import Header, agreement_for, allowed_verbs, compose, validate


class PostError(ValueError):
    pass


def _dissent_section(dissent: dict[str, str]) -> str:
    return "\n".join(f"- **{g}**: {t}" for g, t in sorted(dissent.items()))


def post(ctx, verb: str, ref: str | None = None, qualifier: str | None = None, text: str = "", joint: bool = False) -> dict:
    from meld.status import build_status

    _, board = build_status(ctx)
    actor = ctx.actor()
    if joint and actor.is_human:
        raise PostError("humans cannot post [joint] comments")
    party = Party("joint") if joint else actor.party
    if verb not in allowed_verbs(party):
        raise PostError(f"{verb} is not a verb {party.label} may post (allowed: {', '.join(sorted(allowed_verbs(party)))})")
    body_text = text
    if joint:
        if not ref:
            raise PostError("joint comments require a ref")
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if agreement.status in ("none", "open"):
            raise PostError(f"agreement on {ref} is {agreement.status}; cannot post [joint]")
        if agreement.proposer != actor.group:
            raise PostError(f"only the proposer ({agreement.proposer}) may post the joint comment for {ref}")
        if verb == "PLAN" and board.phase != "planning":
            raise PostError("PLAN may only be posted during planning")
        if verb == "PLAN-ADD" and board.phase != "executing":
            raise PostError("PLAN-ADD may only be posted during executing")
        base = text.strip() or (agreement.text or "")
        if agreement.status == "agreed":
            body_text = base
        elif agreement.status == "majority":
            body_text = f"{base}\n\n## DISSENT\n{_dissent_section(agreement.dissent)}"
        else:
            if qualifier != "DISAGREE":
                raise PostError(f"agreement on {ref} has no majority; post with qualifier DISAGREE")
            positions = f"- **{agreement.proposer}** (proposal): {agreement.text}\n{_dissent_section(agreement.dissent)}"
            body_text = f"## Positions\n{positions}\n\n{text.strip()}".strip()
    elif verb == "AMEND" and party.kind in ("group", "pm"):
        if not ref:
            raise PostError("AMEND requires a ref")
        agreement = agreement_for(board.comments, ref, ctx.cfg)
        if not agreement.can_amend(ctx.cfg):
            raise PostError(f"amend cap ({ctx.cfg.amend_cap}) reached for {ref}; post AGREE or DISSENT")
    header = Header(party, verb, ref, qualifier)
    errors = validate(header, body_text)
    if errors:
        raise PostError("; ".join(errors))
    body = compose(header, body_text)
    resp = ctx.gh.create_comment(board.coordination_issue, body)
    return {"comment_id": resp.get("id"), "url": resp.get("html_url"), "header": body.splitlines()[0]}
```

- [ ] **Step 4: Add the command to `src/meld/cli.py`**

Add `from meld.posting import PostError, post as do_post` to the imports, then append:

```python
@main.command()
@click.argument("verb")
@click.argument("ref", required=False, default=None)
@click.option("--qualifier", default=None, help="Optional trailing qualifier, e.g. DISAGREE or FAIL.")
@click.option("-m", "--message", "message", default=None, help="Comment text.")
@click.option("--body-file", type=click.Path(path_type=Path, exists=True), default=None, help="Read comment text from a file.")
@click.option("--joint", is_flag=True, help="Post as [joint] after verifying agreement on REF.")
@click.pass_context
def post(ctx: click.Context, verb: str, ref: str | None, qualifier: str | None, message: str | None,
         body_file: Path | None, joint: bool) -> None:
    """Post a coordination comment: VERB [REF] with the calling party's header."""
    c = _load(ctx)
    text = body_file.read_text() if body_file else (message or "")
    try:
        emit(_guard(lambda: do_post(c, verb.upper(), ref=ref, qualifier=qualifier, text=text, joint=joint)), ctx.obj["pretty"])
    except PostError as e:
        emit({"error": str(e)}, ctx.obj["pretty"])
        ctx.exit(2)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_posting.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests pass, 0 failures.

- [ ] **Step 7: Reconcile the spec and README**

In `docs/superpowers/specs/2026-09-03-meld-design.md`, section 4 "Repo layout", change `models.toml               # per adapter: ...` to `src/meld/data/models.toml   # per adapter: ...`. In section 5, add `name = "owner/name"` as the first line of the `[repo]` block. In section 7's verb table, add `WORKER` to the joint row (`PLAN`, `PLAN-ADD`, `ANSWER`, `DECISION`, `WORKER`) and `RELEASE` to the human row. In section 8, replace the `**claim**` bullet's opening with `- **claim**: caller is a PM or a human; milestone is \`executing\`; ...` and the `**dispatch**` bullet's opening with `- **dispatch**: caller is a PM; caller's bot is assignee; ...`.

Replace `README.md` with:

```markdown
# Meld

Multi-AI coordination: N independent AI coding systems (Claude Code, Codex CLI, ...) work on one GitHub repo as separate groups, negotiate on a coordination issue, claim work via issue assignment, review and modify each other's PRs, and answer to one human.

Design: `docs/superpowers/specs/2026-09-03-meld-design.md`. Plans: `docs/superpowers/plans/`.

## Develop

    uv sync
    uv run pytest
    uv run meld --help

## Verbs (Plan 1)

| Command | What it does |
|---|---|
| `meld status` | Board as JSON plus the legal actions for the calling party |
| `meld wait [--timeout S] [--interval S]` | Block until the board changes, then print status with a `changes` list |
| `meld claim N` | Assign this party to a ready issue (first assignment wins) |
| `meld release N [--force]` | Return an issue to ready; `--force` is human-only |
| `meld post VERB [REF] [-m TEXT] [--joint]` | Post a coordination comment in the header grammar |
| `meld models [--adapter X]` | Models and efforts an adapter accepts |

The calling party comes from `MELD_GROUP` and `MELD_ROLE` (`orchestrator` or `pm`); unset means human, authenticated as `gh`'s current user. Group tokens come from the `token_env` named in `.meld.toml`. Local state lives under `MELD_HOME` (default `~/.meld`) and can be deleted at any time.
```

- [ ] **Step 8: Commit**

```bash
git add src/meld/posting.py src/meld/cli.py tests/test_posting.py docs/superpowers/specs/2026-09-03-meld-design.md README.md
git commit -m "feat: meld post with joint-comment verification and amend cap; reconcile spec"
```

---

## Plan 1 exit criteria

- `uv run pytest -q` passes with zero failures and no network access.
- `MELD_GROUP=claude MELD_ROLE=pm MELD_TOKEN_CLAUDE=... uv run meld status` against a real repo prepared by hand (labels, a milestone, a `meld:coordination` issue with `meld:plan-approved`) prints the board with legal actions. Manual until Plan 3 delivers `meld setup`.
- Every rule in spec section 8 that concerns `claim`, `release`, `dispatch`, `collect`, `reopen`, `approve`, `request-changes`, `push-fix`, and `merge` has a named test in `tests/test_rules_issues.py` or `tests/test_rules_prs.py`.
- Plan 2 (execution: adapters, dispatch, collect, reap, runners) and Plan 3 (collaboration and bootstrap) are written against the module names in this plan's Interfaces blocks.
