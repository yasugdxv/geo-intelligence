"""コンテナ起動時に環境変数からconfig.jsonを組み立ててAPIサーバー(api_server.py)を起動する。

このコンテナが提供するのはAPIバックエンドのみ（FastAPI/uvicorn）。
フロントエンド(eco-digest-spark、TanStack Start)とPMOレビュー用のStreamlitアプリ
(sustainability_expert_dashboard.py)は別プロセス/別デプロイとして扱う
（DEPLOY_GUIDE.md参照。フロントエンドの本番ビルド配信は未検証のため、
現状はこのコンテナと同一ホストで`npm run dev`を動かす運用を想定）。
"""
import json
import os

from config_utils import CONFIG_PATH, build_config_from_env


def _sync_config_file(env_config: dict, config_path) -> str:
    """環境変数から構築した設定で、config.json を書き込む。戻り値はログ出力用メッセージ。

    Azure App Service（Linux）では /home が再起動・デプロイをまたいで残るため、「ファイルが無い時だけ生成」だと、
    後から環境変数（キー・接続先）を変えても、古い config.json が使われ続けてしまう（サステナアプリで実際に発生した不具合）。
    そのため、環境変数が設定されている実行環境では、起動のたびに常に上書きして最新の値を反映する。
    環境変数が一切無い（ローカル開発等）場合だけ、手動で用意した既存の config.json をそのまま使う。"""
    if env_config or not config_path.exists():
        config_path.write_text(json.dumps(env_config, ensure_ascii=False, indent=2), encoding="utf-8")
        return "config.json を環境変数から生成しました（既存ファイルがあれば上書き）。" if env_config else "config.json を環境変数から生成しました（空の設定です）。"
    return "環境変数からは生成できないため、既存の config.json を使用します。"


def main():
    print(_sync_config_file(build_config_from_env(), CONFIG_PATH))

    os.environ.setdefault("API_HOST", "0.0.0.0")
    os.environ.setdefault("API_PORT", os.environ.get("DASHBOARD_PORT", "8000"))

    import uvicorn
    # Post-Freeze Integrity Audit Finding CRITICAL-5(2026-09-22発見)対応: Expert AI
    # Capability Flag（GEO_EXPERT_FOUNDATION_CONTEXT_V1_ENABLED等）の既定値は
    # api_server.pyのモジュールimport時に設定される（詳細はそちら参照）——この
    # `from api_server import app`が、コンテナ内でExpert AIが実際に起動する唯一の経路。
    from api_server import app

    uvicorn.run(app, host=os.environ["API_HOST"], port=int(os.environ["API_PORT"]))


if __name__ == "__main__":
    main()
