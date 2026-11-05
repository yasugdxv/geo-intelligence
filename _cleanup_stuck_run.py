import sys
import time

sys.path.insert(0, ".")
from geo_report_current_pipeline_api import _client  # noqa: E402

RUN_ID = "e573f4d0-c956-43fd-becd-0bd65ce327f3"


def chunked_delete(table: str, chunk_size: int):
    total_deleted = 0
    while True:
        rows = _client.select(table, {
            "select": "id", "run_id": f"eq.{RUN_ID}", "limit": str(chunk_size),
        })
        if not rows:
            break
        ids = ",".join(r["id"] for r in rows)
        _client.delete(table, {"id": f"in.({ids})"})
        total_deleted += len(rows)
        print(f"{table}: deleted {total_deleted}", flush=True)
    print(f"{table}: DONE, total {total_deleted}", flush=True)


t0 = time.time()
chunked_delete("geo_report_records", 300)
chunked_delete("geo_report_sentiment_cluster_representatives", 300)
chunked_delete("geo_report_sentiment_clusters", 300)
chunked_delete("geo_report_run_artifacts", 300)

print("deleting remaining run row...", flush=True)
_client.delete("geo_report_collection_runs", {"run_id": f"eq.{RUN_ID}"})
print(f"ALL DONE in {time.time() - t0:.1f}s", flush=True)
