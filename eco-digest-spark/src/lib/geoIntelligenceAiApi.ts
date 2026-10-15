// Phase 2F-2: Intelligence AI Search Results UI用のAPIクライアント。
// Phase 2F-1で完成したPermission-filtered Lexical Hybrid Search
// （POST /api/geo/intelligence-ai/search）をそのまま呼ぶだけで、Retrieval/Permission
// ロジックはBackend側のみが持つ（Frontendで権限filterを再実装しない）。
import { API_BASE as API_BASE_URL } from "@/lib/apiBase";
import { useMutation } from "@tanstack/react-query";
import { getIosAuthHeaders } from "@/lib/geoIosAuthHeader";

const API_BASE = API_BASE_URL;

export type GeoIntelligenceAiReviewStatus = "uploaded_only" | "in_review" | "approved";
export type GeoIntelligenceAiConfidentialityLabel =
  "DEPARTMENT_ONLY" | "SHD_ONLY" | "GROUP_SHAREABLE";

export interface GeoIntelligenceAiReportResult {
  source_type: "report";
  report_id: string;
  title: string | null;
  summary: string | null;
  date: string | null;
  region: string[] | null;
  theme: string | null;
  key_actors: string[] | null;
  review_status: GeoIntelligenceAiReviewStatus | null;
  confidentiality_label: GeoIntelligenceAiConfidentialityLabel | null;
  version: number | null;
  author: string | null;
  report_type: string | null;
  text_score: number;
  snippet: string | null;
}

export interface GeoIntelligenceAiKnowledgeResult {
  source_type: "knowledge_item";
  knowledge_item_id: string;
  title: string | null;
  summary: string | null;
  date: string | null;
  region: null;
  theme: null;
  key_actors: null;
  review_status: "approved";
  confidentiality_label: GeoIntelligenceAiConfidentialityLabel | null;
  source_report_id: string | null;
  source_report_project_id: string | null;
  text_score: number;
  snippet: string | null;
}

export type GeoIntelligenceAiSearchResult =
  GeoIntelligenceAiReportResult | GeoIntelligenceAiKnowledgeResult;

export interface GeoIntelligenceAiSearchResponse {
  results: GeoIntelligenceAiSearchResult[];
  report_count: number;
  knowledge_item_count: number;
}

// Phase 2F-3: date/region/theme/key_actor/review_status/source_type/confidentiality_labelは
// Permission判定合格後にBackend側のEligible Corpus解決へ適用される（UI側の後付けfilterとは
// 別に、分析対象の決定はこのfiltersを使う）。
export interface GeoIntelligenceAiFilters {
  date_from?: string;
  date_to?: string;
  region?: string;
  theme?: string;
  key_actor?: string;
  review_status?: GeoIntelligenceAiReviewStatus;
  source_type?: "report" | "knowledge_item";
  confidentiality_label?: GeoIntelligenceAiConfidentialityLabel;
}

export function useSearchIntelligenceAi() {
  return useMutation({
    mutationFn: async (params: {
      query: string;
      includeDemo?: boolean;
      limit?: number;
      filters?: GeoIntelligenceAiFilters;
    }): Promise<GeoIntelligenceAiSearchResponse> => {
      const res = await fetch(`${API_BASE}/api/geo/intelligence-ai/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getIosAuthHeaders() },
        body: JSON.stringify({
          query: params.query,
          include_demo: params.includeDemo ?? false,
          limit: params.limit ?? 50,
          filters: params.filters,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json();
    },
  });
}

// ── Phase 2F-3: Cross-report Grounded AI Analysis ──────────────────────────
export type GeoIntelligenceAiAnalysisMode =
  "summary" | "compare" | "temporal" | "coverage" | "reviewed";
export type GeoIntelligenceAiClaimType =
  "source_supported" | "cross_report_synthesis" | "ai_inference";

export interface GeoIntelligenceAiFinding {
  claim: string;
  claim_type: GeoIntelligenceAiClaimType;
  source_refs: string[];
}

export interface GeoIntelligenceAiCitation {
  ref_id: string;
  source_type: "report" | "knowledge_item";
  report_id?: string;
  knowledge_item_id?: string;
  title: string | null;
  date: string | null;
  version: number | null;
  review_status: GeoIntelligenceAiReviewStatus | null;
  confidentiality_label: GeoIntelligenceAiConfidentialityLabel | null;
  snippet: string | null;
}

export interface GeoIntelligenceAiAnalyzeResponse {
  answer: string;
  insufficient_evidence: boolean;
  findings: GeoIntelligenceAiFinding[];
  coverage: { regions: string[]; themes: string[]; key_actors: string[] };
  temporal_notes: string | null;
  warnings: string[];
  source_refs: GeoIntelligenceAiCitation[];
  retrieval_metadata: Record<string, unknown>;
}

export interface GeoIntelligenceAiSelectedSourceRef {
  source_type: "report" | "knowledge_item";
  id: string;
}

// ── Phase 2F-4: True Multi-turn Chat ────────────────────────────────────────
// 既存/api/geo/chat（Expert AI、別システム）と同じ「毎request全履歴をクライアントが送る」
// ステートレスパターン。Backend側は会話セッションを保持しない——historyには
// role/contentのプレーンテキストのみを入れ、Source本文やPermission結果は含めない
// （それらは毎turn本requestのidentityでゼロから再評価される）。
export interface GeoIntelligenceAiChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface GeoIntelligenceAiChatResponse extends GeoIntelligenceAiAnalyzeResponse {
  resolved_query: string;
}

export function useIntelligenceAiChat() {
  return useMutation({
    mutationFn: async (params: {
      message: string;
      history: GeoIntelligenceAiChatMessage[];
      mode?: GeoIntelligenceAiAnalysisMode;
      filters?: GeoIntelligenceAiFilters;
      includeDemo?: boolean;
      limit?: number;
    }): Promise<GeoIntelligenceAiChatResponse> => {
      const res = await fetch(`${API_BASE}/api/geo/intelligence-ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getIosAuthHeaders() },
        body: JSON.stringify({
          message: params.message,
          history: params.history,
          mode: params.mode ?? "summary",
          filters: params.filters,
          include_demo: params.includeDemo ?? false,
          limit: params.limit ?? 50,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json();
    },
  });
}

export function useAnalyzeIntelligenceAi() {
  return useMutation({
    mutationFn: async (params: {
      query: string;
      mode?: GeoIntelligenceAiAnalysisMode;
      filters?: GeoIntelligenceAiFilters;
      selectedSourceRefs?: GeoIntelligenceAiSelectedSourceRef[];
      includeDemo?: boolean;
      limit?: number;
    }): Promise<GeoIntelligenceAiAnalyzeResponse> => {
      const res = await fetch(`${API_BASE}/api/geo/intelligence-ai/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getIosAuthHeaders() },
        body: JSON.stringify({
          query: params.query,
          mode: params.mode ?? "summary",
          filters: params.filters,
          selected_source_refs: params.selectedSourceRefs,
          include_demo: params.includeDemo ?? false,
          limit: params.limit ?? 50,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `API error ${res.status}`);
      }
      return res.json();
    },
  });
}
