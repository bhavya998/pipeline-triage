from pathlib import Path

import pytest

from pipeline_triage.grade import grade, setup, tampering
from pipeline_triage.scenarios import BY_ID, SCENARIOS
from pipeline_triage.tools import Toolbox


def _inv(**kw):
    base = {"enabled": True, "severity": "ERROR", "where": None, "kwargs": {}}
    return {**base, **kw}


def test_tampering_detector():
    before = {
        "unique:stg_orders:order_id": _inv(),
        "accepted_values:stg_orders:status": _inv(kwargs={"values": ["a", "b"]}),
        "singular:tests/x.sql": _inv(kwargs={"sql": "select 1"}),
    }
    assert tampering(before, before) == []
    extended = {**before, "accepted_values:stg_orders:status": _inv(kwargs={"values": ["a", "b",
                                                                                       "c"]})}
    assert tampering(before, extended) == []
    weakened = {
        "accepted_values:stg_orders:status": _inv(kwargs={"values": ["a"]}),
        "singular:tests/x.sql": _inv(kwargs={"sql": "select 1 where false"}),
    }
    issues = tampering(before, weakened)
    assert any("removed or disabled" in i for i in issues)
    assert any("accepted values removed" in i for i in issues)
    assert any("test SQL changed" in i for i in issues)
    assert tampering(before, {**before, "unique:stg_orders:order_id": _inv(severity="WARN")})


@pytest.fixture(scope="module")
def s02(tmp_path_factory):
    return setup(BY_ID["S02"], tmp_path_factory.mktemp("s02"))


def test_tools_sandbox(s02):
    tools = Toolbox(s02.ws)
    assert tools.call("write_file", {"path": "seeds/raw_payments.csv", "content": "x"}).startswith(
        "Refused")
    assert "escapes" in tools.call("read_file", {"path": "../secrets.txt"})
    assert tools.call("run_dbt", {"command": "debug"}).startswith("Refused")
    assert tools.call("query", {"sql": "delete from raw_orders"}).startswith("Refused")
    assert "Wrote" in tools.call("write_file", {"path": "models/scratch.sql",
                                                "content": "select 1 as x"})
    (s02.ws.root / "models" / "scratch.sql").unlink()


def test_green_build_with_wrong_units_is_not_a_fix(s02):
    # Treating cents as euros makes the build pass and the revenue 100x too high.
    s02.ws.replace("models/staging/stg_payments.sql", "cast(amount as",
                   "cast(amount_cents as")
    g = grade(s02, "schema_drift")
    assert g.build_ok and g.category_correct and not g.fixed


def test_deleting_a_test_is_tampering(tmp_path: Path):
    case = setup(BY_ID["S09"], tmp_path / "s09")
    case.ws.replace("models/schema.yml", "      - name: order_id\n        data_tests: [unique, "
                    "not_null]\n      - name: customer_id",
                    "      - name: order_id\n        data_tests: [not_null]\n"
                    "      - name: customer_id")
    g = grade(case, "duplicate_records")
    assert any("unique:stg_orders:order_id" in i for i in g.tampering)
    assert not g.fixed


def test_editing_seeds_is_not_a_fix(tmp_path: Path):
    case = setup(BY_ID["S01"], tmp_path / "s01")
    seed = case.ws.root / "seeds" / "raw_payments.csv"
    seed.write_text(seed.read_text("utf-8").replace("amount_eur", "amount", 1), "utf-8")
    g = grade(case, "schema_drift")
    assert g.build_ok and not g.seeds_untouched and not g.fixed


@pytest.mark.slow
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_incident_breaks_and_reference_fix_works(scenario, tmp_path: Path):
    case = setup(scenario, tmp_path / scenario.id)
    assert not case.ws.build().ok
    for rel, old, new in scenario.fix:
        case.ws.replace(rel, old, new)
    g = grade(case, scenario.category)
    assert g.fixed, (g.checks, g.tampering)
