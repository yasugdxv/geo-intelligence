"""Report Studio「Design」領域: Cluster品質改善のためのSemantic Layer（2026-09-16追加）。

ユーザー要望の背景: max_cluster_records（既定200）に到達したCluster（cap hit）が、
異質な内容を含んだまま1 Clusterとして確定されており、代表投稿（8件程度）だけを見た
Prompt1がEvidence誤分類を起こすリスクがあった。また、discovery_mode=trueのSignal
（S6等）では、既知テーマのkeyword matchingをPrimary routingに使うと新規論点が
ほぼ全てFallbackへ落ちてしまう問題があった。

このモジュールは、Preprocess（Python、決定的）が担う範囲を明確に区切るための
「coherence判定」プリミティブを提供する。ユーザー確認済みの責務分離:

    Preprocess（本モジュール）:
        - cap-hit Clusterのheterogeneity検知（"意味的に同一か"の最終判定ではなく、
          recursive splitのCANDIDATE生成のためのシグナル）
        - discovery_mode Signalのdedup / coarse clustering / noise reduction
    Prompt1 Evidence Intelligence（evidence_intelligence_v1）:
        - 本当のsemantic coherence判定
        - S6 topic discovery（topic_label/topic_summary等の意味づけ）
        - S1-S5とのsemantic overlap判定
        - materiality判断

現在のbackendはdeterministic（LLM/embedding呼び出しなし、Azure OpenAIに
embeddings用Deploymentが存在しないため——2026-09-16確認）。`coherence_backend`
引数で将来embedding backendへ差し替え可能なinterfaceにしてあるが、
embedding backend自体は未実装（NotImplementedErrorを送出する）。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 共有トークナイザ（決定的、ML/外部呼び出し無し）
# ---------------------------------------------------------------------------

# ごく短い/一般的すぎる語を除外する最小限のstopword（英語中心、日本語は文字種の関係で
# トークン化の粒度が粗くなるため別途2文字以上の連続文字列としてのみ扱う）。
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "at", "for", "and", "or", "but", "with", "as", "by", "from", "this", "that",
    "it", "its", "will", "would", "could", "should", "has", "have", "had", "not", "no",
    "new", "says", "said", "about", "into", "over", "after", "before", "than", "then",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[぀-ヿ一-鿿]{2,}")


def tokenize(text: str) -> set:
    """テキストを小文字化・分割し、stopword/短すぎるトークンを除いたトークン集合を返す。
    Jaccard類似度等の「意味的近さ」の決定的近似（embeddingではない）に使う。"""
    if not text:
        return set()
    tokens = _TOKEN_RE.findall(text.lower())
    return {t for t in tokens if len(t) >= 3 and t not in _STOPWORDS}


def jaccard_similarity(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def dominant_share(values: list) -> float:
    """カテゴリ値リストのうち、最頻値が占める割合（0..1）。高いほど「単一カテゴリに
    偏っている」＝均質、低いほど分散している＝異質、の目安にする。"""
    non_empty = [v for v in values if v]
    if not non_empty:
        return 1.0
    counts = Counter(non_empty)
    return max(counts.values()) / len(non_empty)


def salient_tokens(text: str, top_k: int = 2) -> tuple:
    """テキストから頻度上位トークンを取り出す（discovery clusteringの粗いsignature用）。
    「意味」を理解しているわけではなく、単純な頻度ベースの決定的処理であることに注意。

    2026-09-16修正: tokenize()はsetを返すため、そのままlist化するとPythonの
    ハッシュシード（プロセスごとにランダム化される）次第で要素の並びが変わり、
    Counter.most_commonの同数タイブレークがプロセスによって異なる結果になり得た
    （実際に、異なる2つのテキストがどちらも共通の1語をtop-1として選んでしまい、
    偶然signatureが衝突するテストがハッシュシード次第で失敗する事例で発覚）。
    ソートしてから数えることで、同数タイブレークを常に辞書順に固定する。"""
    tokens = sorted(tokenize(text))
    if not tokens:
        return tuple()
    counts = Counter(tokens)
    return tuple(t for t, _ in counts.most_common(top_k))


# ---------------------------------------------------------------------------
# lexical_v2 backend（2026-10-04）: cluster key由来で一定の次元（attitude/source_bucket/matched_terms）に
# 依存せず、本文の語彙だけで「cluster全体が同じ話題か」を測る。既定のdeterministicは変更しない（実データを
# 強制splitしないため）。US実データではdeterministicのdiversityが0.5付近で頭打ちになり、4話題が混在した
# clusterでもLowに届かないことをsynthetic fixtureで確認している。
# ---------------------------------------------------------------------------

BACKEND_DETERMINISTIC = "deterministic"
BACKEND_LEXICAL_V2 = "lexical_v2"
SUPPORTED_BACKENDS = (BACKEND_DETERMINISTIC, BACKEND_LEXICAL_V2)

_LATIN_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile("[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")
# diversity = 1 - cosine(各docと全体の語彙centroid)。実fixtureでの目安: 同一話題の言い換え≈0.2、4話題混在≈0.6、
# 完全な転載=0。US実データ(cap-hit 669件)の分布は報告書で確認し、既定backendへ昇格するかは別途判断する。
_V2_HIGH_DIVERSITY = 0.40   # 以下ならHigh
_V2_LOW_DIVERSITY = 0.55    # 以上ならLow
_V2_ANCHOR_MAX_SHARE = 0.6  # これより多くのdocに出る語は話題の識別に使わない


def tokenize_v2(text: str) -> set:
    """Latin語(3文字以上・stopword除外)とCJKの文字bigramの集合。CJKは連続文字列全体を1語にしない。"""
    if not text:
        return set()
    lowered = text.lower()
    tokens = {t for t in _LATIN_WORD_RE.findall(lowered) if len(t) >= 3 and t not in _STOPWORDS}
    for run in _CJK_RUN_RE.findall(lowered):
        tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def _doc_tokens_v2(members: list) -> list:
    # 本文(text_for_review)を優先し、空のときだけtitleを使う。SNSのtitleは`Twitter Post - <name>`のような
    # 全docに共通の定型語で、類似度を押し上げて異質なclusterを均質に見せてしまう（media等は本文=title）。
    docs = []
    for m in members:
        docs.append(tokenize_v2(m.get("text_for_review") or m.get("title") or ""))
    return docs


# ---------------------------------------------------------------------------
# Coherence評価（cap-hit Clusterのheterogeneity検知）
# ---------------------------------------------------------------------------

# 判定しきい値（決定的proxyのため恣意性は残る。実データで再チューニングする前提の
# 初期値。Prompt1側の最終semantic判定を代替するものではない——あくまでrecursive
# splitのcandidate生成トリガーとしてのみ使う）。
_HIGH_COHERENCE_DIVERSITY = 0.45  # 以下ならHigh（splitしない）
_LOW_COHERENCE_DIVERSITY = 0.65   # 以上ならLow（split candidate）


@dataclass
class CoherenceResult:
    status: str  # "High" | "Medium" | "Low"
    representative_diversity: float  # 0..1、高いほど異質
    backend: str  # "deterministic"（現状固定）
    avg_text_similarity: float
    avg_dominant_share: float
    sample_size: int


def evaluate_cluster_coherence(members: list, *, backend: str = "deterministic") -> CoherenceResult:
    """membersはrepresentative dict（'title'/'text_for_review'/'attitude_hint'/
    'source_bucket'/'matched_terms'キーを想定）のリスト。cap-hit Clusterに対してのみ
    呼ぶ想定（非cap-hit Clusterまで評価すると大規模Runで計算量が無駄に増える）。

    まずrepresentative（reservoir sample、既定8件程度）だけで安価に評価し、
    Low/Medium域であれば呼び出し元がall_members（shard cap分、最大200件程度）を
    渡して再評価する、という2段階運用を想定する（Azure Embeddingが無い現状では
    どちらの段階も同じdeterministic計算だが、将来embedding backendを追加した際に
    「まずrepresentativeだけ埋め込み、必要な場合のみ全件へ展開する」という
    段階的呼び出しパターンをそのまま踏襲できるようにしている）。"""
    if backend == BACKEND_LEXICAL_V2:
        return _evaluate_lexical_v2(members)
    if backend != BACKEND_DETERMINISTIC:
        raise NotImplementedError(
            f"coherence_backend='{backend}' は未実装です。提供しているのは{SUPPORTED_BACKENDS}のみです"
            "（Azure OpenAIにembeddings用Deploymentが無いためembedding backendは未実装、2026-09-16確認）。"
        )
    if len(members) < 2:
        return CoherenceResult(
            status="High", representative_diversity=0.0, backend=backend,
            avg_text_similarity=1.0, avg_dominant_share=1.0, sample_size=len(members))

    token_sets = [tokenize(f"{m.get('title', '')} {m.get('text_for_review', '')}") for m in members]
    pair_sims = []
    n = len(token_sets)
    # O(n^2)だがnはreservoir sample(既定8件)またはshard cap(既定200件)で有界なため許容する。
    for i in range(n):
        for j in range(i + 1, n):
            pair_sims.append(jaccard_similarity(token_sets[i], token_sets[j]))
    avg_text_similarity = sum(pair_sims) / len(pair_sims) if pair_sims else 1.0

    # 2026-09-16修正: matched_termsはFallback status（match_status!=matched）の
    # レコードでは常に空文字列になる（match_masterの仕様）。「全員が空」を
    # dominant_share=1.0（均質）と誤判定すると、まさに今回問題になったS3のような
    # 全件Fallback Clusterで異質性を過小評価してしまう（当該dimensionが空文字列
    # だらけの場合、それは"情報が無い"のであって"均質"ではない）。値が1件も
    # 非空でないdimensionは平均から除外する。
    dominant_components = []
    for field_name in ("attitude_hint", "source_bucket", "matched_terms"):
        values = [m.get(field_name, "") for m in members]
        if any(values):
            dominant_components.append(dominant_share(values))
    avg_dominant_share = sum(dominant_components) / len(dominant_components) if dominant_components else 0.5

    representative_diversity = round(1.0 - (0.5 * avg_text_similarity + 0.5 * avg_dominant_share), 4)
    if representative_diversity <= _HIGH_COHERENCE_DIVERSITY:
        status = "High"
    elif representative_diversity >= _LOW_COHERENCE_DIVERSITY:
        status = "Low"
    else:
        status = "Medium"

    return CoherenceResult(
        status=status, representative_diversity=representative_diversity, backend=backend,
        avg_text_similarity=round(avg_text_similarity, 4), avg_dominant_share=round(avg_dominant_share, 4),
        sample_size=len(members),
    )


def _evaluate_lexical_v2(members: list) -> "CoherenceResult":
    """各docを「cluster内で2doc以上に出る語」(共有語彙)の集合で表し、全体の語彙centroid(語→出現率)との
    cosine類似度の平均を求める。同じ話題の言い換えは共有語彙が多く高い値、別話題の混在は各docが
    自分の話題の語だけを持つためcentroidと半分程度しか向きが合わず低い値になる。
    転載（同一本文）は1.0、source/attitudeの違いは入力に使わないので影響しない。
    本文が空のdoc（metadata-only）は評価から除く。"""
    docs = [d for d in _doc_tokens_v2(members) if d]
    if len(docs) < 2:
        return CoherenceResult(status="High", representative_diversity=0.0, backend=BACKEND_LEXICAL_V2,
                               avg_text_similarity=1.0, avg_dominant_share=1.0, sample_size=len(members))
    n = len(docs)
    df = Counter(t for d in docs for t in d)
    shared = {t: c / n for t, c in df.items() if c >= 2}
    if not shared:
        cosine = 0.0
    else:
        norm = math.sqrt(sum(v * v for v in shared.values()))
        total = 0.0
        for d in docs:
            terms = [t for t in d if t in shared]
            if terms:
                total += sum(shared[t] for t in terms) / (math.sqrt(len(terms)) * norm)
        cosine = total / n
    diversity = round(1.0 - cosine, 4)
    status = "High" if diversity <= _V2_HIGH_DIVERSITY else ("Low" if diversity >= _V2_LOW_DIVERSITY else "Medium")
    return CoherenceResult(status=status, representative_diversity=diversity, backend=BACKEND_LEXICAL_V2,
                           avg_text_similarity=round(cosine, 4), avg_dominant_share=1.0, sample_size=len(members))


def _choose_split_lexical_v2(members: list) -> Optional[tuple]:
    """語彙のanchorで分割する。残りのdocのうち最も多くのdocに出る語（ただし大半のdocに出る語は除く）を
    anchorとして、その語を持つdocを1グループにする。これをMIN_SPLIT_GROUP_SIZE未満になるまで繰り返す。
    attitude/source_bucket等の次元は使わない（同じ意味でsourceが違うだけのclusterを分割しない）。"""
    docs = _doc_tokens_v2(members)
    remaining = list(range(len(members)))
    groups: dict = {}
    while len(remaining) >= MIN_SPLIT_GROUP_SIZE and len(groups) < _MAX_SPLIT_GROUPS - 1:
        df = Counter(t for i in remaining for t in docs[i])
        limit = _V2_ANCHOR_MAX_SHARE * len(remaining)
        candidates = [(c, t) for t, c in df.items() if MIN_SPLIT_GROUP_SIZE <= c <= limit]
        if not candidates:
            break
        best = max(candidates, key=lambda ct: (ct[0], [-ord(ch) for ch in ct[1]]))   # df最大、同数は辞書順で最小
        anchor = best[1]
        picked = [i for i in remaining if anchor in docs[i]]
        groups[anchor] = [members[i] for i in picked]
        picked_set = set(picked)
        remaining = [i for i in remaining if i not in picked_set]
    if not groups:
        return None
    if remaining:
        groups["(other)"] = [members[i] for i in remaining]
    if len(groups) < 2:
        return None
    return "lexical_anchor", groups


# ---------------------------------------------------------------------------
# Recursive Split: heterogeneous cap-hit Clusterの分割候補生成
# ---------------------------------------------------------------------------

# matched_terms（完全一致した含意語）→attitude_hint→source_bucketの順で、
# 「ある程度均等に分かれ、かつ値の種類が複数ある」discriminatorを優先的に採用する。
# いずれも既存のClusterStats.representatives/all_membersに既に載っている決定的な
# フィールドのみを使う（新たな解析処理は行わない）。
_SPLIT_DISCRIMINATOR_PRIORITY = ("matched_terms", "attitude_hint", "source_bucket")
_MAX_SPLIT_GROUPS = 4
# これ未満の断片グループは"other"へまとめ、無意味な過分割を避ける。呼び出し元
# （geo_report_current_pipeline_service._split_cluster_recursive）が「再帰継続に
# 値するグループサイズか」を判定する際にも同じ値を使うため公開定数にする。
MIN_SPLIT_GROUP_SIZE = 5


def choose_split_discriminator(members: list, backend: str = BACKEND_DETERMINISTIC) -> Optional[tuple]:
    """(discriminator_name, {value: [members...]}) を返す。適切なdiscriminatorが
    見つからない場合（全メンバーが同一値、または値が細分化しすぎる）はNoneを返し、
    呼び出し元はtoken signatureへフォールバックする。backend='lexical_v2'では語彙anchorで分割する。"""
    if backend == BACKEND_LEXICAL_V2:
        return _choose_split_lexical_v2(members)
    for field_name in _SPLIT_DISCRIMINATOR_PRIORITY:
        groups: dict = {}
        for m in members:
            value = (m.get(field_name) or "").strip() or "(empty)"
            groups.setdefault(value, []).append(m)
        if len(groups) < 2:
            continue
        # 断片的すぎるグループはother へ統合
        merged: dict = {}
        other: list = []
        for value, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if len(items) >= MIN_SPLIT_GROUP_SIZE and len(merged) < _MAX_SPLIT_GROUPS - 1:
                merged[value] = items
            else:
                other.extend(items)
        if other:
            merged["(other)"] = other
        if len(merged) >= 2:
            return field_name, merged

    # フォールバック: タイトルの頻出トークン2件を組み合わせたsignatureで分割する。
    # top_k=1（単一トークン）だと、2つの異なる話題が共通の1語（"today"等）を
    # たまたま最頻出として共有した場合にsignatureが衝突しやすい。2語の組み合わせに
    # することで偶発的な衝突を大幅に減らす。
    token_groups: dict = {}
    for m in members:
        sig = salient_tokens(f"{m.get('title', '')} {m.get('text_for_review', '')}", top_k=2)
        key = "+".join(sig) if sig else "(no_token)"
        token_groups.setdefault(key, []).append(m)
    merged = {}
    other = []
    for value, items in sorted(token_groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) >= MIN_SPLIT_GROUP_SIZE and len(merged) < _MAX_SPLIT_GROUPS - 1:
            merged[value] = items
        else:
            other.extend(items)
    if other:
        merged["(other)"] = other
    if len(merged) >= 2:
        return "salient_token", merged
    return None
