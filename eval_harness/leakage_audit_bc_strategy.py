# -*- coding: utf-8 -*-
"""Phase I-B0.1 Section 8: Leakage Audit for the BC Strategy case (T0=2026-02-19).

Confirms the known-contaminating 2026-05/2026-08 Iran documents are excluded through
AsOfEvaluationClient, and are (as previously found) present without it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
import geo_db_client  # noqa: E402
from eval_harness.as_of_evaluation_client import AsOfEvaluationClient  # noqa: E402

cfg = config_utils.load_config()
real_client = geo_db_client.SupabaseClient(cfg)
proxy = AsOfEvaluationClient(real_client, "2026-02-19")

result = {"t0": "2026-02-19"}

# 1. Without proxy: confirm the known future documents ARE retrievable (baseline).
without_proxy = real_client.select("geo_documents", {"select": "document_id,published_at,title", "title": "ilike.*Iran*", "limit": "50"})
result["without_proxy_iran_doc_count"] = len(without_proxy)
result["without_proxy_future_dated"] = [
    {"document_id": d["document_id"], "published_at": d["published_at"], "title": d["title"][:60]}
    for d in without_proxy if (d.get("published_at") or "") > "2026-02-19"
]

# 2. With proxy: the same query must exclude every one of those future-dated rows.
with_proxy = proxy.select("geo_documents", {"select": "document_id,published_at,title", "title": "ilike.*Iran*", "limit": "50"})
result["with_proxy_iran_doc_count"] = len(with_proxy)
result["with_proxy_future_dated_leaked"] = [
    {"document_id": d["document_id"], "published_at": d["published_at"]}
    for d in with_proxy if (d.get("published_at") or "") > "2026-02-19"
]

# 3. Explicit check on the two specific dates called out in the spec.
may_2026 = [d for d in without_proxy if str(d.get("published_at") or "").startswith("2026-05")]
aug_2026 = [d for d in without_proxy if str(d.get("published_at") or "").startswith("2026-08")]
result["2026-05_doc_excluded_without_proxy_count"] = len(may_2026)
result["2026-08_doc_excluded_without_proxy_count"] = len(aug_2026)
may_after = [d for d in with_proxy if str(d.get("published_at") or "").startswith("2026-05")]
aug_after = [d for d in with_proxy if str(d.get("published_at") or "").startswith("2026-08")]
result["2026-05_doc_present_with_proxy"] = len(may_after) > 0
result["2026-08_doc_present_with_proxy"] = len(aug_after) > 0

result["audit_report"] = proxy.audit_report()

result["PASS"] = (
    len(result["with_proxy_future_dated_leaked"]) == 0
    and not result["2026-05_doc_present_with_proxy"]
    and not result["2026-08_doc_present_with_proxy"]
    and len(result["without_proxy_future_dated"]) > 0  # sanity: the risk must have been real
)

out_path = Path(__file__).parent / "leakage_audit_bc_strategy_result.json"
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {out_path}")
print("PASS:", result["PASS"])
print("without_proxy future-dated found:", len(result["without_proxy_future_dated"]))
print("with_proxy future-dated leaked:", len(result["with_proxy_future_dated_leaked"]))
