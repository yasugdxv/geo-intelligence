import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/lib/i18n";
import {
  useExpertSessionCase,
  useStartExpertSessionCase,
  useSaveExpertFirstInput,
  useLockExpertSessionCase,
  useRevealExpertSessionCase,
  useSubmitExpertSessionCase,
  useSaveExpertSessionEvaluation,
  type GeoExpertSessionCaseView as CaseViewData,
  type GeoExpertSessionExpertFirstInput,
  type GeoExpertSessionRubricKey,
  type GeoExpertSessionRubricItem,
  type GeoExpertSessionGapReview,
} from "@/lib/geoExpertSessionCaptureApi";
import { useSituationPresentationByResponseId } from "@/lib/geoIosSituationPresentationApi";
import { ReadingPresentationV2Report } from "@/components/geo/geo-ios-reading-presentation-v2";

// Expert Session Capture V1（Phase S2-S3、2026-10-02）: Gold/Calibration両Flowの入口。
// Reading表示はS1で共通化したReadingPresentationV2Reportをそのまま使う（§13、二重実装
// しない）。Calibrationのresponse_idはrevealed_at確定後のみAPIから返る（Blind Integrity、
// §9/§15）——このファイル側でcssや条件分岐によるご誤魔化しの非表示は行わず、単に
// view.response_idが無ければ何も取得・表示しないだけ。
const RUBRIC_ITEMS: Array<{ key: GeoExpertSessionRubricKey; labelKey: string }> = [
  { key: "situation_understanding", labelKey: "geo.expertSession.rubricSituationUnderstanding" },
  { key: "initial_cue", labelKey: "geo.expertSession.rubricInitialCue" },
  { key: "actor_structure", labelKey: "geo.expertSession.rubricActorStructure" },
  { key: "multiple_readings", labelKey: "geo.expertSession.rubricMultipleReadings" },
  { key: "primary_reading", labelKey: "geo.expertSession.rubricPrimaryReading" },
  { key: "evidence_weakening", labelKey: "geo.expertSession.rubricEvidenceWeakening" },
  { key: "switch_condition", labelKey: "geo.expertSession.rubricSwitchCondition" },
];

const RATING_OPTIONS: Array<{
  value: NonNullable<GeoExpertSessionRubricItem["rating"]>;
  labelKey: string;
}> = [
  { value: "valid", labelKey: "geo.expertSession.ratingValid" },
  { value: "needs_minor_fix", labelKey: "geo.expertSession.ratingNeedsMinorFix" },
  { value: "needs_major_fix", labelKey: "geo.expertSession.ratingNeedsMajorFix" },
];

const FEEDBACK_TAGS: Array<{ value: string; labelKey: string }> = [
  { value: "missing_cue", labelKey: "geo.expertSession.tagMissingCue" },
  { value: "wrong_weight", labelKey: "geo.expertSession.tagWrongWeight" },
  { value: "missing_actor_structure", labelKey: "geo.expertSession.tagMissingActorStructure" },
  { value: "wrong_causal_link", labelKey: "geo.expertSession.tagWrongCausalLink" },
  { value: "missing_alternative", labelKey: "geo.expertSession.tagMissingAlternative" },
  { value: "missing_evidence", labelKey: "geo.expertSession.tagMissingEvidence" },
  { value: "primary_selection", labelKey: "geo.expertSession.tagPrimarySelection" },
  { value: "switch_condition", labelKey: "geo.expertSession.tagSwitchCondition" },
  { value: "presentation", labelKey: "geo.expertSession.tagPresentation" },
];

