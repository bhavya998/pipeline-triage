"""The agent's tools. Writes are confined to models/, tests/ and macros/; seeds are read-only."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePosixPath
from typing import Any

from pipeline_triage.scenarios import CATEGORIES
from pipeline_triage.workspace import Workspace

WRITABLE = ("models/", "tests/", "macros/")
DBT_COMMANDS = {"build", "run", "test", "compile", "seed", "parse", "ls", "show"}
_KEEP = re.compile(r"ERROR|FAIL|WARN|Error|error|Done\.|Got \d+ result|Compilation|Database|"
                   r"Runtime|not found|does not exist|Conversion|Binder|Parser|column|"
                   r"Completed|Nothing to do", re.I)


def _string(desc: str) -> dict[str, str]:
    return {"type": "string", "description": desc}


SPECS: list[dict[str, Any]] = [
    {"name": "list_files", "description": "List the files in the dbt project.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "read_file", "description": "Read a project file.",
     "parameters": {"type": "object",
                    "properties": {"path": _string("e.g. models/marts/fct_orders.sql")},
                    "required": ["path"]}},
    {"name": "write_file",
     "description": "Overwrite a file under models/, tests/ or macros/ with new full content.",
     "parameters": {"type": "object", "properties": {"path": _string("file path"),
                                                     "content": _string("complete new content")},
                    "required": ["path", "content"]}},
    {"name": "run_dbt",
     "description": "Run a dbt command, e.g. 'build', 'build --select stg_orders+', "
                    "'test --select fct_orders', 'compile'. Returns the relevant log lines.",
     "parameters": {"type": "object", "properties": {"command": _string("dbt arguments")},
                    "required": ["command"]}},
    {"name": "query",
     "description": "Run a read-only DuckDB SQL query on the warehouse. Raw tables: raw_customers, "
                    "raw_orders, raw_payments; models are tables/views with their model names.",
     "parameters": {"type": "object", "properties": {"sql": _string("SQL")}, "required": ["sql"]}},
    {"name": "submit", "description": "Finish: report the root cause category and what you fixed.",
     "parameters": {"type": "object", "properties": {
         "category": {"type": "string", "enum": list(CATEGORIES)},
         "root_cause": _string("one or two sentences"),
         "fix": _string("what you changed and why")},
         "required": ["category", "root_cause", "fix"]}},
]


_NOISE = re.compile(r" START | PASS | OK created | OK loaded ")


def condense(log: str, limit: int = 3500) -> str:
    """Failures, errors and the summary line — not the per-node progress."""
    lines = [ln for ln in log.splitlines() if _KEEP.search(ln) and not _NOISE.search(ln)]
    text = "\n".join(lines[-80:]) or log[-limit:]
    return text[-limit:]


class Toolbox:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.submission: dict[str, str] | None = None

    def _safe(self, path: str) -> str:
        rel = PurePosixPath(path.replace("\\", "/").lstrip("/"))
        if ".." in rel.parts:
            raise ValueError("path escapes the project")
        return rel.as_posix()

    def call(self, name: str, args: dict[str, Any]) -> str:
        try:
            return getattr(self, f"_{name}")(**args)
        except (TypeError, AttributeError) as exc:
            return f"Error: bad tool call {name}({json.dumps(args)[:200]}): {exc}"
        except Exception as exc:  # tool errors are observations, not crashes
            return f"Error: {type(exc).__name__}: {str(exc)[:500]}"

    def _list_files(self) -> str:
        return "\n".join(self.ws.files())

    def _read_file(self, path: str) -> str:
        return self.ws.read(self._safe(path))[:8000]

    def _write_file(self, path: str, content: str) -> str:
        rel = self._safe(path)
        if not rel.startswith(WRITABLE):
            return f"Refused: only files under {', '.join(WRITABLE)} may be edited."
        self.ws.write(rel, content)
        return f"Wrote {rel} ({len(content)} chars)."

    def _run_dbt(self, command: str) -> str:
        args = shlex.split(command.replace("dbt ", "", 1) if command.startswith("dbt ")
                           else command)
        if not args or args[0] not in DBT_COMMANDS:
            return f"Refused: allowed commands are {', '.join(sorted(DBT_COMMANDS))}."
        res = self.ws.dbt(*args)
        return ("SUCCESS\n" if res.ok else "FAILED\n") + condense(res.output)

    def _query(self, sql: str) -> str:
        if not re.match(r"\s*(select|with|describe|show|summarize|pragma)\b", sql, re.I):
            return "Refused: read-only queries only."
        if ";" in sql.strip().rstrip(";"):
            return "Refused: one statement per query."
        rows = self.ws.query(sql, limit=30)
        return "\n".join(" | ".join(str(v) for v in row) for row in rows) or "(no rows)"

    def _submit(self, category: str, root_cause: str, fix: str) -> str:
        self.submission = {"category": category, "root_cause": root_cause, "fix": fix}
        return "Submitted."
