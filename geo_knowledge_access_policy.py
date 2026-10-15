"""Geopolitical Intelligence OS Phase E: Access Policy Service。

geo_knowledge_items.human_visibility / agent_sharing_policy（Phase Bで追加済みだが、これまで
どのRetrieval経路からも参照されていなかった列）を、実際に強制するための判定ロジックを1箇所へ
集約する。各APIへ判定をコピペしない（§12）。

絶対条件: LLM promptだけで制御しない。ここはDB/Service層の純粋なPythonロジックであり、
Access Filter自体はいかなるLLM呼び出しにも依存しない。unknownは常にdeny側へ倒す（§11, §26）。

Human VisibilityとAgent Sharing Policyは独立した2軸として評価する（§10: human_visibility=
companyだからといってagent_sharing_policy=geopolitics_onlyを無視して良いわけではない）。

既存auth/session機構が存在しない（Phase 0/E監査で確認済み: geo_role_guardは自己申告・
verify_api_keyはrequester識別を伴わない単一共有鍵）ため、本モジュールが要求するcontextは
「実際に取得可能な範囲」に留める。取得できない場合はunknown/anonymous扱いとし、
company可視性以外は許可しない（安全側のデフォルト）。将来の実認証基盤導入時、
build_requester_context()の中身だけを差し替えられるよう、判定ロジックからは分離してある。
"""
from __future__ import annotations

import geo_audit
import geo_knowledge_gap_service

HUMAN_VISIBILITY_LEVELS = ("private", "project", "geopolitics_team", "department", "company", "custom")
AGENT_SHARING_POLICIES = ("none", "geopolitics_only", "allowed_cross_domain", "custom")

# ---- Phase 2E: Report / Knowledge Confidentiality — UI Label層 ----------------
# ユーザー向けの3ラベルは、既存のhuman_visibility/agent_sharing_policy 6値+4値enumへ
# 無理に1:1で追加せず（§4「Label と内部Policyを分離」）、"custom"値 +
# allowed_org_units/allowed_domains（sql/2026-09-28b_geo_report_knowledge_access_policy.sql）
# の組み合わせとしてここで解決する。UI Label → Policyの変換はこのモジュールに一元化する
# （resolve_ui_label_to_policy/derive_ui_label_from_policy）。
UI_LABELS = ("DEPARTMENT_ONLY", "SHD_ONLY", "GROUP_SHAREABLE")

# SHDの実際の所属部門・domain一覧は、Phase 2E時点でコード上に手がかりが無く（Audit済み、
# コードベース全体に"SHD"という語自体が存在しない）、推測で実装しない（ユーザー判断:
# 2026-09-28、「空リストで実装し、後で設定してもらう」）。空のままの間はSHD_ONLYの
# Reportは所有者本人以外アクセスできない（fail-closed）。実際の組織範囲が確定次第、
# この定数を書き換える（DBへの動的設定APIは今回作らない、コード変更のみで足りる規模のため）。
SHD_ALLOWED_ORG_UNITS: tuple = ()
SHD_ALLOWED_DOMAINS: tuple = ()

# グループ配信可（GROUP_SHAREABLE）の「許可されたグループ」も同様に、Agent軸
# （他domain AIへの共有）については具体的な許可domain一覧が未確定のため空のままにする
# （Human軸は"company"=社内であれば他部門からも閲覧可、を採用——「公開」ではなく
# 既存のcompany visibilityの意味そのものであり、新しい推測を伴わない）。
GROUP_SHAREABLE_ALLOWED_DOMAINS: tuple = ()

# geo_intelligence_auth.GEOPOLITICS_INTERNAL_DOMAINと同じ値（Business Logic側は
# geo_intelligence_authへ依存させない——Authはgeo_intelligence_api.py層のみが知っていればよく、
# ここでは文字列定数として複製する。値がズレた場合に気づけるようテストで突き合わせる）。
GEOPOLITICS_INTERNAL_DOMAIN = "geopolitics"

# geo_knowledge_items列のDB既定値と一致させる（Phase B: sql/2026-09-16_geo_ios_foundation.sql）。
# 列が万一欠落している行（select列を絞った呼び出し等）に対しても、実DBの既定と同じ挙動にする。
DEFAULT_HUMAN_VISIBILITY = "company"
DEFAULT_AGENT_SHARING_POLICY = "geopolitics_only"

