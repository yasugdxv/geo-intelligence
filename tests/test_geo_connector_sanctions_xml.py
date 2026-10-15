"""GEO_DIRECT_COLLECTION_V1: 制裁リスト（UN/UK）Collectorのテスト。Baseline→Diffの分離、Signal爆発防止を確認する。"""
import types

import pytest

import geo_connector_base as base
import geo_connector_sanctions_xml as sx
import geo_direct_collection_common as common

UN_XML = b"""<?xml version="1.0"?><CONSOLIDATED_LIST dateGenerated="2026-10-03T23:00:03.428Z">
<INDIVIDUALS>
<INDIVIDUAL><DATAID>1</DATAID><VERSIONNUM>1</VERSIONNUM><FIRST_NAME>ERIC</FIRST_NAME><SECOND_NAME>BADEGE</SECOND_NAME>
<UN_LIST_TYPE>DRC</UN_LIST_TYPE><REFERENCE_NUMBER>CDi.001</REFERENCE_NUMBER><LISTED_ON>2012-12-31</LISTED_ON>
<NATIONALITY><VALUE>Congo</VALUE></NATIONALITY><INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>E. Badege</ALIAS_NAME></INDIVIDUAL_ALIAS></INDIVIDUAL>
<INDIVIDUAL><DATAID>2</DATAID><FIRST_NAME>ANNA</FIRST_NAME><UN_LIST_TYPE>XX</UN_LIST_TYPE><REFERENCE_NUMBER>X.1</REFERENCE_NUMBER></INDIVIDUAL>
</INDIVIDUALS><ENTITIES>
<ENTITY><DATAID>3</DATAID><FIRST_NAME>ADF</FIRST_NAME><UN_LIST_TYPE>DRC</UN_LIST_TYPE><REFERENCE_NUMBER>CDe.001</REFERENCE_NUMBER>
<ENTITY_ALIAS><ALIAS_NAME>Allied</ALIAS_NAME></ENTITY_ALIAS></ENTITY></ENTITIES></CONSOLIDATED_LIST>"""

UK_XML = b"""<?xml version="1.0"?><Designations><DateGenerated>02/10/2026</DateGenerated>
<Designation><UniqueID>AFG0001</UniqueID><DateDesignated>29/06/2012</DateDesignated><RegimeName>Afghanistan</RegimeName>
<IndividualEntityShip>Entity</IndividualEntityShip><Names><Name><Name6>HAJI KHAIRULLAH</Name6><NameType>Primary Name</NameType></Name>
<Name><Name6>Haji Alim</Name6><NameType>Alias</NameType></Name></Names></Designation></Designations>"""


def test_parse_un_and_uk():
    recs, gen = sx.parse_un_xml(UN_XML)
    assert gen == "2026-10-03T23:00:03.428Z" and [r["external_record_id"] for r in recs] == ["1", "2", "3"]
    assert recs[0]["entity_name"] == "ERIC BADEGE" and recs[0]["aliases"] == ["E. Badege"] and recs[0]["nationality"] == "Congo"
    assert recs[0]["program"] == "DRC / CDi.001" and recs[2]["kind"] == "entity" and recs[2]["aliases"] == ["Allied"]
    uk, gen = sx.parse_uk_xml(UK_XML)
    assert gen == "2026-10-02" and uk[0]["entity_name"] == "HAJI KHAIRULLAH" and uk[0]["aliases"] == ["Haji Alim"]
    assert uk[0]["list_date"] == "2012-06-29" and uk[0]["program"] == "Afghanistan"


def test_record_hash_changes_when_content_changes():
    a, _ = sx.parse_un_xml(UN_XML)
    b, _ = sx.parse_un_xml(UN_XML.replace(b"Good", b"Good").replace(b"E. Badege", b"E. Badege2"))
    assert a[0]["record_hash"] != b[0]["record_hash"] and a[1]["record_hash"] == b[1]["record_hash"]


