# -*- coding: utf-8 -*-
"""Phase 6A-0/6B: Gold Case Evaluation Isolation.

GoldCaseEvaluationIsolationClient is a READ-ONLY-for-one-table, evaluation-only proxy
that wraps a real geo_db_client.SupabaseClient and blocks a single table
(geo_case_hypotheses) from being read, before delegating everything else to the real
client unchanged.

THIS IS NOT PRODUCTION CODE, and it does not change Production Behavior. It exists so
that Quality Evaluation (Gold Case regression, blind validation, etc.) can re-run the
REAL, unmodified production pipeline (geo_expert_engine.run_expert_request_staged)
without a single line of production retrieval/generation logic changed, while
guaranteeing that Call1 never sees case-specific hypothesis text that was pre-populated
into a Case's record before this evaluation run (e.g. by a pilot/development seed
script, a prior benchmark setup, or a human analyst's prior working notes on that same
Case) -- text that would otherwise leak "the expected answer" into the model's own
input and invalidate an independent measurement of its Hypothesis Generation capability.

Why this is scoped to exactly one table (geo_case_hypotheses), not a general "no case
history at all" policy:
  - geo_expert_engine.build_case_context() -> geo_retrieval_service.get_case_context()
    is the ONLY place a Case's own prior "existing hypotheses" can reach Call1's
    user_prompt (rendered as the "【既存の仮説】" section). Confirmed by direct code
    trace 2026-09-19 (see docs/... PHASE_6A_RUNTIME_INTEGRITY_REPAIR_V1.md).
  - geo_cases.final_output / geo_expert_responses are only ever WRITE targets inside
    run_expert_request_staged (insert/update) -- confirmed by repo-wide grep, they are
    never read back into any Call1 prompt-construction path. There is no
    "previous model output leaks back into Call1" path to isolate today.
  - No "benchmark_gold" / "expected_mechanism" table exists in this schema; those
    concepts live only in this project's own markdown design docs, which production
    code never queries. Nothing to isolate there either.
  - Foundation/Reusable/Contested Context, Claims, Knowledge Gaps, Source Pack
    documents/events, Actor/Relationship/Media Intelligence are all legitimate
    as-of-safe Evidence/Foundation Context and are explicitly NOT blocked -- isolating
    those would make this a different (and wrong) thing: a data-poverty test, not an
    independent-reasoning test.

Production Behavior is NOT changed by this module. geo_case_hypotheses / Case Memory
continuity (a case's own accumulated working hypotheses feeding forward into later
analysis of the same case) remains a legitimate, unmodified Production feature -- real
users still see it. This proxy is opt-in, evaluation-only, and never imported by
production code.
"""

_ISOLATED_TABLE = "geo_case_hypotheses"


class GoldCaseEvaluationIsolationClient:
    """Wraps a real client. Every method delegates to the wrapped client unchanged,
    except .select(table=="geo_case_hypotheses", ...), which returns [] without ever
    reaching the database (so an evaluation run cannot depend on -- or be accused of
    depending on -- filtering results client-side after a real fetch)."""

    def __init__(self, real_client):
        self._real_client = real_client

    def select(self, table, params=None):
        if table == _ISOLATED_TABLE:
            return []
        return self._real_client.select(table, params)

    def __getattr__(self, name):
        # Any method/attribute this proxy does not explicitly override (insert, update,
        # delete, rpc, etc.) passes straight through to the real client.
        return getattr(self._real_client, name)


def run_expert_request_staged_gold_isolated(engine_module, client, azure_client, model, mode,
                                             theme_id, query_text, draft_text=None, *,
                                             case_id=None, profile="standard", **kwargs):
    """Runs engine_module.run_expert_request_staged (the real, unmodified production
    Reasoning Path) with `client` wrapped in GoldCaseEvaluationIsolationClient.

    engine_module: pass the imported geo_expert_engine module (not imported here, to
    keep this file dependency-free / importable from a plain evaluation script without
    pulling in the full production module graph unless the caller wants to).
    """
    isolated_client = GoldCaseEvaluationIsolationClient(client)
    return engine_module.run_expert_request_staged(
        isolated_client, azure_client, model, mode, theme_id, query_text, draft_text,
        case_id=case_id, profile=profile, **kwargs)
