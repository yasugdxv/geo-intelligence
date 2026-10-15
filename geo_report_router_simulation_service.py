"""Report Studio → Intelligence Analysis Platform: Router Simulation（Phase 2）。

詳細設計: docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md（Section K）。

20/30/40/50/70%は「confidence閾値」ではなく「目標Strong LLM escalation率
(target_total_escalation_rate)」である。Mandatory escalation（novelty gate/
importance）を先に適用し、残った集団に対してだけconfidence cutoffを解く
（remaining_rate = (t - m) / (1 - m)、m >= tならtarget_rate_infeasible）。

Routerが使うconfidenceは常に較正済みの`overall_calibrated_confidence`のみ
（`raw_confidence`・`unseen_reference_label_fields`はBenchmark診断専用であり
Production Routerの意思決定には使わない）。

escalated分の扱いはOracle/Upper-boundである（Strong LLM再実行がReferenceと100%
一致すると仮定した理論上限値）。Realistic Expected Reference Agreementは、
Representative Benchmark（Section F）で実測したhistorical-vs-rerun一致率を
`realistic_rerun_agreement`として渡せば併記できる。
"""

from __future__ import annotations

from geo_external_signal_service import _select_all_paginated

TARGET_ESCALATION_RATES = (0.2, 0.3, 0.4, 0.5, 0.7)


def load_predictions(client, benchmark_run_id: str, task: str, split_scheme: str, method: str) -> dict:
    """戻り値: {"validation": [...], "test": [...]}（predictions行のリスト）。"""
    rows = _select_all_paginated(client, "geo_report_semantic_census_predictions", {
        "select": "*", "benchmark_run_id": f"eq.{benchmark_run_id}", "task": f"eq.{task}",
        "split_scheme": f"eq.{split_scheme}", "method": f"eq.{method}",
    })
    result = {"validation": [], "test": []}
    for r in rows:
        if r["split"] in result:
            result[r["split"]].append(r)
    return result


def _mandatory_escalate(row: dict, novelty_gate_threshold: float, *, importance_by_cluster: dict = None,
                          importance_threshold: float = 0.8, importance_routing_enabled: bool = False) -> bool:
    if (row.get("novelty_proxy_score") or 0.0) > novelty_gate_threshold:
        return True
    if importance_routing_enabled and importance_by_cluster is not None:
        imp = importance_by_cluster.get(row["cluster_id"])
        if imp is not None and imp >= importance_threshold:
            return True
    return False


def solve_novelty_gate_threshold(validation_rows: list, *, percentile: float = 0.95) -> float:
    """Validation上のnovelty_proxy_scoreのpercentile分位点をgate閾値とする
    （デフォルトは上位5%を強制escalate対象とする保守的な設定）。"""
    scores = sorted(r.get("novelty_proxy_score") or 0.0 for r in validation_rows)
    if not scores:
        return 1.0
    idx = min(len(scores) - 1, int(round(percentile * (len(scores) - 1))))
    return scores[idx]


def solve_confidence_cutoff_for_target_rate(rows: list, target_rate: float) -> float:
    """rows中のoverall_calibrated_confidenceの分位点から、target_rate割合が
    escalate(confidence < cutoff)されるconfidence cutoffを求める。"""
    confidences = sorted(r["overall_calibrated_confidence"] for r in rows
                           if r.get("overall_calibrated_confidence") is not None)
    if not confidences:
        return 0.0
    idx = min(len(confidences) - 1, max(0, int(round(target_rate * len(confidences)))))
    return confidences[idx]


