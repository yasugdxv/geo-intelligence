import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useSituationPresentation,
  useGenerateMoreReadings,
  type GeoIosReadingExposureItem,
  type GeoIosReadingRole,
  type GeoIosGenerateMoreDirection,
} from "@/lib/geoIosSituationPresentationApi";
import { GeoIosConsultationThread } from "@/components/geo/geo-ios-consultation-view";
import { useLanguage } from "@/lib/i18n";
import {
  readingRoleLabel,
  SEMANTIC_TAG_CHIP,
  POSITION_CHIP,
  ReadingSelectorGrid,
  ReadingReportCard,
  ExpertReviewReport,
  type ReadingSelectorItem,
} from "@/components/geo/geo-ios-reading-presentation-v2";

// Geopolitical Intelligence OS: Situation Analysis Presentation v1（「局面を読む」）。
// 既存Expert AI構造化出力（CQAS候補・Actor Intelligence・Hypothesis Register等）を
// geo_ios_situation_presentation_service（backend）が7セクションへ整形した結果を、
// そのまま表示するだけのPresentation層。新しい推論・新しいLLM呼び出しはここでは行わない
// （表示順・展開/折りたたみ・接続語のみがこのファイルの責務）。
//
// raw_analysis（selected_scenario_candidate_id等）とpresentationは、APIレスポンス側で既に
// 分離されている。CQAS candidate生成・ranking・selection・Actor選定・Retrieval・Learningの
// 経路には一切書き込まない。
//
// Platform IA / Expert AI Reading UI Cleanup（2026-10-02、ユーザー指示）: Expert AI相談は
// 「Readingを提案・分析するだけの機能」に専念させる。旧「この設計で確定」→Design
// Workspace→Draft生成という後続フローへの導線（onConfirmDesign、採用=selectCandidate、
// 別の読み方を提案=generateScenarios再実行）を全て削除した——次に進む行き先自体が
// 無くなったため。レポート作成が必要な場合は、独立したレポートレビュー（/geo/ios/
// review-intake）のアップロード/貼り付け入口を使う。
//
// Reading Studio v2（2026-09-25、Step2 UI/UX再設計）: §23 Presentation Transformation
// Layer（バックエンドの新しいLLM呼び出し、geo_ios_reading_presentation_transform_service）
// が生成したreading_presentationがある場合はそちらを人間可読な表示に使い、無い場合
// （LLM未設定・呼び出し失敗）は既存の生フィールド表示にフォールバックする——このファイル
// 自体は新しい推論・新しいLLM呼び出しを行わない、という既存方針を維持する。
interface Props {
  sessionId: string;
  actorId: string;
  // REVIEW MODE（§17-22、2026-09-25追加）: "review"の場合、CREATE MODE専用のAnalysis
  // Brief・Chatを出さない。reviewPendingSelectedIdはReview Mode側のRevision Strategy
  // 選択状態と連動する「見ているReading」のフォールバック値として使う
  // （onReviewSelectReading経由の実際の選択トリガーは、このRouteからは現在配線されて
  // いない——geo.ios.review-mode.$sessionId.tsx参照、Review ModeはGeoIosDraftComposeView
  // 側の別経路を使っており、このcomponent自体はmode="review"で呼ばれていない）。
  mode?: "create" | "review";
  reviewPendingSelectedId?: string | null;
}

// §13「＋ 別の読み方を探す」の10方向（自由入力含む）。バックエンドの
// geo_ios_reading_exposure_composer._DIRECTION_INSTRUCTIONSのキーと一致させる。
const GENERATE_MORE_DIRECTIONS: GeoIosGenerateMoreDirection[] = [
  "auto",
  "more_orthodox",
  "invert_premise",
  "different_actor_lead",
  "domestic_politics",
  "international_relations",
  "economic_structure",
  "negotiation_relationship",
  "bold_hypothesis",
  "custom",
];

