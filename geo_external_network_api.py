"""External Network v1: 外部コンタクト / 面談ログ API。

権限チェックはすべてサーバー側（geo_external_network_service）で行う。画面側の出し分けは安全境界ではない。
テーブル未作成（SQL未適用）の場合は 503 で「SQL未適用」を返す。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_external_network_service as svc
from geo_ios_identity import verify_ios_identity

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_external_network_router = APIRouter(prefix="/api/geo/external-network")

_MISSING_TABLE_MARKERS = ("PGRST205", "PGRST200", "does not exist", "Could not find the table", "42P01")


def _run(fn, *args, **kwargs):
    """サービス例外をHTTPに変換する。メモ等の本文はエラーに含めない。"""
    try:
        return fn(*args, **kwargs)
    except svc.AccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except svc.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except svc.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except svc.ExternalNetworkError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        response = getattr(exc, "response", None)      # requests.HTTPError は本文（PGRST205等）を response に持つ
        if response is not None:
            try:
                text += " " + (response.text or "")[:500]
            except Exception:  # noqa: BLE001
                pass
        if any(m in text for m in _MISSING_TABLE_MARKERS):
            raise HTTPException(status_code=503, detail="External Network のテーブルが未作成です（sql/2026-10-09_geo_external_network.sql を適用してください）")
        raise HTTPException(status_code=500, detail=f"処理に失敗しました（{type(exc).__name__}）")


class ContactBody(BaseModel):
    name: Optional[str] = None
    affiliation_title: Optional[str] = None
    memo: Optional[str] = None
    owner_ids: Optional[List[str]] = None
    expertise_note: Optional[str] = None          # 任意。登録時に書く専門領域・コメント


class LogPatch(BaseModel):
    contact_ids: Optional[List[str]] = None
    contact_date: Optional[str] = None
    participant_ids: Optional[List[str]] = None
    memo: Optional[str] = None


class DisplayNameBody(BaseModel):
    display_name: str


@geo_external_network_router.put("/users/me/display-name")
def set_display_name(body: DisplayNameBody, identity=Depends(verify_ios_identity)):
    return _run(svc.set_my_display_name, _client, identity, body.display_name)


class EditedSummary(BaseModel):
    edited: dict


@geo_external_network_router.get("/users")
def list_users(identity=Depends(verify_ios_identity)):
    return {"items": _run(svc.list_users, _client, identity), "me": identity.identity_id}


# ---- Contacts ----
@geo_external_network_router.get("/contacts")
def list_contacts(q: Optional[str] = None, owner_id: Optional[str] = None, include_inactive: bool = False,
                  identity=Depends(verify_ios_identity)):
    return {"items": _run(svc.list_contacts, _client, identity, q=q, owner_id=owner_id, include_inactive=include_inactive)}


@geo_external_network_router.post("/contacts")
def create_contact(body: ContactBody, identity=Depends(verify_ios_identity)):
    return _run(svc.create_contact, _client, identity, name=body.name, affiliation_title=body.affiliation_title,
                memo=body.memo, owner_ids=body.owner_ids, expertise_note=body.expertise_note)


@geo_external_network_router.get("/contacts/{contact_id}")
def get_contact(contact_id: str, identity=Depends(verify_ios_identity)):
    contact = _run(svc.get_contact, _client, identity, contact_id)
    return {**contact, "logs": _run(svc.logs_for_contact, _client, identity, contact_id)}


@geo_external_network_router.patch("/contacts/{contact_id}")
def update_contact(contact_id: str, body: ContactBody, identity=Depends(verify_ios_identity)):
    return _run(svc.update_contact, _client, identity, contact_id, name=body.name,
                affiliation_title=body.affiliation_title, memo=body.memo, owner_ids=body.owner_ids,
                expertise_note=body.expertise_note)


@geo_external_network_router.post("/contacts/{contact_id}/deactivate")
def deactivate_contact(contact_id: str, identity=Depends(verify_ios_identity)):
    return _run(svc.deactivate_contact, _client, identity, contact_id)


# ---- Logs ----
@geo_external_network_router.get("/logs")
def list_logs(contact_id: Optional[str] = None, participant_id: Optional[str] = None, date_from: Optional[str] = None,
              date_to: Optional[str] = None, q: Optional[str] = None, identity=Depends(verify_ios_identity)):
    return {"items": _run(svc.list_logs, _client, identity, contact_id=contact_id, participant_id=participant_id,
                          date_from=date_from, date_to=date_to, q=q)}


@geo_external_network_router.post("/logs")
async def create_log(contact_ids: List[str] = Form(default=[]), contact_date: Optional[str] = Form(default=None),
                     participant_ids: List[str] = Form(default=[]), memo: Optional[str] = Form(default=None),
                     files: List[UploadFile] = File(default=[]), identity=Depends(verify_ios_identity)):
    loaded = []
    for f in files:
        if not f.filename:
            continue
        raw = await f.read(svc.MAX_FILE_BYTES + 1)            # 上限を超える分は読まない
        loaded.append({"filename": f.filename, "raw": raw, "mime": f.content_type})
    return _run(svc.create_log, _client, identity, contact_ids=contact_ids, contact_date=contact_date,
                participant_ids=participant_ids or None, memo=memo, files=loaded)


@geo_external_network_router.get("/logs/{log_id}")
def get_log(log_id: str, identity=Depends(verify_ios_identity)):
    return _run(svc.get_log, _client, identity, log_id)


@geo_external_network_router.patch("/logs/{log_id}")
def update_log(log_id: str, body: LogPatch, identity=Depends(verify_ios_identity)):
    return _run(svc.update_log, _client, identity, log_id, contact_ids=body.contact_ids, contact_date=body.contact_date,
                participant_ids=body.participant_ids, memo=body.memo)


@geo_external_network_router.delete("/logs/{log_id}")
def delete_log(log_id: str, identity=Depends(verify_ios_identity)):
    _run(svc.delete_log, _client, identity, log_id)
    return {"ok": True}


# ---- Attachments ----
@geo_external_network_router.post("/logs/{log_id}/attachments")
async def add_attachment(log_id: str, file: UploadFile = File(...), identity=Depends(verify_ios_identity)):
    raw = await file.read(svc.MAX_FILE_BYTES + 1)
    return _run(svc.add_attachment, _client, identity, log_id, filename=file.filename or "file", raw=raw, mime=file.content_type)


@geo_external_network_router.get("/logs/{log_id}/attachments/{attachment_id}")
def download_attachment(log_id: str, attachment_id: str, identity=Depends(verify_ios_identity)):
    info = _run(svc.get_attachment_file, _client, identity, log_id, attachment_id)
    # 添付は常にダウンロード扱い（ブラウザ内で実行・表示させない）
    return FileResponse(info["path"], filename=info["filename"], media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@geo_external_network_router.delete("/logs/{log_id}/attachments/{attachment_id}")
def delete_attachment(log_id: str, attachment_id: str, identity=Depends(verify_ios_identity)):
    _run(svc.delete_attachment, _client, identity, log_id, attachment_id)
    return {"ok": True}


# ---- AI整理 ----
@geo_external_network_router.post("/logs/{log_id}/ai-summary/run")
def run_ai_summary(log_id: str, identity=Depends(verify_ios_identity)):
    return _run(svc.run_ai_summary, _client, _azure_client, _model, identity, log_id)


@geo_external_network_router.put("/logs/{log_id}/ai-summary")
def save_ai_edit(log_id: str, body: EditedSummary, identity=Depends(verify_ios_identity)):
    return _run(svc.save_ai_edit, _client, identity, log_id, body.edited)


@geo_external_network_router.post("/logs/{log_id}/ai-summary/adopt-ai")
def adopt_ai(log_id: str, identity=Depends(verify_ios_identity)):
    return _run(svc.adopt_ai_version, _client, identity, log_id)
