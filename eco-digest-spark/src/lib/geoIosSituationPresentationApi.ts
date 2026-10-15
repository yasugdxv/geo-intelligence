// Geopolitical Intelligence OS: Situation Analysis Presentation v1（「局面を読む」）APIクライアント。
// バックエンドは/api/geo/ios/situation-presentation/{id}（geo_ios_situation_presentation_api.py）
// に接続する。新しい推論はフロント側でも行わない——既存のraw_analysisを整形したpresentationを
// そのまま表示するだけ。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const API_BASE = API_BASE_URL;

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  return res.json();
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return fetchJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface GeoIosPresentationItem {
  value: string;
  _source?: string;
}

export interface GeoIosPresentationTextList {
  value: string[];
  _source?: string;
}

export interface GeoIosKeyPoint {
  investigation_question: GeoIosPresentationItem | null;
  one_line_assessment: GeoIosPresentationItem | null;
  // 2026-09-23追加、ユーザー指示「①何に気づいた？」への対応: 分析全体で共通の
  // Cue（最初の違和感）。既存key_point_assessment.initial_anomalyはバックエンドの
  // build_key_pointが以前から返していたが、IOS画面側では未使用だった。
  initial_anomaly: GeoIosPresentationItem | null;
  primary_key_point: GeoIosPresentationItem | null;
  primary_hinge: GeoIosPresentationItem | null;
  why_this_is_the_key_point: GeoIosPresentationItem | null;
}

export interface GeoIosInterpretationOutlook {
  if_correct: GeoIosPresentationTextList | null;
  weakens_if: GeoIosPresentationTextList | null;
}

// Phase 10 Evidence Binding: evidence_relevance/weakening_evidence_relevanceのID付き
// 構造化Evidence。summaryはclaims[].source_refから逆引きした短い要約文（無ければnull、
// フロント側で発明しない）。evidence_idはUI表示には使わない（内部参照のみ）。
export interface GeoIosStructuredEvidenceItem {
  evidence_id: string;
  summary: string | null;
  relation: "WEAKENS" | "CONTRADICTS" | null;
  claim_relevance: "direct" | "strongly_relevant" | "structural" | "peripheral" | null;
  source_type: string | null;
  rationale: string | null;
  // 5つの読み方案UI再設計（2026-10-01）: geo_evidence.document_id→geo_documents.
  // published_atという既存DB経路から決定論的に補完したdate。解決できない場合はnull
  // （フロント側で推測しない、空欄のまま表示する）。
  date?: string | null;
  // Reading Report v2（2026-10-02）: このReading自身のinteraction_basis.actor_ref由来の
  // 主要主体名（build_actor_rolesと同じ決定論的な値）。解決できない場合はnull。
  actor?: string | null;
  _source?: string;
}

// 5つの読み方案UI再設計（2026-10-01、ユーザー指示）: 新しい推論は行わず、既存の
// interaction_basis/event_roleだけから機械的に分類したPrimary/Peripheral。
// 一意に決められない場合はunclassified（無理に推測しない）。
export interface GeoIosActorRole {
  actor: string;
  role: "primary" | "peripheral" | "unclassified";
  _source?: string | null;
}

// 既存trajectory_assessment/key_actor_profiles/situation_actor_assessmentを
// 「きっかけ→観測された行動→相手の反応→現在」へ再配置しただけ。新規LLM生成なし。
// 対応するfieldが無い段階は配列に含まれない（4段階に無理に揃えない）。
export interface GeoIosSituationPhase {
  phase: "trigger" | "primary_action" | "reaction" | "current";
  actor: string | null;
  description: GeoIosPresentationItem;
}

// Expert Review View（2026-10-04、読み方UI改善）: 「専門家からレビューコメントをもらう」体験用の
// 表示構造。バックエンドgeo_ios_expert_review_viewが保存済みExpert AI応答から決定論的に作り、
// headline/commentだけはキャッシュ済みPresentation LLM Transformation（comment_origin="llm"）、
// 無い/失敗時は既存文章を短く整えたFallback（"fallback"）。新しい推論はフロントでも行わない。
export interface GeoIosExpertReviewStakeholder {
  name: string;
  role: "primary" | "related";
}

