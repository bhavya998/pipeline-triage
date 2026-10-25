"""pipeline-triage command line."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import typer

from pipeline_triage.scenarios import BY_ID, SCENARIOS

app = typer.Typer(add_completion=False, no_args_is_help=True)
RESULTS = Path("results")


@app.command("list")
def list_cmd() -> None:
    """Show the incidents."""
    for s in SCENARIOS:
        typer.echo(f"{s.id}  {s.category:22s} {s.title}")


@app.command()
def check() -> None:
    """Prove every incident fails as shipped and passes with its reference fix."""
    from pipeline_triage.grade import grade, setup

    base = Path(tempfile.mkdtemp(prefix="triage-check-"))
    for s in SCENARIOS:
        case = setup(s, base / s.id)
        broken = not case.ws.build().ok
        for rel, old, new in s.fix:
            case.ws.replace(rel, old, new)
        g = grade(case, s.category)
        typer.echo(f"{s.id} fails_before={broken} fixed_after={g.fixed}")


@app.command()
def run(endpoint: str, scenarios: str = "", repeats: int = 1, workers: int = 2,
        max_turns: int = 30, budget: float = 0.3, tag: str = "",
        guard: bool = typer.Option(True, help="Repeat-call warnings and turn-budget notes")
        ) -> None:
    """Run the agent on incidents (all by default) and append episodes to results/."""
    from pipeline_triage.bench import run as bench_run

    chosen = [BY_ID[s] for s in scenarios.split(",")] if scenarios else None
    suffix = ("-" + tag if tag else "") + ("" if guard else "-noguard")
    out = RESULTS / f"{endpoint}{suffix}.jsonl"
    bench_run(endpoint, out, chosen, repeats, workers, max_turns, budget, guard)


@app.command()
def summary() -> None:
    """Summarise every results file."""
    from pipeline_triage.bench import summarise

    table = {p.stem: summarise(p) for p in sorted(RESULTS.glob("*.jsonl"))}
    (RESULTS / "summary.json").write_text(json.dumps(table, indent=2))
    typer.echo(json.dumps(table, indent=2))


LABELS = {
    "deepseek-flash": "DeepSeek V4.1 Flash (API), with guard",
    "deepseek-flash-noguard": "DeepSeek V4.1 Flash (API), no guard",
    "qwen3.5-9b": "Qwen3.5-9B Q4_K_M (local), with guard",
    "qwen3.5-4b": "Qwen3.5-4B Q4_K_M (local), with guard",
}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


@app.command()
def report(readme: Path = Path("README.md")) -> None:
    """Render results/summary.json into the README results block."""
    import re

    raw = json.loads((RESULTS / "summary.json").read_text())
    order = [k for k in LABELS if k in raw] + [k for k in raw if k not in LABELS]
    table = {k: raw[k] for k in order}
    lines = ["| Agent | Fixed | Diagnosed | Green but wrong | Tampered | Tool calls | Minutes "
             "| $ / incident | Episodes |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, s in table.items():
        usd = f"${s['usd_per_incident']:.4f}" if s["usd_per_incident"] else "local"
        lines.append(f"| {LABELS.get(name, name)} | **{_pct(s['fixed'])}** | "
                     f"{_pct(s['diagnosed'])} | {_pct(s['green_but_wrong'])} | "
                     f"{_pct(s['tampered'])} | {s['avg_tool_calls']} | {s['avg_minutes']} | "
                     f"{usd} | {s['episodes']} |")
    cats = sorted({c for s in table.values() for c in s["fixed_by_category"]})
    lines += ["", "Fixed, by incident category:", "",
              "| Agent | " + " | ".join(c.replace("_", " ") for c in cats) + " |",
              "|---|" + "---:|" * len(cats)]
    for name, s in table.items():
        lines.append(f"| {LABELS.get(name, name)} | "
                     + " | ".join(s["fixed_by_category"].get(c, "—") for c in cats) + " |")
    block = "<!-- RESULTS:START -->\n\n" + "\n".join(lines) + "\n\n<!-- RESULTS:END -->"
    text = readme.read_text(encoding="utf-8")
    readme.write_text(re.sub(r"<!-- RESULTS:START -->.*?<!-- RESULTS:END -->", block, text,
                             flags=re.S), encoding="utf-8")
    typer.echo("README results updated")


if __name__ == "__main__":
    app()
