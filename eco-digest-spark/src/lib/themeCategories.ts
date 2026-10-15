// テーマ探索のカテゴリ分類（決定論。AI・DB変更なし）。
// 候補（ExplorerTheme）のタイトル・要約・タグ・地域・Actorに含まれる語から、複数のカテゴリに属せる。
// 目的は「全体像を件数で把握し、絞り込める」こと。分類は目安であり、候補そのものの確定ではない。

import type { ExplorerTheme } from "@/lib/geoSourceLibraryApi";

export type ThemeCategoryKey =
  | "economic"
  | "export_sanctions"
  | "semiconductor"
  | "taiwan"
  | "middle_east"
  | "energy"
  | "russia"
  | "china"
  | "supply"
  | "other";

export interface ThemeCategoryDef {
  key: ThemeCategoryKey;
  /** 小文字で比較する語。日本語は部分一致、英単語は部分一致（短い語は含めない） */
  terms: string[];
  /** バッジの配色（Tailwind。視認性のため色相で区別する） */
  tone: string;
}

export const THEME_CATEGORIES: ThemeCategoryDef[] = [
  { key: "economic", tone: "bg-sky-50 text-sky-800 border-sky-200",
    terms: ["経済安全保障", "経済安保", "economic security", "economic_security", "関税", "tariff", "輸出規制", "規制強化"] },
  { key: "export_sanctions", tone: "bg-rose-50 text-rose-800 border-rose-200",
    terms: ["輸出管理", "輸出規制", "export control", "制裁", "sanction", "ofac", "entity list", "bis "] },
  { key: "semiconductor", tone: "bg-violet-50 text-violet-800 border-violet-200",
    terms: ["半導体", "semiconductor", "チップ", " chip", "先端技術", "tsmc", "nvidia", "ai chip"] },
  { key: "taiwan", tone: "bg-teal-50 text-teal-800 border-teal-200",
    terms: ["台湾", "taiwan", "インド太平洋", "indo-pacific", "南シナ海", "東シナ海", "南西", "統合幕僚"] },
  { key: "middle_east", tone: "bg-amber-50 text-amber-800 border-amber-200",
    terms: ["ホルムズ", "hormuz", "中東", "middle east", "red sea", "紅海", "イラン", "iran", "海上", "maritime", "海運", "shipping", "ukmto"] },
  { key: "energy", tone: "bg-orange-50 text-orange-800 border-orange-200",
    terms: ["エネルギー", "energy", "原油", "oil", "lng", "資源", "石油"] },
  { key: "russia", tone: "bg-slate-100 text-slate-700 border-slate-300",
    terms: ["ロシア", "russia", "ウクライナ", "ukraine"] },
  { key: "china", tone: "bg-red-50 text-red-800 border-red-200",
    terms: ["中国", "china", "chinese", "習近平", "北京"] },
  { key: "supply", tone: "bg-emerald-50 text-emerald-800 border-emerald-200",
    terms: ["サプライチェーン", "supply chain", "supply_chain", "サプライ"] },
];

export const OTHER_CATEGORY: ThemeCategoryDef = { key: "other", terms: [], tone: "bg-muted text-muted-foreground border-border" };

/** 候補の全文（検索・分類の対象）。小文字化して返す */
export function themeHaystack(th: ExplorerTheme): string {
  return [
    th.title, th.question, th.why_now, th.signal_summary, th.observed_change,
    ...th.themes, ...th.regions, ...th.actors, ...th.business_relevance,
    ...(th.evidence_articles ?? []).map((a) => a.title ?? ""),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

/** 候補が属するカテゴリ（複数可）。どれにも当たらなければ other */
export function categorizeTheme(th: ExplorerTheme): ThemeCategoryDef[] {
  const text = ` ${themeHaystack(th)} `;
  const hits = THEME_CATEGORIES.filter((c) => c.terms.some((w) => text.includes(w.toLowerCase())));
  return hits.length ? hits : [OTHER_CATEGORY];
}

/** キーワード絞り込み。空白区切りは全語を含む（AND）。AIは使わない */
export function matchesKeyword(th: ExplorerTheme, keyword: string): boolean {
  const terms = keyword.toLowerCase().split(/[\s　]+/).filter(Boolean);
  if (terms.length === 0) return true;
  const text = themeHaystack(th);
  return terms.every((w) => text.includes(w));
}

/** クイック検索の語（よく使う観測テーマ。利用統計ではなく、運用で追うテーマの固定リスト） */
export const QUICK_KEYWORDS = [
  "半導体", "輸出管理", "BIS", "ホルムズ海峡", "台湾", "制裁", "ロシア", "中国", "エネルギー", "サプライチェーン",
];