def test_diff_records_classifies_added_amended_relisted_delisted():
    recs = [{"external_record_id": x, "record_hash": h} for x, h in (("1", "a"), ("2", "CHANGED"), ("3", "c"), ("5", "e"))]
    existing = {"1": {"status": "active", "hash": "a"}, "2": {"status": "active", "hash": "b"},
                "3": {"status": "delisted", "hash": "c"}, "4": {"status": "active", "hash": "d"}}
    d = sx.diff_records(existing, recs)
    assert [r["external_record_id"] for r in d["added"]] == ["5"] and [r["external_record_id"] for r in d["amended"]] == ["2"]
    assert [r["external_record_id"] for r in d["relisted"]] == ["3"] and d["delisted"] == ["4"] and d["unchanged"] == 1


class MemDb:
    """Entry/Version/Signalをメモリに保持する最小の疑似DB（Collectorが使う呼び出しだけ）。"""
    def __init__(self):
        self.entries, self.versions, self.signals, self.snapshots = {}, {}, [], {}

    def insert(self, table, rows, prefer="return=representation"):
        if table == "geo_sanctions_entries":
            for r in rows:
                self.entries[r["sanctions_entry_id"]] = {**self.entries.get(r["sanctions_entry_id"], {}), **r}
        elif table == "geo_sanctions_entry_versions":
            for r in rows:
                self.versions[r["version_id"]] = dict(r)
        return rows

    def update(self, table, params, patch):
        key = next(iter(params.values())).removeprefix("eq.")
        {"geo_sanctions_entries": self.entries, "geo_sanctions_entry_versions": self.versions}.get(table, {}).get(key, {}).update(patch)

    def select(self, table, params):
        if table == "geo_sanctions_entries":
            return [e for e in self.entries.values() if e["source_id"] == params["source_id"].removeprefix("eq.")]
        if table == "geo_sanctions_entry_versions":
            ids = params["version_id"].removeprefix("in.(").rstrip(")").split(",")
            return [self.versions[i] for i in ids if i in self.versions]
        return []


CFG = {"sanctions_lists": {"un": {"registry_domain": "scsanctions.un.org", "url": "https://un/xml", "format": "un_xml", "language": "en"}}}
SRC = {"source_id": "S1", "name": "国連安保理 統合制裁リスト（UN）", "url": "https://scsanctions.un.org/", "automation_allowed": True,
       "connector_enabled": True, "credential_configured": True}


def _setup(monkeypatch, db, xml):
    seen = db.snapshots
    monkeypatch.setattr(common, "resolve_source", lambda c, d: SRC)
    monkeypatch.setattr(base, "create_ingestion_run", lambda c, **k: {"ingestion_run_id": "run"})
    monkeypatch.setattr(base, "link_run_to_snapshot", lambda c, **k: {})
    monkeypatch.setattr(common, "finish_run", lambda *a, **k: None)
    monkeypatch.setattr(sx.geo_audit, "log_audit", lambda *a, **k: None)

    def snap(c, *, raw_bytes, **k):
        h = __import__("hashlib").sha256(raw_bytes).hexdigest()
        is_new = h not in seen
        seen[h] = True
        return {"raw_snapshot_id": h[:8], "content_hash": h}, is_new
    monkeypatch.setattr(base, "find_or_save_raw_snapshot", snap)
    monkeypatch.setattr(sx.signal_service, "bulk_upsert_signals",
                        lambda c, sigs, **k: db.signals.extend(sigs) or {"inserted": len(sigs), "updated": 0})
    fetch = lambda url: types.SimpleNamespace(content=xml[0], status_code=200, headers={"Content-Type": "text/xml"})
    return fetch


