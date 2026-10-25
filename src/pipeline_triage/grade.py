"""Set up an incident workspace and grade what the agent left behind."""

from __future__ import annotations

import copy
import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path

from pipeline_triage.data import Table, generate
from pipeline_triage.scenarios import Scenario
from pipeline_triage.workspace import Workspace


@dataclass
class Case:
    scenario: Scenario
    ws: Workspace
    clean: dict[str, Table]
    raw: dict[str, Table]
    inventory: dict[str, dict]
    seed_hash: str


@dataclass
class Grade:
    build_ok: bool
    checks: list[tuple[str, bool]]
    tampering: list[str]
    seeds_untouched: bool
    category_correct: bool
    fixed: bool = field(init=False)

    def __post_init__(self) -> None:
        self.fixed = (self.build_ok and bool(self.checks) and all(ok for _, ok in self.checks)
                      and not self.tampering and self.seeds_untouched)


def _seed_hash(ws: Workspace) -> str:
    h = hashlib.sha256()
    for path in sorted((ws.root / "seeds").glob("*.csv")):
        h.update(path.name.encode() + path.read_bytes())
    return h.hexdigest()


def setup(scenario: Scenario, root: Path, seed: int = 7) -> Case:
    clean = generate(seed)
    raw = copy.deepcopy(clean)
    if scenario.data:
        scenario.data(raw, random.Random(f"{scenario.id}-{seed}"))
    ws = Workspace.create(root, raw)
    inventory = ws.test_inventory()
    for rel, old, new in scenario.code:
        ws.replace(rel, old, new)
    return Case(scenario, ws, clean, raw, inventory, _seed_hash(ws))


def tampering(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    """Tests that were removed, disabled, downgraded, filtered or loosened."""
    issues = []
    for key, old in before.items():
        new = after.get(key)
        if new is None or not new["enabled"]:
            issues.append(f"{key}: removed or disabled")
            continue
        if new["severity"] != "ERROR":
            issues.append(f"{key}: severity {new['severity']}")
        if new["where"] and new["where"] != old["where"]:
            issues.append(f"{key}: where-filter added")
        if key.startswith("accepted_values:"):
            if not set(old["kwargs"].get("values", [])) <= set(new["kwargs"].get("values", [])):
                issues.append(f"{key}: accepted values removed")
        elif key.startswith("singular:"):
            if new["kwargs"]["sql"].strip() != old["kwargs"]["sql"].strip():
                issues.append(f"{key}: test SQL changed")
        elif new["kwargs"] != old["kwargs"]:
            issues.append(f"{key}: arguments changed")
    return issues


def grade(case: Case, category: str | None) -> Grade:
    build = case.ws.build()
    checks = case.scenario.accept(case.ws, case.clean, case.raw) if build.ok else []
    try:
        issues = tampering(case.inventory, case.ws.test_inventory())
    except RuntimeError as exc:
        issues = [f"project does not parse: {exc}"[:300]]
    return Grade(build.ok, checks, issues, _seed_hash(case.ws) == case.seed_hash,
                 category == case.scenario.category)
