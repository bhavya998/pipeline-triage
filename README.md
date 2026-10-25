<div align="center">

# pipeline-triage

**An LLM agent on call for a dbt warehouse: it reads the failing build, queries the raw data, patches the models and proves the fix — graded on what it leaves behind, not on what it says.**

[![CI](https://github.com/bhavya998/pipeline-triage/actions/workflows/ci.yml/badge.svg)](https://github.com/bhavya998/pipeline-triage/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![dbt](https://img.shields.io/badge/dbt-1.12%20%2B%20DuckDB-FF694B?logo=dbt&logoColor=white)](https://docs.getdbt.com)
[![License](https://img.shields.io/badge/License-MIT-22c55e)](LICENSE)

</div>

---

## Results

<!-- RESULTS:START -->
<!-- RESULTS:END -->

## The incidents

A clean dbt project (`shopflow`: customers, orders, payments → staging → marts, 25 tests,
two reconciliation tests) and 16 incidents that break it the way production pipelines break:

| | Category | What happened |
|---|---|---|
| S01 | schema drift | `payments.amount` renamed to `amount_eur` |
| S02 | schema drift | `amount` replaced by integer `amount_cents` |
| S03 | schema drift | `full_name` split into `first_name` / `last_name` |
| S04 | format change | a batch of orders arrives with `DD/MM/YYYY` dates |
| S05 | format change | a new payment provider sends `€12,50` |
| S06 | format change | a batch of orders has upper-case statuses |
| S07 | new category value | partial returns launched: a new order status |
| S08 | new category value | Apple Pay launched: a new payment method |
| S09 | duplicate records | an orders batch was delivered twice |
| S10 | duplicate records | payment rows re-sent |
| S11 | duplicate records | customer updates arrive as new rows |
| S12 | referential integrity | test payments for orders that do not exist |
| S13 | logic bug | payments joined without aggregation (fan-out) |
| S14 | logic bug | typo in the revenue filter |
| S15 | logic bug | lifetime value counts every order |
| S16 | logic bug | customers joined on the wrong key |

Every incident ships with a reference fix, and the test suite proves each one fails as shipped
and passes every check once fixed.

## How an episode is graded

```
 ticket + failing build log ─► agent ─► tools: list/read/write files · run dbt · read-only SQL · submit
                                              (writes only under models/ tests/ macros/)
 after submit:
   dbt build --full-refresh          → build green?
   hidden acceptance checks          → numbers right? (computed in Python from the raw data)
   test inventory vs. original       → any test removed, disabled, downgraded, filtered?
   seed hash vs. original            → raw data untouched?
   submitted category                → diagnosis right?
 fixed = green ∧ acceptance ∧ no tampering ∧ seeds untouched
```

*Green but wrong* — the build passes and the numbers do not — is counted separately. It is the
failure an on-call engineer most needs to catch.

## Reproduce

```bash
uv sync
uv run pipeline-triage check                         # every incident breaks, every reference fix passes
uv run pipeline-triage run deepseek-flash --repeats 3   # needs DEEPSEEK_API_KEY
uv run pipeline-triage run qwen3.5-4b                   # llama-server on :8091
uv run pipeline-triage summary
uv run pytest -q                                      # includes the slow per-incident checks
```

`results/*.jsonl` holds every episode with its full tool transcript.

## Layout

```
src/pipeline_triage/
  warehouse/   the clean dbt project (models, schema tests, reconciliation tests)
  data.py      deterministic raw data
  scenarios.py incidents: mutation, category, reference fix, acceptance checks
  workspace.py disposable project copy: dbt runs, read-only queries, test inventory
  tools.py     the agent's sandboxed tools
  agent.py     the tool-calling loop (+ optional repeat/turn-budget guard)
  grade.py     setup and grading
  bench.py     parallel runs, JSONL results, summaries
docs/adr/      design decisions
```

## License

MIT
