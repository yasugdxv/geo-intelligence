"""地政学インテリジェンス基盤: Explanatory Model Activation v0.3（ユーザー指示）。

Eguchi Reverse Engineering成果物（scratchpad/eguchi_corpus/explanatory_model_v1/の
M01-M10インベントリ、Downloads/eguchi_reasoning_grammar_v0_2.mdのG01-G15、
Downloads/reasoning_episodes_v0_2.csvの30エピソード）から抽出した、再利用可能な
Explanatory Model（違和感に対して具体的な因果説明候補を生成できるパターン）を
静的Libraryとして構造化する。DB migrationは行わない（Version-controlledなPython
定数）。

**Reasoning OperationとExplanatory Modelの違い（重要、ユーザー指示Section 2）**:
Premise Challenge(G01)/Feasibility Check(G08)/Timing Check(G07)/Source Intent
Check(G10)等は「何を確認するか」というDiagnostic Lensであり、それ自体は具体的な
因果説明を生成しない——これらはExpert Reasoning Pipeline v0.2のExplore/Decide
Note側にとどまり、本Libraryには含めない。本Libraryに含めるのは、観測された違和感を
別の具体的な因果説明（Actor/Mechanism/Causal Pathまで踏み込める説明）へ変換できる
テンプレートのみ。

以下は本Libraryから意図的に除外したもの（研究資料には存在するがModelとしては
不採用、理由を明記する）:
- **M09 Comparator-based Anomaly Detection**: 研究資料内で明記されている通り、これは
  Detection専用ツールであり「なぜ」に答える説明を提供しない。Explanatory Modelの
  定義（因果説明候補を生成する）に合致しないため不採用。将来的にはv0.2 Explore Note側の
  Diagnostic Lensとして検討し得るが、今回のスコープ外。
- **M10 Source-provided Frame Adoption**: 「Sourceが既に示す説明をそのまま採用する」は
  現行AIの既定動作そのものであり、Activationを要する追加テンプレートではない
  （研究資料内で「これが現行AIのデフォルトの得意技」と明記）。

Runtime Cardには過去ケースの人物名・Gold結論・専門家が実際に選んだ答えを一切含めない
（ユーザー指示Section 4・19、Gold Leakage防止）。supporting_episode_idsは
provenance metadataとしてのみLibrary内に保持し、Runtime Prompt（Activation Index /
Full Card）には出力しない。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Activation Tier（ユーザー指示Section 5）
# ---------------------------------------------------------------------------
# ACTIVE: Direct Expert Observation(EXPERT_OBSERVED 12episode)があり、再利用可能な
#         Modelとして一定の支持があるもの。v0.3 Default RuntimeはACTIVEのみを対象にする。
# EXPERIMENTAL: 主にReconstructed Gold/少数例(EXPERT_OBSERVEDだが単発かつ構成が複合的)。
# HOLD: 30-episode setのみで支持され、12-episode EXPERT_OBSERVED setには根拠が無い、
#       またはprovenanceが引き続き弱いもの(Precedent/Actor Learning、Meta-signal等、
#       ユーザー指示Section 5で名指しされている2種)。Session3で検証予定。
ACTIVE = "ACTIVE"
EXPERIMENTAL = "EXPERIMENTAL"
HOLD = "HOLD"


MODEL_LIBRARY: dict = {
    "EM01": {
        "model_id": "EM01",
        "name": "Signal vs Substance / Strategic Theater",
        "trigger_signature": [
            "Actors with an existing relationship display an unusually visible or "
            "publicized conflict, criticism, or confrontation.",
            "The degree of publicity itself seems disproportionate to what a genuine "
            "substantive disagreement would require.",
        ],
        "explanatory_move": (
            "Test whether the visible conflict itself serves an audience, bargaining, "
            "or signaling function rather than assuming the confrontation reflects the "
            "actors' true underlying position. Separate what is said/shown publicly "
            "from what the actors are actually doing."
        ),
        "instantiation_prompts": [
            "Who is the intended audience of this public display (domestic base, "
            "third-party counterpart, allied audience)?",
            "What would the actors' behavior look like if this were genuine "
            "disagreement, versus if it were coordinated theater — do observed actions "
            "match the words, or contradict them?",
            "Does either actor have a plausible reason to prefer appearing at odds "
            "rather than aligned right now?",
        ],
        "required_context": ["actor", "relationship"],
        "disqualifiers": [
            "No prior or ongoing relationship between the actors exists to make theater "
            "plausible (unrelated strangers behaving inconsistently is not this pattern).",
            "The observed behavior is entirely private/non-public (no visibility to test).",
        ],
        "provenance_strength": "high",
        "supporting_episode_ids": ["E02", "E05", "E16"],
        "tier": ACTIVE,
    },
    "EM02": {
        "model_id": "EM02",
        "name": "Coordinated Role Differentiation (Good Cop / Bad Cop)",
        "trigger_signature": [
            "Two or more actors from the same camp, government, or coalition show "
            "clearly different tones or postures (one conciliatory, one hardline) "
            "toward the same counterparty or issue.",
            "The differing postures are accompanied by observable behavioral patterns "
            "(not just rhetoric), e.g. differing travel/attendance/engagement choices.",
        ],
        "explanatory_move": (
            "Test whether the apparent internal split is instead a deliberate division "
            "of roles designed to manage the counterparty's psychology — one actor "
            "applies pressure while another preserves a channel for accommodation — "
            "rather than assuming it reflects a genuine internal disagreement."
        ),
        "instantiation_prompts": [
            "Which actor is functioning as the pressure/hardline role and which as the "
            "conciliatory/access-preserving role in this specific interaction?",
            "Is there a plausible shared objective that both roles serve jointly, even "
            "though their public tone differs?",
            "Would treating this as genuine internal conflict instead predict different "
            "observable behavior than what has actually been observed?",
        ],
        "required_context": ["actor", "relationship"],
        "disqualifiers": [
            "Only one actor is involved (no second actor to play the contrasting role).",
            "The differing postures come from actors with no coordinating authority over "
            "each other (e.g., rival factions with no shared leadership).",
        ],
        "provenance_strength": "high",
        "supporting_episode_ids": ["E08"],
        "tier": ACTIVE,
    },
    "EM03": {
        "model_id": "EM03",
        "name": "Beneficiary / Principal-Agent Divergence",
        "trigger_signature": [
            "A nominal actor's (or its subordinate/proxy's) behavior appears to conflict "
            "with that actor's own stated interest or an outcome that was supposedly "
            "favorable to it.",
        ],
        "explanatory_move": (
            "Instead of assuming the nominal actor simply acted irrationally or that the "
            "surface narrative is complete, test who else actually benefits from the "
            "observed outcome, and whether the acting party (if a proxy/subordinate) may "
            "have interests that diverge from its principal's."
        ),
        "instantiation_prompts": [
            "If the stated outcome were truly favorable to the nominal actor, who would "
            "have an incentive to disrupt it — and did that party have the means to do so?",
            "Does the acting party (if not the nominal principal itself) have autonomy or "
            "interests that could diverge from the principal's stated interest?",
            "What concrete mechanism would let a named alternative beneficiary or "
            "blocker actually produce the observed outcome?",
        ],
        "required_context": ["actor", "relationship"],
        "disqualifiers": [
            "No plausible alternative beneficiary or blocker can be named from the "
            "available actor/relationship context (do not assert an unnamed 'someone').",
            "The acting party has no autonomy from the nominal principal (pure agent "
            "with no independent interest or discretion).",
        ],
        "provenance_strength": "medium",
        "supporting_episode_ids": ["E03"],
        "tier": ACTIVE,
    },
    "EM04": {
        "model_id": "EM04",
        "name": "Existential / Escalation Rationality Shift",
        "trigger_signature": [
            "A peacetime or normal-conditions cost-benefit explanation is being applied "
            "to an actor facing a severe crisis, existential threat, or 'nothing left to "
            "lose' situation, without checking whether that premise still holds.",
        ],
        "explanatory_move": (
            "Test whether the actor's ordinary rational-restraint calculus flips once "
            "survival or an existential stake is at issue — actions that look "
            "self-destructive under normal-condition logic can become rational once the "
            "loss function itself has changed."
        ),
        "instantiation_prompts": [
            "Does the current situation represent a normal/peacetime condition for this "
            "actor, or a crisis/existential one — and does the standing explanation "
            "assume the former?",
            "If the actor's loss function has shifted (nothing left to lose, or survival "
            "at stake), what previously 'irrational' action becomes rational?",
            "What observable evidence would distinguish 'premise still holds' from "
            "'premise has flipped under crisis conditions'?",
        ],
        "required_context": ["actor", "structural"],
        "disqualifiers": [
            "No evidence the actor is under acute crisis/existential pressure (do not "
            "invoke this for routine, low-stakes situations).",
        ],
        "provenance_strength": "medium",
        "supporting_episode_ids": ["E01"],
        "tier": ACTIVE,
    },
    "EM05": {
        "model_id": "EM05",
        "name": "Internal Succession / Rivalry Competition",
        "trigger_signature": [
            "Multiple actors within the same administration, party, or institution are "
            "each taking positions on unrelated-seeming issues, and a longer-term "
            "leadership or succession contest between them is plausible.",
            "One such actor takes a politically costly or notable action while others "
            "in the same camp stay silent or are seen to acquiesce.",
        ],
        "explanatory_move": (
            "Read the individual issue-position not only on its own terms, but as a "
            "move in an ongoing relative-standing competition among potential "
            "successors/rivals within the same camp — the same action can simultaneously "
            "serve the stated policy purpose and shift internal positioning."
        ),
        "instantiation_prompts": [
            "Who are the plausible rivals/potential successors within this actor's own "
            "camp, and how does this action affect each of their relative standing?",
            "Is there a pattern of this actor being allowed to take risk on this class "
            "of issue while others are not — what does that suggest about current "
            "internal positioning?",
            "Would a purely policy-driven reading and a succession-competition reading "
            "predict different future behavior from this actor?",
        ],
        "required_context": ["actor", "relationship"],
        "disqualifiers": [
            "No identifiable internal rivals or succession stakes exist for this actor "
            "(single, uncontested leadership with no visible internal competition).",
        ],
        "provenance_strength": "high",
        "supporting_episode_ids": ["E04", "E08"],
        "tier": ACTIVE,
    },
    "EM06": {
        "model_id": "EM06",
        "name": "Effective Dependency Reversal",
        "trigger_signature": [
            "An actor that is nominally weaker, more dependent, or lower-status in a "
            "relationship is observed approaching, appealing to, or accommodating an "
            "actor that is nominally stronger or a formal adversary — directly, rather "
            "than through the expected protector/patron.",
        ],
        "explanatory_move": (
            "Do not take the formal hierarchy (who is nominally strong/weak, "
            "patron/client) at face value. Test whether the actual operational "
            "dependency runs the other way — the 'weak' actor may in practice hold "
            "leverage, or the expected protector may be unable/unwilling to provide the "
            "protection the formal hierarchy implies, forcing direct accommodation."
        ),
        "instantiation_prompts": [
            "Which party actually needs the other's cooperation, restraint, or resources "
            "right now, independent of formal/nominal status?",
            "Has the expected protector/patron's reliability or capacity to act become "
            "questionable in this situation — is that what is forcing the direct move?",
            "What concrete leverage does the nominally weaker actor hold over the "
            "nominally stronger one (economic exposure, access, information, etc.)?",
        ],
        "required_context": ["actor", "relationship"],
        "disqualifiers": [
            "The formal hierarchy and the observed behavior are not actually in tension "
            "(the weaker party is acting entirely through, and consistent with, the "
            "expected protector's own strategy).",
        ],
        "provenance_strength": "medium",
        "supporting_episode_ids": ["E28"],
        "tier": ACTIVE,
    },
    "EM07": {
        "model_id": "EM07",
        "name": "Indirect Leverage via Third-Party Incentive",
        "trigger_signature": [
            "A direct request, demand, or question to a target actor is impractical, "
            "risky, or unlikely to succeed, and an indirect route through a third "
            "party's own self-interest is available instead.",
        ],
        "explanatory_move": (
            "Instead of expecting the target's behavior to be shaped by a direct ask, "
            "test whether an intermediary is being (or should be) moved by aligning "
            "with that intermediary's own interest, which in turn shapes the target's "
            "environment or behavior."
        ),
        "instantiation_prompts": [
            "Who is the plausible intermediary, and what self-interest of theirs aligns "
            "with the outcome being sought from the target actor?",
            "Is a direct approach to the target actor actually available here, and if "
            "so why would an indirect route be preferred instead?",
        ],
        "required_context": ["actor", "relationship"],
        "disqualifiers": [
            "No plausible third party with independent leverage over the target actor "
            "can be identified from context.",
        ],
        "provenance_strength": "low",
        "supporting_episode_ids": ["E07"],
        "tier": EXPERIMENTAL,
    },
    "EM08": {
        "model_id": "EM08",
        "name": "Prior Preparation / Altered-or-Withdrawn Plan",
        "trigger_signature": [
            "The timing, content, or framing of an announcement/action does not match "
            "what would be expected given the apparent preparation time required, or "
            "an anticipated escalatory step conspicuously does not appear.",
        ],
        "explanatory_move": (
            "Instead of treating the mismatch as unexplained noise or assuming no plan "
            "existed, test whether a more extensive or escalatory version of the action "
            "was prepared in advance and then scaled back, delayed, or dropped at the "
            "last moment — and infer what would have needed to change to cause that."
        ),
        "instantiation_prompts": [
            "What is the minimum lead time this kind of action would plausibly require, "
            "and does the observed timeline imply preparation started earlier than "
            "publicly acknowledged?",
            "What escalatory element would a fuller version of this action have "
            "contained, and is that element conspicuously absent?",
            "What development shortly before the observed action could plausibly have "
            "caused a scaled-back or withdrawn version to be chosen instead?",
        ],
        "required_context": ["actor", "structural"],
        "disqualifiers": [
            "No preparation-time-sensitive action is actually involved (e.g., a purely "
            "rhetorical statement with no logistical lead time to reason about).",
        ],
        "provenance_strength": "low",
        "supporting_episode_ids": ["E16"],
        "tier": EXPERIMENTAL,
    },
    "EM09": {
        "model_id": "EM09",
        "name": "Precedent-Driven Expectation Shift",
        "trigger_signature": [
            "An actor's current risk tolerance, confidence, or expectations follow "
            "shortly after a notable prior success, failure, or closely analogous past "
            "episode involving that actor or a comparable one.",
        ],
        "explanatory_move": (
            "Test whether the actor's current posture is best explained not purely by "
            "present-day incentives, but by a precedent effect — rational updating, "
            "overconfidence, trauma-driven caution, or a false analogy carried over "
            "from the prior episode."
        ),
        "instantiation_prompts": [
            "What specific prior episode plausibly shaped this actor's current "
            "expectations, and in which direction (more confident/bold vs. more "
            "cautious/wary)?",
            "Is the current situation actually analogous to the precedent in the "
            "respects that matter, or is the actor over-applying a surface-level "
            "similarity (false analogy)?",
        ],
        "required_context": ["actor", "structural"],
        "disqualifiers": [
            "No identifiable, sufficiently recent or salient precedent exists for this "
            "actor to be drawing on.",
        ],
        "provenance_strength": "low",
        "supporting_episode_ids": [],
        "tier": HOLD,
    },
    "EM10": {
        "model_id": "EM10",
        "name": "Revealed Concern via the Question Itself",
        "trigger_signature": [
            "An actor repeatedly asks, confirms, or raises a specific question or "
            "concern, and the content of what they are asking has not been analyzed "
            "as a signal in its own right.",
        ],
        "explanatory_move": (
            "Treat the fact and content of the question itself as evidence — what an "
            "actor chooses to ask about reveals what they currently see as uncertain, "
            "important, or unresolved, independent of any answer they receive."
        ),
        "instantiation_prompts": [
            "What does the specific thing being asked/confirmed reveal about what this "
            "actor currently does NOT know or is unsure about?",
            "Is this question more consistent with genuine uncertainty, information-"
            "gathering for a separate purpose, or a deliberate signal to the party being "
            "asked?",
        ],
        "required_context": ["actor"],
        "disqualifiers": [
            "No repeated or notable question/concern pattern is actually present in the "
            "source material (do not infer one from a single incidental mention).",
        ],
        "provenance_strength": "low",
        "supporting_episode_ids": [],
        "tier": HOLD,
    },
}


ACTIVE_MODEL_IDS = tuple(m["model_id"] for m in MODEL_LIBRARY.values() if m["tier"] == ACTIVE)
EXPERIMENTAL_MODEL_IDS = tuple(m["model_id"] for m in MODEL_LIBRARY.values() if m["tier"] == EXPERIMENTAL)
HOLD_MODEL_IDS = tuple(m["model_id"] for m in MODEL_LIBRARY.values() if m["tier"] == HOLD)


def build_activation_index(model_ids: tuple = ACTIVE_MODEL_IDS) -> str:
    """Call1へ渡すCompact Activation Index（ユーザー指示Section 7）。1 Modelにつき1行、
    trigger_signatureの要約のみ。Full Model Card（instantiation_prompts等）は含めない
    ——v0.1の5,762字Prompt肥大化を再発させないため。"""
    lines = []
    for model_id in model_ids:
        card = MODEL_LIBRARY[model_id]
        cue_summary = " / ".join(card["trigger_signature"])
        lines.append(f"{model_id} | {card['name']} | {cue_summary}")
    return "\n".join(lines)


def get_full_card_text(model_id: str) -> str | None:
    """Call2へ渡すFull Model Card（case-agnostic、Runtime向け）。provenance_strength・
    supporting_episode_idsはGold Leakage防止のため出力しない（ユーザー指示Section 4・19）。
    Library未登録のmodel_idはNoneを返す（LLMが存在しないIDを生成した場合に無言でスキップする
    ため、呼び出し側でfabricationしない）。"""
    card = MODEL_LIBRARY.get(model_id)
    if card is None:
        return None
    lines = [f"[{card['model_id']}] {card['name']}"]
    lines.append("Trigger: " + " / ".join(card["trigger_signature"]))
    lines.append("Explanatory move: " + card["explanatory_move"])
    lines.append("Instantiate by asking:")
    for p in card["instantiation_prompts"]:
        lines.append(f"  - {p}")
    lines.append("Required context: " + ", ".join(card["required_context"]))
    if card["disqualifiers"]:
        lines.append("Do NOT apply if: " + " / ".join(card["disqualifiers"]))
    return "\n".join(lines)


def build_full_sweep_text(tiers: tuple = (ACTIVE, EXPERIMENTAL)) -> str:
    """Selector-Free Model Sweep実験（ユーザー指示）用: 指定Tierの全ModelのFull Cardを
    まとめて1つのテキストへ連結する（model_activation_requestsによる事前選抜を経ない）。
    EXPERIMENTALのCardにはtier表示を付け、Primary昇格のためだけの根拠にしないことを
    明示する（Candidate生成Threshold ≠ Primary昇格Threshold、既存原則どおり）。HOLDは
    今回も対象外（デフォルトtiersに含めない）。provenance_strength・supporting_episode_ids
    はget_full_card_text同様に出力しない（Gold Leakage防止）。"""
    blocks = []
    for model_id, card in MODEL_LIBRARY.items():
        if card["tier"] not in tiers:
            continue
        text = get_full_card_text(model_id)
        if card["tier"] == EXPERIMENTAL:
            text += "\n[Tier: EXPERIMENTAL — low provenance. Applying this model is fine, " \
                    "but do not promote the resulting candidate to Primary on that basis alone.]"
        blocks.append(text)
    return "\n\n".join(blocks)