const FIRST_INPUT_FIELDS: Array<{ key: keyof GeoExpertSessionExpertFirstInput; labelKey: string }> =
  [
    { key: "situation", labelKey: "geo.expertSession.firstInputSituation" },
    { key: "what_to_look_at", labelKey: "geo.expertSession.firstInputWhatToLookAt" },
    { key: "key_actor", labelKey: "geo.expertSession.firstInputKeyActor" },
    {
      key: "relationship_structure",
      labelKey: "geo.expertSession.firstInputRelationshipStructure",
    },
    { key: "multiple_readings", labelKey: "geo.expertSession.firstInputMultipleReadings" },
    { key: "primary_reading", labelKey: "geo.expertSession.firstInputPrimaryReading" },
    { key: "supporting_evidence", labelKey: "geo.expertSession.firstInputSupportingEvidence" },
    { key: "weakening_evidence", labelKey: "geo.expertSession.firstInputWeakeningEvidence" },
    { key: "switch_condition", labelKey: "geo.expertSession.firstInputSwitchCondition" },
  ];

function FeedbackTagPicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const { t } = useLanguage();
  return (
    <div className="space-y-2">
      <b className="block text-[12px] text-[#0d879a]">{t("geo.expertSession.feedbackTagsLabel")}</b>
      <div className="flex flex-wrap gap-[8px]">
        {FEEDBACK_TAGS.map((tag) => {
          const active = value.includes(tag.value);
          return (
            <button
              key={tag.value}
              type="button"
              onClick={() =>
                onChange(active ? value.filter((v) => v !== tag.value) : [...value, tag.value])
              }
              className={`px-3 py-[6px] rounded-full text-[12px] font-bold border ${
                active
                  ? "border-[#36bfd6] bg-[#eaf8fb] text-[#0d879a]"
                  : "border-[#d8e2e7] bg-white text-[#687b88]"
              }`}
            >
              {t(tag.labelKey)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function GoldEvaluationForm({ view, caseKey }: { view: CaseViewData; caseKey: string }) {
  const { t } = useLanguage();
  const saveEvaluation = useSaveExpertSessionEvaluation(caseKey);
  const submitMutation = useSubmitExpertSessionCase(caseKey);

  const [rubric, setRubric] = useState<
    Partial<Record<GeoExpertSessionRubricKey, GeoExpertSessionRubricItem>>
  >(view.evaluation?.rubric_assessments ?? {});
  const [comment, setComment] = useState(view.evaluation?.expert_comment ?? "");
  const [tags, setTags] = useState<string[]>(view.evaluation?.feedback_tags ?? []);

  useEffect(() => {
    setRubric(view.evaluation?.rubric_assessments ?? {});
    setComment(view.evaluation?.expert_comment ?? "");
    setTags(view.evaluation?.feedback_tags ?? []);
  }, [view.run_id, view.evaluation]);

  const setRating = (
    key: GeoExpertSessionRubricKey,
    rating: GeoExpertSessionRubricItem["rating"],
  ) => setRubric((prev) => ({ ...prev, [key]: { ...prev[key], rating } }));
  const setItemComment = (key: GeoExpertSessionRubricKey, itemComment: string) =>
    setRubric((prev) => ({ ...prev, [key]: { ...prev[key], comment: itemComment } }));

  const handleSave = () =>
    saveEvaluation.mutate({
      rubric_assessments: rubric,
      expert_comment: comment,
      feedback_tags: tags,
    });
  const handleSubmit = async () => {
    await saveEvaluation.mutateAsync({
      rubric_assessments: rubric,
      expert_comment: comment,
      feedback_tags: tags,
    });
    submitMutation.mutate();
  };

  return (
    <section className="rounded-[18px] border border-[#d8e2e7] bg-white p-[22px] space-y-5">
      <h2 className="m-0 text-[19px] font-black text-[#0b3568]">
        {t("geo.expertSession.goldEvaluationTitle")}
      </h2>
      {RUBRIC_ITEMS.map((item) => (
        <div key={item.key} className="space-y-1.5">
          <b className="block text-[14px] text-[#172d3d]">{t(item.labelKey)}</b>
          <div className="flex gap-[8px] flex-wrap">
            {RATING_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setRating(item.key, opt.value)}
                className={`px-3 py-[6px] rounded-full text-[12px] font-bold border ${
                  rubric[item.key]?.rating === opt.value
                    ? "border-[#36bfd6] bg-[#eaf8fb] text-[#0d879a]"
                    : "border-[#d8e2e7] bg-white text-[#687b88]"
                }`}
              >
                {t(opt.labelKey)}
              </button>
            ))}
          </div>
          <input
            className="w-full rounded-[8px] border border-[#d8e2e7] px-3 py-1.5 text-sm"
            placeholder={t("geo.expertSession.rubricCommentPlaceholder")}
            value={rubric[item.key]?.comment ?? ""}
            onChange={(e) => setItemComment(item.key, e.target.value)}
          />
        </div>
      ))}

      <div className="pt-2 border-t border-[#d8e2e7] space-y-1.5">
        <b className="block text-[14px] text-[#172d3d]">
          {t("geo.expertSession.expertCommentLabel")}
        </b>
        <textarea
          className="w-full rounded-[10px] border border-[#d8e2e7] p-3 text-sm"
          rows={4}
          placeholder={t("geo.expertSession.expertCommentPlaceholder")}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </div>

      <FeedbackTagPicker value={tags} onChange={setTags} />

      <div className="flex gap-[10px] items-center">
        <Button variant="outline" disabled={saveEvaluation.isPending} onClick={handleSave}>
          {saveEvaluation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {t("geo.expertSession.saveButton")}
        </Button>
        <Button disabled={submitMutation.isPending} onClick={handleSubmit}>
          {submitMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {t("geo.expertSession.submitButton")}
        </Button>
        {saveEvaluation.isSuccess && !saveEvaluation.isPending && (
          <span className="text-[12px] text-[#2f7a63]">{t("geo.expertSession.savedNote")}</span>
        )}
      </div>
    </section>
  );
}

function GoldCaseFlow({ view, caseKey }: { view: CaseViewData; caseKey: string }) {
  const presentationQuery = useSituationPresentationByResponseId(view.response_id ?? "");
  return (
    <div className="space-y-6">
      {presentationQuery.data && (
        <ReadingPresentationV2Report presentation={presentationQuery.data.presentation} />
      )}
      <GoldEvaluationForm view={view} caseKey={caseKey} />
    </div>
  );
}

function ExpertFirstInputForm({ view, caseKey }: { view: CaseViewData; caseKey: string }) {
  const { t } = useLanguage();
  const saveFirstInput = useSaveExpertFirstInput(caseKey);
  const lockMutation = useLockExpertSessionCase(caseKey);
  const [values, setValues] = useState<GeoExpertSessionExpertFirstInput>(
    view.expert_first_input ?? {},
  );

  useEffect(() => {
    setValues(view.expert_first_input ?? {});
  }, [view.run_id, view.expert_first_input]);

  const requiredFilled =
    !!values.situation?.trim() &&
    !!values.what_to_look_at?.trim() &&
    !!values.primary_reading?.trim();

  return (
    <section className="rounded-[18px] border border-[#d8e2e7] bg-white p-[22px] space-y-5">
      <h2 className="m-0 text-[19px] font-black text-[#0b3568]">
        {t("geo.expertSession.expertFirstInputTitle")}
      </h2>
      {FIRST_INPUT_FIELDS.map((field) => (
        <div key={field.key} className="space-y-1.5">
          <b className="block text-[14px] text-[#172d3d]">{t(field.labelKey)}</b>
          <textarea
            className="w-full rounded-[10px] border border-[#d8e2e7] p-3 text-sm"
            rows={2}
            value={values[field.key] ?? ""}
            onChange={(e) => setValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
          />
        </div>
      ))}
      <p className="m-0 text-[12px] text-[#687b88]">
        {t("geo.expertSession.requiredBeforeLockNote")}
      </p>
      <div className="flex gap-[10px] items-center">
        <Button
          variant="outline"
          disabled={saveFirstInput.isPending}
          onClick={() => saveFirstInput.mutate(values)}
        >
          {saveFirstInput.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {t("geo.expertSession.saveButton")}
        </Button>
        <Button
          disabled={!requiredFilled || lockMutation.isPending}
          onClick={async () => {
            await saveFirstInput.mutateAsync(values);
            lockMutation.mutate();
          }}
        >
          {lockMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {t("geo.expertSession.lockButton")}
        </Button>
        {saveFirstInput.isSuccess && !saveFirstInput.isPending && (
          <span className="text-[12px] text-[#2f7a63]">{t("geo.expertSession.savedNote")}</span>
        )}
      </div>
    </section>
  );
}

function GapReviewForm({ view, caseKey }: { view: CaseViewData; caseKey: string }) {
  const { t } = useLanguage();
  const saveEvaluation = useSaveExpertSessionEvaluation(caseKey);
  const submitMutation = useSubmitExpertSessionCase(caseKey);
  const [gap, setGap] = useState<GeoExpertSessionGapReview>(view.evaluation?.gap_review ?? {});
  const [tags, setTags] = useState<string[]>(view.evaluation?.feedback_tags ?? []);

  useEffect(() => {
    setGap(view.evaluation?.gap_review ?? {});
    setTags(view.evaluation?.feedback_tags ?? []);
  }, [view.run_id, view.evaluation]);

  const update = (patch: Partial<GeoExpertSessionGapReview>) =>
    setGap((prev) => ({ ...prev, ...patch }));
  const handleSave = () => saveEvaluation.mutate({ gap_review: gap, feedback_tags: tags });
  const handleSubmit = async () => {
    await saveEvaluation.mutateAsync({ gap_review: gap, feedback_tags: tags });
    submitMutation.mutate();
  };

  return (
    <section className="rounded-[18px] border border-[#d8e2e7] bg-white p-[22px] space-y-5">
      <h2 className="m-0 text-[19px] font-black text-[#0b3568]">
        {t("geo.expertSession.gapReviewTitle")}
      </h2>
      <div className="space-y-1.5">
        <b className="block text-[14px] text-[#172d3d]">
          {t("geo.expertSession.gapExpertHadAiMissed")}
        </b>
        <textarea
          className="w-full rounded-[10px] border border-[#d8e2e7] p-3 text-sm"
          rows={2}
          value={gap.expert_had_ai_missed ?? ""}
          onChange={(e) => update({ expert_had_ai_missed: e.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <b className="block text-[14px] text-[#172d3d]">
          {t("geo.expertSession.gapAiHadExpertMissed")}
        </b>
        <textarea
          className="w-full rounded-[10px] border border-[#d8e2e7] p-3 text-sm"
          rows={2}
          value={gap.ai_had_expert_missed ?? ""}
          onChange={(e) => update({ ai_had_expert_missed: e.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <b className="block text-[14px] text-[#172d3d]">
          {t("geo.expertSession.gapWeightDifferences")}
        </b>
        <textarea
          className="w-full rounded-[10px] border border-[#d8e2e7] p-3 text-sm"
          rows={2}
          value={gap.weight_differences ?? ""}
          onChange={(e) => update({ weight_differences: e.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <b className="block text-[14px] text-[#172d3d]">
          {t("geo.expertSession.gapPrimaryReadingDiffLabel")}
        </b>
        <div className="flex gap-[8px] flex-wrap">
          {(["same", "different", "partially_different"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => update({ primary_reading_diff: v })}
              className={`px-3 py-[6px] rounded-full text-[12px] font-bold border ${
                gap.primary_reading_diff === v
                  ? "border-[#36bfd6] bg-[#eaf8fb] text-[#0d879a]"
                  : "border-[#d8e2e7] bg-white text-[#687b88]"
              }`}
            >
              {t(
                `geo.expertSession.gapPrimary${v === "same" ? "Same" : v === "different" ? "Different" : "PartiallyDifferent"}`,
              )}
            </button>
          ))}
        </div>
        <input
          className="w-full rounded-[8px] border border-[#d8e2e7] px-3 py-1.5 text-sm"
          placeholder={t("geo.expertSession.gapPrimaryReadingDiffReason")}
          value={gap.primary_reading_diff_reason ?? ""}
          onChange={(e) => update({ primary_reading_diff_reason: e.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <b className="block text-[14px] text-[#172d3d]">{t("geo.expertSession.gapHowToFixAi")}</b>
        <textarea
          className="w-full rounded-[10px] border border-[#d8e2e7] p-3 text-sm"
          rows={3}
          value={gap.how_to_fix_ai ?? ""}
          onChange={(e) => update({ how_to_fix_ai: e.target.value })}
        />
      </div>

      <FeedbackTagPicker value={tags} onChange={setTags} />

      <div className="flex gap-[10px] items-center">
        <Button variant="outline" disabled={saveEvaluation.isPending} onClick={handleSave}>
          {saveEvaluation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {t("geo.expertSession.saveButton")}
        </Button>
        <Button disabled={submitMutation.isPending} onClick={handleSubmit}>
          {submitMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {t("geo.expertSession.submitButton")}
        </Button>
      </div>
    </section>
  );
}

function CalibrationCaseFlow({ view, caseKey }: { view: CaseViewData; caseKey: string }) {
  const { t } = useLanguage();
  const revealMutation = useRevealExpertSessionCase(caseKey);
  const isLocked = !!view.locked_at;
  const isRevealed = !!view.revealed_at;
  const presentationQuery = useSituationPresentationByResponseId(view.response_id ?? "");

  return (
    <div className="space-y-6">
      {!isLocked && <ExpertFirstInputForm view={view} caseKey={caseKey} />}
      {isLocked && !isRevealed && (
        <section className="rounded-[18px] border border-[#d8e2e7] bg-white p-[22px] space-y-4">
          <p className="m-0 text-[14px] text-[#2f7a63]">{t("geo.expertSession.lockedNote")}</p>
          <Button disabled={revealMutation.isPending} onClick={() => revealMutation.mutate()}>
            {revealMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {t("geo.expertSession.revealButton")}
          </Button>
        </section>
      )}
      {isRevealed && presentationQuery.data && (
        <>
          <ReadingPresentationV2Report presentation={presentationQuery.data.presentation} />
          <GapReviewForm view={view} caseKey={caseKey} />
        </>
      )}
    </div>
  );
}

export function GeoExpertSessionCaseView({ caseKey }: { caseKey: string }) {
  const { t } = useLanguage();
  const caseQuery = useExpertSessionCase(caseKey);
  const startMutation = useStartExpertSessionCase(caseKey);

  useEffect(() => {
    if (caseQuery.data && caseQuery.data.status === "not_started" && !startMutation.isPending) {
      startMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseQuery.data?.status]);

  const backLink = (
    <Link to="/geo/expert-session" className="text-[13px] text-[#0d879a] font-bold">
      ← {t("geo.expertSession.backToHome")}
    </Link>
  );

  if (caseQuery.isLoading || startMutation.isPending) {
    return (
      <div className="space-y-4">
        {backLink}
        <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
          <Loader2 className="h-4 w-4 animate-spin" /> ...
        </div>
      </div>
    );
  }
  if (caseQuery.isError || !caseQuery.data) {
    return (
      <div className="space-y-4">
        {backLink}
        <p className="text-sm text-muted-foreground py-6">
          {caseQuery.error instanceof Error ? caseQuery.error.message : "error"}
        </p>
      </div>
    );
  }

  const view = caseQuery.data;

  return (
    <div className="space-y-6">
      {backLink}
      <header className="space-y-1">
        <span className="text-[11px] font-black text-[#687b88]">
          {view.case_type.toUpperCase()}
        </span>
        <h1 className="m-0 text-[23px] font-black text-[#0b3568]">{view.title}</h1>
      </header>
      {view.case_type === "gold" ? (
        <GoldCaseFlow view={view} caseKey={caseKey} />
      ) : (
        <CalibrationCaseFlow view={view} caseKey={caseKey} />
      )}
    </div>
  );
}
