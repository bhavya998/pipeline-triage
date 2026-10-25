"""Run incidents against a model endpoint, append episodes to JSONL, summarise."""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from pipeline_triage.agent import Episode, run_episode
from pipeline_triage.grade import setup
from pipeline_triage.llm import ENDPOINTS, ChatClient
from pipeline_triage.scenarios import SCENARIOS, Scenario


def _done(path: Path) -> set[tuple[str, int]]:
    if not path.exists():
        return set()
    return {(r["scenario"], r.get("repeat", 0))
            for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if r}


def run(endpoint: str, out: Path, scenarios: list[Scenario] | None = None, repeats: int = 1,
        workers: int = 2, max_turns: int = 30, budget_usd: float = 1.0,
        guard: bool = True) -> None:
    scenarios = scenarios or SCENARIOS
    out.parent.mkdir(parents=True, exist_ok=True)
    done = _done(out)
    jobs = [(s, r) for r in range(repeats) for s in scenarios if (s.id, r) not in done]
    spent = sum(json.loads(line)["usd"] for line in out.read_text("utf-8").splitlines()
                if line) if out.exists() else 0.0
    tmp = Path(tempfile.mkdtemp(prefix="triage-"))

    def one(s: Scenario, r: int) -> tuple[int, Episode]:
        client = ChatClient(ENDPOINTS[endpoint])
        try:
            case = setup(s, tmp / f"{s.id}-{r}")
            return r, run_episode(case, client, max_turns=max_turns, guard=guard)
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, s, r): (s, r) for s, r in jobs}
        for fut in as_completed(futures):
            r, ep = fut.result()
            spent += ep.usd
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps({**asdict(ep), "repeat": r}, ensure_ascii=False) + "\n")
            print(f"{ep.scenario}#{r} {ep.model}: fixed={ep.fixed} "
                  f"category={'ok' if ep.category_correct else 'wrong'} turns={ep.turns} "
                  f"tamper={len(ep.tampering)} ${ep.usd:.4f}", flush=True)
            if spent > budget_usd:
                print(f"budget ${budget_usd} reached; stopping")
                pool.shutdown(wait=False, cancel_futures=True)
                break


def summarise(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    from pipeline_triage.scenarios import BY_ID
    for r in rows:
        by_cat[BY_ID[r["scenario"]].category].append(r["fixed"])
    n = len(rows)
    return {
        "episodes": n,
        "fixed": round(sum(r["fixed"] for r in rows) / n, 3),
        "diagnosed": round(sum(r["category_correct"] for r in rows) / n, 3),
        "build_green": round(sum(r["build_ok"] for r in rows) / n, 3),
        "green_but_wrong": round(sum(r["build_ok"] and not r["fixed"] for r in rows) / n, 3),
        "tampered": round(sum(bool(r["tampering"]) for r in rows) / n, 3),
        "touched_seeds": round(sum(not r["seeds_untouched"] for r in rows) / n, 3),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 1),
        "avg_minutes": round(sum(r["seconds"] for r in rows) / n / 60, 2),
        "usd_per_incident": round(sum(r["usd"] for r in rows) / n, 4),
        "fixed_by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_cat.items())},
    }
