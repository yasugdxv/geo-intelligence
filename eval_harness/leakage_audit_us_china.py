# -*- coding: utf-8 -*-
"""Phase I-B0.3 Section 6: Leakage Audit for the US-China Summit / Absence Signal case
(T0=2026-05-12, M016 delegation-list moment, summit itself scheduled 5/14-15)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config_utils  # noqa: E402
import geo_db_client  # noqa: E402
from eval_harness.as_of_evaluation_client import AsOfEvaluationClient  # noqa: E402

T0 = "2026-05-12"
KEYWORDS = ["China", "Trump", "Xi", "summit", "visit", "Hass"]

cfg = config_utils.load_config()
real_client = geo_db_client.SupabaseClient(cfg)
proxy = AsOfEvaluationClient(real_client, T0)

result = {"t0": T0, "keywords": KEYWORDS, "by_keyword": {}}

for kw in KEYWORDS:
    entry = {}
    without = real_client.select("geo_documents", {"select": "document_id,published_at,title", "title": f"ilike.*{kw}*", "limit": "50"})
    with_proxy = proxy.select("geo_documents", {"select": "document_id,published_at,title", "title": f"ilike.*{kw}*", "limit": "50"})
    entry["geo_documents"] = {
        "without_proxy_count": len(without),
        "without_proxy_future_dated": [d for d in without if (d.get("published_at") or "") > T0],
        "with_proxy_count": len(with_proxy),
        "with_proxy_future_leaked": [d for d in with_proxy if (d.get("published_at") or "") > T0],
    }
    without_ev = real_client.select("geo_events", {"select": "event_id,start_at,actor,action,target", "or": f"(actor.ilike.*{kw}*,action.ilike.*{kw}*,target.ilike.*{kw}*)", "limit": "50"})
    with_ev = proxy.select("geo_events", {"select": "event_id,start_at,actor,action,target", "or": f"(actor.ilike.*{kw}*,action.ilike.*{kw}*,target.ilike.*{kw}*)", "limit": "50"})
    entry["geo_events"] = {
        "without_proxy_count": len(without_ev),
        "without_proxy_future_dated": [e for e in without_ev if (e.get("start_at") or "") > T0],
        "with_proxy_count": len(with_ev),
        "with_proxy_future_leaked": [e for e in with_ev if (e.get("start_at") or "") > T0],
    }
    without_ki = real_client.select("geo_knowledge_items", {"select": "knowledge_item_id,title,created_at,as_of_date", "title": f"ilike.*{kw}*", "limit": "50"})
    with_ki = proxy.select("geo_knowledge_items", {"select": "knowledge_item_id,title,created_at,as_of_date", "title": f"ilike.*{kw}*", "limit": "50"})
    entry["geo_knowledge_items"] = {
        "without_proxy_count": len(without_ki),
        "without_proxy_future_created": [k for k in without_ki if (k.get("created_at") or "") > T0],
        "with_proxy_count": len(with_ki),
        "with_proxy_future_leaked": [k for k in with_ki if (k.get("created_at") or "") > T0],
    }
    result["by_keyword"][kw] = entry

result["audit_report"] = proxy.audit_report()

all_leaked = []
any_future_existed = False
for kw, entry in result["by_keyword"].items():
    for table in ("geo_documents", "geo_events", "geo_knowledge_items"):
        all_leaked += entry[table]["with_proxy_future_leaked"]
        future_key = [k for k in entry[table] if k.endswith("future_dated") or k.endswith("future_created")][0]
        if entry[table][future_key]:
            any_future_existed = True

result["PASS"] = (len(all_leaked) == 0) and any_future_existed

out_path = Path(__file__).parent / "leakage_audit_us_china_result.json"
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {out_path}")
print("PASS:", result["PASS"])
for kw, entry in result["by_keyword"].items():
    for table in ("geo_documents", "geo_events", "geo_knowledge_items"):
        print(f"  {kw}/{table}: without={entry[table]['without_proxy_count']} with={entry[table]['with_proxy_count']}")
