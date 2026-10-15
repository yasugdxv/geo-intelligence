"""地政学インテリジェンス基盤: 文書（アップロードファイル）の保存・本文抽出。

article_crawler.py の SupabaseClient・PDF/DOCX抽出ライブラリ（pypdf/trafilatura/docx）を
そのまま再利用する（既存ファイルへの変更は一切行わない）。Phase1は手動アップロードのみを
対象とし、URLからの自動取得（connector）はPhase2で追加する。

保存項目は指示書8.2章に準拠: URL・canonical URL・取得日時・タイトル・原文・抽出本文・言語・
content hash・MIME type・原本保存先・取得成功/失敗・HTTP status・parser versionをgeo_documents
へ保持する。原文を捨ててAI要約だけを保存することはしない（extracted_textは本文そのもの）。
"""

import hashlib
import io
from pathlib import Path
from urllib.parse import urlparse

import requests
import trafilatura
from docx import Document as DocxDocument
from pypdf import PdfReader

from config_utils import upload_base

PARSER_VERSION = "geo-1.0"
UPLOAD_ROOT = upload_base() / "geo"
REQUEST_TIMEOUT = 30
# article_crawler.pyと同じヘッダー（実ブラウザ相当のUser-Agent/Accept/Accept-Language）。
# 自己申告のbot UAだと一部の政府サイトのWAFに弾かれる（台湾大陸委員会サイトで403を確認済み）
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


def compute_content_hash(raw_bytes: bytes) -> str:
    """article_crawler.py の content_hash と同じ方式（生バイト列のsha256）"""
    return hashlib.sha256(raw_bytes).hexdigest()


def _detect_kind(filename: str, mime_type: str = "") -> str:
    lower = (filename or "").lower()
    if lower.endswith(".pdf") or mime_type == "application/pdf":
        return "pdf"
    if lower.endswith(".docx") or mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "docx"
    if lower.endswith((".html", ".htm")) or mime_type in ("text/html",):
        return "html"
    return "text"


