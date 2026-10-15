import { Loader2 } from "lucide-react";
import { useSituationPresentationByResponseId } from "@/lib/geoIosSituationPresentationApi";
import { ReadingPresentationV2Report } from "@/components/geo/geo-ios-reading-presentation-v2";
import { useLanguage } from "@/lib/i18n";

// Expert Session Readiness Patch Phase S1（2026-10-02）: response_id直接指定の読み取り
// 専用Presentation View。IOS Analysis Session（CQAS/Design/相談スレッド等）には一切
// 依存しない——Gold/Calibration Case評価用のresponse_idを直接見るための、選択・書き込み
// を行わない純粋な読み取り専用画面。表示にはgeo-ios-situation-analysis-view.tsxと同じ
// Reading Presentation v2（ReadingSelectorGrid＋7セクションReport Card）を共有
// コンポーネント（geo-ios-reading-presentation-v2.tsx）経由でそのまま使う——IOS Session
// 経由ルートと見た目・ロジックを二重実装しない。新しい推論・新しいLLM呼び出しはここでは
// 行わない。
interface Props {
  responseId: string;
  debug?: boolean;
}

export function GeoSituationPresentationView({ responseId, debug = false }: Props) {
  const { t } = useLanguage();
  const presentationQuery = useSituationPresentationByResponseId(responseId, debug);

  if (presentationQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("geo.ios.situation.loading")}
      </div>
    );
  }
  if (presentationQuery.isError || !presentationQuery.data) {
    return (
      <p className="text-sm text-muted-foreground py-6">
        {presentationQuery.error instanceof Error
          ? presentationQuery.error.message
          : t("geo.ios.situation.notAvailableYet")}
      </p>
    );
  }

  return <ReadingPresentationV2Report presentation={presentationQuery.data.presentation} />;
}
