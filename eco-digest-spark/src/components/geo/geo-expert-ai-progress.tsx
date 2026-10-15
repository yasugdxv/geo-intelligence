import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useLanguage } from "@/lib/i18n";

// Expert AI呼び出し（Generate Scenarios/Generate Design/Chatでの再分析等）は実測で
// 1〜2分程度かかる（Knowledge DB検索＋複数回のLLM呼び出しを内部で行うため、既存の
// Expert AI Core自体は正常——単に時間がかかるだけで、ハングしているわけではない）。
// Backend側の段階（Stage1/2/3等）とは連動しない、あくまで「動いている」ことを伝える
// ための経過時間＋ローテーションメッセージ。実際のStage進捗をBackendから取得する
// 仕組みではない（フロントエンドのみの体感改善、Expert AI Core/APIは無変更）。
const ROTATION_INTERVAL_SEC = 14;

// startedAtを渡す場合はそこからの経過時間を使う（ページ離脱・再訪してもDBのcreated_at基準
// で経過時間が再計算できる、コンポーネントのmount時刻に依存しない）。省略時はmount時刻基準
// （既存のGenerate Scenarios等、単発ボタン押下からの経過表示用、DBに開始時刻を持たない）。
function useElapsedSeconds(active: boolean, startedAt?: string): number {
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active) {
      setElapsed(0);
      startRef.current = null;
      return;
    }
    startRef.current = startedAt ? new Date(startedAt).getTime() : Date.now();
    const tick = () => {
      if (startRef.current != null) {
        setElapsed(Math.max(0, Math.floor((Date.now() - startRef.current) / 1000)));
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [active, startedAt]);

  return elapsed;
}

export function GeoExpertAiProgress({
  active, className, startedAt,
}: { active: boolean; className?: string; startedAt?: string }) {
  const { t } = useLanguage();
  const elapsed = useElapsedSeconds(active, startedAt);
  if (!active) return null;

  const messages = [
    t("geo.expertAiProgress.step1"),
    t("geo.expertAiProgress.step2"),
    t("geo.expertAiProgress.step3"),
    t("geo.expertAiProgress.step4"),
  ];
  const message = messages[Math.min(
    Math.floor(elapsed / ROTATION_INTERVAL_SEC), messages.length - 1,
  )];

  return (
    <div className={`flex items-center gap-2 text-xs text-muted-foreground ${className ?? ""}`}>
      <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
      <span>{message} {t("geo.expertAiProgress.elapsed", { seconds: String(elapsed) })}</span>
    </div>
  );
}
