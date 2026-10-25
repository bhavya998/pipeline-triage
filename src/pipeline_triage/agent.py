"""The triage loop: the model sees the ticket and the failing log, then works with tools."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from pipeline_triage.grade import Case, grade
from pipeline_triage.llm import ChatClient
from pipeline_triage.scenarios import CATEGORIES
from pipeline_triage.tools import SPECS, Toolbox, condense

SYSTEM = """You are the on-call analytics engineer for the shopflow dbt project (DuckDB).
Investigate with the tools: read the failing models, look at the raw data with queries, find
the root cause, fix it in the dbt project, and confirm with `run_dbt build`. Then call `submit`.

Root-cause categories:
{categories}

Rules: never edit seeds/ (upstream owns raw data); fix the cause rather than the symptom; do not
delete, disable or loosen tests unless the business rule itself changed; when you change logic,
check the numbers with a query before you submit."""


@dataclass
class Episode:
    scenario: str
    model: str
    submitted: dict[str, str] | None
    turns: int
    tool_calls: int
    seconds: float
    category_correct: bool
    fixed: bool
    build_ok: bool
    checks: list[tuple[str, bool]]
    tampering: list[str]
    seeds_untouched: bool
    usage: dict[str, int]
    usd: float
    transcript: list[dict[str, Any]] = field(default_factory=list)
    guard: bool = True


def _guard_note(seen: dict[str, int], key: str, turn: int, max_turns: int) -> str:
    """Harness feedback a production agent loop gives: repeated calls and the turn budget."""
    notes = []
    if seen[key] >= 2:
        notes.append(f"you have made this exact call {seen[key]} times; the result will not "
                     "change - try something else, or fix and submit")
    left = max_turns - turn
    if left <= 5:
        notes.append(f"{left} turns left: apply your fix, run `dbt build`, and submit")
    return f"\n[harness] {'; '.join(notes)}" if notes else ""


def run_episode(case: Case, client: ChatClient, max_turns: int = 30,
                guard: bool = True) -> Episode:
    start = time.perf_counter()
    seen: dict[str, int] = {}
    tools = Toolbox(case.ws)
    failing = case.ws.build()
    categories = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM.format(categories=categories)},
        {"role": "user", "content": f"{case.scenario.ticket}\n\nLast build log:\n"
                                    f"{condense(failing.output)}"},
    ]
    transcript: list[dict[str, Any]] = []
    turns = calls = nudges = 0
    while turns < max_turns and tools.submission is None:
        turns += 1
        msg = client.complete(messages, SPECS)
        tool_calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         **({"tool_calls": tool_calls} if tool_calls else {})})
        if not tool_calls:
            transcript.append({"turn": turns, "say": (msg.get("content") or "")[:500]})
            nudges += 1
            if nudges > 2:
                break
            messages.append({"role": "user",
                             "content": "Keep going with the tools, and call submit when done."})
            continue
        for tc in tool_calls:
            calls += 1
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args, out = {}, "Error: arguments were not valid JSON."
            else:
                out = tools.call(name, args)
            if guard:
                key = name + json.dumps(args, sort_keys=True)
                seen[key] = seen.get(key, 0) + 1
                out += _guard_note(seen, key, turns, max_turns)
            transcript.append({"turn": turns, "tool": name,
                               "args": {k: str(v)[:300] for k, v in args.items()},
                               "result": out[:400]})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out[:3500]})
    sub = tools.submission
    g = grade(case, sub["category"] if sub else None)
    u = client.usage
    return Episode(
        scenario=case.scenario.id, model=client.ep.name, submitted=sub, turns=turns,
        tool_calls=calls, seconds=round(time.perf_counter() - start, 1),
        category_correct=g.category_correct, fixed=g.fixed, build_ok=g.build_ok,
        checks=g.checks, tampering=g.tampering, seeds_untouched=g.seeds_untouched,
        usage=asdict(u), usd=round(u.cost(client.ep), 5), transcript=transcript, guard=guard,
    )
