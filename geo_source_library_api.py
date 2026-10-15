"""Source Library / Source Set / Source Research API。

- /api/geo/source-library      : 組織共通Source Master（検索・CRUD(soft delete)・Tag・Domain・Theme関連・推薦）
- /api/geo/source-sets         : Theme Explorer / Monitoring / Research Studioで共有するSource Set
- /api/geo/source-research     : Source指定のResearch（既存Research Studio Projectへ調査コンテキストを渡す）

Mutationは既存のverify_ios_identity（X-IOS-Credential）で保護する（theme-candidates APIと同方針）。
GET系は既存の他Intelligence APIと同様に未認証でも閲覧可。
migration（sql/2026-10-05*.sql）未適用でテーブル/列が無い場合は503 + 案内を返す。
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client  # noqa: E402
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_report_studio_service as studio
import geo_source_library_service as lib
import geo_source_recommendation_service as recommend
import geo_source_research_service as research
import geo_source_search_service as search
import geo_source_set_service as sets

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_source_library_router = APIRouter(prefix="/api/geo/source-library")
geo_source_set_router = APIRouter(prefix="/api/geo/source-sets")
geo_source_research_router = APIRouter(prefix="/api/geo/source-research")

_MIGRATION_HINT = "DB migration（sql/2026-10-05*.sql）が未適用の可能性があります。"


@contextmanager
def _guard():
    """検証エラー→400、テーブル/列なし(migration未適用)→503へ変換する。"""
    try:
        yield
    except lib.SourceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except requests.HTTPError as exc:
        if lib._is_missing_column_error(exc):
            raise HTTPException(status_code=503, detail=_MIGRATION_HINT)
        raise HTTPException(status_code=502, detail=f"DBアクセスに失敗しました: {exc}")


def _actor(identity: geo_ios_identity.IosIdentity) -> str | None:
    return identity.identity_id


# ---------------------------------------------------------------------------
# Source Library
# ---------------------------------------------------------------------------

@geo_source_library_router.get("/schema-status")
def schema_status():
    return lib.get_schema_status(_client)


@geo_source_library_router.get("/meta")
def library_meta():
    return {"media_types": list(lib.MEDIA_TYPES), "tag_types": list(lib.TAG_TYPES),
            "stale_after_days": lib.STALE_AFTER_DAYS, "data_freshness": lib.get_global_freshness(_client)}


@geo_source_library_router.get("")
def list_sources(q: str | None = None, media_type: str | None = None, region: str | None = None,
                 language: str | None = None, is_primary: bool | None = None, theme_id: str | None = None,
                 tag: str | None = None, include_archived: bool = False, created_after: str | None = None,
                 sort: str = "name", order: str = "asc", page: int = 1, page_size: int = 25):
    with _guard():
        return lib.list_sources(
            _client, q=q, media_type=media_type, region=region, language=language, is_primary=is_primary,
            theme_id=theme_id, tag=tag, include_archived=include_archived, created_after=created_after,
            sort=sort, order=order, page=page, page_size=page_size)


@geo_source_library_router.get("/search")
def natural_language_search(text: str, media_type: str | None = None, region: str | None = None,
                            language: str | None = None, is_primary: bool | None = None,
                            page: int = 1, page_size: int = 25):
    """自然言語検索。解釈結果(interpretation)を返し、UIはchipとして表示する。"""
    with _guard():
        return search.search_sources(
            _client, _azure_client, _model, text, page=page, page_size=page_size,
            extra_filters={"media_type": media_type, "region": region, "language": language,
                           "is_primary": is_primary})


class SourceBody(BaseModel):
    name: str | None = None
    url: str | None = None
    media_type: str | None = None
    region: str | None = None
    is_primary: bool | None = None
    description: str | None = None
    reliability: str | None = None
    source_tier: str | None = None
    source_type: str | None = None
    notes: str | None = None
    languages: list[str] | None = None
    update_cadence: str | None = None
    thematic_scope: str | None = None
    geographic_scope: str | None = None
    source_owner: str | None = None
    suggest_tags: bool = False


def _body_fields(body: SourceBody) -> dict:
    return {k: v for k, v in body.model_dump(exclude={"suggest_tags"}).items() if v is not None}


@geo_source_library_router.post("")
def create_source(body: SourceBody, identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        source = lib.create_source(_client, _body_fields(body), registered_by=_actor(identity))
        out = {"source": source, "domain_conflicts": [], "tag_suggestion": None}
        domain = lib.normalize_domain(source.get("url"))
        if domain:
            out["domain_conflicts"] = [s for s in lib.find_domain_conflicts(_client, domain) if s != source["source_id"]]
        if body.suggest_tags:  # LLMが失敗してもSource登録自体は成功させる
            out["tag_suggestion"] = lib.suggest_tags(_azure_client, _model, source)
            if out["tag_suggestion"]["tags"]:
                lib.save_suggested_tags(_client, source["source_id"], out["tag_suggestion"]["tags"])
        return out


@geo_source_library_router.get("/{source_id}")
def get_source_detail(source_id: str, article_limit: int = 20):
    with _guard():
        detail = lib.get_source_detail(_client, source_id, article_limit=min(article_limit, 100))
    if not detail:
        raise HTTPException(status_code=404, detail="Sourceが見つかりません")
    return detail


@geo_source_library_router.patch("/{source_id}")
def update_source(source_id: str, body: SourceBody,
                  identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        source = lib.update_source(_client, source_id, _body_fields(body))
    if not source:
        raise HTTPException(status_code=404, detail="Sourceが見つかりません")
    return {"source": source}


@geo_source_library_router.delete("/{source_id}")
def archive_source(source_id: str, identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """soft delete（archived_at）。Article/Source Set/Monitoringの参照は維持される。"""
    with _guard():
        source = lib.archive_source(_client, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Sourceが見つかりません")
    return {"source": source, "archived": True}


@geo_source_library_router.post("/{source_id}/restore")
def restore_source(source_id: str, identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        source = lib.restore_source(_client, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Sourceが見つかりません")
    return {"source": source}


class TagBody(BaseModel):
    tag_type: str
    tag_value: str


@geo_source_library_router.post("/{source_id}/suggest-tags")
def suggest_tags(source_id: str, save: bool = True,
                 identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """LLMでTag提案。save=trueならorigin=ai/is_confirmed=falseで保存（人間が承認するまで未確定）。"""
    with _guard():
        source = lib.get_source(_client, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Sourceが見つかりません")
        result = lib.suggest_tags(_azure_client, _model, source)
        if save and result["tags"]:
            result["saved"] = lib.save_suggested_tags(_client, source_id, result["tags"])
        return result


@geo_source_library_router.post("/{source_id}/tags")
def add_tag(source_id: str, body: TagBody,
            identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        return {"tag": lib.add_tag(_client, source_id, body.tag_type, body.tag_value, origin="human",
                                   created_by=_actor(identity))}


@geo_source_library_router.post("/{source_id}/tags/{tag_id}/confirm")
def confirm_tag(source_id: str, tag_id: str, confirmed: bool = True,
                identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        lib.confirm_tag(_client, source_id, tag_id, confirmed=confirmed)
    return {"ok": True}


@geo_source_library_router.delete("/{source_id}/tags/{tag_id}")
def remove_tag(source_id: str, tag_id: str,
               identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        lib.remove_tag(_client, source_id, tag_id)
    return {"ok": True}


class DomainBody(BaseModel):
    domain: str
    is_primary: bool = False


@geo_source_library_router.post("/{source_id}/domains")
def add_domain(source_id: str, body: DomainBody,
               identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        row = lib.add_domain(_client, source_id, body.domain, is_primary=body.is_primary)
        conflicts = [s for s in lib.find_domain_conflicts(_client, row["domain"]) if s != source_id]
        return {"domain": row, "conflicts": conflicts}


@geo_source_library_router.patch("/{source_id}/domains/{domain_id}")
def toggle_domain(source_id: str, domain_id: str, match_enabled: bool,
                  identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        lib.set_domain_match_enabled(_client, source_id, domain_id, match_enabled)
    return {"ok": True}


@geo_source_library_router.delete("/{source_id}/domains/{domain_id}")
def remove_domain(source_id: str, domain_id: str,
                  identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        lib.remove_domain(_client, source_id, domain_id)
    return {"ok": True}


class ThemeLinkBody(BaseModel):
    theme_id: str
    relation_type: str = "manual"


@geo_source_library_router.post("/{source_id}/theme-links")
def add_theme_link(source_id: str, body: ThemeLinkBody,
                   identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        return {"link": lib.add_theme_link(_client, source_id, body.theme_id, relation_type=body.relation_type,
                                           created_by=_actor(identity))}


@geo_source_library_router.delete("/{source_id}/theme-links/{link_id}")
def remove_theme_link(source_id: str, link_id: str,
                      identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        lib.remove_theme_link(_client, source_id, link_id)
    return {"ok": True}


class RecommendBody(BaseModel):
    title: str | None = None
    actors: list[str] = []
    regions: list[str] = []
    themes: list[str] = []
    keywords: list[str] = []
    theme_id: str | None = None
    candidate_id: str | None = None
    limit: int = 12


@geo_source_library_router.post("/recommend")
def recommend_sources(body: RecommendBody):
    """Theme（候補ID or 直接指定）→ おすすめSource。理由つき。"""
    import geo_theme_explorer_service as explorer
    with _guard():
        theme = body.model_dump(exclude={"limit", "candidate_id"})
        if body.candidate_id:
            from_cand = explorer.candidate_for_recommendation(_client, body.candidate_id)
            if not from_cand:
                raise HTTPException(status_code=404, detail="Theme Candidateが見つかりません")
            from_cand.pop("candidate", None)
            theme = {**theme, **{k: v for k, v in from_cand.items() if v}}
        return recommend.recommend_sources(_client, theme, limit=min(body.limit, 30))


# ---------------------------------------------------------------------------
# Source Set
# ---------------------------------------------------------------------------

class SourceSetBody(BaseModel):
    name: str | None = None
    description: str | None = None
    source_ids: list[str] | None = None


@geo_source_set_router.get("")
def list_source_sets(include_archived: bool = False):
    """通常はarchived_at IS NULLのみ。include_archived=trueでアーカイブ済みも返す。"""
    with _guard():
        return {"source_sets": sets.list_source_sets(_client, include_archived=include_archived)}


@geo_source_set_router.post("")
def create_source_set(body: SourceSetBody,
                      identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        return {"source_set": sets.create_source_set(
            _client, name=body.name or "", description=body.description, source_ids=body.source_ids,
            created_by=_actor(identity))}


@geo_source_set_router.get("/{source_set_id}")
def get_source_set(source_set_id: str):
    with _guard():
        s = sets.get_source_set(_client, source_set_id)
    if not s:
        raise HTTPException(status_code=404, detail="Source Setが見つかりません")
    return {"source_set": s}


@geo_source_set_router.patch("/{source_set_id}")
def update_source_set(source_set_id: str, body: SourceSetBody,
                      identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        s = sets.update_source_set(_client, source_set_id, name=body.name, description=body.description)
        if s is not None and body.source_ids is not None:
            sets.set_members(_client, source_set_id, body.source_ids, added_by=_actor(identity))
            s = sets.get_source_set(_client, source_set_id)
    if not s:
        raise HTTPException(status_code=404, detail="Source Setが見つかりません")
    return {"source_set": s}


class AddMemberBody(BaseModel):
    source_id: str


@geo_source_set_router.post("/{source_set_id}/members")
def add_member(source_set_id: str, body: AddMemberBody,
               identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        if not sets.get_source_set(_client, source_set_id):
            raise HTTPException(status_code=404, detail="Source Setが見つかりません")
        return {"sources": sets.add_member(_client, source_set_id, body.source_id, added_by=_actor(identity))}


@geo_source_set_router.delete("/{source_set_id}")
def archive_source_set(source_set_id: str, force: bool = False,
                       identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """論理削除（archived_at）。Source自体・member・Monitoringの参照は変更しない。
    有効なMonitoringが参照中なら409（force=trueでarchiveしてもMonitoringは動作を続ける）。"""
    with _guard():
        try:
            result = sets.archive_source_set(_client, source_set_id, force=force)
        except sets.SourceSetInUse as exc:
            raise HTTPException(status_code=409, detail={
                "message": "有効なMonitoringが参照中のためarchiveできません（force=trueで強制可。Monitoringは動作を続けます）",
                "monitoring_themes": exc.themes})
        if result is None:
            raise HTTPException(status_code=404, detail="Source Setが見つかりません")
    return {"archived": True, **result}


@geo_source_set_router.post("/{source_set_id}/restore")
def restore_source_set(source_set_id: str,
                       identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        s = sets.restore_source_set(_client, source_set_id)
    if not s:
        raise HTTPException(status_code=404, detail="Source Setが見つかりません")
    return {"source_set": s}


# ---------------------------------------------------------------------------
# Source Research（既存Research Studio Projectへ調査コンテキストを渡して実行）
# ---------------------------------------------------------------------------

class ResearchProjectBody(BaseModel):
    theme: str
    query: str | None = None
    source_ids: list[str] = []
    source_set_id: str | None = None
    origin: str = "theme_explorer"


@geo_source_research_router.post("/projects")
def create_research_project(body: ResearchProjectBody,
                            identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """Theme / Query / source_idsを引き継いだResearch Studio Projectを作成する。
    既存Report Studio Project（geo_report_projects）を作るだけで、新しいResearch Engineは作らない。"""
    with _guard():
        source_ids = list(body.source_ids)
        if body.source_set_id:
            source_ids = [s["source_id"] for s in sets.resolve_set_sources(_client, body.source_set_id)
                          if not s.get("archived_at")] or source_ids
        if not source_ids:
            raise HTTPException(status_code=400, detail="Sourceを1つ以上選択してください")
        _client.select("geo_report_projects", {"select": "research_context", "limit": "1"})  # DB-5未適用なら503
        names = [s["name"] for s in _client.select("geo_source_registry", {
            "select": "source_id,name", "source_id": "in.(" + ",".join(source_ids) + ")"})]
        code = f"research-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        project = studio.get_or_create_project(_client, code, (body.theme or "Research")[:120])
        context = {"theme": body.theme, "query": body.query, "source_ids": source_ids,
                   "source_names": names, "source_set_id": body.source_set_id, "origin": body.origin}
        _client.update("geo_report_projects", {"project_id": f"eq.{project['project_id']}"},
                       {"research_context": context})
        return {"project": {**project, "research_context": context}}


@geo_source_research_router.get("/projects/{project_id}/context")
def get_research_context(project_id: str):
    with _guard():
        rows = _client.select("geo_report_projects", {"select": "project_id,project_title,research_context",
                                                      "project_id": f"eq.{project_id}"})
    if not rows:
        raise HTTPException(status_code=404, detail="project_idが見つかりません")
    return {"project_id": project_id, "research_context": rows[0].get("research_context")}


class ResearchRunBody(BaseModel):
    query: str | None = None
    source_ids: list[str] | None = None
    use_web: bool = True


@geo_source_research_router.post("/projects/{project_id}/run")
def run_research(project_id: str, body: ResearchRunBody,
                 identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    with _guard():
        rows = _client.select("geo_report_projects", {"select": "project_id,research_context",
                                                      "project_id": f"eq.{project_id}"})
        if not rows:
            raise HTTPException(status_code=404, detail="project_idが見つかりません")
        ctx = rows[0].get("research_context") or {}
        query = body.query or ctx.get("query") or ""
        source_ids = body.source_ids or ctx.get("source_ids") or []
        if body.query and body.query != ctx.get("query"):  # 問いの更新は次回に引き継ぐ
            _client.update("geo_report_projects", {"project_id": f"eq.{project_id}"},
                           {"research_context": {**ctx, "query": body.query}})
        return research.run_source_research(
            _client, _azure_client, _model, theme=ctx.get("theme") or "", query=query,
            source_ids=source_ids, use_web=body.use_web)