# Officially Retrievableとみなすstatus/retrieval_status（§3 Official Knowledge Definition）。
_RETRIEVABLE_STATUS = "active"
_RETRIEVABLE_RETRIEVAL_STATUS = "available"


def resolve_is_internal_caller(normalized: dict) -> bool:
    """Phase F: is_internal_caller判定の単一の正。geo_intelligence_service.process_requestが
    caller_identity（認証済み、geo_intelligence_auth.verify_api_key経由）を受け取っていれば、
    そちらのcaller_domainのみを信頼する——Body側のrequester_app自己申告（"_caller_authenticated"
    =Falseの場合のみ使うフォールバック）だけで内部/外部を判定しない（requester_app=geo_chat_ui
    へのなりすまし対策、§7）。

    caller_identity未指定（HTTP境界を経由しない内部プロセス内呼び出し・既存テストの直接呼び出し）
    の場合のみ、既存のrequester_app自己申告ベースの判定にフォールバックする——この経路は
    そもそも外部から到達不可能なため、自己申告を信頼しても安全（呼び出しているのは自分自身の
    信頼済みコードだけ）。"""
    if normalized.get("_caller_authenticated"):
        return normalized.get("_authenticated_caller_domain") == GEOPOLITICS_INTERNAL_DOMAIN
    return normalized.get("requester_app") == geo_knowledge_gap_service.CHAT_UI_REQUESTER_APP


def is_retrievable(item: dict) -> bool:
    """approved(=active) AND not superseded/candidate/rejected/... AND retrieval_status=available。
    Draft/Review中/Superseded/CandidateなKnowledgeを通常Retrievalへ混ぜない（絶対条件）。"""
    return (item.get("status") == _RETRIEVABLE_STATUS
            and item.get("retrieval_status") == _RETRIEVABLE_RETRIEVAL_STATUS)


# ---- Agent Sharing（他Domain Agent向け） -----------------------------------

def can_agent_access(item: dict, *, is_internal_caller: bool, caller_domain: str = None) -> bool:
    """is_internal_caller=True: 地政学アプリ自身の内部利用（Expert AI本体・Report Studio・
    IOS自身等）。agent_sharing_policyは「他Domain Agentへの共有可否」を表す軸であり、
    地政学アプリ自身の内部利用を制限するものではないため、内部呼び出しは常に許可する。

    is_internal_caller=False: /api/v1/geo-intelligence/query経由の他Domain Agent。
    agent_sharing_policyの値に厳密に従う。unknown/未対応値はdeny（安全側）。

    caller_domain（Phase 2E追加）: policy="custom"の場合のみ参照し、item["allowed_domains"]
    に含まれるかを厳密に照合する（未指定・空リストならdeny、fail-closed）。"""
    if is_internal_caller:
        return True
    policy = item.get("agent_sharing_policy") or DEFAULT_AGENT_SHARING_POLICY
    if policy == "allowed_cross_domain":
        return True
    if policy == "custom":
        allowed_domains = item.get("allowed_domains") or []
        return bool(caller_domain) and caller_domain in allowed_domains
    if policy in ("geopolitics_only", "none"):
        return False
    # 未対応値: 最も安全な側（deny）に倒す。
    return False


def filter_agent_accessible_knowledge_items(items: list, *, is_internal_caller: bool,
                                            require_retrievable: bool = True,
                                            caller_domain: str = None) -> list:
    """geo_knowledge_items由来の行（"knowledge_item_id"キーを持つ行）のみを対象に、
    agent_sharing_policy（常に適用）+ is_retrievable（require_retrievable=Trueの場合のみ）を
    適用する。knowledge_item由来でない行（Claim/Entity/Relationship等、visibility列を持たない
    既存エンティティ）はそのまま通す——Phase Eのスコープはgeo_knowledge_itemsのvisibility強制
    であり、Claim等の別軸のガバナンスを新設するものではない。

    require_retrievable=False: "contested"次元向け。contestedな知識はstatus="contested"で
    あることが前提のため、is_retrievable（status=active要求）を適用すると常に全滅してしまう
    ——agent_sharing_policyのみ適用し、対立の可視化という目的を壊さない。"""
    out = []
    for item in items:
        if "knowledge_item_id" not in item:
            out.append(item)
            continue
        if require_retrievable and not is_retrievable(item):
            continue
        if can_agent_access(item, is_internal_caller=is_internal_caller, caller_domain=caller_domain):
            out.append(item)
    return out