export interface GeoIosExpertReviewEvidenceRow {
  actor: string | null;
  what: string;
  // 「このEvidenceがこの読みにどう効くか」。保存済みの理由があればそれ（"saved"）、無い場合はPresentation
  // LLMがEvidenceとReadingの関係だけを整理したもの（"generated"）。Evidence本体は変更しない。
  meaning: string | null;
  meaning_origin?: "saved" | "generated" | null;
  date: string | null;
}

export interface GeoIosExpertReviewFlowStep {
  phase: string;
  label: string;
  actor: string | null;
  text: string;
  full_text: string;
}

export interface GeoIosExpertReviewBehindRow {
  actor: string;
  interest: string;
  action: string;
  constraint: string;
}

export interface GeoIosExpertReviewCard {
  version: string;
  perspective: { label: string; key: string };
  perspective_tags: { key: string; label: string }[];
  stakeholders: GeoIosExpertReviewStakeholder[];
  is_primary: boolean;
  headline: string;
  comment: string;
  comment_origin: "llm" | "fallback";
  origin: string | null;
  surface_behind: { surface: string | null; behind: GeoIosExpertReviewBehindRow[] } | null;
  flow: GeoIosExpertReviewFlowStep[];
  supporting: GeoIosExpertReviewEvidenceRow[];
  contradicting: GeoIosExpertReviewEvidenceRow[];
  break_conditions: string[];
  watch: string[];
}

export interface GeoIosExpertReviewOverview {
  version: string;
  point: string | null;
  anomaly: string | null;
  // 「ここまでの流れ」（局面全体で共通、3〜6ステップの力学の変化の連鎖）。無ければ各カードのflow
  // （保存済み文の再配置）へフォールバックする。
  flow?: { steps: string[]; origin: "llm" } | null;
  key_actors: { name: string; why: string | null }[];
  layers: { key: string; label: string; present: boolean }[];
  // pending=キャッシュ未作成（フロントが一度だけ生成をリクエストする）、fallback=生成失敗
  comment_status?: "cache_hit" | "generated" | "pending" | "fallback" | "empty" | null;
}

export interface GeoIosInterpretation {
  scenario_candidate_id: string;
  rank: number;
  displayed: boolean;
  is_main: boolean;
  expert_review?: GeoIosExpertReviewCard | null;
  title: string;
  description: GeoIosPresentationItem | null;
  key_actors: GeoIosPresentationTextList | null;
  // 5つの読み方案UI再設計（2026-10-01）: key_actorsのうち、このReading自体が
  // 根拠にしているActor（Primary）とそれ以外（Peripheral）を機械的に分類したもの。
  // key_actorsが無い/分類できない場合は空配列。
  actor_roles: GeoIosActorRole[];
  // 既存fieldの再配置のみ。対応する段階が無い場合はnull。
  situation_phases: GeoIosSituationPhase[] | null;
  why_this_matters: GeoIosPresentationItem | null;
  confidence: GeoIosPresentationItem | null;
  // 2026-09-23変更、ユーザー指摘「『この読み方が強い理由は』はまとめないで、CQASを
  // それぞれのパートに分けて説明しよ」: reasons（selection_rationaleとsupporting_
  // evidence等を混在させた単一配列）を、由来の異なる2つのfieldへ分離した。
  selection_rationale: GeoIosPresentationItem | null;
  supporting_points: GeoIosPresentationItem[];
  weak_points: GeoIosPresentationItem[];
  unknowns: GeoIosPresentationItem[];
  outlook: GeoIosInterpretationOutlook | null;
  // Phase 10 Evidence Binding: 構造化Evidence（空の場合はsupporting_points/weak_points
  // という自由記述fallbackを使うこと）。
  supporting_evidence: GeoIosStructuredEvidenceItem[];
  weakening_evidence: GeoIosStructuredEvidenceItem[];
  // 2026-09-23追加、ユーザー指示「気づく→見る→疑う→仮説→読む」というストーリー化:
  // derived_from_hypothesis_idが無い（hypothesis_registerが生成されない分析
  // プロファイルの）候補では全てnullになる——その場合はステップ自体を自然に省略する
  // こと（欠けたfieldを埋めるための新しい文章をフロント側でも生成しない）。
  what_was_seen: GeoIosPresentationItem | null;
  what_was_doubted: GeoIosPresentationItem | null;
  hypothesis_formed: GeoIosPresentationItem | null;
}

