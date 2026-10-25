"""A disposable copy of the shopflow dbt project: seeds, dbt runs, read-only queries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

from pipeline_triage.data import Table, to_csv

TEMPLATE = Path(__file__).parent / "warehouse"
DBT = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")


@dataclass
class DbtResult:
    ok: bool
    output: str


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def create(cls, root: Path, tables: dict[str, Table]) -> Workspace:
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(TEMPLATE, root, ignore=shutil.ignore_patterns("target", "*.duckdb",
                                                                      "logs", "dbt_packages"))
        (root / "seeds").mkdir(exist_ok=True)
        for name, table in tables.items():
            (root / "seeds" / f"{name}.csv").write_text(to_csv(table), encoding="utf-8")
        return cls(root)

    # --- files --------------------------------------------------------------------------
    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def replace(self, rel: str, old: str, new: str) -> None:
        text = self.read(rel)
        if old not in text:
            raise ValueError(f"{rel}: text to replace not found: {old[:60]!r}")
        self.write(rel, text.replace(old, new, 1))

    def files(self) -> list[str]:
        skip = {"target", "logs", "dbt_packages"}
        out = []
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root)
            if p.is_file() and not (set(rel.parts) & skip) and p.suffix != ".duckdb":
                out.append(rel.as_posix())
        return out

    # --- dbt ----------------------------------------------------------------------------
    def dbt(self, *args: str, timeout: int = 300) -> DbtResult:
        env = {**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "NO_COLOR": "1",
               "DBT_USE_COLORS": "false"}
        proc = subprocess.run(
            [str(DBT), *args, "--project-dir", ".", "--profiles-dir", "."],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env,
        )
        return DbtResult(proc.returncode == 0, proc.stdout + proc.stderr)

    def build(self) -> DbtResult:
        return self.dbt("build", "--full-refresh")

    # --- data ---------------------------------------------------------------------------
    def query(self, sql: str, limit: int = 50) -> list[tuple]:
        conn = duckdb.connect(str(self.root / "warehouse.duckdb"), read_only=True)
        try:
            return conn.execute(sql).fetchmany(limit)
        finally:
            conn.close()

    def scalar(self, sql: str):
        rows = self.query(sql, 1)
        return rows[0][0] if rows else None

    # --- test inventory -----------------------------------------------------------------
    def test_inventory(self) -> dict[str, dict]:
        """Every data test after `dbt parse`, keyed by a stable identity that survives edits
        to its arguments: (test type, model, column) for generic tests, file for singular."""
        res = self.dbt("parse")
        if not res.ok:
            raise RuntimeError("dbt parse failed:\n" + res.output[-2000:])
        manifest = json.loads((self.root / "target" / "manifest.json").read_text("utf-8"))
        inventory: dict[str, dict] = {}
        for node in manifest["nodes"].values():
            if node["resource_type"] != "test":
                continue
            meta = node.get("test_metadata") or {}
            config = node.get("config", {})
            if meta:
                model = (node.get("attached_node") or "").split(".")[-1]
                key = f"{meta['name']}:{model}:{node.get('column_name')}"
                kwargs = meta.get("kwargs", {})
            else:
                key = f"singular:{node['original_file_path'].replace(chr(92), '/')}"
                kwargs = {"sql": node.get("raw_code", "")}
            inventory[key] = {
                "enabled": config.get("enabled", True),
                "severity": str(config.get("severity", "ERROR")).upper(),
                "where": config.get("where"),
                "kwargs": kwargs,
            }
        return inventory
