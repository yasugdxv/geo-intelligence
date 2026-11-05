# -*- coding: utf-8 -*-
"""Merge pilot-grade IOS identities + Cross-Agent api_keys into the real config.json.
Reads credential tokens from pilot_credentials.txt (scratchpad only, never committed).
Never prints any secret value -- only reports which top-level keys were added/updated."""
import json
from pathlib import Path

CONFIG_PATH = Path(r"c:/Users/269811/python/projects/geopolitical_monitor_dashboard/config.json")
CRED_PATH = Path(__file__).parent / "pilot_credentials.txt"

creds = {}
for line in CRED_PATH.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    name, token = line.split(maxsplit=1)
    creds[name] = token

config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

config.setdefault("geo_ios", {})
config["geo_ios"]["identities"] = [
    {"credential": creds["pilot_approver"], "identity_id": "pilot_approver", "role": "approver"},
    {"credential": creds["pilot_knowledge_steward"], "identity_id": "pilot_knowledge_steward", "role": "knowledge_steward"},
    {"credential": creds["pilot_analyst"], "identity_id": "pilot_analyst", "role": "analyst"},
    {"credential": creds["pilot_expert_reviewer"], "identity_id": "pilot_expert_reviewer", "role": "expert_reviewer"},
    {"credential": creds["pilot_administrator"], "identity_id": "pilot_administrator", "role": "administrator"},
]

config.setdefault("geo_intelligence", {})
config["geo_intelligence"].setdefault("api_keys", [])
existing_keys = config["geo_intelligence"]["api_keys"]
# 既存の文字列型/未設定placeholderエントリを保持しつつ、Phase I-A.1 SEC-3用の実credentialを
# caller_domain付きで追加する（重複防止: 同じcaller_appが既にあれば置き換える）。
existing_keys = [
    e for e in existing_keys
    if not (isinstance(e, dict) and e.get("caller_app") in ("sustainability_intelligence", "erm_intelligence"))
    and not (isinstance(e, str) and "APIキーを入力" in e)
]
existing_keys.append({"key": creds["sustainability_agent"], "caller_app": "sustainability_intelligence", "caller_domain": "sustainability"})
existing_keys.append({"key": creds["erm_agent"], "caller_app": "erm_intelligence", "caller_domain": "erm"})
config["geo_intelligence"]["api_keys"] = existing_keys

CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
print("updated top-level keys: geo_ios.identities (5 entries), geo_intelligence.api_keys (+2 real entries, placeholder/dup entries removed)")
