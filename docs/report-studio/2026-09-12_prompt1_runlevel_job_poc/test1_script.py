"""Phase 3 PoC: Run-level Job Engine (Responses API + code_interpreter) for Prompt1
Analysis Cluster classification, benchmarked against the Current Cluster Batch Engine
on the SAME 100 real clusters (run_id=d558df7e, matching the earlier Current-Engine
benchmark: batch=20,concurrency=5,n=100 -> wall=110.9s, 5 calls, avg_input=15123,
avg_output=4614/call = 23,070 total output tokens for the whole 100-cluster job).
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
N = 100

rows = client.select("geo_report_analysis_clusters", {"select": "*", "run_id": f"eq.{RUN_ID}", "limit": str(N)})
clusters = []
for r in rows:
    cluster_row, reps = orch._load_analysis_cluster_and_representatives(client, RUN_ID, r["analysis_cluster_id"])
    clusters.append((cluster_row, reps))
print(f"loaded {len(clusters)} clusters (same set family as Current Engine benchmark)", flush=True)

# --- Build the two real Current Prompt1 input files ---
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

print(f"cluster_labeling_input.csv: {len(labeling_csv)} chars, cluster_representatives.csv: {len(rep_csv)} chars", flush=True)

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
    "cluster_labeling_input.csv（各行=1 Analysis Cluster、cluster_id列で識別）と "
    "cluster_representatives.csv（cluster_idごとの代表投稿、複数行）が添付されています。"
    "Code interpreterでpandasを使ってこの2ファイルを読み込み、cluster_idごとにcluster_representatives.csvの"
    "text_for_review本文を確認したうえで、上記のPrompt1 §3 Analysis Cluster Classification instructions"
    "に従って各clusterを独立に分類してください。\n\n"
    "重要な制約:\n"
    "- Evaluate every cluster independently. Never transfer evidence, sentiment, source class, "
    "target, stance, or interpretation from one cluster to another.\n"
    "- Return exactly one result row for every cluster_id in cluster_labeling_input.csv (no more, no less).\n"
    "- Do not merge clusters. Do not omit clusters. Do not invent cluster_ids.\n\n"
    "最終的にresult.csvという名前のCSVファイルをcode interpreterで書き出してください。列は次の順:\n"
    "cluster_id,final_topic_label,source_assessment,tone_gradient,voice_direction,report_use_status,"
    "continuity_relevance,matched_previous_topic_id,change_signal_summary,noise_flag,noise_reason,"
    "voice_summary,notable_phrases,representative_quality,notes_for_prompt2\n"
    "（各列の定義・enum値はPrompt1指示文の§3を厳密に参照すること）"
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
    time.sleep(10)

wall = time.monotonic() - t0
print(f"\n=== JOB FINISHED: status={r2.status}, wall_clock={wall:.1f}s ===", flush=True)

usage = getattr(r2, "usage", None)
if usage:
    print("usage:", usage.model_dump() if hasattr(usage, "model_dump") else usage, flush=True)

print("\noutput_text (first 2000 chars):")
print((r2.output_text or "")[:2000], flush=True)

# 出力ファイルを探して取得
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
                    print("found output file annotation:", ann.get("filename"), result_file_id, flush=True)

result_text = None
if result_file_id and container_id:
    content = azure_client.containers.files.content.retrieve(result_file_id, container_id=container_id)
    result_text = content.read().decode("utf-8", errors="replace")
    print(f"\nresult.csv size: {len(result_text)} chars", flush=True)
    out_path = r"C:\Users\269811\AppData\Local\Temp\claude\c--Users-269811-python\43982ba1-3771-46b7-b030-410738a53df5\scratchpad\poc_result.csv"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(result_text)
    print("saved to:", out_path, flush=True)
else:
    print("NO output file found via annotations.", flush=True)

# 保存
with open(r"C:\Users\269811\AppData\Local\Temp\claude\c--Users-269811-python\43982ba1-3771-46b7-b030-410738a53df5\scratchpad\poc_response_dump.json", "w", encoding="utf-8") as fh:
    json.dump(r2.model_dump(), fh, ensure_ascii=False, indent=2, default=str)
print("\nfull response dumped for inspection.", flush=True)
