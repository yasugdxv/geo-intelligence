import { cloneElement, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  type GeoIosReadingExposureItem,
  type GeoIosReadingRole,
  type GeoIosPresentationItem,
  type GeoIosStructuredEvidenceItem,
  type GeoIosReadingNarrative,
  type GeoIosReadingReasoning,
  type GeoIosReactionChainItem,
  type GeoIosKeyPoint,
  type GeoIosActorRole,
  type GeoIosSituationPhase,
  type GeoIosActor,
  type GeoIosBusinessImpact,
  type GeoIosNextWatchItem,
  type GeoIosSituationPresentation,
  type GeoIosExpertReviewCard,
  type GeoIosExpertReviewEvidenceRow,
} from "@/lib/geoIosSituationPresentationApi";
import { useLanguage } from "@/lib/i18n";

// Expert Session Readiness Patch Phase S1（2026-10-02）: Reading Presentation v2の
// 表示primitive（ReportSection〜ReadingReportCard、ReadingSelectorGrid）を、
// geo-ios-situation-analysis-view.tsx（IOS Analysis Session経由）から切り出した共有
// モジュール。Frozen済みのIOS Session側ページの挙動・出力は一切変更しない（そのまま
// ここからimportし直すだけ）——新しい消費者は、同じresponse_idを直接指定するGold Case
// 確認用ページ（geo-situation-presentation-view.tsx）。情報構造・推論ロジックは変更
// しない、純粋なコード移動＋読み取り専用の合成コンポーネント追加のみ。

export function isEmpty(item: { value: unknown } | null | undefined): boolean {
  if (!item) return true;
  return Array.isArray(item.value) ? item.value.length === 0 : !item.value;
}

// LLMが情報不足時に出力する定型文（§24: 明示されていない情報は無理に埋めない）。UI側では
// この定型文をそのまま並べず、該当セクション自体を静かに省略する。
export function isDisclosed(text: string | null | undefined): text is string {
  if (!text) return false;
  const trimmed = text.trim();
  return trimmed.length > 0 && trimmed !== "明示されていない";
}

// Reading Report v2（2026-10-02、縦型レポートPresentation）: 7セクションを1枚の連続した
// レポートとして見せる。個々のセクションは独立カードではなく、1つの大きなCard内の
// border-bottom区切りとして並べる（ユーザー指示§15: Cardが細切れに見えないこと）。
function ReportSection({
  step,
  title,
  children,
}: {
  step: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="px-[22px] sm:px-[32px] py-[24px] sm:py-[28px] border-b border-[#d8e2e7] last:border-b-0 space-y-3">
      <h3 className="m-0 flex items-center gap-[10px] text-[19px] sm:text-[21px] font-black text-[#0b3568]">
        <span className="w-[28px] h-[28px] rounded-full bg-[#e8f1ff] text-[#0e5fc2] grid place-items-center text-[13px] font-black shrink-0">
          {step}
        </span>
        {title}
      </h3>
      {children}
    </section>
  );
}

// §6 読み方詳細: reading_presentation.narrativeがあればWHO→OBJECTIVE→ACTION→
// INTERACTION→CURRENT_SITUATION→DIRECTIONの段落＋reasoning.interpretation（結論）を、
// 無ければ既存のfallback組み立て（what_was_seen/doubted/hypothesis_formed等）を連続した
// 文章として表示する。新しい文章の生成・言い換えはしない——既存フィールドをそのまま
// 段落として並べるだけ（ユーザー指示: 箇条書き主体にしない）。
function ReadingDetailSection({
  interpretation,
  oneLineLabel,
}: {
  interpretation: GeoIosReadingExposureItem;
  oneLineLabel: string;
}) {
  const rp = interpretation.reading_presentation;
  const oneLineThesis = rp?.one_line_thesis || interpretation.description?.value;
  let paragraphs: string[] = [];
  let structuredNotes: { label: string; text: string }[] = [];
  if (rp) {
    const order: Array<keyof GeoIosReadingNarrative> = [
      "actors",
      "objectives",
      "action",
      "interaction",
      "current_situation",
      "direction",
    ];
    paragraphs = order.map((k) => rp.narrative[k]).filter(isDisclosed);
    if (isDisclosed(rp.reasoning.interpretation)) paragraphs.push(rp.reasoning.interpretation);
    structuredNotes = [
      { label: oneLineLabel, text: rp.narrative.orientation_and_counterparty_view },
      { label: "", text: rp.narrative.constraints_and_leverage },
    ].filter((n): n is { label: string; text: string } => isDisclosed(n.text));
  } else {
    const fallbackReasoning = buildFallbackReasoning(interpretation);
    paragraphs = [
      fallbackReasoning?.observation,
      fallbackReasoning?.why_it_matters,
      fallbackReasoning?.interpretation,
    ].filter(isDisclosed);
  }
  if (!isDisclosed(oneLineThesis) && paragraphs.length === 0) return null;
  return (
    <>
      {isDisclosed(oneLineThesis) && (
        <p className="m-0 text-[19px] sm:text-[20px] leading-[1.9] font-semibold text-[#12345a]">
          {oneLineThesis}
        </p>
      )}
      {paragraphs.map((p, i) => (
        <p key={i} className="m-0 text-[18px] leading-[1.9] text-[#173758]">
          {p}
        </p>
      ))}
      {structuredNotes.length > 0 && (
        <div className="pt-2 space-y-2">
          {structuredNotes.map((n, i) => (
            <p key={i} className="m-0 text-[17px] leading-[1.85] text-[#294a6d]">
              {n.text}
            </p>
          ))}
        </div>
      )}
    </>
  );
}

