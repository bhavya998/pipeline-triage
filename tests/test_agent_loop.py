import json
from pathlib import Path

from pipeline_triage.agent import run_episode
from pipeline_triage.grade import setup
from pipeline_triage.llm import Endpoint, Usage
from pipeline_triage.scenarios import BY_ID


class ScriptedClient:
    """Replays fixed tool calls, standing in for a model."""

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.ep = Endpoint("scripted", "http://unused", "scripted")
        self.usage = Usage()
        self.calls = iter(calls)

    def complete(self, messages, tools, **_):
        name, args = next(self.calls)
        return {"content": "", "tool_calls": [{"id": f"c{name}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_scripted_agent_fixes_typo_incident(tmp_path: Path):
    s = BY_ID["S14"]
    case = setup(s, tmp_path / "s14")
    rel, old, new = s.fix[0]
    fixed_sql = case.ws.read(rel).replace(old, new)
    client = ScriptedClient([
        ("read_file", {"path": rel}),
        ("write_file", {"path": rel, "content": fixed_sql}),
        ("run_dbt", {"command": "build"}),
        ("submit", {"category": "logic_bug", "root_cause": "typo", "fix": "fixed filter"}),
    ])
    ep = run_episode(case, client, max_turns=10)
    assert ep.fixed and ep.category_correct and ep.tool_calls == 4
    assert ep.transcript[2]["result"].startswith("SUCCESS")


def test_guard_flags_repeated_calls(tmp_path: Path):
    case = setup(BY_ID["S14"], tmp_path / "s14g")
    short = {"path": "tests/assert_no_negative_amounts.sql"}
    client = ScriptedClient([("read_file", short)] * 3 + [
        ("submit", {"category": "logic_bug", "root_cause": "x", "fix": "y"})])
    ep = run_episode(case, client, max_turns=10)
    assert "[harness]" not in ep.transcript[0]["result"]
    assert "made this exact call 2 times" in ep.transcript[1]["result"]
    assert not ep.fixed  # nothing was changed, so the build stays red
