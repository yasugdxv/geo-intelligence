// Reading Presentation Layer v1（ユーザー指示）: response_id直接指定の汎用Presentation
// APIクライアント。バックエンドは/api/geo/situation-presentation/{response_id}
// （geo_ios_situation_presentation_api.py）に接続する。表示ロジックは既存IOS route
// （geoIosSituationPresentationApi.ts）と共通（同じgeo_ios_situation_presentation_service）
// のため、型定義もその上位互換として定義する。新しい推論はフロント側でも行わない。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useQuery } from "@tanstack/react-query";

const API_BASE = API_BASE_URL;

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

export interface GeoPresentationItem {
  value: string;
  _source?: string;
}

export interface GeoPresentationTextList {
  value: string[];
  _source?: string;
}

export interface GeoKeyPoint {
  investigation_question: GeoPresentationItem | null;
  one_line_assessment: GeoPresentationItem | null;
  initial_anomaly: GeoPresentationItem | null;
  primary_key_point: GeoPresentationItem | null;
  primary_hinge: GeoPresentationItem | null;
  why_this_is_the_key_point: GeoPresentationItem | null;
}

export interface GeoInterpretationOutlook {
  if_correct: GeoPresentationTextList | null;
  weakens_if: GeoPresentationTextList | null;
}

// Phase 10 Evidence Binding（ユーザー指示、Primary Source）: evidence_relevance /
// weakening_evidence_relevanceのID付き構造化Evidence。summaryはclaims[].source_refから
// 逆引きした短い要約文（無ければnull、フロント側で発明しない）。
export interface GeoStructuredEvidenceItem {
  evidence_id: string;
  summary: string | null;
  relation: "WEAKENS" | "CONTRADICTS" | null;
  claim_relevance: "direct" | "strongly_relevant" | "structural" | "peripheral" | null;
  source_type: string | null;
  rationale: string | null;
  _source?: string;
}

export interface GeoInterpretation {
  scenario_candidate_id: string | null;
  rank: number;
  displayed: boolean;
  is_main: boolean;
  closest_alternative: boolean;
  title: string | null;
  description: GeoPresentationItem | null;
  key_actors: GeoPresentationTextList | null;
  why_this_matters: GeoPresentationItem | null;
  confidence: GeoPresentationItem | null;
  // 2026-09-23変更: reasons（selection_rationaleとsupporting_evidence等を混在させた
  // 単一配列）を、由来の異なる2つのfieldへ分離した（geo_ios_situation_presentation_
  // service.pyのbuild_interpretations参照）。
  selection_rationale: GeoPresentationItem | null;
  supporting_points: GeoPresentationItem[];
  weak_points: GeoPresentationItem[];
  unknowns: GeoPresentationItem[];
  outlook: GeoInterpretationOutlook | null;
  supporting_evidence: GeoStructuredEvidenceItem[];
  weakening_evidence: GeoStructuredEvidenceItem[];
}

export interface GeoTrajectory {
  summary: GeoPresentationItem | null;
  gaps: GeoPresentationTextList | null;
}

export interface GeoActor {
  actor: string;
  wants: GeoPresentationItem | null;
  constraint: GeoPresentationItem | null;
  current_move: GeoPresentationItem | null;
  why_it_matters: GeoPresentationItem | null;
}

export interface GeoRelationshipPerception {
  observer: string | null;
  target: string | null;
  perception: GeoPresentationItem | null;
  claim_type: string | null;
  confidence: string | null;
}

export interface GeoRelationshipLeverage {
  actor: string | null;
  issue_component: string | null;
  leverage: GeoPresentationItem | null;
  constraint: GeoPresentationItem | null;
  actor_expected_response: GeoPresentationItem | null;
  analyst_expected_response: GeoPresentationItem | null;
  target_actor: string | null;
}

export interface GeoRelationships {
  perceptions: GeoRelationshipPerception[];
  leverage: GeoRelationshipLeverage[];
}

export interface GeoStructuralConstraints {
  discretion_mode: "HUMAN_DISCRETION_DOMINANT" | "STRUCTURE_DOMINANT" | "MIXED" | null;
  mode_rationale: GeoPresentationItem | null;
}

export interface GeoMediaEditorialItem {
  outlet: string | null;
  caveat: GeoPresentationItem | null;
}

export interface GeoMediaNarrativeDeviation {
  source: string | null;
  deviation_from_mainstream: GeoPresentationItem | null;
  why_it_matters: GeoPresentationItem | null;
}

export interface GeoMediaContext {
  editorial_context: GeoMediaEditorialItem[];
  narrative_deviation: GeoMediaNarrativeDeviation[];
  source_of_sources_note: GeoPresentationItem | null;
}

export interface GeoNextWatchItem {
  text: string;
  why?: string | null;
  _source?: string;
}

export interface GeoSituationPresentation {
  presentation_version: string;
  key_point: GeoKeyPoint;
  interpretations: GeoInterpretation[];
  trajectory: GeoTrajectory | null;
  actors: GeoActor[];
  relationships: GeoRelationships | null;
  structural_constraints: GeoStructuralConstraints | null;
  media_context: GeoMediaContext | null;
  monitoring_signals: GeoNextWatchItem[];
  research_needs: GeoNextWatchItem[];
}

export interface GeoSituationPresentationByResponseIdResponse {
  response_id: string;
  presentation_version: string;
  raw_analysis: { expert_response_id: string };
  presentation: GeoSituationPresentation;
}

export function useSituationPresentationByResponseId(responseId: string, debug = false) {
  return useQuery({
    queryKey: ["geoSituationPresentationByResponseId", responseId, debug],
    queryFn: () =>
      fetchJson<GeoSituationPresentationByResponseIdResponse>(
        `/api/geo/situation-presentation/${responseId}${debug ? "?debug=true" : ""}`,
      ),
    enabled: !!responseId,
  });
}