def filter_agent_accessible_retrieval_dict(retrieval: dict, *, is_internal_caller: bool,
                                           caller_domain: str = None,
                                           dimension_keys: tuple = ("foundation", "precedent"),
                                           non_retrievable_dimension_keys: tuple = ("contested",)) -> dict:
    """retrieve_knowledge()等が返す{dimension: [items...]}形式の辞書へ一括適用する。
    internal_expert_knowledgeはkind="knowledge_item"/"claim"が混在するため個別に扱う。"""
    filtered = dict(retrieval)
    for key in dimension_keys:
        if key in filtered and isinstance(filtered[key], list):
            filtered[key] = filter_agent_accessible_knowledge_items(
                filtered[key], is_internal_caller=is_internal_caller, caller_domain=caller_domain)
    for key in non_retrievable_dimension_keys:
        if key in filtered and isinstance(filtered[key], list):
            filtered[key] = filter_agent_accessible_knowledge_items(
                filtered[key], is_internal_caller=is_internal_caller, require_retrievable=False,
                caller_domain=caller_domain)
    if isinstance(filtered.get("internal_expert_knowledge"), list):
        kept = []
        for entry in filtered["internal_expert_knowledge"]:
            if entry.get("kind") != "knowledge_item":
                kept.append(entry)  # claim由来はvisibility列を持たないため対象外
                continue
            if is_retrievable(entry) and can_agent_access(
                    entry, is_internal_caller=is_internal_caller, caller_domain=caller_domain):
                kept.append(entry)
        filtered["internal_expert_knowledge"] = kept
    return filtered


# ---- Human Visibility（人間ユーザー向け） -----------------------------------

def build_requester_context(*, user_id: str = None, department: str = None,
                             is_authenticated: bool = None) -> dict:
    """既存auth基盤が無いため、呼び出し元（API層）が把握できる範囲の値だけを渡す。
    is_authenticatedを明示しない場合、user_idの有無で推定する（両方無ければ匿名扱い、
    §11: unknownをallow扱いにしない）。"""
    return {
        "user_id": user_id, "department": department,
        "is_authenticated": bool(is_authenticated) if is_authenticated is not None else bool(user_id),
    }


ANONYMOUS_CONTEXT = build_requester_context()


def can_human_access(item: dict, *, requester_context: dict = None) -> bool:
    """human_visibilityの意味をここで一元的に定義する（§9）。既存identity/groupモデルが
    無いため、private/project/customは「作成者本人のみ」または「deny」という最小限の解釈に
    留め、実運用で本格的なACLが必要になった時点でcustomの中身だけを拡張する。"""
    ctx = requester_context or ANONYMOUS_CONTEXT
    visibility = item.get("human_visibility") or DEFAULT_HUMAN_VISIBILITY

    if visibility == "company":
        return True
    if visibility in ("geopolitics_team", "department"):
        # 現行システムには地政学チーム以外の人間ユーザーがこのAppを直接使う経路が無いため、
        # 「認証済み利用者である」ことをもって地政学チーム/指定部門相当とみなす（Phase E時点の
        # 簡略化。部門を跨いだ人間ユーザーモデルが増えた場合はrequester_context["department"]を
        # 使った厳密な一致判定へ差し替える——本関数のこの分岐だけを直す）。
        return bool(ctx.get("is_authenticated"))
    if visibility == "private":
        return bool(ctx.get("user_id")) and ctx.get("user_id") == item.get("created_by")
    if visibility == "project":
        # Project member一覧を持つデータソースが現状無いため、作成者本人のみ許可する
        # （安全側のフォールバック。Project membershipモデルが出来次第ここを拡張する）。
        return bool(ctx.get("user_id")) and ctx.get("user_id") == item.get("created_by")
    if visibility == "custom":
        # Phase 2E: DEPARTMENT_ONLY/SHD_ONLY（geo_reports）が使う分岐。allowed_org_unitsに
        # requester_context["department"]（認証済みIosIdentity.department由来のみを正とする、
        # クライアント自己申告は信頼しない——呼び出し側APIの責務）が含まれるかで判定する。
        # allowed_org_unitsが空（SHD等、実体未設定）の間は、作成者/uploaded_by本人のみ許可
        # （fail-closed、「分からないから許可」をしない）。
        allowed_org_units = item.get("allowed_org_units") or []
        requester_department = ctx.get("department")
        if requester_department and requester_department in allowed_org_units:
            return True
        creator = item.get("created_by") or item.get("uploaded_by")
        return bool(ctx.get("user_id")) and ctx.get("user_id") == creator
    # 未対応値: ACL/group等は未実装のため安全側でdeny。
    return False