def _decode_text(raw_bytes: bytes) -> str:
    """アップロードファイルはHTTPレスポンスと違いCharsetヘッダーが無いため、
    utf-8を優先しつつ日本語でよくあるエンコーディングへ順にフォールバックする"""
    for encoding in ("utf-8", "cp932", "euc-jp"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def extract_text(raw_bytes: bytes, filename: str, mime_type: str = "") -> dict:
    """アップロードされた文書から title/text/language/published_at を抽出する。
    published_at（HTML限定、trafilatura with_metadata=Trueのpage metadata/可視日付
    表記からの解決）は取得できなければNone——URLや現在時刻からの推測は行わない。
    戻り値: {"title": str, "text": str, "language": str | None, "published_at": str | None,
    "ok": bool, "error": str | None}"""
    kind = _detect_kind(filename, mime_type)
    published_at = None
    try:
        if kind == "pdf":
            reader = PdfReader(io.BytesIO(raw_bytes))
            meta = reader.metadata
            title = (getattr(meta, "title", None) or "").strip() if meta else ""
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        elif kind == "docx":
            doc = DocxDocument(io.BytesIO(raw_bytes))
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            title = (doc.core_properties.title or "").strip() or (paragraphs[0] if paragraphs else "")
            text = "\n\n".join(paragraphs).strip()
        elif kind == "html":
            html = _decode_text(raw_bytes)
            json_str = trafilatura.extract(html, output_format="json", with_metadata=True, favor_recall=True)
            if not json_str:
                return {"ok": False, "error": "本文抽出に失敗（対象外ページの可能性）", "title": "", "text": "", "language": None, "published_at": None}
            import json as _json
            data = _json.loads(json_str)
            title = data.get("title") or ""
            text = (data.get("text") or "").strip()
            # trafilatura with_metadata=Trueはpage metadata（OGP/構造化データ）と
            # 本文中の可視な日付表記の両方からdateを解決しようとする（推測はしない
            # ライブラリの既定挙動をそのまま使う。取得できなければNone、URLや現在
            # 時刻からの推測は一切行わない——ユーザー指示のpublished_at resolution
            # Gate）。
            published_at = data.get("date") or None
        else:
            text = _decode_text(raw_bytes).strip()
            title = ""
    except Exception as e:
        return {"ok": False, "error": f"抽出処理エラー {type(e).__name__}: {e}", "title": "", "text": "", "language": None, "published_at": None}

    if not text:
        return {"ok": False, "error": "本文抽出に失敗（空）", "title": title, "text": "", "language": None, "published_at": None}
    return {"ok": True, "error": None, "title": title, "text": text, "language": None, "published_at": published_at}


def find_existing_document(client, content_hash: str) -> dict | None:
    rows = client.select("geo_documents", {"select": "*", "content_hash": f"eq.{content_hash}", "limit": "1"})
    return rows[0] if rows else None


def _persist_document(client, *, theme_id: str, theme_code: str, source_id: str | None,
                       filename: str, mime_type: str, raw_bytes: bytes,
                       title: str, url: str | None, published_at: str | None,
                       http_status: int | None, is_mock: bool) -> dict:
    """geo_documentsへの実際の保存処理（アップロード・URL取得の両方から共通で使う）。
    同一content_hashの文書が既にあればそれを返す（is_new=False）。
    戻り値: {"document": dict, "is_new": bool}"""
    content_hash = compute_content_hash(raw_bytes)
    existing = find_existing_document(client, content_hash)
    if existing:
        return {"document": existing, "is_new": False}

    extracted = extract_text(raw_bytes, filename, mime_type)

    theme_dir = UPLOAD_ROOT / theme_code
    theme_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ""
    storage_path = theme_dir / f"{content_hash}{ext}"
    storage_path.write_bytes(raw_bytes)

    row = {
        "theme_id": theme_id,
        "source_id": source_id,
        "title": title or extracted["title"] or filename,
        "url": url,
        "canonical_url": url,
        "published_at": published_at or extracted.get("published_at"),
        "language": extracted["language"],
        "content_hash": content_hash,
        "mime_type": mime_type,
        "storage_path": str(storage_path),
        "extracted_text": extracted["text"],
        "fetch_status": "成功" if extracted["ok"] else "失敗",
        "http_status": http_status,
        "parser_version": PARSER_VERSION,
        "is_mock": is_mock,
    }
    inserted = client.insert("geo_documents", [row])
    return {"document": inserted[0], "is_new": True}


def save_document(client, *, theme_id: str, theme_code: str, source_id: str | None,
                   filename: str, mime_type: str, raw_bytes: bytes,
                   title: str = "", url: str = "", published_at: str = None,
                   is_mock: bool = False) -> dict:
    """手動アップロードされた文書を保存する"""
    return _persist_document(
        client, theme_id=theme_id, theme_code=theme_code, source_id=source_id,
        filename=filename, mime_type=mime_type, raw_bytes=raw_bytes,
        title=title, url=url or None, published_at=published_at,
        http_status=None, is_mock=is_mock,
    )


def fetch_and_save_document(client, *, theme_id: str, theme_code: str, source_id: str,
                             url: str, proxies: dict, verify: bool) -> dict:
    """公式ソースのURLを取得し保存する（geo_official_crawler.py用）。
    戻り値: {"document": dict, "is_new": bool} または取得失敗時 {"error": str, "http_status": int|None}"""
    try:
        resp = requests.get(url, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"{type(e).__name__}: {e}", "http_status": getattr(e.response, "status_code", None)}

    filename = Path(urlparse(url).path).name or "document.html"
    content_type = resp.headers.get("Content-Type", "text/html").split(";")[0].strip()
    result = _persist_document(
        client, theme_id=theme_id, theme_code=theme_code, source_id=source_id,
        filename=filename, mime_type=content_type, raw_bytes=resp.content,
        title="", url=url, published_at=None,
        http_status=resp.status_code, is_mock=False,
    )
    return result


# =============================================================================
# Backfill（Corpus Coverage Audit v1の発見: qualify_and_persist_citations経由で
# 保存されたexternal_research文書はextracted_text=titleのみのstub——Discovery
# citationはあるが本文取得が接続されていない。既存fetch_and_save_document/
# _persist_documentは常にINSERTのみ（同一content_hashなら既存行を返すだけ）で、
# 既存document_idをUPDATEする経路が無いため、以下を新設する。ユーザー指示:
# 最小のUPDATE経路 + dry_run既定 + Discovery provenance（document_id/
# canonical_url/research_origin/元Citation情報）を壊さない。
# =============================================================================

def _is_stub_text(title: str, text: str) -> bool:
    """extracted_textが実質的な本文を持たない（title-onlyスタブ）かどうかの
    最小判定。Foundation Context Builder側の判定（geo_expert_engine.
    _foundation_context_is_stub）と同じ考え方だが、循環importを避けるため
    このモジュール内で独立して持つ。"""
    text = (text or "").strip()
    title = (title or "").strip()
    if not text:
        return True
    if text == title:
        return True
    return len(text) < 80


def fetch_url_for_backfill(url: str, *, proxies: dict, verify: bool) -> dict:
    """Backfill専用のfetch+extract（DBには一切触れない、副作用無し）。
    戻り値: {"ok": bool, "error": str|None, "title": str, "text": str,
    "published_at": str|None, "http_status": int|None, "content_hash": str|None}"""
    try:
        resp = requests.get(url, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "http_status": getattr(e.response, "status_code", None),
                "title": "", "text": "", "published_at": None, "content_hash": None}

    filename = Path(urlparse(url).path).name or "document.html"
    content_type = resp.headers.get("Content-Type", "text/html").split(";")[0].strip()
    extracted = extract_text(resp.content, filename, content_type)
    if not extracted["ok"]:
        return {"ok": False, "error": extracted["error"], "http_status": resp.status_code,
                "title": extracted["title"], "text": "", "published_at": None, "content_hash": None}

    return {
        "ok": True, "error": None, "title": extracted["title"], "text": extracted["text"],
        "published_at": extracted["published_at"], "http_status": resp.status_code,
        "content_hash": compute_content_hash(resp.content),
    }


def update_existing_document_from_fetch(client, document_id: str, *, extracted_text: str,
                                         content_hash: str, http_status: int | None,
                                         published_at: str | None, fetch_method: str,
                                         pre_backfill_content_hash: str | None,
                                         backfilled_at: str) -> dict:
    """既存のgeo_documents行をdocument_id指定でUPDATEする（INSERTしない）。
    document_id/canonical_url/url/theme_id/source_id/research_origin/title
    （＝元Citation情報）には一切触れない——extracted_text/content_hash/
    fetch_status/http_status/parser_version/published_atのみ更新する。
    published_atは既存値がまだ無い場合のみ埋める（既存の値は上書きしない）。
    backfill自体の来歴（backfill_status/backfilled_at/fetch_method/
    backfillされる前のcontent_hash）は、専用カラムを新設せず既存の未使用jsonb
    カラムresponse_headersへ記録する（geo_ingestion_runs用の同名カラムとは
    別物、geo_documentsのresponse_headersはこれまでどのコードからも書き込まれて
    いない）。"""
    existing = client.select("geo_documents", {
        "select": "published_at", "document_id": f"eq.{document_id}", "limit": "1",
    })
    existing_published_at = existing[0].get("published_at") if existing else None

    patch = {
        "extracted_text": extracted_text,
        "content_hash": content_hash,
        "fetch_status": "成功",
        "http_status": http_status,
        "parser_version": PARSER_VERSION,
        "response_headers": {
            "backfill_status": "success", "backfilled_at": backfilled_at,
            "fetch_method": fetch_method, "pre_backfill_content_hash": pre_backfill_content_hash,
        },
    }
    if not existing_published_at and published_at:
        patch["published_at"] = published_at

    client.update("geo_documents", {"document_id": f"eq.{document_id}"}, patch)
    return {"document_id": document_id, "patch": patch}


def backfill_stub_document(client, document_id: str, *, proxies: dict, verify: bool,
                            dry_run: bool = True) -> dict:
    """stub文書1件をdocument_id指定でBackfillする。dry_run=True（既定）では
    DBを一切書き換えず、取得できたであろう差分のみ返す。取得失敗・Quality Gate
    不合格の場合は元のstub行に一切触れない（部分的な書き換えをしない）。
    戻り値のstatus: "dry_run" | "updated" | "fetch_failed" | "rejected" | "not_found" """
    rows = client.select("geo_documents", {"select": "*", "document_id": f"eq.{document_id}", "limit": "1"})
    if not rows:
        return {"status": "not_found", "document_id": document_id}
    before = rows[0]

    url = before.get("canonical_url") or before.get("url")
    if not url:
        return {"status": "rejected", "document_id": document_id, "reasons": ["no_url"]}

    fetched = fetch_url_for_backfill(url, proxies=proxies, verify=verify)
    if not fetched["ok"]:
        return {"status": "fetch_failed", "document_id": document_id, "error": fetched["error"],
                "http_status": fetched["http_status"]}

    reasons = []
    if _is_stub_text(before.get("title"), fetched["text"]):
        reasons.append("fetched_text_still_stub")

    duplicate_of = None
    dup_rows = client.select("geo_documents", {
        "select": "document_id", "content_hash": f"eq.{fetched['content_hash']}", "limit": "2",
    })
    dup_other = [r for r in dup_rows if r["document_id"] != document_id]
    if dup_other:
        duplicate_of = dup_other[0]["document_id"]
        reasons.append("duplicate_of_existing_document")

    gates = {
        "text_substantive": "fetched_text_still_stub" not in reasons,
        "not_duplicate": "duplicate_of_existing_document" not in reasons,
        "published_at_resolved": bool(fetched["published_at"]),
    }

    if reasons:
        return {"status": "rejected", "document_id": document_id, "reasons": reasons,
                "duplicate_of": duplicate_of, "gates": gates}

    diff = {
        "extracted_text": {"before_len": len(before.get("extracted_text") or ""),
                            "after_len": len(fetched["text"])},
        "content_hash": {"before": before.get("content_hash"), "after": fetched["content_hash"]},
        "published_at": {"before": before.get("published_at"), "after": fetched["published_at"]},
        "http_status": fetched["http_status"],
    }

    if dry_run:
        return {"status": "dry_run", "document_id": document_id, "diff": diff, "gates": gates,
                "text_preview": fetched["text"][:300]}

    import datetime
    result = update_existing_document_from_fetch(
        client, document_id, extracted_text=fetched["text"], content_hash=fetched["content_hash"],
        http_status=fetched["http_status"], published_at=fetched["published_at"],
        fetch_method="geo_documents_store.backfill_stub_document",
        pre_backfill_content_hash=before.get("content_hash"),
        backfilled_at=datetime.datetime.utcnow().isoformat() + "Z",
    )
    return {"status": "updated", "document_id": document_id, "diff": diff, "gates": gates,
            "patch": result["patch"]}