// READING_EXPOSURE_LAYER_V1配線（2026-09-24）: Step2の最終5件構成。MAIN=Sが選んだ本線
// (Reading1)、ALTERNATIVE=Sが選んだ代替(Reading2-3、interpretationsと同じ内容)、
// FRONTIER=Sの本線には採用されなかったが検討価値のある読み(Reading4)、
// EXPLORATORY=Reading Portfolio/Expert Explorationから選ばれた別系統の読み(Reading5)。
// REVIEW MODE（§19-20、2026-09-25追加）: CURRENT=既存レポートから再構成した現在の読み
// (Reading A)。ALTERNATIVEはREVIEW MODEでも再利用する(Expert AIが提案するB以降)。
export type GeoIosReadingRole = "MAIN" | "ALTERNATIVE" | "FRONTIER" | "EXPLORATORY" | "CURRENT";

// GeoIosInterpretationと同じCard形状に、Reading Exposure固有のfieldを足したもの。
// FRONTIER/EXPLORATORYではselection_rationale/outlook/what_was_seen等はnullになる
// （hypothesis_register/Reading Portfolio由来のraw構造が異なるため、無理に埋めない）。
// §5 ユーザー向けTag。内部role（MAIN/ALTERNATIVE/FRONTIER/EXPLORATORY）とは別軸——
// 5つのReadingの内容を比較した上でPresentation Transformation Layerが判断する
// （役割からの機械的な1:1変換ではない）。
export type GeoIosReadingSemanticTag =
  "王道" | "有力な別解" | "前提を疑う" | "構造から読む" | "ジャンプ視点";

export interface GeoIosReadingNarrative {
  actors: string;
  objectives: string;
  action: string;
  interaction: string;
  current_situation: string;
  direction: string;
  // 13-4対応（2026-09-28追加）: 上位思想・相手観 / 現在の制約・使えるレバーを
  // Reading Detailで一読できるよう抜き出した短い構造化フィールド。
  // 旧セッションのcacheには存在しないため任意（省略時はUI側で非表示にする）。
  orientation_and_counterparty_view?: string;
  constraints_and_leverage?: string;
}

export interface GeoIosReadingReasoning {
  observation: string;
  why_it_matters: string;
  interpretation: string;
}

export interface GeoIosReactionChainItem {
  actor: string;
  trigger: string;
  likely_reaction: string;
}

// §23 Presentation Transformation Layerの出力shape。既存の生フィールド（description等）
// を人間可読な形へ再編集したもの。バックエンドLLM呼び出しが未設定/失敗した場合はnullになり
// ——その場合フロント側は既存の生フィールド表示にフォールバックする。
export interface GeoIosActorDynamicsCheck {
  actor_is_grammatical_subject: boolean;
  states_actor_objective_or_orientation: boolean;
  states_counterparty_or_constraint: boolean;
  is_event_only: boolean;
}

export interface GeoIosReadingPresentation {
  semantic_tag: GeoIosReadingSemanticTag;
  title: string;
  one_line_thesis: string;
  narrative: GeoIosReadingNarrative;
  reasoning: GeoIosReadingReasoning;
  reaction_chain: GeoIosReactionChainItem[];
  expected_direction: string;
  // Final Expert Session Readiness Validation Phase B（2026-09-28、ユーザー指示）:
  // EVENT_ONLY_READING自己申告。内部QA/Release Gate専用signalであり、専門家向けUIには
  // 表示しない（このファイルの型定義に存在するのみで、レンダリングコードからは参照しない）。
  actor_dynamics_check?: GeoIosActorDynamicsCheck;
}

export interface GeoIosReadingExposureItem extends GeoIosInterpretation {
  reading_role: GeoIosReadingRole;
  exploratory: boolean;
  selected: boolean;
  // 内部status(retained/unresolved/rejected)をそのまま見せず自然文へ翻訳済み
  // （バックエンドgeo_ios_situation_presentation_service._why_not_main_label）。
  // MAIN/ALTERNATIVEではnull。
  why_not_main: GeoIosPresentationItem | null;
  reading_presentation: GeoIosReadingPresentation | null;
}

