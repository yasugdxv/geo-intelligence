// Intelligence AI の Chat タブを包むシェル。会話モードが無効（Feature Flag OFF・通信失敗）のときは、
// 何も足さず children（従来のチャット）だけを描画する。有効なときだけ4モード選択を出し、
// 通常モードでは children をそのまま表示する（非表示中も状態を保つため、アンマウントしない）。
import { useRef, useState, type ReactNode } from "react";
import { ModeSelector, type ShellMode } from "@/components/geo/conversation-mode/mode-selector";
import { ConversationModePanel } from "@/components/geo/conversation-mode/conversation-mode-panel";
import {
  useConversationModeConfig,
  type CmCarry,
  type CmState,
} from "@/lib/geoConversationModeApi";

export function IntelligenceModeShell({ children }: { children: ReactNode }) {
  const config = useConversationModeConfig();
  const [mode, setMode] = useState<ShellMode>("normal");
  const [carry, setCarry] = useState<CmCarry | null>(null);
  const [nonce, setNonce] = useState(0);
  const [busy, setBusy] = useState(false);
  const latest = useRef<CmState | null>(null);

  if (!config.data?.enabled) return <>{children}</>;

  const change = (next: ShellMode) => {
    if (next === mode) return;
    // 会話モード間の切替だけ、同じテーマの「出典つきの事実」と「未解決の問い」を引き継ぐ（仮定・演出は引き継がない）
    const s = latest.current;
    if (mode !== "normal" && next !== "normal" && s) {
      setCarry({
        facts: s.ledger.facts.filter((f) => (f.ref_ids ?? []).length > 0),
        open_questions: s.ledger.open_questions,
        evidence: s.evidence,
        theme: s.theme,
        parent_id: s.conversation_id,
      });
    } else {
      setCarry(null);
    }
    latest.current = null;
    setNonce((n) => n + 1);
    setMode(next);
  };

  return (
    <div className="space-y-5">
      <ModeSelector
        modes={config.data.modes ?? []}
        value={mode}
        onChange={change}
        disabled={busy}
      />
      <div hidden={mode !== "normal"}>{children}</div>
      {mode !== "normal" && (
        <ConversationModePanel
          key={`${mode}-${nonce}`}
          mode={mode}
          config={config.data}
          carry={carry}
          onStateChange={(s) => {
            latest.current = s;
          }}
          onBusyChange={setBusy}
        />
      )}
    </div>
  );
}
