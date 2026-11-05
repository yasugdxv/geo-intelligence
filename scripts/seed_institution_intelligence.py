"""Expert AI Phase R1 Chunk3: Institution Intelligence MVPの手動Seed投入。

承認済みPlan: C:\\Users\\269811\\.claude\\plans\\pure-drifting-cosmos.md

設計方針（scripts/seed_actor_intelligence.pyと同じ）:
- 既定はcandidateまでしか作らない。--activateを明示的に付けた場合のみactive化する。
- 広く確立された、時間経過で大きく変わらない構造的事実（mandate/decision_authority/
  constraints等）のみを収録する。特定時点の規則番号・日付等の変わりやすい詳細は含めない。
- Person Actorのような persistent_preference/worldview/red_lines 等は一切持たせない
  （Institution Profileの擬人化を防ぐことがPhase R1 Chunk3の目的そのもの）。

使い方:
    python scripts/seed_institution_intelligence.py            # candidateのみ作成/更新
    python scripts/seed_institution_intelligence.py --activate # 上記に加えてactive化する
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from geo_db_client import SupabaseClient  # noqa: E402
from config_utils import load_config  # noqa: E402

import geo_institution_intelligence_service as inst  # noqa: E402

SEED_ACTOR_ID = "institution_intelligence_seed_phase_r1c3"

INSTITUTIONS = [
    {
        "institution_id": "us_bis",
        "canonical_name": "Bureau of Industry and Security (BIS)",
        "entity_type": "ministry",
        "mandate": (
            "米国商務省内の一部局として、輸出管理規則(Export Administration Regulations, "
            "EAR)を執行する。デュアルユース品目・先端半導体/AI関連ハードウェア等、"
            "国家安全保障・外交政策上の意義がある品目の輸出許可・規制を所掌する。"
        ),
        "decision_authority": {
            "description": (
                "規則制定・輸出許可判断はBIS（商務省）が行うが、国家安全保障・外交政策上"
                "重要な規制変更は、通常、国防総省・国務省・エネルギー省等との省庁間調整を"
                "経たうえで連邦官報(Federal Register)へ公表される。"
            ),
            "veto_players": ["Department of Defense（国家安全保障レビュー）", "Department of State（外交政策レビュー）"],
            "decision_process": "連邦規則制定手続き（Federal Register公表、重要案件は省庁間調整を経る）",
        },
        "constraints": [
            "Export Administration Regulations(EAR)の枠内でのみ規則制定・許可判断ができる",
            "行政手続法上の公表・コメント手続きを要する場合がある",
            "予算・人員は商務省の歳出予算に依存する",
        ],
        "internal_stakeholders": [
            {"name_or_role": "Bureau of Industry and Security (BIS)",
             "position_or_influence": "規則の起草・輸出許可発行を担当する実務部局"},
            {"name_or_role": "Department of Defense",
             "position_or_influence": "国家安全保障の観点から規則案へ意見を述べる"},
            {"name_or_role": "Department of State",
             "position_or_influence": "外交政策の観点から規則案へ意見を述べる"},
        ],
        "external_dependencies": [
            {"dependency": "同盟国の輸出管理協調（ワッセナー・アレンジメント等の多国間枠組み）",
             "description": "単独での規制強化は同盟国からの代替供給によって実効性が下がり得るため、"
                             "多国間協調の有無が実効性に影響する"},
        ],
        "historical_decision_patterns": [
            {"pattern": "先端半導体・AI関連規制は、一定の予告・調整期間を経て段階的に強化される傾向がある",
             "confidence": "中"},
        ],
        "known_exceptions": [
            {"description": "国家安全保障上の緊急性が高いと判断された場合、通常より短い予告期間で"
                             "即時発効する規則が発行されることがある",
             "confidence": "低"},
        ],
        "coverage_notes": "広く確立された構造的事実に限定して収録。Phase R1 Chunk3 Seedの初期投入分",
    },
]


def seed_institutions(client, *, activate: bool) -> list:
    created = []
    for institution in INSTITUTIONS:
        item = inst.seed_institution_profile(
            client,
            institution_id=institution["institution_id"],
            canonical_name=institution["canonical_name"],
            entity_type=institution["entity_type"],
            mandate=institution["mandate"],
            decision_authority=institution["decision_authority"],
            constraints=institution["constraints"],
            internal_stakeholders=institution["internal_stakeholders"],
            external_dependencies=institution["external_dependencies"],
            historical_decision_patterns=institution["historical_decision_patterns"],
            known_exceptions=institution["known_exceptions"],
            coverage_notes=institution["coverage_notes"],
            created_by=SEED_ACTOR_ID, activate=activate, reviewer_id=SEED_ACTOR_ID,
        )
        created.append(item)
        print(f"- {institution['canonical_name']} ({institution['institution_id']}): "
              f"knowledge_item_id={item['knowledge_item_id']}, status={item['status']}")
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true",
                         help="candidateをreview経路でactive化する（既定はcandidateのまま）")
    args = parser.parse_args()

    config = load_config()
    client = SupabaseClient(config)
    seed_institutions(client, activate=args.activate)


if __name__ == "__main__":
    main()