export interface GeoIosTrajectory {
  summary: GeoIosPresentationItem | null;
  gaps: GeoIosPresentationTextList | null;
}

export interface GeoIosActor {
  actor: string;
  wants: GeoIosPresentationItem | null;
  constraint: GeoIosPresentationItem | null;
  current_move: GeoIosPresentationItem | null;
  why_it_matters: GeoIosPresentationItem | null;
}

// 5つの読み方案UI再設計（2026-10-01）: 既存media_source_context.media_editorial_
// context[].observed[]/hypotheses[]を媒体単位の比較表へ展開しただけ。Emphasized
// Actor/DateはExpert AI出力に存在しないため、この型にも持たせない（無理に補わない）。
export interface GeoIosMediaComparisonRow {
  outlet: string;
  framing: GeoIosPresentationItem | GeoIosPresentationTextList | null;
  interpretation: GeoIosPresentationTextList | null;
}

// Reading Report v2（2026-10-02）: 既存response.business_impact（direct/indirect/
// environment_change）をsocial/industryの2区分へ割り当てただけ。新しい企業影響は
// 生成しない。Primary Storyline（MAIN Reading）専用のresponse-level fieldのため、
// 表示する側（フロント）はis_main=trueのReadingでのみ使うこと。
export interface GeoIosBusinessImpact {
  social_environment: GeoIosPresentationItem | null;
  industry_business: GeoIosPresentationTextList | null;
}

export interface GeoIosNextWatchItem {
  text: string;
  why?: string | null;
  _source?: string;
}

export interface GeoIosSituationPresentation {
  presentation_version: string;
  expert_review?: GeoIosExpertReviewOverview | null;
  key_point: GeoIosKeyPoint;
  interpretations: GeoIosInterpretation[];
  // READING_EXPOSURE_LAYER_V1配線（2026-09-24）: Step2の最終5件構成
  // (Reading1-3=S Rank#1-3, Reading4=FRONTIER, Reading5=EXPLORATORY)。空配列の場合は
  // Reading Exposure Composerが未接続/失敗しており、interpretationsへフォールバック
  // すること（Backward Compatibility）。
  reading_exposure: GeoIosReadingExposureItem[];
  trajectory: GeoIosTrajectory | null;
  actors: GeoIosActor[];
  // 5つの読み方案UI再設計（2026-10-01）: 媒体単位の比較（§11B）。既存build_media_
  // contextとは別に、媒体ごとのobserved/hypothesesを展開したもの。無ければnull。
  media_comparison: GeoIosMediaComparisonRow[] | null;
  // Reading Report v2（2026-10-02）: Primary Storyline専用。MAIN Readingの「見えるシナリオ」
  // セクションでのみ使う。無ければnull。
  business_impact: GeoIosBusinessImpact | null;
  // ①(の一部)：特定の読み方に紐付かない、局面全体のmonitoring_indicators。
  // 読み方ごとのif_correct(各interpretation.outlook.if_correct)と合わせて①として表示する。
  monitoring_signals: GeoIosNextWatchItem[];
  // ②：まだ分かっていない、追加調査が必要な要素(identified_knowledge_gaps/additional_research)。
  research_needs: GeoIosNextWatchItem[];
}

export interface GeoIosReviewImport {
  review_import_id: string;
  analysis_session_id: string;
  original_filename: string | null;
  status: "pending" | "context_extracted" | "readings_ready" | "failed";
  error_message: string | null;
  extracted_context: {
    analysis_theme: string;
    current_situation: string;
    question: string;
    report_context: string;
    not_explicit_fields: string[];
  } | null;
  reconstructed_reading: Record<string, unknown> | null;
  current_reading_scenario_candidate_id: string | null;
  selected_scenario_candidate_id: string | null;
  revision_strategy: "keep_and_reinforce" | "replace" | "integrate" | null;
}

export interface GeoIosSituationPresentationResponse {
  analysis_session_id: string;
  presentation_version: string;
  raw_analysis: {
    expert_response_id: string | null;
    selected_scenario_candidate_id: string | null;
  };
  presentation: GeoIosSituationPresentation;
  // REVIEW MODE（mode="review"のsessionのみ）。CREATE MODEでは常にundefined。
  review_import?: GeoIosReviewImport | null;
}

