# ADR 001: Design decisions

## Status
Accepted

## 1. Grade outcomes, not transcripts

An agent's summary ("fixed, all tests pass") is not evidence. After every episode the grader
runs a fresh `dbt build --full-refresh` in the agent's workspace and then checks:

1. **build** — every model and test passes;
2. **acceptance** — hidden checks computed in Python from the raw data, independent of the
   dbt models (completed revenue, rows per month, one row per customer with the latest email…);
3. **no tampering** — every original test still exists, is enabled, has severity `error`, has no
   new `where` filter and the same arguments. Extending an `accepted_values` list is allowed:
   when the business adds a status, that *is* the fix;
4. **seeds untouched** — raw data belongs to upstream teams; editing it is not a fix.

`fixed` requires all four. A green build that fails acceptance is reported separately as
*green but wrong* — the failure mode that matters most in production.

## 2. Incidents are mutations of a known-good project

Each incident is a data mutation (what upstream sent) or a code mutation (what a bad commit
changed) applied to a clean project whose build is green. Every incident ships a reference fix,
and the test suite proves for all of them that the build fails as shipped and that the
reference fix passes every check. An incident nobody can solve measures nothing.

## 3. The diagnosis is a closed set

Free-text root causes are hard to grade consistently. The agent picks one of six categories
and also writes its reasoning, which is stored for review but not scored.

## 4. Realistic constraints on the agent

Writes are limited to `models/`, `tests/` and `macros/`. Queries are read-only. dbt commands
are an allow-list. The agent starts where an on-call engineer starts: the ticket and the failing
log, nothing else.

## 5. Same harness for API and local models

The loop speaks the OpenAI tool-calling format, so DeepSeek and a llama.cpp server run through
identical code; cost is computed from the provider's reported token usage.
