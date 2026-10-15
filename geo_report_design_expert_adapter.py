"""Report Studio「Design」領域: Expert AI Adapter（Phase C）。

Design Chatは既存Expert AI（`geo_expert_engine.py`）の固定Stage実行・固定Schema・
固定永続化テーブル（`run_expert_request_with_mode`）をそのまま呼ばない——Report
Studio Projectとは無関係なスキーマに固定されるため（kind-napping-hare.md
「重要な事実確認3」参照）。代わりに、Expert AIの部品（Knowledge Block構築・
Actor/Media Intelligence Retrieval・`call_llm_structured`）だけを`ReportDesignExpertAdapter`
という薄いInterface越しに再利用する。

`GeoExpertDesignAdapter`が現状唯一の実装（Expert AI Registryは作らない、
`expert_adapter_type='geo'`を記録するのみ）。将来
`SustainabilityExpertDesignAdapter`/`PAExpertDesignAdapter`等を同じProtocolで
追加できる。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import geo_expert_common
import geo_report_synthesis_service as synthesis_service

_PROMPT0_PATH = (
    Path(__file__).parent / "resources" / "report_studio" / "prompt0" / "v1" / "instruction.md"
)


@runtime_checkable
class ReportDesignExpertAdapter(Protocol):
    def get_expert_type(self) -> str: ...
    def get_knowledge_version(self) -> str: ...
    def build_knowledge_context(self) -> str: ...
    def retrieve_context(self, client, mentioned_entity_names: list) -> str: ...
    def build_design_guidance(self) -> str: ...


def load_report_design_framework() -> str:
    """Current Prompt0（`prompt0_report_design_query_continuity.md`）の本文を
    Report Design Frameworkとしてそのまま読む（Design ChatはこのFrameworkの考え方
    ——中心仮説・起点イベント・Signal Design・Fixed/Event Query・Narrative Master・
    Report Template・Continuity・Watch Conditions・Assumptions/Open Questions——を
    Existing Expert AIの思考パターンと組み合わせて使う。kind-napping-hare.md
    「Current Prompt0もDesign FrameworkのSource of Truthとして使う」章参照）。"""
    if not _PROMPT0_PATH.is_file():
        return ""
    return _PROMPT0_PATH.read_text(encoding="utf-8")


class GeoExpertDesignAdapter:
    """geo（地政学）Expert AI向けAdapter。既存`geo_expert_common`/
    `geo_report_synthesis_service`の部品のみを再利用し、`geo_expert_engine`の
    固定Stage実行・固定Schema・固定永続化テーブルには一切依存しない。"""

    def get_expert_type(self) -> str:
        return "geo"

    def get_knowledge_version(self) -> str:
        return geo_expert_common.load_geo_expert_knowledge()["version"]

    def build_knowledge_context(self) -> str:
        return geo_expert_common.build_knowledge_block()

    def retrieve_context(self, client, mentioned_entity_names: list) -> str:
        """会話中に言及された固有名詞（アクター名等）からActor/Media Intelligenceを
        取得する。`build_expert_context_block`はCase/Issueに依存しない名前ベースの
        橋渡し関数（geo_report_synthesis_service.py、Report Studio既存Synthesisと
        同じもの）。言及が無ければMedia Intelligenceのみ、それも無ければ空文字列。"""
        text, _known_refs = synthesis_service.build_expert_context_block(
            client, mentioned_entity_names or [])
        return text

    def build_design_guidance(self) -> str:
        return load_report_design_framework()