def filter_human_accessible_knowledge_items(items: list, *, requester_context: dict = None) -> list:
    out = []
    for item in items:
        if "knowledge_item_id" not in item:
            out.append(item)
            continue
        if is_retrievable(item) and can_human_access(item, requester_context=requester_context):
            out.append(item)
    return out


# ---- Phase 2E: UI Label ↔ Policy Resolver（§4） -------------------------------

def resolve_ui_label_to_policy(label: str, *, owner_department: str = None) -> dict:
    """3つのUI Label（部内限り/SHD限り/グループ配信可）を、既存human_visibility/
    agent_sharing_policy語彙 + allowed_org_units/allowed_domainsへ変換する。UI/Serviceは
    このLabelだけを扱い、内部enumを直接組み立てない（§4「Label と内部Policyを分離」）。

    DEPARTMENT_ONLY: owner_department必須（省略時はReportそのものが所属部門不明という
    ことであり、allowed_org_unitsを空のままにする＝fail-closed、作成者本人のみ許可）。"""
    if label == "DEPARTMENT_ONLY":
        return {
            "human_visibility": "custom",
            "agent_sharing_policy": "none",
            "allowed_org_units": [owner_department] if owner_department else [],
            "allowed_domains": [],
        }
    if label == "SHD_ONLY":
        return {
            "human_visibility": "custom",
            "agent_sharing_policy": "custom",
            "allowed_org_units": list(SHD_ALLOWED_ORG_UNITS),
            "allowed_domains": list(SHD_ALLOWED_DOMAINS),
        }
    if label == "GROUP_SHAREABLE":
        return {
            "human_visibility": "company",
            "agent_sharing_policy": "custom",
            "allowed_org_units": [],
            "allowed_domains": list(GROUP_SHAREABLE_ALLOWED_DOMAINS),
        }
    raise ValueError(f"未対応のUI Labelです: {label}（許可: {UI_LABELS}）")


# UI Labelの「広さ」の順序（§7 Report → Knowledge inheritance判定専用）。3 Label間だけの
# 単純な順位付けであり、既存6値/4値enum全体の一般的な順序関係を定義するものではない。
_UI_LABEL_RANK = {"DEPARTMENT_ONLY": 0, "SHD_ONLY": 1, "GROUP_SHAREABLE": 2}


def derive_ui_label_from_policy(item: dict) -> str | None:
    """resolve_ui_label_to_policyの逆変換（表示用・§7継承チェック用）。owner_department等
    Report固有の値は問わず、human_visibility/agent_sharing_policy/allowed_domainsの形だけで
    判定する（allowed_org_unitsの中身がowner_departmentと一致するかまでは見ない——
    DEPARTMENT_ONLYとして登録された時点のowner_departmentが後で変わることは無い前提)。
    どの3 Labelにも一致しない組み合わせ（Phase 2E以前のlegacyデータ等）はNoneを返す
    （§7の継承チェックでは「Noneは最も安全側＝DEPARTMENT_ONLY相当」として扱うこと）。"""
    hv = item.get("human_visibility")
    asp = item.get("agent_sharing_policy")
    if hv == "custom" and asp == "none":
        return "DEPARTMENT_ONLY"
    if hv == "custom" and asp == "custom":
        return "SHD_ONLY"
    if hv == "company" and asp == "custom":
        return "GROUP_SHAREABLE"
    return None