// §7 主要なステークホルダーと、その思惑（Reading Report v2）: actor_roles（このReadingの
// Primary判定）と、response-levelのpresentation.actors（既存key_actor_profiles由来の
// wants/constraint/current_move/why_it_matters）を突き合わせ、Primary Actor1件＝
// Stakeholderカード1枚として表示する。委員会名・内部派閥等、既存fieldに存在しない
// 粒度のsub-actorは創作しない（ユーザー指示§17 Data Grounding）。
function StakeholderSection({
  actorRoles,
  allActors,
  peripheralLabel,
}: {
  actorRoles: GeoIosActorRole[];
  allActors: GeoIosActor[];
  peripheralLabel: string;
}) {
  const primary = actorRoles.filter((r) => r.role === "primary");
  const peripheral = actorRoles.filter((r) => r.role !== "primary");
  const actorByName = new Map(allActors.map((a) => [a.actor, a]));
  if (primary.length === 0 && peripheral.length === 0) return null;
  return (
    <>
      <div className="grid sm:grid-cols-2 gap-[16px]">
        {primary.map((r) => {
          const profile = actorByName.get(r.actor);
          const roleLine = profile?.wants?.value;
          const bodyParts = [profile?.current_move?.value, profile?.why_it_matters?.value].filter(
            isDisclosed,
          );
          const constraintPart = profile?.constraint?.value;
          return (
            <div
              key={r.actor}
              className="border border-[#d8e2e7] rounded-[14px] p-[20px] bg-[#fafcff]"
            >
              <h4 className="m-0 text-[19px] font-black text-[#0b3568]">{r.actor}</h4>
              {isDisclosed(roleLine) && (
                <p className="m-0 mt-1.5 text-[15px] font-bold text-[#36587b]">{roleLine}</p>
              )}
              {bodyParts.map((p, i) => (
                <p key={i} className="m-0 mt-2 text-[16px] leading-[1.85] text-[#26486c]">
                  {p}
                </p>
              ))}
              {isDisclosed(constraintPart) && (
                <div className="mt-3 pt-3 border-t border-dashed border-[#cbd6e2] text-[15px] text-[#26486c]">
                  {constraintPart}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {peripheral.length > 0 && (
        <div className="flex items-start gap-2 flex-wrap text-[14px] text-[#687b88]">
          <span className="font-bold shrink-0">{peripheralLabel}:</span>
          <span>{peripheral.map((r) => r.actor).join("、")}</span>
        </div>
      )}
    </>
  );
}

// §8 局面に至るまでの状況推移（Reading Report v2）: 既存build_situation_phases
// （trigger/primary_action/reaction/current）を、事象の羅列ではなくActorの判断の変化
// として読めるラベル・縦のTimeline UIへ再配置する。新しい状況推移の生成はしない
// （ユーザー指摘「事象ベースへの先祖返り」への対応——ラベル・見せ方だけを判断軸にする）。
function TimelineSection({
  phases,
  phaseLabels,
}: {
  phases: GeoIosSituationPhase[] | null;
  phaseLabels: Record<GeoIosSituationPhase["phase"], string>;
}) {
  if (!phases || phases.length === 0) return null;
  return (
    <div className="mt-2 border-l-[3px] border-[#bdd0e8] pl-[22px] space-y-[20px]">
      {phases.map((p, i) => (
        <div key={i} className="relative">
          <span className="absolute -left-[31px] top-[8px] w-[12px] h-[12px] rounded-full bg-[#1769d7] border-[3px] border-[#e8f1ff]" />
          <p className="m-0 text-[14px] font-black text-[#0f63cc]">
            {phaseLabels[p.phase]}
            {p.actor && <span className="ml-1 font-bold text-[#103d73]">（{p.actor}）</span>}
          </p>
          <p className="m-0 mt-1 text-[17px] leading-[1.85] text-[#173758]">
            {p.description.value}
          </p>
        </div>
      ))}
    </div>
  );
}

// §9 踏まえて、この読み方が有力になるポイント（Reading Report v2）: selection_rationale
// （主軸に選んだ理由）とsupporting_points（個別材料）を、箇条書きではなく連続した論証の
// パラグラフとして表示する。末尾にweakening_evidence/weak_points/outlook.weakens_ifの
// 最初の1件だけを「一方で〜なら弱まる」という形で自然に添える（§16: 独立sectionを増やさず
// 既存Weakening Evidenceを保持する）。新しい論証文はここで作文しない——既存テキストの
// 再配置・再ラベリングのみ。
function ArgumentSection({
  interpretation,
  weakensPrefix,
}: {
  interpretation: GeoIosReadingExposureItem;
  weakensPrefix: string;
}) {
  const paragraphs = [
    interpretation.selection_rationale?.value,
    ...interpretation.supporting_points.map((p) => p.value),
  ].filter(isDisclosed);
  const weakensText =
    interpretation.weakening_evidence.find((e) => e.summary)?.summary ??
    interpretation.weak_points[0]?.value ??
    interpretation.outlook?.weakens_if?.value?.[0];
  if (paragraphs.length === 0 && !isDisclosed(weakensText)) return null;
  return (
    <div className="bg-[#f8fbff] border-l-[5px] border-[#1769d7] rounded-[10px] px-[20px] sm:px-[22px] py-[18px] space-y-[14px]">
      {paragraphs.map((p, i) => (
        <p key={i} className="m-0 text-[18px] sm:text-[19px] leading-[1.9] text-[#173758]">
          {p}
        </p>
      ))}
      {isDisclosed(weakensText) && (
        <p className="m-0 text-[16px] leading-[1.85] text-[#687b88]">
          {weakensPrefix}
          {weakensText}
        </p>
      )}
    </div>
  );
}

// §10 それを裏付ける動き（Reading Report v2）: 構造化EvidenceをActor帰属＋日付＋意味
// （rationale）の形で示す。記事本数の羅列ではなく、人物・組織の具体的な言動を中心にする
// （ユーザー指示）。fallback（supporting_points）はActor/日付情報を持たないため、文章の
// みをそのまま表示する。
function EvidenceReportSection({
  items,
  fallback,
}: {
  items: GeoIosStructuredEvidenceItem[];
  fallback: GeoIosPresentationItem[];
}) {
  const withSummary = items.filter((it) => it.summary);
  if (withSummary.length === 0 && fallback.length === 0) return null;
  return (
    <div className="grid gap-[13px]">
      {withSummary.length > 0
        ? withSummary.map((it, i) => (
            <div key={i} className="border border-[#d8e2e7] rounded-[13px] p-[18px] bg-white">
              <h4 className="m-0 text-[18px] font-black text-[#0b3568]">{it.summary}</h4>
              {(it.actor || it.date) && (
                <p className="m-0 mt-1.5 text-[14px] font-bold text-[#48617a]">
                  {[it.actor, it.date?.slice(0, 10)].filter(Boolean).join(" ／ ")}
                </p>
              )}
              {it.rationale && (
                <p className="m-0 mt-2.5 pt-2.5 border-t border-dashed border-[#d2dce6] text-[16px] leading-[1.8] text-[#17395d]">
                  {it.rationale}
                </p>
              )}
            </div>
          ))
        : fallback.map((it, i) => (
            <div key={i} className="border border-[#d8e2e7] rounded-[13px] p-[18px] bg-white">
              <p className="m-0 text-[16px] leading-[1.8] text-[#17395d]">{it.value}</p>
            </div>
          ))}
    </div>
  );
}

// §11 追加調査案（Reading Report v2）: research_needsを最大3件に絞り、
// 「何を確認するか／それがReadingの何を確認・反証できるか」のQ&A形式で示す
// （ユーザー指示: 5件以上並べない、Research Methodologyの説明は不要）。
function ResearchReportSection({ items }: { items: GeoIosNextWatchItem[] }) {
  const shown = items.slice(0, 3);
  if (shown.length === 0) return null;
  return (
    <div>
      {shown.map((item, i) => (
        <div key={i} className={`py-[16px] ${i === 0 ? "pt-0" : "border-t border-[#d8e2e7]"}`}>
          <p className="m-0 text-[18px] font-black text-[#103c72]">
            {i + 1}. {item.text}
          </p>
          {item.why && (
            <p className="m-0 mt-1.5 text-[16px] leading-[1.85] text-[#294a6d]">{item.why}</p>
          )}
        </div>
      ))}
    </div>
  );
}

// §12 この読み方で見えるシナリオ（Reading Report v2）: reaction_chain（Actor→trigger→
// likely_reaction）をActor判断の連鎖として一続きの文章に組み立て、expected_directionで
// 締める。business_impactはPrimary Storyline専用のresponse-level fieldのため、
// MAIN Readingの時だけ表示する（他Readingへ架空の企業影響を使い回さない）。新しい
// シナリオ文章はここで作文しない——既存reaction_chain/expected_direction/business_impact
// の文言をそのまま使う。
function ScenarioSection({
  interpretation,
  businessImpact,
  socialImpactLabel,
  industryImpactLabel,
}: {
  interpretation: GeoIosReadingExposureItem;
  businessImpact: GeoIosBusinessImpact | null;
  socialImpactLabel: string;
  industryImpactLabel: string;
}) {
  const rp = interpretation.reading_presentation;
  const chain = rp?.reaction_chain ?? buildFallbackReactionChain(interpretation);
  const expectedDirection = rp?.expected_direction ?? "";
  const chainSentences = chain
    .filter((r) => isDisclosed(r.likely_reaction))
    .map((r) =>
      isDisclosed(r.trigger)
        ? `${r.actor}は、${r.trigger}を受けて、${r.likely_reaction}`
        : `${r.actor}は${r.likely_reaction}`,
    );
  const showImpact = interpretation.is_main && !!businessImpact;
  if (chainSentences.length === 0 && !isDisclosed(expectedDirection) && !showImpact) return null;
  return (
    <div className="bg-[#f7faff] border border-[#d8e4f2] rounded-[14px] px-[20px] sm:px-[24px] py-[20px] space-y-[14px]">
      {chainSentences.map((s, i) => (
        <p key={i} className="m-0 text-[18px] sm:text-[19px] leading-[1.9] text-[#173758]">
          {s}
        </p>
      ))}
      {isDisclosed(expectedDirection) && (
        <p className="m-0 text-[18px] sm:text-[19px] leading-[1.9] italic text-[#173758]">
          {expectedDirection}
        </p>
      )}
      {showImpact && (
        <div className="grid sm:grid-cols-2 gap-[14px] pt-[4px]">
          {businessImpact?.social_environment && (
            <div className="bg-white border border-[#d8e2e7] rounded-[12px] p-[16px]">
              <h4 className="m-0 text-[16px] font-black text-[#0b3568]">{socialImpactLabel}</h4>
              <p className="m-0 mt-1.5 text-[15px] leading-[1.8] text-[#294a6d]">
                {businessImpact.social_environment.value}
              </p>
            </div>
          )}
          {businessImpact?.industry_business && (
            <div className="bg-white border border-[#d8e2e7] rounded-[12px] p-[16px]">
              <h4 className="m-0 text-[16px] font-black text-[#0b3568]">{industryImpactLabel}</h4>
              {businessImpact.industry_business.value.map((v, i) => (
                <p key={i} className="m-0 mt-1.5 text-[15px] leading-[1.8] text-[#294a6d]">
                  {v}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 5つの読み方一覧（Reading Report v2）: 01〜05のグリッド。クリックで下の単一Report Cardの
// 内容を切り替えるだけ（ページ遷移なし、§2）。色はSEMANTIC_TAG_CHIP／位置ベースの
// フォールバック（CATEGORY_CHIPがMAIN以外同じ色になり得るため、5件が必ず視覚的に区別
// できるようPOSITION_CHIPへフォールバックする——新しい色は追加せず、既存5色を使い回す）。
export interface ReadingSelectorItem {
  id: string;
  number: string;
  tag: string;
  chipClass: string;
  title: string;
  summary: string;
}
export function ReadingSelectorGrid({
  items,
  selectedId,
  onSelect,
}: {
  items: ReadingSelectorItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-[10px]">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          onClick={() => onSelect(it.id)}
          className={`text-left rounded-[14px] border p-[14px] min-h-[126px] transition-colors ${
            it.id === selectedId
              ? "border-2 border-[#36bfd6] bg-[#fbfdff]"
              : "border-[#d8e2e7] bg-white hover:border-[#9bbce7]"
          }`}
        >
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[20px] font-black text-[#163e70]">{it.number}</span>
            <span
              className={`px-2 py-[3px] rounded-full text-[11px] font-black whitespace-nowrap ${it.chipClass}`}
            >
              {it.tag}
            </span>
          </div>
          <h3 className="m-0 text-[15px] leading-[1.5] font-black text-[#0e315c] line-clamp-2">
            {it.title}
          </h3>
          <p className="m-0 mt-2 text-[13px] leading-[1.55] text-[#405a75] line-clamp-2">
            {it.summary}
          </p>
        </button>
      ))}
    </div>
  );
}

// READING_EXPOSURE_LAYER_V1配線（2026-09-24）: 内部role(MAIN/ALTERNATIVE/FRONTIER/
// EXPLORATORY)ごとのタグ配色。FRONTIER/EXPLORATORYは「低品質な代替」ではなく
// 「検討する価値のある視点」であることが伝わるよう、warning/danger色(赤・濃い橙)は
// 使わず、MAIN/ALTERNATIVEと視覚的に区別できる落ち着いた別色にする。reading_presentation
// が無い場合（Presentation Transformation未設定・失敗）のfallback表示に使う。
export const CATEGORY_CHIP: Record<GeoIosReadingRole, string> = {
  MAIN: "bg-[#eaf8fb] text-[#0d879a] border border-[#c9ecf1]",
  ALTERNATIVE: "bg-[#f0efff] text-[#6d68e8] border border-[#dcd9fa]",
  FRONTIER: "bg-[#fdf6ec] text-[#a97c1f] border border-[#eddcb0]",
  EXPLORATORY: "bg-[#eef6f3] text-[#2f7a63] border border-[#cbe6dc]",
  // REVIEW MODE（2026-09-25追加）: 既存レポートから再構成した現在の読み。
  CURRENT: "bg-[#eef2f8] text-[#2f4b7a] border border-[#cfd9ec]",
};

// §5 ユーザー向けTag（5種、内容判断ベース）の配色。内部roleとは別軸——Presentation
// Transformation Layerが5件を比較して判断した結果をそのまま表示するだけ。
export const SEMANTIC_TAG_CHIP: Record<string, string> = {
  王道: "bg-[#eaf8fb] text-[#0d879a] border border-[#c9ecf1]",
  有力な別解: "bg-[#f0efff] text-[#6d68e8] border border-[#dcd9fa]",
  前提を疑う: "bg-[#fdf6ec] text-[#a97c1f] border border-[#eddcb0]",
  構造から読む: "bg-[#eef6f3] text-[#2f7a63] border border-[#cbe6dc]",
  ジャンプ視点: "bg-[#fdeef0] text-[#b8567a] border border-[#f3d6e0]",
};

// Reading Report v2（2026-10-02、ユーザー指摘「全Reading Tagを同じ青にしない」）:
// semantic_tagが無い（Presentation Transformation Layer未設定・失敗）場合、
// CATEGORY_CHIPは同じroleが複数出現すると同じ色になる（ALTERNATIVEが2件あると
// どちらも同じ紫、等）——5件が必ず視覚的に区別できるよう、位置(0-4)ベースで
// SEMANTIC_TAG_CHIPと同じ5色を使い回すfallbackを用意する。新しい色は追加しない。
export const POSITION_CHIP = [
  "bg-[#eaf8fb] text-[#0d879a] border border-[#c9ecf1]",
  "bg-[#fdeef0] text-[#b8567a] border border-[#f3d6e0]",
  "bg-[#eef6f3] text-[#2f7a63] border border-[#cbe6dc]",
  "bg-[#fdf6ec] text-[#a97c1f] border border-[#eddcb0]",
  "bg-[#f0efff] text-[#6d68e8] border border-[#dcd9fa]",
];

// ALTERNATIVE roleは最大2件出現し得るため、出現順で「有力な別解」
// （1件目）/「もう一つの読み」（2件目）と表示を分ける。新しい判定基準は作らない
// ——既存rank順序（S自身の生成順）をそのまま出現順として使うだけ。reading_presentation
// が無い場合のfallback表示にのみ使う（新しいTagが有る場合はそちらを優先表示する）。
export function readingRoleLabel(
  t: (key: string, vars?: Record<string, string>) => string,
  role: GeoIosReadingRole,
  altOccurrence: number,
): string {
  switch (role) {
    case "MAIN":
      return t("geo.ios.situation.roleMain");
    case "ALTERNATIVE":
      return altOccurrence === 0
        ? t("geo.ios.situation.roleAlternativePrimary")
        : t("geo.ios.situation.roleAlternativeSecondary");
    case "FRONTIER":
      return t("geo.ios.situation.roleFrontier");
    case "EXPLORATORY":
      return t("geo.ios.situation.roleExploratory");
    case "CURRENT":
      return t("geo.ios.situation.roleCurrent");
    default:
      return t("geo.ios.situation.roleAlternativeSecondary");
  }
}

// §2 今回のポイント→Analysis Brief: 生のkey_point全fieldを並べるのではなく、重複を除いた
// 最大3件を優先順位順に選ぶだけ（新しい要約文はここで生成しない、既存fieldの再選択のみ）。
// investigation_question（Question）はページ上部ヘッダーへ移した（§25重複排除）ため対象外。
export function buildAnalysisBrief(keyPoint: GeoIosKeyPoint): GeoIosPresentationItem[] {
  const candidates = [
    keyPoint.one_line_assessment,
    keyPoint.primary_key_point,
    keyPoint.primary_hinge,
    keyPoint.why_this_is_the_key_point,
  ];
  const seen = new Set<string>();
  const result: GeoIosPresentationItem[] = [];
  for (const c of candidates) {
    if (!c || !c.value) continue;
    const key = String(c.value).trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(c);
    if (result.length >= 3) break;
  }
  return result;
}

// Final Expert Session Readiness Validation Phase B（2026-09-28、ユーザー指示13-5/13-6
// fallback底上げ）: Presentation Transformation Layerが未設定/失敗の場合、旧来の5ステップ
// 「気づく→見る→疑う→仮説→読む」ストーリー・平坦なoutlookリストへ戻さず、reading_
// presentation成功時と同じ①②③構造・同じActor-anchoredな「次に」構造を、既に取得済みの
// 生フィールドだけから機械的に組み立てる（新規LLM callは追加しない、新しい文章も生成
// しない——既存フィールドの再配置・再ラベリングのみ）。
export function buildFallbackReasoning(
  interpretation: GeoIosReadingExposureItem,
): GeoIosReadingReasoning | null {
  const observation = interpretation.what_was_seen?.value ?? "";
  const whyItMatters = interpretation.why_this_matters?.value ?? "";
  const interpretationSteps = [
    interpretation.what_was_doubted?.value,
    interpretation.hypothesis_formed?.value,
    interpretation.is_main ? interpretation.selection_rationale?.value : undefined,
  ].filter(isDisclosed);
  const reasoning: GeoIosReadingReasoning = {
    observation,
    why_it_matters: whyItMatters,
    interpretation: interpretationSteps.join("\n\n"),
  };
  if (
    !isDisclosed(reasoning.observation) &&
    !isDisclosed(reasoning.why_it_matters) &&
    !isDisclosed(reasoning.interpretation)
  ) {
    return null;
  }
  return reasoning;
}

export function buildFallbackReactionChain(
  interpretation: GeoIosReadingExposureItem,
): GeoIosReactionChainItem[] {
  const actorLabel = interpretation.key_actors?.value?.length
    ? interpretation.key_actors.value.join("・")
    : interpretation.title;
  // response_id直接route（IOS Sessionにラップされていない合成candidate）向けの修正:
  // _synthesize_candidates_from_response()はalternative_scenariosのtitle/key_actorsを
  // 持たずnullを返すため、無補正だと文中に文字列"null"がそのまま出力されてしまう
  // （2026-10-02、S1実ブラウザ監査で発見）。Actor名が無い場合は新しい主語を作文せず、
  // このchain自体を静かに省略する（他セクションと同じ「無ければ省く」方針）。
  if (!isDisclosed(actorLabel)) return [];
  return (interpretation.outlook?.if_correct?.value ?? [])
    .filter(isDisclosed)
    .map((text) => ({ actor: actorLabel, trigger: "", likely_reaction: text }));
}

// Reading Report v2（2026-10-02、縦型レポートPresentation）: 選択中の1件だけを、
// 7セクション連続の単一レポートCardとして表示する（§15: 細切れCardにしない）。
// 展開/折りたたみは廃止——選択されている間は常に全文を表示する。
export function ReadingReportCard({
  number,
  tag,
  chipClass,
  interpretation,
  allActors,
  businessImpact,
  researchNeeds,
}: {
  number: string;
  tag: string;
  chipClass: string;
  interpretation: GeoIosReadingExposureItem;
  allActors: GeoIosActor[];
  businessImpact: GeoIosBusinessImpact | null;
  researchNeeds: GeoIosNextWatchItem[];
}) {
  const { t } = useLanguage();
  const rp = interpretation.reading_presentation;
  const displayTitle = rp?.title || interpretation.title;

  return (
    <article
      id={`reading-card-${interpretation.scenario_candidate_id}`}
      className="rounded-[22px] border border-[#d8e2e7] bg-white overflow-hidden shadow-[0_8px_28px_rgba(15,45,80,0.05)] scroll-mt-4"
    >
      {/* §5 選択Reading Header: 番号＋Reading Tag＋Titleのみ。Platform IA / Expert AI
          Reading UI Cleanup（2026-10-02、ユーザー指示）: 「この読み方を採用する」ボタンは
          削除した——次に進むフロー（Design Workspace/Draft生成）自体が無くなったため、
          採用という概念自体が宙に浮いていたため。 */}
      <header className="px-[22px] sm:px-[32px] pt-[24px] pb-[20px] border-b border-[#d8e2e7] bg-gradient-to-b from-[#fbfdff] to-white">
        <div className="flex items-center gap-2 flex-wrap mb-1.5">
          <span className="text-[15px] font-black text-[#0d879a]">{number}</span>
          <span
            className={`px-2 py-[3px] rounded-full text-[11px] font-black whitespace-nowrap ${chipClass}`}
          >
            {tag}
          </span>
          {interpretation.exploratory && (
            <span className="inline-flex items-center gap-1 px-2 py-[3px] rounded-full bg-[#f4f5f6] text-[#687b88] text-[10px] font-bold whitespace-nowrap">
              <span className="w-[5px] h-[5px] rounded-full bg-[#9aa7ae]" />
              {t("geo.ios.situation.exploratoryIndicator")}
            </span>
          )}
          {!isEmpty(interpretation.confidence) && (
            <span className="text-[11px] text-[#687b88]">
              {t("geo.ios.situation.confidence")}: {interpretation.confidence!.value}
            </span>
          )}
        </div>
        <h2 className="m-0 text-[23px] sm:text-[27px] leading-[1.4] font-black text-[#0b3568]">
          {displayTitle}
        </h2>
        {interpretation.why_not_main && (
          <p className="m-0 mt-2 text-[14px] leading-[1.7] text-[#a97c1f]">
            {interpretation.why_not_main.value}
          </p>
        )}
      </header>

      {/* ユーザー指示「意味のあるfieldが1つも無ければそのセクション自体を省略する」:
          各section本体を先に関数として評価し、nullならReportSectionの見出しごと省略する
          （空の見出しだけが残らないようにする、2026-10-02目視監査で発見したバグの修正）。 */}
      {(() => {
        const detail = ReadingDetailSection({
          interpretation,
          oneLineLabel: t("geo.ios.situation.orientationCounterpartyLabel"),
        });
        return (
          detail && (
            <ReportSection step={1} title={t("geo.ios.situation.report.detailTitle")}>
              {detail}
            </ReportSection>
          )
        );
      })()}

      {interpretation.actor_roles.length > 0 && (
        <ReportSection step={2} title={t("geo.ios.situation.report.stakeholdersTitle")}>
          <StakeholderSection
            actorRoles={interpretation.actor_roles}
            allActors={allActors}
            peripheralLabel={t("geo.ios.situation.actorRolesPeripheralLabel")}
          />
        </ReportSection>
      )}

      {interpretation.situation_phases && interpretation.situation_phases.length > 0 && (
        <ReportSection step={3} title={t("geo.ios.situation.report.timelineTitle")}>
          <TimelineSection
            phases={interpretation.situation_phases}
            phaseLabels={{
              trigger: t("geo.ios.situation.report.phaseInitial"),
              primary_action: t("geo.ios.situation.report.phaseJudgmentShift"),
              reaction: t("geo.ios.situation.phaseReaction"),
              current: t("geo.ios.situation.phaseCurrent"),
            }}
          />
        </ReportSection>
      )}

      {(() => {
        const argument = ArgumentSection({
          interpretation,
          weakensPrefix: t("geo.ios.situation.report.weakensPrefix"),
        });
        return (
          argument && (
            <ReportSection step={4} title={t("geo.ios.situation.report.argumentTitle")}>
              {argument}
            </ReportSection>
          )
        );
      })()}

      {(interpretation.supporting_evidence.length > 0 ||
        interpretation.supporting_points.length > 0) && (
        <ReportSection step={5} title={t("geo.ios.situation.report.evidenceTitle")}>
          <EvidenceReportSection
            items={interpretation.supporting_evidence}
            fallback={interpretation.supporting_points}
          />
        </ReportSection>
      )}

      {researchNeeds.length > 0 && (
        <ReportSection step={6} title={t("geo.ios.situation.report.researchTitle")}>
          <ResearchReportSection items={researchNeeds} />
        </ReportSection>
      )}

      {(() => {
        const scenario = ScenarioSection({
          interpretation,
          businessImpact,
          socialImpactLabel: t("geo.ios.situation.report.impactSocialTitle"),
          industryImpactLabel: t("geo.ios.situation.report.impactIndustryTitle"),
        });
        return (
          scenario && (
            <ReportSection step={7} title={t("geo.ios.situation.report.scenarioTitle")}>
              {scenario}
            </ReportSection>
          )
        );
      })()}
    </article>
  );
}

// Expert Session Readiness Patch Phase S1（2026-10-02）: IOS Analysis Session（CQAS/
// Design/相談スレッド等）に依存しない、読み取り専用の合成コンポーネント。response_id
// 直接指定route（Gold/Calibration Case確認用）から使う——Session新規生成・Expert AI
// 再実行は一切行わない。presentationは呼び出し元が既存hookで取得済みのものをそのまま渡す。
export function ReadingPresentationV2Report({
  presentation,
  effectiveSelectedId = null,
  showAnalysisBrief = true,
}: {
  presentation: GeoIosSituationPresentation;
  effectiveSelectedId?: string | null;
  showAnalysisBrief?: boolean;
}) {
  const { t } = useLanguage();
  const [viewingId, setViewingId] = useState<string | null>(null);
  const [showAllInterpretations, setShowAllInterpretations] = useState(false);

  const { key_point: keyPoint } = presentation;
  const analysisBrief = buildAnalysisBrief(keyPoint);

  // READING_EXPOSURE_LAYER_V1配線と同じBackward Compatibility: Composerが未接続/失敗
  // した場合（reading_exposureが空、= response_idがIOS Sessionにラップされていない等）
  // のみ、既存interpretationsへフォールバックする。
  // response_id直接route（IOS Sessionにラップされていないケース）向けフォールバック:
  // _synthesize_candidates_from_response()（backend）はscenario_candidate_idを常にnullで
  // 返すため、無補正だとグリッド内で全Readingが同一key(null)になり、切替操作が機能しなく
  // なる（Reactの子要素識別が壊れる）。新しいCQAS/Candidate IDは発行せず、表示専用の
  // 安定した合成idを割り当てるだけ（既存データの意味は変えない）。
  const readingCards: GeoIosReadingExposureItem[] =
    presentation.reading_exposure.length > 0
      ? presentation.reading_exposure
      : presentation.interpretations.map((interp, i) => ({
          ...interp,
          scenario_candidate_id: interp.scenario_candidate_id || `synthesized-reading-${i}`,
          reading_role: (interp.is_main ? "MAIN" : "ALTERNATIVE") as GeoIosReadingRole,
          exploratory: false,
          selected: false,
          why_not_main: null,
          reading_presentation: null,
        }));

  const displayedInterpretations = readingCards.filter((i) => i.displayed);
  const hiddenInterpretations = readingCards.filter((i) => !i.displayed);
  const shownInterpretations = showAllInterpretations ? readingCards : displayedInterpretations;

  // Expert Review UI（2026-10-04）: バックエンドがexpert_review（表示構造＋レビューコメント）を
  // 付けている場合は「専門家から複数の読み方のレビューコメントをもらう」UIで表示する。付いて
  // いない場合（旧バックエンド等）のみ、従来のグリッド＋縦型レポートへフォールバックする。
  if (
    presentation.expert_review &&
    shownInterpretations.length > 0 &&
    shownInterpretations.every((i) => i.expert_review)
  ) {
    return (
      <ExpertReviewReport
        presentation={presentation}
        cards={shownInterpretations}
        hiddenCount={hiddenInterpretations.length}
        showAll={showAllInterpretations}
        onToggleShowAll={() => setShowAllInterpretations((v) => !v)}
      />
    );
  }

  let altOccurrence = 0;
  const roleLabels = shownInterpretations.map((r) => {
    const label = readingRoleLabel(
      t,
      r.reading_role,
      r.reading_role === "ALTERNATIVE" ? altOccurrence : 0,
    );
    if (r.reading_role === "ALTERNATIVE") altOccurrence += 1;
    return label;
  });

  const selectorItems: ReadingSelectorItem[] = shownInterpretations.map((interp, i) => {
    const rp = interp.reading_presentation;
    const tag = rp?.semantic_tag ?? roleLabels[i];
    const chipClass = rp?.semantic_tag
      ? (SEMANTIC_TAG_CHIP[rp.semantic_tag] ?? POSITION_CHIP[i % POSITION_CHIP.length])
      : POSITION_CHIP[i % POSITION_CHIP.length];
    return {
      id: interp.scenario_candidate_id,
      number: String(i + 1).padStart(2, "0"),
      tag,
      chipClass,
      title: rp?.title || interp.title,
      summary: rp?.one_line_thesis || interp.description?.value || "",
    };
  });

  const effectiveViewingId =
    viewingId ?? effectiveSelectedId ?? shownInterpretations[0]?.scenario_candidate_id ?? null;
  const viewingIndex = Math.max(
    0,
    shownInterpretations.findIndex((i) => i.scenario_candidate_id === effectiveViewingId),
  );
  const viewingInterpretation = shownInterpretations[viewingIndex];
  const viewingSelectorItem = selectorItems[viewingIndex];

  const hasKeyPointContent = analysisBrief.length > 0 || !isEmpty(keyPoint.initial_anomaly);

  return (
    <div className="space-y-6">
      {showAnalysisBrief && hasKeyPointContent && (
        <section className="space-y-3 rounded-[14px] border border-[#d8e2e7] bg-white p-[20px]">
          <h2 className="text-xl font-semibold text-[#172d3d]">
            {t("geo.ios.situation.keyPointTitle")}
          </h2>
          {analysisBrief.length > 0 && (
            <div className="space-y-3">
              {analysisBrief.map((item, i) => (
                <div key={i} className="flex gap-3">
                  <span className="shrink-0 w-[26px] h-[26px] rounded-[8px] bg-[#eaf8fb] text-[#0d879a] grid place-items-center text-[12px] font-black">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <p className="m-0 text-base geo-prose">{item.value}</p>
                </div>
              ))}
            </div>
          )}
          {!isEmpty(keyPoint.initial_anomaly) && (
            <div className="rounded-[10px] border border-[#d8e2e7] bg-[#fbfcfd] p-[13px]">
              <b className="text-[13px] text-[#0d879a]">
                {t("geo.ios.situation.startingPointLabel")}
              </b>
              <p className="m-0 mt-1.5 text-base geo-prose">{keyPoint.initial_anomaly!.value}</p>
            </div>
          )}
        </section>
      )}

      <ReadingSelectorGrid
        items={selectorItems}
        selectedId={effectiveViewingId}
        onSelect={setViewingId}
      />
      {hiddenInterpretations.length > 0 && (
        <Button variant="ghost" size="sm" onClick={() => setShowAllInterpretations((v) => !v)}>
          {showAllInterpretations
            ? t("geo.ios.situation.hideOtherViews")
            : `${t("geo.ios.situation.seeOtherViews")} (+${hiddenInterpretations.length})`}
        </Button>
      )}

      {viewingInterpretation && viewingSelectorItem && (
        <ReadingReportCard
          key={viewingInterpretation.scenario_candidate_id}
          number={viewingSelectorItem.number}
          tag={viewingSelectorItem.tag}
          chipClass={viewingSelectorItem.chipClass}
          interpretation={viewingInterpretation}
          allActors={presentation.actors}
          businessImpact={presentation.business_impact}
          researchNeeds={presentation.research_needs}
        />
      )}
    </div>
  );
}


// ============================================================================
// Expert Review UI（2026-10-04、読み方UI改善）
// ============================================================================
// 「AIレポート」ではなく「専門家から複数の読み方についてレビューコメントをもらう」体験。
// トップには ①今回のポイント ②鍵になる人物・組織 ③見ているレイヤー ④読み方カード（視点・
// 結論タイトル・2〜4文のレビューコメント・人物タグ）だけを出し、詳しい推論は各カードの
// 「なぜそう読んだか」を開いた先にまとめる。表示する内容は全て保存済みExpert AI応答から
// バックエンドが組み立てたexpert_review（決定論的なView＋キャッシュ済みPresentation LLM
// Transformation）。ここでは新しい推論・文章生成をしない。技術用語・内部IDは出さない。

const REVIEW_PERSPECTIVE_CHIP: Record<string, string> = {
  main: "bg-[#eaf8fb] text-[#0d879a] border border-[#c9ecf1]",
  actor: "bg-[#fdeef0] text-[#b8567a] border border-[#f3d6e0]",
  relationship: "bg-[#f0efff] text-[#6d68e8] border border-[#dcd9fa]",
  structural: "bg-[#eef6f3] text-[#2f7a63] border border-[#cbe6dc]",
  contrarian: "bg-[#fdf6ec] text-[#a97c1f] border border-[#eddcb0]",
  alternative: "bg-[#f0efff] text-[#6d68e8] border border-[#dcd9fa]",
};

function ReviewDetailBlock({
  step,
  title,
  children,
}: {
  step: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2.5">
      <h4 className="m-0 flex items-center gap-2 text-[16px] font-black text-[#0b3568]">
        <span className="w-[22px] h-[22px] rounded-full bg-[#e8f1ff] text-[#0e5fc2] grid place-items-center text-[12px] font-black shrink-0">
          {step}
        </span>
        {title}
      </h4>
      {children}
    </div>
  );
}

function ReviewEvidenceRows({ rows }: { rows: GeoIosExpertReviewEvidenceRow[] }) {
  return (
    <div className="space-y-3">
      {rows.map((r, i) => (
        <div key={i} className="border-l-[3px] border-[#bdd0e8] pl-3 space-y-1">
          <p className="m-0 text-[15px] leading-[1.8] text-[#173758]">
            {r.actor && <span className="font-black text-[#0b3568]">{r.actor}　→　</span>}
            {r.what}
            {r.date && <span className="ml-2 text-[12px] text-[#687b88]">{r.date}</span>}
          </p>
          {r.meaning && (
            <p className="m-0 text-[14px] leading-[1.8] text-[#294a6d]">
              <span className="font-black text-[#0f63cc]">この読みにとって：</span>
              {r.meaning}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function ReviewDetail({
  review,
  interpretation,
  businessImpact,
  researchNeeds,
  sharedFlow,
}: {
  review: GeoIosExpertReviewCard;
  interpretation: GeoIosReadingExposureItem;
  businessImpact: GeoIosBusinessImpact | null;
  researchNeeds: GeoIosNextWatchItem[];
  sharedFlow: string[] | null;
}) {
  const [showFull, setShowFull] = useState(false);
  const sb = review.surface_behind;
  const blocks: React.ReactElement<{ step: string }>[] = [];

  if (review.origin) {
    blocks.push(
      <ReviewDetailBlock key="origin" step="1" title="この読みの起点（何が気になったか）">
        <p className="m-0 text-[16px] leading-[1.9] text-[#173758]">{review.origin}</p>
      </ReviewDetailBlock>,
    );
  }

  if (sb && (sb.surface || sb.behind.length > 0)) {
    blocks.push(
      <ReviewDetailBlock key="dynamics" step="2" title="関係者の思惑・力学">
        {sb.surface && (
          <div className="rounded-[12px] bg-[#f4f7fb] border border-[#d8e2e7] px-4 py-3">
            <p className="m-0 text-[12px] font-black text-[#687b88]">表面で起きていること</p>
            <p className="m-0 mt-1 text-[15px] leading-[1.8] text-[#173758]">{sb.surface}</p>
          </div>
        )}
        {sb.surface && sb.behind.length > 0 && (
          <div className="text-center text-[#1769d7] text-[18px] leading-none" aria-hidden>
            ↓
          </div>
        )}
        {sb.behind.length > 0 && (
          <div className="space-y-2.5">
            <p className="m-0 text-[12px] font-black text-[#687b88]">その裏で見ている力学</p>
            <div className="grid sm:grid-cols-2 gap-3">
              {sb.behind.map((b) => (
                <div
                  key={b.actor}
                  className="rounded-[12px] border border-[#d8e2e7] bg-[#fafcff] px-4 py-3 space-y-1.5"
                >
                  <p className="m-0 text-[16px] font-black text-[#0b3568]">{b.actor}</p>
                  {b.interest && (
                    <p className="m-0 text-[14px] leading-[1.75] text-[#26486c]">
                      <span className="font-black text-[#0f63cc]">思惑　</span>
                      {b.interest}
                    </p>
                  )}
                  {b.action && (
                    <p className="m-0 text-[14px] leading-[1.75] text-[#26486c]">
                      <span className="font-black text-[#0f63cc]">動き　</span>
                      {b.action}
                    </p>
                  )}
                  {b.constraint && (
                    <p className="m-0 text-[14px] leading-[1.75] text-[#26486c]">
                      <span className="font-black text-[#0f63cc]">制約　</span>
                      {b.constraint}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </ReviewDetailBlock>,
    );
  }

  // ③ここまでの流れ: 局面全体の「どういう変化で今の力学になったか」（LLMが保存済みの力学情報から
  // 圧縮した3〜6ステップ）があればそれを使い、無ければ保存済み文の再配置（各カードのflow）。
  if (sharedFlow && sharedFlow.length > 0) {
    blocks.push(
      <ReviewDetailBlock key="flow" step="3" title="ここまでの流れ（どうしてこうなったか）">
        <div>
          {sharedFlow.map((text, i) => (
            <div key={i}>
              <div
                className={`rounded-[12px] border px-4 py-2.5 ${
                  i === sharedFlow.length - 1
                    ? "border-[#36bfd6] bg-[#f2fbfd]"
                    : "border-[#d8e2e7] bg-white"
                }`}
              >
                <p className="m-0 text-[15px] leading-[1.75] text-[#173758]">{text}</p>
              </div>
              {i < sharedFlow.length - 1 && (
                <div className="text-center text-[#1769d7] text-[16px] leading-[1.4]" aria-hidden>
                  ↓
                </div>
              )}
            </div>
          ))}
        </div>
      </ReviewDetailBlock>,
    );
  } else if (review.flow.length > 0) {
    blocks.push(
      <ReviewDetailBlock key="flow" step="3" title="ここまでの流れ">
        <div>
          {review.flow.map((s, i) => (
            <div key={i}>
              <div className="rounded-[12px] border border-[#d8e2e7] bg-white px-4 py-2.5">
                <p className="m-0 text-[12px] font-black text-[#0f63cc]">
                  {s.label}
                  {s.actor && <span className="ml-1 text-[#103d73]">（{s.actor}）</span>}
                </p>
                <p className="m-0 mt-0.5 text-[15px] leading-[1.75] text-[#173758]">{s.text}</p>
              </div>
              {i < review.flow.length - 1 && (
                <div className="text-center text-[#1769d7] text-[16px] leading-[1.4]" aria-hidden>
                  ↓
                </div>
              )}
            </div>
          ))}
        </div>
      </ReviewDetailBlock>,
    );
  }

  if (review.supporting.length > 0) {
    blocks.push(
      <ReviewDetailBlock key="supporting" step="4" title="この読みを支える動き">
        <ReviewEvidenceRows rows={review.supporting} />
      </ReviewDetailBlock>,
    );
  }
  if (review.contradicting.length > 0) {
    blocks.push(
      <ReviewDetailBlock key="contradicting" step="5" title="この読みと合わない動き">
        <ReviewEvidenceRows rows={review.contradicting} />
      </ReviewDetailBlock>,
    );
  }
  if (review.break_conditions.length > 0) {
    blocks.push(
      <ReviewDetailBlock key="break" step="6" title="この読みが崩れるとすれば">
        <ul className="m-0 pl-5 space-y-1.5 text-[15px] leading-[1.8] text-[#173758] list-disc">
          {review.break_conditions.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      </ReviewDetailBlock>,
    );
  }
  if (review.watch.length > 0) {
    blocks.push(
      <ReviewDetailBlock key="watch" step="7" title="次に見るもの">
        <ul className="m-0 pl-5 space-y-1.5 text-[15px] leading-[1.8] text-[#173758] list-disc">
          {review.watch.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      </ReviewDetailBlock>,
    );
  }

  // 「さらに詳しく」: 上の7項目に収まらない既存の文章（元の説明・論拠・シナリオ・追加調査案）。
  // 情報は消さず、既定では閉じておく。
  const detailBody = ReadingDetailSection({
    interpretation,
    oneLineLabel: "志向と相手の見方",
  });
  const argument = ArgumentSection({ interpretation, weakensPrefix: "一方で、" });
  const scenario = ScenarioSection({
    interpretation,
    businessImpact,
    socialImpactLabel: "社会・環境への影響",
    industryImpactLabel: "業界・ビジネスへの影響",
  });
  const hasFull = !!(detailBody || argument || scenario || researchNeeds.length > 0);

  return (
    <div className="space-y-6 px-[20px] sm:px-[28px] py-[22px] border-t border-[#d8e2e7] bg-[#fbfdff]">
      {/* 表示する項目だけで1から振り直す（証拠が保存されていない等で項目が省略されても番号が飛ばない） */}
      {blocks.map((el, i) => cloneElement(el, { step: String(i + 1) }))}
      {hasFull && (
        <div className="pt-1">
          <Button variant="ghost" size="sm" onClick={() => setShowFull((v) => !v)}>
            {showFull ? "文章での詳しい説明を閉じる" : "さらに詳しく（文章で読む）"}
          </Button>
          {showFull && (
            <div className="mt-3 space-y-5">
              {detailBody && <div className="space-y-3">{detailBody}</div>}
              {argument}
              {scenario}
              {researchNeeds.length > 0 && (
                <div>
                  <p className="m-0 mb-2 text-[14px] font-black text-[#0b3568]">追加で確認したいこと</p>
                  <ResearchReportSection items={researchNeeds} />
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ReviewCommentCard({
  interpretation,
  review,
  expanded,
  onToggle,
  businessImpact,
  researchNeeds,
  sharedFlow,
}: {
  interpretation: GeoIosReadingExposureItem;
  review: GeoIosExpertReviewCard;
  expanded: boolean;
  onToggle: () => void;
  businessImpact: GeoIosBusinessImpact | null;
  researchNeeds: GeoIosNextWatchItem[];
  sharedFlow: string[] | null;
}) {
  const chip = REVIEW_PERSPECTIVE_CHIP[review.perspective.key] ?? REVIEW_PERSPECTIVE_CHIP.alternative;
  return (
    <article
      className={`rounded-[18px] border bg-white overflow-hidden shadow-[0_6px_22px_rgba(15,45,80,0.05)] ${
        review.is_primary ? "border-[#36bfd6] border-2" : "border-[#d8e2e7]"
      }`}
    >
      <div className="px-[20px] sm:px-[28px] pt-[20px] pb-[18px] space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`px-2.5 py-[3px] rounded-full text-[12px] font-black whitespace-nowrap ${chip}`}
          >
            {review.perspective.label}
          </span>
          {review.is_primary && (
            <span className="px-2.5 py-[3px] rounded-full text-[12px] font-black whitespace-nowrap bg-[#0d879a] text-white">
              現時点で最も有力
            </span>
          )}
          {!review.is_primary && interpretation.why_not_main && (
            <span className="text-[12px] text-[#a97c1f]">{interpretation.why_not_main.value}</span>
          )}
        </div>
        <h3 className="m-0 text-[20px] sm:text-[22px] leading-[1.5] font-black text-[#0b3568]">
          {review.headline}
        </h3>
        <p className="m-0 text-[16px] sm:text-[17px] leading-[1.95] text-[#173758]">{review.comment}</p>
        {(review.stakeholders.length > 0 || review.perspective_tags.length > 0) && (
          <div className="flex flex-wrap gap-x-3 gap-y-1.5 pt-1">
            {review.stakeholders.map((s) => (
              <span
                key={s.name}
                className={`text-[13px] font-bold ${s.role === "primary" ? "text-[#0d879a]" : "text-[#48617a]"}`}
              >
                #{s.name}
              </span>
            ))}
            {review.perspective_tags.map((t) => (
              <span key={t.key} className="text-[13px] text-[#687b88]">
                #{t.label}
              </span>
            ))}
          </div>
        )}
        <div>
          <Button variant="outline" size="sm" onClick={onToggle}>
            {expanded ? "閉じる" : "なぜそう読んだか"}
          </Button>
        </div>
      </div>
      {expanded && (
        <ReviewDetail
          review={review}
          interpretation={interpretation}
          businessImpact={businessImpact}
          researchNeeds={researchNeeds}
          sharedFlow={sharedFlow}
        />
      )}
    </article>
  );
}

export function ExpertReviewReport({
  presentation,
  cards,
  hiddenCount,
  showAll,
  onToggleShowAll,
}: {
  presentation: GeoIosSituationPresentation;
  cards: GeoIosReadingExposureItem[];
  hiddenCount: number;
  showAll: boolean;
  onToggleShowAll: () => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const overview = presentation.expert_review;
  const point = overview?.point;
  const anomaly = overview?.anomaly;
  const keyActors = overview?.key_actors ?? [];
  const layers = overview?.layers ?? [];

  return (
    <div className="space-y-6">
      {(point || anomaly) && (
        <section className="rounded-[18px] border border-[#d8e2e7] bg-white p-[22px] space-y-4">
          <h2 className="m-0 text-[20px] font-black text-[#172d3d]">今回のポイント</h2>
          {point && <p className="m-0 text-[17px] leading-[1.95] text-[#173758]">{point}</p>}
          {anomaly && (
            <div className="rounded-[12px] border border-[#d8e2e7] bg-[#fbfcfd] px-4 py-3">
              <b className="text-[13px] text-[#0d879a]">ここが気になる</b>
              <p className="m-0 mt-1.5 text-[16px] leading-[1.9] text-[#173758]">{anomaly}</p>
            </div>
          )}
        </section>
      )}

      {keyActors.length > 0 && (
        <section className="space-y-2.5">
          <h2 className="m-0 text-[17px] font-black text-[#172d3d]">今回、鍵になる人物・組織</h2>
          <div className="grid sm:grid-cols-2 gap-3">
            {keyActors.map((a) => (
              <div key={a.name} className="rounded-[14px] border border-[#d8e2e7] bg-white px-4 py-3">
                <p className="m-0 text-[16px] font-black text-[#0b3568]">{a.name}</p>
                {a.why && <p className="m-0 mt-1 text-[14px] leading-[1.75] text-[#405a75]">{a.why}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {layers.length > 0 && (
        <section className="space-y-2">
          <p className="m-0 text-[13px] font-black text-[#687b88]">この読みで見ているレイヤー</p>
          <div className="flex flex-wrap gap-2">
            {layers.map((l) => (
              <span
                key={l.key}
                className={`px-3 py-[4px] rounded-full text-[13px] font-bold border ${
                  l.present
                    ? "bg-[#eaf3ff] text-[#0e5fc2] border-[#c9dcf5]"
                    : "bg-[#f4f5f6] text-[#a0aab2] border-[#e3e7ea]"
                }`}
              >
                {l.label}
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <h2 className="m-0 text-[20px] font-black text-[#172d3d]">専門家の見立て（複数の読み方）</h2>
          {overview?.comment_status === "pending" && (
            <span className="text-[12px] text-[#687b88]">レビューコメントを整えています…</span>
          )}
        </div>
        {cards.map((card) =>
          card.expert_review ? (
            <ReviewCommentCard
              key={card.scenario_candidate_id}
              interpretation={card}
              review={card.expert_review}
              expanded={expandedId === card.scenario_candidate_id}
              onToggle={() =>
                setExpandedId((cur) =>
                  cur === card.scenario_candidate_id ? null : card.scenario_candidate_id,
                )
              }
              businessImpact={presentation.business_impact}
              researchNeeds={presentation.research_needs}
              sharedFlow={overview?.flow?.steps ?? null}
            />
          ) : null,
        )}
        {hiddenCount > 0 && (
          <Button variant="ghost" size="sm" onClick={onToggleShowAll}>
            {showAll ? "他の見方を閉じる" : `他の見方も見る (+${hiddenCount})`}
          </Button>
        )}
      </section>
    </div>
  );
}
