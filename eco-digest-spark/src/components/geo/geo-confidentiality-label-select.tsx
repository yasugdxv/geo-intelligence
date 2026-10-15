import { CONFIDENTIALITY_LABELS, type GeoConfidentialityLabel } from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

// Phase 2E（Report/Knowledge Confidentiality & Retrieval Access Control）§8: Direct Report
// Registrationとreview Flow Publishの両方が、同じ3つのUI Label（部内限り/SHD限り/
// グループ配信可）とResolverへ収束させる（別々のUIを作らない）。内部Policy（human_visibility/
// agent_sharing_policy/allowed_org_units/allowed_domains）への変換はBackend側の単一の正
// （geo_knowledge_access_policy.resolve_ui_label_to_policy）に委ね、ここではLabel文字列のみを
// 扱う。
export function GeoConfidentialityLabelSelect({
  value,
  onChange,
}: {
  value: GeoConfidentialityLabel | "";
  onChange: (label: GeoConfidentialityLabel) => void;
}) {
  const { t } = useLanguage();
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">{t("geo.confidentiality.required")}</p>
      <div className="grid gap-2 sm:grid-cols-3">
        {CONFIDENTIALITY_LABELS.map((label) => (
          <button
            key={label}
            type="button"
            onClick={() => onChange(label)}
            className={`text-left rounded-[10px] border px-3 py-2 text-sm transition-colors ${
              value === label
                ? "border-[#36bfd6] bg-[#f4fcfd] text-[#0d879a] font-bold"
                : "border-input bg-background hover:border-[#36bfd6]"
            }`}
          >
            <div>{t(`geo.confidentiality.${label}`)}</div>
            <p className="mt-1 text-[11px] font-normal text-muted-foreground">
              {t(`geo.confidentiality.${label}.hint`)}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