def validate_publication_scope(*, report_policy: dict, knowledge_policy: dict) -> None:
    """§7 Report → Knowledge inheritance: KnowledgeのUI LabelがReportのUI Labelより広い
    （＝より多くの人・Agentに公開される）場合はPublicationError相当のValueErrorを送出する。
    Reportの側がderive不能（legacyデータ等）な場合は最も安全側＝DEPARTMENT_ONLY相当として
    扱う（Reportの実際の公開範囲が不明な以上、Knowledge側を無制限に広げてよい根拠が無い、
    §13 fail-closed）。Knowledge側がderive不能な場合も同様に安全側（最も広いGROUP_SHAREABLE
    ではなくDEPARTMENT_ONLY相当）とみなし、ランクが不明＝0として扱う（＝実質必ず許可される
    側になるが、これは「まだLabelとして解決されていないKnowledge側の値をこの関数だけでは
    弾けない」ことを意味するのみで、呼び出し側が別途resolve_ui_label_to_policy経由の値を
    渡す限り発生しない）。"""
    report_label = derive_ui_label_from_policy(report_policy) or "DEPARTMENT_ONLY"
    knowledge_label = derive_ui_label_from_policy(knowledge_policy) or "DEPARTMENT_ONLY"
    if _UI_LABEL_RANK[knowledge_label] > _UI_LABEL_RANK[report_label]:
        raise ValueError(
            f"Knowledge公開範囲（{knowledge_label}）がReport公開範囲（{report_label}）より"
            "広いため、Publicationを拒否しました（Report→Knowledgeでscopeを広げることは"
            "できません）。")


# ---- Phase 2E: Report Access Policy（Knowledge Itemと同じ考え方の共通resolver、§6） -----

REPORT_OPERATIONS = ("list", "read", "retrieve_for_ai", "cross_domain_share")


def can_access_report(report: dict, *, operation: str, requester_context: dict = None,
                       is_internal_caller: bool = True, caller_domain: str = None) -> bool:
    """geo_reportsの行に対する統一Access判定。Knowledge Itemと同じ2軸（human_visibility/
    agent_sharing_policy + allowed_org_units/allowed_domains）をそのまま再利用し、新しい
    独立のセキュリティモデルは作らない。operationは最低限の4種のみ（必要以上に細分化しない、
    §6）:
    - "list"/"read": 人間ユーザー向け（一覧・検索・詳細取得・ダウンロード）→can_human_access
    - "retrieve_for_ai"/"cross_domain_share": AI/Agent向け（Intelligence AI・Cross-domain
      Agent Retrieval）→can_agent_access。is_internal_caller未指定時はTrue扱い（地政学アプリ
      自身の内部利用が既定、既存can_agent_accessと同じ既定）。"""
    if operation in ("list", "read"):
        return can_human_access(report, requester_context=requester_context)
    if operation in ("retrieve_for_ai", "cross_domain_share"):
        return can_agent_access(report, is_internal_caller=is_internal_caller, caller_domain=caller_domain)
    raise ValueError(f"未対応のoperationです: {operation}（許可: {REPORT_OPERATIONS}）")


def filter_human_accessible_reports(reports: list, *, requester_context: dict = None) -> list:
    """Reportにはis_retrievable相当のstatus/retrieval_status概念が無い（アップロードされた
    時点で常に存在するもの）ため、Knowledge Item版と異なりvisibility判定のみ適用する。"""
    return [r for r in reports
            if can_access_report(r, operation="list", requester_context=requester_context)]


def filter_agent_accessible_reports(reports: list, *, is_internal_caller: bool,
                                     caller_domain: str = None) -> list:
    return [r for r in reports
            if can_access_report(r, operation="retrieve_for_ai", is_internal_caller=is_internal_caller,
                                 caller_domain=caller_domain)]


# ---- Audit（§22: 個別denyの大量ログは避け、集計のみ記録） --------------------

def log_access_filter_summary(client, *, actor: str, action: str, total_before: int, total_after: int,
                               context: dict = None) -> None:
    denied = total_before - total_after
    if denied <= 0:
        return
    geo_audit.log_audit(client, actor=actor, action=action, target_table="geo_knowledge_items",
                         target_id=None, detail={"total_before": total_before, "total_after": total_after,
                                                  "denied_count": denied, "context": context or {}})