export function GeoIosSituationAnalysisView({
  sessionId,
  actorId,
  mode = "create",
  reviewPendingSelectedId,
}: Props) {
  const isReviewMode = mode === "review";
  const { t } = useLanguage();
  const presentationQuery = useSituationPresentation(sessionId);
  const generateMoreReadings = useGenerateMoreReadings(sessionId);

  const [showAllInterpretations, setShowAllInterpretations] = useState(false);
  // Reading Report v2（2026-10-02）: 「見ているReading」はDB上の正式な採用選択
  // (effectiveSelectedId)とは独立したローカル状態——グリッドをクリックしても
  // まだ採用はされない（§2「読み方をクリックしても採用されない、下のレポート内容が
  // 切り替わるだけ」）。
  const [viewingId, setViewingId] = useState<string | null>(null);
  const [showGenerateMore, setShowGenerateMore] = useState(false);
  const [generateMoreDirection, setGenerateMoreDirection] =
    useState<GeoIosGenerateMoreDirection>("auto");
  const [generateMoreCustomText, setGenerateMoreCustomText] = useState("");

  if (presentationQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("geo.ios.situation.loading")}
      </div>
    );
  }
  if (presentationQuery.isError || !presentationQuery.data) {
    return (
      <p className="text-sm text-muted-foreground py-6">{t("geo.ios.situation.notAvailableYet")}</p>
    );
  }

  const { presentation, raw_analysis: rawAnalysis } = presentationQuery.data;

  // READING_EXPOSURE_LAYER_V1配線（2026-09-24）: Composerが返した最大5件(以降、Generate
  // More Readingsで追加された分を含む)をSource of Truthとして使う。Composerが未接続/
  // 失敗した場合（空配列）のみ、既存interpretationsへフォールバックする（Backward
  // Compatibility）。
  const readingCards: GeoIosReadingExposureItem[] =
    presentation.reading_exposure.length > 0
      ? presentation.reading_exposure
      : presentation.interpretations.map((interp) => ({
          ...interp,
          reading_role: (interp.is_main ? "MAIN" : "ALTERNATIVE") as GeoIosReadingRole,
          exploratory: false,
          selected: false,
          why_not_main: null,
          reading_presentation: null,
        }));

  const displayedInterpretations = readingCards.filter((i) => i.displayed);
  const hiddenInterpretations = readingCards.filter((i) => !i.displayed);
  const shownInterpretations = showAllInterpretations ? readingCards : displayedInterpretations;
  // 「勝手にRank1を選択済みにしない」（ユーザー指示）: 見た目上のデフォルト選択は
  // 作らない。実際にDB上で選択済み(selected_scenario_candidate_id)の場合のみ選択済み
  // として扱う——ユーザーが明示的にカードをクリックするまでは未選択のまま。
  const effectiveSelectedId = isReviewMode
    ? (reviewPendingSelectedId ?? rawAnalysis.selected_scenario_candidate_id ?? null)
    : (rawAnalysis.selected_scenario_candidate_id ?? null);

  // ALTERNATIVE roleは最大2件出現し得るため、出現順で表示ラベルを分ける
  // （「有力な別解」→「もう一つの読み」、既存rank順序をそのまま出現順に使うだけ）。
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

  // 5つの読み方一覧（Reading Report v2）: 01〜05＋Reading Tag＋title＋summary。
  // 色はsemantic_tagがあればそちらを、無ければ位置ベースのPOSITION_CHIPを使い、
  // 同じroleが複数あっても5件が必ず視覚的に区別できるようにする。
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
  // 見ているReading: ローカル選択が無ければ、DB上採用済みのReading→無ければ先頭、の順で
  // フォールバックする（常に何かが表示されている状態を保つ）。
  const effectiveViewingId =
    viewingId ?? effectiveSelectedId ?? shownInterpretations[0]?.scenario_candidate_id ?? null;
  const viewingIndex = Math.max(
    0,
    shownInterpretations.findIndex((i) => i.scenario_candidate_id === effectiveViewingId),
  );
  const viewingInterpretation = shownInterpretations[viewingIndex];
  const viewingSelectorItem = selectorItems[viewingIndex];

  const canGenerateMore = readingCards.length < 8;
  // Expert Review UI（2026-10-04）: バックエンドがexpert_reviewを付けている場合のみ使う。
  const useExpertReviewUi =
    !!presentation.expert_review &&
    shownInterpretations.length > 0 &&
    shownInterpretations.every((i) => i.expert_review);

  return (
    <div className="space-y-6">
      {/* §3-§4 あり得る読み方 */}
      <section className="space-y-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-xl font-semibold text-[#172d3d]">
              {t("geo.ios.situation.interpretationsTitle")}
            </h2>
            <p className="m-0 mt-1 geo-meta max-w-[640px]">
              {t("geo.ios.situation.readingsSectionSubtitle")}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setShowGenerateMore(true)}>
            {t("geo.ios.situation.generateMoreButton")}
          </Button>
        </div>

        {useExpertReviewUi ? (
          // Expert Review UI（2026-10-04）: Frozenページと同じ「専門家のレビューコメント」表示。
          <ExpertReviewReport
            presentation={presentation}
            cards={shownInterpretations}
            hiddenCount={hiddenInterpretations.length}
            showAll={showAllInterpretations}
            onToggleShowAll={() => setShowAllInterpretations((v) => !v)}
          />
        ) : (
          <>
            {/* §3 5つの読み方一覧: クリックで下のReport Card内容を切り替えるだけ
                （ページ遷移なし、採用はここでは行わない）。 */}
            <ReadingSelectorGrid
              items={selectorItems}
              selectedId={effectiveViewingId}
              onSelect={setViewingId}
            />
            {hiddenInterpretations.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowAllInterpretations((v) => !v)}
              >
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
          </>
        )}
      </section>

      {/* §13 別の読み方を探す: 既存A-Eを文脈として使い、方向性を指定して追加生成する。
          既存の読みとの意味的重複はバックエンド(generate_directed_exploration_reading)側
          で避ける。新規は末尾(F/G/...)へ追記されるだけで、既存の読みは上書きしない。 */}
      <Dialog open={showGenerateMore} onOpenChange={setShowGenerateMore}>
        <DialogContent className="max-w-[540px]">
          <DialogHeader>
            <DialogTitle>{t("geo.ios.situation.generateMoreModalTitle")}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-2">
            {GENERATE_MORE_DIRECTIONS.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setGenerateMoreDirection(d)}
                className={`text-left rounded-[10px] border px-3 py-2 text-sm transition-colors ${
                  generateMoreDirection === d
                    ? "border-[#36bfd6] bg-[#eaf8fb] text-[#0d879a] font-bold"
                    : "border-[#d8e2e7] hover:border-[#36bfd6]"
                }`}
              >
                {t(`geo.ios.situation.generateMoreDirection.${d}`)}
              </button>
            ))}
          </div>
          {generateMoreDirection === "custom" && (
            <textarea
              className="w-full rounded-[10px] border border-[#d8e2e7] p-2 text-sm"
              rows={3}
              placeholder={t("geo.ios.situation.generateMoreCustomPlaceholder")}
              value={generateMoreCustomText}
              onChange={(e) => setGenerateMoreCustomText(e.target.value)}
            />
          )}
          {!canGenerateMore && (
            <p className="text-xs text-destructive">
              {t("geo.ios.situation.generateMoreMaxReached")}
            </p>
          )}
          {generateMoreReadings.isSuccess && generateMoreReadings.data.created === false && (
            <p className="text-xs text-muted-foreground">
              {generateMoreReadings.data.reason === "max_readings_reached"
                ? t("geo.ios.situation.generateMoreMaxReached")
                : t("geo.ios.situation.generateMoreNoneFound")}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowGenerateMore(false)}>
              {t("geo.ios.situation.generateMoreCancel")}
            </Button>
            <Button
              disabled={
                !canGenerateMore ||
                generateMoreReadings.isPending ||
                (generateMoreDirection === "custom" && !generateMoreCustomText.trim())
              }
              onClick={() =>
                generateMoreReadings.mutate(
                  {
                    direction: generateMoreDirection,
                    custom_text: generateMoreCustomText,
                    actor_id: actorId,
                  },
                  {
                    onSuccess: (res) => {
                      if (res.created) setShowGenerateMore(false);
                    },
                  },
                )
              }
            >
              {generateMoreReadings.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {generateMoreReadings.isPending
                ? t("geo.ios.situation.generateMoreSearching")
                : t("geo.ios.situation.generateMoreSubmit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* この局面についてさらに話す: 切り口相談(framing)は「局面を読む」と同じ
          research_designモードのため、別画面には分けずここへ直接埋め込む
          （2箇所で似た内容が別デザインで出て読みづらい、というユーザー指摘への対応）。
          REVIEW MODEにはCQAS前提のconsultationモードが無いため出さない（§14-16は
          今回スコープ外、Known Gap）。 */}
      {!isReviewMode && (
        <section className="space-y-2 border-t border-border pt-4">
          <h2 className="text-xl font-semibold text-[#172d3d]">
            {t("geo.ios.situation.discussTitle")}
          </h2>
          <GeoIosConsultationThread
            sessionId={sessionId}
            actorId={actorId}
            mode="framing"
            height="h-[360px]"
          />
        </section>
      )}
    </div>
  );
}
