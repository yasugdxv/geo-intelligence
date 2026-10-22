// 会話モードの画像: モード選択の画像パネルと、チャットの話者のアイコン。
// 画像は public/conversation-mode/ に置く（お渡しのイラストを縮小・切り出したもの）。
//   パネル: mode-gal.webp / mode-consult.webp / mode-roleplay.webp（900×600）
//   アイコン: avatar-mika / avatar-nakasone / avatar-consultant / avatar-todo .webp（160×160、同じイラストから顔を切り出し）
// 通常モードのパネルと、進行役（ミナト）のアイコンは、コードで描いた簡易なSVG。
export type CharacterId = "gal" | "oyaji" | "consultant" | "partner" | "summarizer";

const AVATAR_IMAGE: Partial<Record<CharacterId, string>> = {
  gal: "/conversation-mode/avatar-mika.webp",
  oyaji: "/conversation-mode/avatar-nakasone.webp",
  consultant: "/conversation-mode/avatar-consultant.webp",
  partner: "/conversation-mode/avatar-todo.webp",
};

const MODE_IMAGE: Record<string, string> = {
  gal_oyaji: "/conversation-mode/mode-gal.webp",
  consult_partner: "/conversation-mode/mode-consult.webp",
  stakeholder: "/conversation-mode/mode-roleplay.webp",
};

/** 丸いアイコン（チャットの話者）。 */
export function CharacterAvatar({ id, size = 36 }: { id: CharacterId; size?: number }) {
  const src = AVATAR_IMAGE[id];
  if (src) {
    return (
      <img
        src={src}
        alt=""
        aria-hidden="true"
        data-avatar={id}
        width={size}
        height={size}
        className="shrink-0 rounded-full object-cover shadow-sm"
        style={{ width: size, height: size }}
      />
    );
  }
  // ミナト（進行役）: ヘッドセットの簡易イラスト
  return (
    <svg
      aria-hidden="true"
      data-avatar={id}
      width={size}
      height={size}
      viewBox="0 0 100 100"
      className="shrink-0 rounded-full shadow-sm"
    >
      <rect width="100" height="100" fill="#e3e8ef" />
      <path d="M14 100 Q50 68 86 100 Z" fill="#6b7a90" />
      <rect x="44" y="74" width="12" height="12" rx="4" fill="#f4d3b8" />
      <ellipse cx="50" cy="53" rx="21" ry="24" fill="#f4d3b8" />
      <path d="M28 48 Q30 26 50 26 Q70 26 72 48 Q64 36 50 37 Q36 37 28 48 Z" fill="#b98a58" />
      <path
        d="M26 50 Q24 22 50 22 Q76 22 74 50"
        stroke="#334155"
        strokeWidth="3"
        fill="none"
        strokeLinecap="round"
      />
      <rect x="21" y="48" width="7" height="14" rx="3.5" fill="#334155" />
      <rect x="72" y="48" width="7" height="14" rx="3.5" fill="#334155" />
      <path
        d="M24 62 Q26 74 42 72"
        stroke="#334155"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
      />
      <circle cx="43" cy="72" r="2.6" fill="#334155" />
      <circle cx="42" cy="55" r="2.6" fill="#3b2a2a" />
      <circle cx="58" cy="55" r="2.6" fill="#3b2a2a" />
      <path
        d="M43 66 Q50 71 57 66"
        stroke="#a5584a"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

/** モード選択の画像パネルの上段の画像（16:9に切り抜いて表示）。通常モードだけ、コードで描いたSVG。 */
export function ModeArt({
  mode,
}: {
  mode: "normal" | "gal_oyaji" | "consult_partner" | "stakeholder";
}) {
  const src = MODE_IMAGE[mode];
  if (src) {
    return (
      <img
        src={src}
        alt=""
        aria-hidden="true"
        loading="lazy"
        className="block h-full w-full object-cover object-center"
      />
    );
  }
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 360 203"
      className="block h-full w-full"
      preserveAspectRatio="xMidYMid slice"
    >
      <defs>
        <linearGradient id="cm-art-normal" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#dbe7f5" />
          <stop offset="1" stopColor="#b8cde6" />
        </linearGradient>
      </defs>
      <rect width="360" height="203" fill="url(#cm-art-normal)" />
      <g transform="translate(0 26)">
        <rect x="70" y="26" width="150" height="46" rx="16" fill="#fff" />
        <path d="M96 72 L88 90 L116 72 Z" fill="#fff" />
        <rect x="88" y="40" width="96" height="6" rx="3" fill="#9db4d2" />
        <rect x="88" y="54" width="66" height="6" rx="3" fill="#c3d2e6" />
        <rect x="150" y="82" width="140" height="44" rx="16" fill="#8de055" />
        <path d="M268 126 L276 142 L250 126 Z" fill="#8de055" />
        <rect x="168" y="95" width="100" height="6" rx="3" fill="#5fae34" opacity="0.55" />
        <rect x="168" y="108" width="62" height="6" rx="3" fill="#5fae34" opacity="0.4" />
        <path
          d="M300 34 l5 12 l12 5 l-12 5 l-5 12 l-5 -12 l-12 -5 l12 -5 z"
          fill="#fff"
          opacity="0.9"
        />
      </g>
    </svg>
  );
}
