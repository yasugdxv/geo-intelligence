# 会話モードを有効（Feature Flag ON）にして、ローカルのAPIサーバーと画面を起動する。
#
# 使い方（PowerShell）:
#   cd projects\geopolitical_monitor_dashboard
#   .\scripts\start_local_with_conversation_mode.ps1
# 画面: http://127.0.0.1:8080/geo/intelligence-ai （上部の4モード選択から使う）
#
# - APIサーバー: ポート8001（画面の開発用の接続先と同じ）。環境変数 GEO_CONVERSATION_MODE_ENABLED=true をこのプロセスにだけ設定する
#   （PC全体の環境変数は変えない）。止めるときは、開いた2つのウィンドウを閉じる。
# - 通常モードの自由質問のソース横断（GEO_IA_CROSS_SOURCE_ENABLED=true）もON。OFFにすると、従来の簡易チャット(/api/geo/chat)に戻る。
# - Web検索（GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED=true）もON（ユーザー依頼）。送るのはテーマの題名（と「Webで調べる」で入力した文）だけ。画面の同意チェックでOFFにできる。
# - Expert AIのWeb検索（GEO_EXPERT_EXTERNAL_RESEARCH_ENABLED=true）もON（2026-11-04ユーザー依頼）。内部データが足りないと判定された時だけ検索する。検索結果はタイトル+URLのみ保存（本文は取得しない設計）。
# - 会話はAI（Azure OpenAI）を呼ぶため、使った分の費用がかかる。
$root = Split-Path -Parent $PSScriptRoot
$api = "`$env:GEO_CONVERSATION_MODE_ENABLED='true'; `$env:GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED='true'; `$env:GEO_IA_CROSS_SOURCE_ENABLED='true'; `$env:GEO_EXPERT_EXTERNAL_RESEARCH_ENABLED='true'; `$env:API_PORT='8001'; `$env:PYTHONUTF8='1'; Set-Location '$root'; python api_server.py"
$web = "Set-Location '$root\eco-digest-spark'; npm run dev -- --port 8080 --host 127.0.0.1"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $api
Start-Process powershell -ArgumentList "-NoExit", "-Command", $web
Write-Host "起動しました。画面: http://127.0.0.1:8080/geo/intelligence-ai"
