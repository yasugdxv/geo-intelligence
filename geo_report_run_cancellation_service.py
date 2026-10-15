"""Report Studio Pipeline: 実行中の処理を中断する（協調的キャンセル、2026-09-11）。

Prompt1/Prompt2のバッチ実行はバックグラウンドジョブ基盤を持たず、1回の同期実行
（`run_in_threadpool`で別スレッドへ逃がしているだけ）として動くため、実行中の
スレッドを強制終了することはできない。代わりに`geo_report_collection_runs.
cancel_requested`フラグを立て、バッチ実行のチェックポイント（新しいバッチを
開始する直前）でこのフラグを確認し、trueなら新しい作業を始めずに早期終了する
（すでに実行中のバッチ自体は完了まで進む）。

新しい実行を開始する際は必ず`clear_cancel_flag`でリセットすること
（前回キャンセルされたRunを再実行した際に即座にまたキャンセルされてしまうのを防ぐ）。
"""

from __future__ import annotations


class PipelineCancelled(Exception):
    """cancel_requestedがtrueだったため、Pipeline実行を中断したことを示す。"""


def is_cancel_requested(client, run_id: str) -> bool:
    rows = client.select("geo_report_collection_runs", {"select": "cancel_requested", "run_id": f"eq.{run_id}"})
    return bool(rows and rows[0].get("cancel_requested"))


def request_cancel(client, run_id: str) -> None:
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, {"cancel_requested": True})


def clear_cancel_flag(client, run_id: str) -> None:
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"}, {"cancel_requested": False})


def raise_if_cancelled(client, run_id: str) -> None:
    if is_cancel_requested(client, run_id):
        raise PipelineCancelled(f"run_id={run_id} はユーザーによりキャンセルされました。")