def simulate_router_for_method(client, benchmark_run_id: str, task: str, split_scheme: str, method: str, *,
                                  target_escalation_rates: tuple = TARGET_ESCALATION_RATES,
                                  importance_by_cluster: dict = None, importance_threshold: float = 0.8,
                                  novelty_gate_percentile: float = 0.95,
                                  realistic_rerun_agreement: float = None) -> list:
    """1 methodについて、target_escalation_rateごとのシミュレーション結果を返す。
    戻り値: [{"target_total_escalation_rate":..., "mandatory_rate":..., "actual_escalation_rate":...,
              "infeasible": bool, "oracle_expected_reference_agreement":...,
              "realistic_expected_reference_agreement":... or None,
              "strong_llm_calls":..., "candidate_method_calls":...}, ...]"""
    predictions = load_predictions(client, benchmark_run_id, task, split_scheme, method)
    validation_rows, test_rows = predictions["validation"], predictions["test"]

    novelty_gate_threshold = solve_novelty_gate_threshold(validation_rows, percentile=novelty_gate_percentile)

    results = []
    for target_rate in target_escalation_rates:
        for importance_routing_enabled in (False, True):
            mandatory_flags_validation = [
                _mandatory_escalate(r, novelty_gate_threshold, importance_by_cluster=importance_by_cluster,
                                      importance_threshold=importance_threshold,
                                      importance_routing_enabled=importance_routing_enabled)
                for r in validation_rows
            ]
            mandatory_rate = (sum(mandatory_flags_validation) / len(mandatory_flags_validation)
                                if mandatory_flags_validation else 0.0)

            entry = {
                "method": method, "target_total_escalation_rate": target_rate,
                "importance_routing_enabled": importance_routing_enabled,
                "mandatory_rate": mandatory_rate,
            }
            if mandatory_rate >= target_rate:
                entry.update({
                    "infeasible": True, "actual_escalation_rate": mandatory_rate,
                    "cutoff": None,
                })
            else:
                remaining_rate = (target_rate - mandatory_rate) / (1 - mandatory_rate)
                remaining_validation = [r for r, m in zip(validation_rows, mandatory_flags_validation) if not m]
                cutoff = solve_confidence_cutoff_for_target_rate(remaining_validation, remaining_rate)
                entry.update({"infeasible": False, "cutoff": cutoff})

            # Test側へ適用
            n_test = len(test_rows)
            escalated_ids = []
            non_escalated_ids = []
            for r in test_rows:
                mandatory = _mandatory_escalate(r, novelty_gate_threshold, importance_by_cluster=importance_by_cluster,
                                                  importance_threshold=importance_threshold,
                                                  importance_routing_enabled=importance_routing_enabled)
                confidence_escalate = (not entry["infeasible"] and entry["cutoff"] is not None
                                          and (r.get("overall_calibrated_confidence") or 0.0) < entry["cutoff"])
                if mandatory or confidence_escalate:
                    escalated_ids.append(r["cluster_id"])
                else:
                    non_escalated_ids.append(r["cluster_id"])
                # non-escalated分のfield一致率は別途Benchmark結果(field_metrics_json)から
                # 参照する想定のため、ここではescalation率のみ集計する（呼び出し側で
                # geo_report_semantic_census_benchmarksのfield_metrics_jsonと突き合わせる）。

            escalated_count = len(escalated_ids)
            actual_escalation_rate = (escalated_count / n_test) if n_test else 0.0
            entry["actual_escalation_rate"] = actual_escalation_rate
            entry["strong_llm_calls"] = escalated_count
            entry["candidate_method_calls"] = n_test - escalated_count
            # Gate 2(Realistic Router Simulation)がToken-aware batchingで実際の
            # API呼び出し数/トークン数を見積もる際に使う、Test split内のcluster_id一覧
            # （strong_llm_calls/candidate_method_callsはcluster数であり、実際のバッチ
            # API呼び出し回数とは異なる——1バッチ=最大20クラスタ相当のため）。
            entry["escalated_cluster_ids"] = escalated_ids
            entry["non_escalated_cluster_ids"] = non_escalated_ids
            entry["novelty_gate_threshold"] = novelty_gate_threshold
            entry["oracle_note"] = (
                "escalated分はStrong LLM=Reference相当(100%一致)と仮定した理論上限値。"
                "Strong LLM再実行がReferenceと必ず一致するとは限らない(Section F参照)。"
            )
            entry["realistic_rerun_agreement"] = realistic_rerun_agreement
            results.append(entry)
    return results