// Expert Review Commentの生成は、画面表示（GET）では行わずキャッシュのみ参照する。キャッシュが
// 未作成（comment_status="pending"）の間は既存文章のFallbackを表示し、このhookが同じidにつき
// 一度だけ生成をPOSTして、完了後にGETを再取得する（Frozen Responseは変更しない・Expert AI Coreは
// 再実行しない。キャッシュ後は同じ内容が安定して表示される）。
function useAutoGenerateExpertReview(
  status: string | null | undefined,
  id: string,
  postPath: string,
  invalidateKey: unknown[],
) {
  const qc = useQueryClient();
  const requestedRef = useRef<string | null>(null);
  useEffect(() => {
    if (status !== "pending" || !id || requestedRef.current === id) return;
    requestedRef.current = id;
    fetchJson(postPath, { method: "POST" })
      .catch(() => undefined)
      .finally(() => qc.invalidateQueries({ queryKey: invalidateKey }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, id]);
}

export function useSituationPresentation(sessionId: string, debug = false) {
  const query = useQuery({
    queryKey: ["geoIosSituationPresentation", sessionId, debug],
    queryFn: () =>
      fetchJson<GeoIosSituationPresentationResponse>(
        `/api/geo/ios/situation-presentation/${sessionId}${debug ? "?debug=true" : ""}`,
      ),
    enabled: !!sessionId,
  });
  useAutoGenerateExpertReview(
    query.data?.presentation?.expert_review?.comment_status,
    sessionId,
    `/api/geo/ios/situation-presentation/${sessionId}/expert-review`,
    ["geoIosSituationPresentation", sessionId],
  );
  return query;
}

// Expert Session Readiness Patch Phase S1（2026-10-02）: IOS Analysis Sessionに
// ラップされていないresponse_id（Gold/Calibration Case）を、同じbuild_situation_
// presentation()経由・同じv2 presentation shapeで直接取得する。バックエンド既存route
// （geo_ios_situation_presentation_api.get_situation_presentation_by_response_id、
// 読み取り専用・新規LLM呼び出しなし）をそのまま叩くだけ——新しいAPI・新しいSession
// 生成・Expert AI再実行は一切行わない。
export interface GeoIosSituationPresentationByResponseIdResponse {
  response_id: string;
  presentation_version: string;
  raw_analysis: { expert_response_id: string };
  presentation: GeoIosSituationPresentation;
}

export function useSituationPresentationByResponseId(responseId: string, debug = false) {
  const query = useQuery({
    queryKey: ["geoSituationPresentationByResponseId", responseId, debug],
    queryFn: () =>
      fetchJson<GeoIosSituationPresentationByResponseIdResponse>(
        `/api/geo/situation-presentation/${responseId}${debug ? "?debug=true" : ""}`,
      ),
    enabled: !!responseId,
  });
  useAutoGenerateExpertReview(
    query.data?.presentation?.expert_review?.comment_status,
    responseId,
    `/api/geo/situation-presentation/${responseId}/expert-review`,
    ["geoSituationPresentationByResponseId", responseId],
  );
  return query;
}

// §13「＋ 別の読み方を探す」の方向性選択肢。バックエンドgeo_ios_reading_exposure_composer.
// _DIRECTION_INSTRUCTIONSのキーと一致させる（"custom"のみフロント固有、custom_textを使う）。
export type GeoIosGenerateMoreDirection =
  | "auto"
  | "more_orthodox"
  | "invert_premise"
  | "different_actor_lead"
  | "domestic_politics"
  | "international_relations"
  | "economic_structure"
  | "negotiation_relationship"
  | "bold_hypothesis"
  | "custom";

export interface GeoIosGenerateMoreReadingsResult {
  created: boolean;
  reason?: string | null;
  total_readings?: number | null;
}

export function useGenerateMoreReadings(sessionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      direction: GeoIosGenerateMoreDirection;
      custom_text?: string;
      actor_id: string;
    }) =>
      postJson<GeoIosGenerateMoreReadingsResult>(
        `/api/geo/ios/situation-presentation/${sessionId}/generate-more-readings`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["geoIosSituationPresentation", sessionId] });
    },
  });
}
