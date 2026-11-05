"""PoC v2: 前回、モデルがcode interpreter内でキーワードマッチングの簡易ヒューリスティック
関数を書いて全クラスタへ機械的適用する「手抜き」が発覚した。今回は、コードによる
ショートカットを明示的に禁止し、各クラスタの本文を実際に読んで個別に判断するよう
強く指示し直して再検証する。
"""
import sys, io, csv, time, json
sys.path.insert(0, r'C:\Users\269811\python\projects\geopolitical_monitor_dashboard')
from config_utils import load_config
from geo_db_client import SupabaseClient
from ai_client import make_openai_client
import geo_report_prompt1_orchestration_service as orch
from geo_report_run_provisioning_service import load_prompt_instruction

config = load_config()
client = SupabaseClient(config)
azure_client, model = make_openai_client(config)

RUN_ID = "d558df7e-ad9d-4805-8836-a75771b11441"
N = 30  # まず小さめで品質検証(前回100件は手抜きだったため、コスト抑えて再検証)

rows = client.select("geo_report_analysis_clusters", {"select": "*", "run_id": f"eq.{RUN_ID}", "limit": str(N)})
clusters = []
for r in rows:
    cluster_row, reps = orch._load_analysis_cluster_and_representatives(client, RUN_ID, r["analysis_cluster_id"])
    clusters.append((cluster_row, reps))
print(f"loaded {len(clusters)} clusters", flush=True)

labeling_buf = io.StringIO()
w = csv.writer(labeling_buf)
w.writerow(["cluster_id", "signal_id", "report_topic_label", "narrative_id", "narrative_name",
            "cluster_records", "eligible_records", "preliminary_consistency"])
for cr, _reps in clusters:
    w.writerow([cr.get("cluster_id"), cr.get("signal_id"), cr.get("report_topic_label"),
                cr.get("narrative_id"), cr.get("narrative_name"), cr.get("cluster_records"),
                cr.get("eligible_records"), cr.get("preliminary_consistency")])
labeling_csv = labeling_buf.getvalue()

rep_buf = io.StringIO()
w2 = csv.writer(rep_buf)
w2.writerow(["cluster_id", "record_id", "title", "source_name", "source_bucket",
             "language_group", "match_status", "text_for_review"])
for cr, reps in clusters:
    for rep in reps:
        w2.writerow([cr.get("cluster_id"), rep.get("record_id"), rep.get("title"),
                     rep.get("source_name"), rep.get("source_bucket"), rep.get("language_group"),
                     rep.get("match_status"), rep.get("text_for_review")])
rep_csv = rep_buf.getvalue()

f1 = azure_client.files.create(file=("cluster_labeling_input.csv", io.BytesIO(labeling_csv.encode())), purpose="assistants")
f2 = azure_client.files.create(file=("cluster_representatives.csv", io.BytesIO(rep_csv.encode())), purpose="assistants")
print("uploaded:", f1.id, f2.id, flush=True)

prompt1_instruction = load_prompt_instruction("prompt1")["text"]

instructions = (
    "あなたはReport Studioの「Prompt 1 Application AI」です。以下は、この分類作業の"
    "正式な指示文（Current Source of Truth）です。この指示文のルール・enum定義・判定順序に厳密に従ってください。\n\n"
    + prompt1_instruction
)

task = (
    "cluster_labeling_input.csv と cluster_representatives.csv が添付されています。\n\n"
    "**重要な警告（絶対厳守）**: これは自然言語の意味理解・解釈タスクです。"
    "キーワードマッチングや文字列検索（'hate'/'great'等の単語リストで判定する等）で"
    "代用することは絶対に禁止します。そのようなヒューリスティックはPrompt1の要求する"
    "品質を満たしません。\n\n"
    "正しい手順:\n"
    "1. pandasでCSVを読み込む（データ読み込みのみコード使用可）\n"
    "2. 各cluster_idについて、text_for_review本文を実際に読み、あなた自身の自然言語理解に基づいて"
    "Prompt1 §3の各フィールドを個別に判断する（コードで機械的に決めない。あなたが自分の言葉で"
    "final_topic_label・voice_summary・notable_phrases・change_signal_summary・notes_for_prompt2を"
    "作文する）\n"
    "3. 全クラスタ分の判断が終わったら、pandasでDataFrameに変換してresult.csvへ書き出す\n\n"
    "対象は30クラスタのみです。全クラスタを独立に評価し、隣接クラスタの情報を混同しないこと。"
    "1つのcluster_idにつき1行、省略・重複・捏造IDなしで返してください。\n\n"
    "列: cluster_id,final_topic_label,source_assessment,tone_gradient,voice_direction,report_use_status,"
    "continuity_relevance,matched_previous_topic_id,change_signal_summary,noise_flag,noise_reason,"
    "voice_summary,notable_phrases,representative_quality,notes_for_prompt2"
)

t0 = time.monotonic()
resp = azure_client.responses.create(
    model=model,
    instructions=instructions,
    input=task,
    tools=[{"type": "code_interpreter", "container": {"type": "auto", "file_ids": [f1.id, f2.id]}}],
    background=True,
)
print("submitted job:", resp.id, "status:", resp.status, flush=True)

poll_count = 0
while True:
    poll_count += 1
    r2 = azure_client.responses.retrieve(resp.id)
    elapsed = time.monotonic() - t0
    print(f"  poll #{poll_count} @ {elapsed:.0f}s: status={r2.status}", flush=True)
    if r2.status in ("completed", "failed", "cancelled", "incomplete"):
        break
    time.sleep(8)

wall = time.monotonic() - t0
print(f"\n=== JOB FINISHED: status={r2.status}, wall_clock={wall:.1f}s ===", flush=True)

usage = getattr(r2, "usage", None)
if usage:
    print("usage:", usage.model_dump() if hasattr(usage, "model_dump") else usage, flush=True)

# コード内容を確認(ヒューリスティック関数を書いていないか)
for item in r2.output:
    d = item.model_dump() if hasattr(item, "model_dump") else {}
    if d.get("type") == "code_interpreter_call":
        code = d.get("code") or ""
        print(f"\n--- code block ({len(code)} chars) ---")
        print(code[:2000])

container_id = None
result_file_id = None
for item in r2.output:
    d = item.model_dump() if hasattr(item, "model_dump") else {}
    if d.get("type") == "code_interpreter_call":
        container_id = d.get("container_id")
    if d.get("type") == "message":
        for c in d.get("content", []):
            for ann in c.get("annotations", []) or []:
                if ann.get("filename", "").endswith(".csv"):
                    result_file_id = ann.get("file_id")
                    container_id = container_id or ann.get("container_id")

if result_file_id and container_id:
    content = azure_client.containers.files.content.retrieve(result_file_id, container_id=container_id)
    result_text = content.read().decode("utf-8", errors="replace")
    out_path = r"C:\Users\269811\AppData\Local\Temp\claude\c--Users-269811-python\43982ba1-3771-46b7-b030-410738a53df5\scratchpad\poc_result2.csv"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(result_text)
    print("\nsaved result to:", out_path)
    print("\n--- first 3000 chars of result ---")
    print(result_text[:3000])
else:
    print("NO output file found.")
