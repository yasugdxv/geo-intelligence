// Intelligence AI の画面上部の4モード選択。画像パネル（上段にイラスト、下段に名前と一言）のカードで選ぶ。
// 通常 / ギャルとおっさんとわたし / コンサルとわたしと同僚 / ロールプレイング会議（タイトルに固有名詞は載せない）
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { ModeArt } from "@/components/geo/conversation-mode/character-art";
import type { CmModeId, CmModeInfo } from "@/lib/geoConversationModeApi";

export type ShellMode = "normal" | CmModeId;

const NORMAL_MODE: { id: "normal"; name: string; tagline: string; icon: string } = {
  id: "normal",
  name: "通常モード",
  tagline: "フラットに会話。プリセットで調査・壁打ち",
  icon: "message-square",
};

export function ModeSelector({
  modes,
  value,
  onChange,
  disabled,
}: {
  modes: CmModeInfo[];
  value: ShellMode;
  onChange: (mode: ShellMode) => void;
  disabled?: boolean;
}) {
  const all: { id: string; name: string; tagline: string; icon: string }[] = [
    NORMAL_MODE,
    ...modes,
  ];
  return (
    <div className="space-y-2">
      <div
        role="radiogroup"
        aria-label="会話モード"
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
      >
        {all.map((m) => {
          const selected = value === m.id;
          return (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => onChange(m.id as ShellMode)}
              className={cn(
                "group relative flex flex-col overflow-hidden rounded-xl border bg-card text-left shadow-sm transition",
                "hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
                selected ? "border-geo-teal ring-2 ring-geo-teal" : "border-border",
              )}
            >
              <span className="block aspect-video w-full overflow-hidden bg-muted">
                <ModeArt mode={m.id as "normal" | CmModeId} />
              </span>
              {selected && (
                <span
                  aria-hidden="true"
                  className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-geo-teal text-white shadow"
                >
                  <Check className="h-4 w-4" />
                </span>
              )}
              <span className="block px-3.5 py-3">
                <span className="block text-sm font-semibold">{m.name}</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">{m.tagline}</span>
              </span>
            </button>
          );
        })}
      </div>
      <p className="text-[11px] text-muted-foreground">
        通常モードは、自由に質問・調査できます。ほかのモードは、同じテーマを別の形で壁打ちできます。
        局面の読みを本格的に出すときは、メニューの「エキスパートAI相談」を使います。
      </p>
      <p className="text-[11px] text-muted-foreground">
        登場人物は、実在しない架空のキャラクターです。
      </p>
    </div>
  );
}