def test_baseline_creates_no_signals_then_unchanged_then_diff_creates_one_summary(monkeypatch):
    db, xml = MemDb(), [UN_XML]
    fetch = _setup(monkeypatch, db, xml)
    r1 = sx.run_sanctions_collection(db, list_key="un", config=CFG, fetch=fetch)
    assert r1["mode"] == "baseline" and r1["inserted_count"] == 3 and r1["signal_created"] is False and db.signals == []
    assert len(db.entries) == 3 and all(e["latest_version_id"] in db.versions for e in db.entries.values())
    r2 = sx.run_sanctions_collection(db, list_key="un", config=CFG, fetch=fetch)       # 同一XML: 何もしない
    assert r2["mode"] == "unchanged" and db.signals == [] and len(db.versions) == 3
    # 1件追加・1件変更・1件削除
    new = UN_XML.replace(b"E. Badege", b"E. Badege X").replace(
        b"</INDIVIDUALS>", b"<INDIVIDUAL><DATAID>9</DATAID><FIRST_NAME>NEW</FIRST_NAME><UN_LIST_TYPE>Z</UN_LIST_TYPE></INDIVIDUAL></INDIVIDUALS>")
    new = new.replace(b"<INDIVIDUAL><DATAID>2</DATAID><FIRST_NAME>ANNA</FIRST_NAME><UN_LIST_TYPE>XX</UN_LIST_TYPE><REFERENCE_NUMBER>X.1</REFERENCE_NUMBER></INDIVIDUAL>", b"")
    xml[0] = new
    r3 = sx.run_sanctions_collection(db, list_key="un", config=CFG, fetch=fetch)
    assert (r3["mode"], r3["added"], r3["amended"], r3["delisted"]) == ("diff", 1, 1, 1)
    assert len(db.signals) == 1 and "追加1件" in db.signals[0]["title"] and db.signals[0]["provider"] == "official"
    st = {e["external_record_id"]: e["current_status"] for e in db.entries.values()}
    assert st["2"] == "delisted" and st["9"] == "active" and st["1"] == "active"
    r4 = sx.run_sanctions_collection(db, list_key="un", config=CFG, fetch=fetch)       # 差分適用後の再実行: 重複Signalなし
    assert r4["mode"] == "unchanged" and len(db.signals) == 1


def test_gate_blocks_unapproved_source(monkeypatch):
    db = MemDb()
    fetch = _setup(monkeypatch, db, [UN_XML])
    monkeypatch.setattr(common, "resolve_source", lambda c, d: {**SRC, "automation_allowed": False})
    with pytest.raises(base.ConnectorDisabledError):
        sx.run_sanctions_collection(db, list_key="un", config=CFG, fetch=fetch)
    assert db.entries == {}


def test_un_date_with_timezone_suffix_is_normalized():
    xml = UN_XML.replace(b"<LISTED_ON>2012-12-31</LISTED_ON>", b"<LISTED_ON>2015-07-01-04:00</LISTED_ON>")
    assert sx.parse_un_xml(xml)[0][0]["list_date"] == "2015-07-01"
    assert sx._date_prefix("garbage") is None and sx._date_prefix("2015-13-40") is None


def test_interrupted_baseline_is_resumed_without_duplicates(monkeypatch):
    """Entryだけ作られてversionが無い（途中失敗）状態から、再実行でversionを補完し、重複Entryや偽の差分Signalを作らない。"""
    db, xml = MemDb(), [UN_XML]
    fetch = _setup(monkeypatch, db, xml)
    recs, _ = sx.parse_un_xml(UN_XML)
    for i, r in enumerate(recs):                      # Entryのみ作成（latest_version_idなし）
        db.entries[f"e{i}"] = {"sanctions_entry_id": f"e{i}", "source_id": "S1", "external_record_id": r["external_record_id"],
                               "latest_version_id": None, "current_status": "active"}
    db.snapshots["x"] = True
    r = sx.run_sanctions_collection(db, list_key="un", config=CFG, fetch=fetch)
    assert r["healed_entries"] == 3 and len(db.entries) == 3 and len(db.versions) == 3 and db.signals == []
    assert all(e["latest_version_id"] in db.versions for e in db.entries.values())
    assert sx.run_sanctions_collection(db, list_key="un", config=CFG, fetch=fetch)["mode"] == "unchanged"
