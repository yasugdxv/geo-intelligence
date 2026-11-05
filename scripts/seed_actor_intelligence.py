"""Actor Intelligence Phase AI-1: 優先4名（Trump/Putin/Xi/Kim Jong Un）のSeed投入。

承認済みPlan: C:\\Users\\269811\\.claude\\plans\\pure-drifting-cosmos.md

設計方針（Plan 9章）:
- 既定はcandidateまでしか作らない（Seedスクリプト自身がknowledge_stewardとして
  自動active化することは、Actor Update Governanceの方針（Core Profileを勝手に
  上書きしない）と矛盾するため）。--activateを明示的に付けた場合のみactive化する。
- Core Profileは広く確立された、時間経過で大きく変わらない構造的事実のみを収録する
  （地位・権限構造・一般に知られた基本的な志向）。特定時点の発言・行動に基づく
  Current Posture/Issue Position/Decision Patternは、専用の調査（実データに基づく
  Evidence収集）を経ていないため今回は収録しない。needs_enrichmentに明記し、
  「不足を隠して埋める」より「不足していることを明示する」方針を取る
  （既存geo_hormuz_pilot_seed.py等と同じ、実確認できていない内容を創作しない方針）。
- Actor IDは安定文字列（例: "donald_trump"）。geo_entities.external_idsへ保存する。

使い方:
    python scripts/seed_actor_intelligence.py            # candidateのみ作成/更新
    python scripts/seed_actor_intelligence.py --activate # 上記に加えてactive化する
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from geo_db_client import SupabaseClient  # noqa: E402
from config_utils import load_config  # noqa: E402

import geo_actor_intelligence_service as ai  # noqa: E402

SEED_ACTOR_ID = "actor_intelligence_seed_phase_ai1"


def _statement(statement: str, *, information_class: str = "internal_hypothesis",
               confidence: str = "中") -> dict:
    return {"statement": statement, "information_class": information_class, "confidence": confidence,
            "evidence_ids": [], "observed_at": None, "last_reviewed_at": None, "reviewed_by": None}


PRIORITY_ACTORS = [
    {
        "actor_intelligence_id": "donald_trump",
        "canonical_name": "Donald Trump",
        "entity_type": "person",
        "core_profile": {
            "core_objectives_summary": ["米国第一（America First）路線での再選後の政策遂行"],
            "core_interests_summary": ["国内支持基盤の維持", "対外関与コストの最小化"],
            "worldview": [_statement(
                "取引（ディール）的な二国間交渉を、多国間枠組みより優先する傾向",
                information_class="external_assessment", confidence="中")],
            "core_objectives": [_statement(
                "国内製造業・雇用の保護を対外交渉の優先目標に位置づける",
                information_class="external_assessment", confidence="中")],
            "decision_preferences": [_statement(
                "関税・制裁等の経済的レバレッジを交渉の初期段階で用いる傾向",
                information_class="external_assessment", confidence="中")],
            "red_lines": [], "preferred_instruments": [], "structural_constraints": [_statement(
                "米国憲法上の三権分立・議会の予算/条約承認権による制約",
                information_class="observed_fact", confidence="高")],
        },
        "needs_enrichment": [
            "current_posture（直近の具体的な言動に基づく現在の姿勢）が未整備",
            "issue_positions（案件別の具体的立場）が未整備。個別Caseで実データ確認後に追加する",
            "decision_patterns（複数の先例から導出したパターン）が未整備。関連Precedent登録後に追加する",
        ],
        "coverage_notes": "Core Profileのみ、広く確立された構造的事実に限定して収録。Phase AI-1 Seedの初期投入分",
    },
    {
        "actor_intelligence_id": "vladimir_putin",
        "canonical_name": "Vladimir Putin",
        "entity_type": "person",
        "core_profile": {
            "core_objectives_summary": ["ロシアの大国としての地位・影響圏の維持"],
            "core_interests_summary": ["政権・体制の安定", "旧ソ連圏における影響力"],
            "worldview": [_statement(
                "NATO東方拡大を安全保障上の脅威として位置づける公式立場",
                information_class="actor_claim", confidence="高")],
            "core_objectives": [_statement(
                "近隣国（旧ソ連圏）に対する影響力の維持・強化",
                information_class="external_assessment", confidence="中")],
            "decision_preferences": [], "red_lines": [_statement(
                "NATOの旧ソ連圏（特にウクライナ）へのさらなる拡大を明確な一線として扱う",
                information_class="actor_claim", confidence="中")],
            "preferred_instruments": [], "structural_constraints": [_statement(
                "エネルギー輸出収入への財政的依存",
                information_class="external_assessment", confidence="中")],
        },
        "needs_enrichment": [
            "current_postureが未整備", "issue_positionsが未整備", "decision_patternsが未整備",
        ],
        "coverage_notes": "Core Profileのみ、広く確立された構造的事実に限定して収録。Phase AI-1 Seedの初期投入分",
    },
    {
        "actor_intelligence_id": "xi_jinping",
        "canonical_name": "Xi Jinping",
        "entity_type": "person",
        "core_profile": {
            "core_objectives_summary": ["中国共産党の一党統治体制の維持・強化", "中華民族の偉大な復興"],
            "core_interests_summary": ["台湾問題における「核心的利益」", "党の権威と社会の安定"],
            "worldview": [_statement(
                "台湾を中国の不可分の一部と位置づけ、統一を長期的な国家目標とする公式立場",
                information_class="actor_claim", confidence="高")],
            "core_objectives": [_statement(
                "経済発展と技術的自立（対外依存の低減）を並行して進める",
                information_class="external_assessment", confidence="中")],
            "decision_preferences": [], "red_lines": [_statement(
                "台湾の独立志向の動きを明確な一線として扱う",
                information_class="actor_claim", confidence="高")],
            "preferred_instruments": [], "structural_constraints": [_statement(
                "党内合意形成・集団指導の伝統的規範（近年は権力集中が進んでいるとの評価もある）",
                information_class="external_assessment", confidence="低")],
        },
        "needs_enrichment": [
            "current_postureが未整備", "issue_positionsが未整備", "decision_patternsが未整備",
        ],
        "coverage_notes": "Core Profileのみ、広く確立された構造的事実に限定して収録。Phase AI-1 Seedの初期投入分",
    },
    {
        "actor_intelligence_id": "kim_jong_un",
        "canonical_name": "Kim Jong Un",
        "entity_type": "person",
        "core_profile": {
            "core_objectives_summary": ["金王朝・体制の存続", "核・ミサイル能力を交渉力の中核とする対外戦略"],
            "core_interests_summary": ["体制の安全保障", "経済制裁の緩和"],
            "worldview": [_statement(
                "核・ミサイル能力を体制存続の最終的な保証と位置づける公式立場",
                information_class="actor_claim", confidence="高")],
            "core_objectives": [], "decision_preferences": [], "red_lines": [_statement(
                "核・ミサイル計画の完全放棄を交渉の前提とすることを拒否する立場",
                information_class="actor_claim", confidence="中")],
            "preferred_instruments": [], "structural_constraints": [_statement(
                "国際制裁下での経済的制約、中国への経済的依存",
                information_class="external_assessment", confidence="中")],
        },
        "needs_enrichment": [
            "current_postureが未整備", "issue_positionsが未整備", "decision_patternsが未整備",
        ],
        "coverage_notes": "Core Profileのみ、広く確立された構造的事実に限定して収録。Phase AI-1 Seedの初期投入分",
    },
]


def seed_priority_actors(client, *, activate: bool) -> list:
    created = []
    for actor in PRIORITY_ACTORS:
        item = ai.seed_actor_profile(
            client, actor_intelligence_id=actor["actor_intelligence_id"],
            canonical_name=actor["canonical_name"], entity_type=actor["entity_type"],
            core_profile=actor["core_profile"], needs_enrichment=actor["needs_enrichment"],
            coverage_notes=actor["coverage_notes"], created_by=SEED_ACTOR_ID,
            activate=activate, reviewer_id=SEED_ACTOR_ID,
        )
        created.append(item)
        print(f"- {actor['canonical_name']} ({actor['actor_intelligence_id']}): "
              f"knowledge_item_id={item['knowledge_item_id']}, status={item['status']}")
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true",
                         help="candidateをreview経路でactive化する（既定はcandidateのまま）")
    args = parser.parse_args()

    config = load_config()
    client = SupabaseClient(config)
    seed_priority_actors(client, activate=args.activate)


if __name__ == "__main__":
    main()
