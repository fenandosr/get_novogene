"""
Bottle web server — dashboard UI + REST API.

Routes:
  GET  /                  → dashboard (latest run)
  GET  /run/<filename>    → dashboard (specific run)
  GET  /api/runs          → JSON list of runs
  GET  /api/run/latest    → JSON latest run
  GET  /api/run/<file>    → JSON specific run
  POST /api/scrape        → trigger a new scrape, return summary
"""

import json
import os
import traceback
from pathlib import Path

import bottle
from bottle import route, post, request, response, run, template, static_file
from dotenv import load_dotenv

load_dotenv()

bottle.TEMPLATE_PATH.insert(0, "./templates")


def _json(data, status=200):
    response.content_type = "application/json"
    response.status = status
    return json.dumps(data, ensure_ascii=False)


STAGE_KEYS   = ["PENDING_ARRIVAL","RECEIVED","SAMPLE_QC","LIB_PREP_OR_SEQUENCING","DATA_QC","FINAL_REPORT","DATA_RELEASE"]
STAGE_COLORS = ["var(--c-pending)","var(--c-received)","var(--c-qc)","var(--c-lib)","var(--c-dataqc)","var(--c-report)","var(--c-release)"]
PROJ_COLORS  = ["var(--c-received)","var(--c-lib)","var(--c-report)","var(--c-dataqc)"]

TRANSITIONS = [
    ("PENDING_ARRIVAL→RECEIVED",              "Envío MX → Recepción"),
    ("RECEIVED→SAMPLE_QC",                    "Recepción → Sample QC"),
    ("SAMPLE_QC→LIB_PREP_OR_SEQUENCING",      "Sample QC → Secuenciación"),
    ("LIB_PREP_OR_SEQUENCING→DATA_QC",        "Secuenciación → Data QC"),
    ("DATA_QC→FINAL_REPORT",                  "Data QC → Reporte final"),
    ("FINAL_REPORT→DATA_RELEASE",             "Reporte final → Data Release"),
]
TRANS_COLORS = ["var(--c-received)","var(--c-qc)","var(--c-lib)","var(--c-dataqc)","var(--c-report)","var(--c-release)"]


def _boxplot_stats(values: list[float]) -> dict | None:
    if len(values) < 3:
        return None
    s = sorted(values)
    n = len(s)

    def percentile(p):
        idx = (n - 1) * p / 100
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    q1, med, q3 = percentile(25), percentile(50), percentile(75)
    iqr = q3 - q1
    w_lo = min(v for v in s if v >= q1 - 1.5 * iqr)
    w_hi = max(v for v in s if v <= q3 + 1.5 * iqr)
    return {
        "min": s[0], "max": s[-1],
        "q1": round(q1, 1), "median": round(med, 1), "q3": round(q3, 1),
        "w_lo": round(w_lo, 1), "w_hi": round(w_hi, 1),
        "outliers": [v for v in s if v < w_lo or v > w_hi],
        "points": s,
        "n": n,
        "mean": round(sum(s) / n, 1),
    }


def _load_aliases() -> dict:
    path = Path(__file__).parent / "projects.json"
    if path.exists():
        import json as _json
        return _json.loads(path.read_text())
    return {}


def _prepare_ctx(run_data):
    """Pre-compute all derived data so the template stays logic-free."""
    if not run_data:
        return {}

    from collections import defaultdict
    from datetime import datetime
    from zoneinfo import ZoneInfo

    aliases = _load_aliases()

    all_samples = [s for p in run_data["projects"] for s in p["samples"]]
    tats = [s["tat"] for s in all_samples if s["tat"] is not None]

    # Bottleneck analysis
    dur_buckets = defaultdict(list)
    for s in all_samples:
        for key, days in s.get("stage_durations", {}).items():
            if days is not None and days >= 0:
                dur_buckets[key].append(days)

    bk_data = []
    for (key, label), color in zip(TRANSITIONS, TRANS_COLORS):
        vals = dur_buckets.get(key, [])
        if vals:
            bk_data.append({
                "key": key, "label": label, "color": color,
                "avg": round(sum(vals) / len(vals), 1),
                "max": max(vals), "n": len(vals),
            })
    bk_data.sort(key=lambda b: -b["avg"])
    max_avg = max((b["avg"] for b in bk_data), default=1)
    top_key = bk_data[0]["key"] if bk_data else None
    for b in bk_data:
        b["pct"] = round(b["avg"] / max_avg * 100)
        b["is_top"] = b["key"] == top_key

    def _in_process_view(s):
        return (
            s.get("needs_data_release")
            and s.get("current_status") != "DESTROYED"
            and not (s.get("current_status") != "FINAL_REPORT" and s["timeline"].get("FINAL_REPORT"))
        )

    # Pending data release — exclude DESTROYED (Sample Disposed) and those with Final Report
    pending_dr = [s for s in all_samples if _in_process_view(s)]
    now = datetime.now(ZoneInfo("America/Mexico_City")).replace(tzinfo=None)
    expected_tat = int(os.getenv("NOVOGENE_EXPECTED_TAT_DAYS", "30"))
    for s in pending_dr:
        received = s["timeline"].get("RECEIVED") or s.get("qc_arrived_date", "")
        days_w = None
        if received:
            try:
                d = datetime.strptime(received[:19], "%Y-%m-%d %H:%M:%S")
                days_w = (now - d).days
            except ValueError:
                pass
        s["days_waiting"] = days_w
        s["days_waiting_cls"] = (
            "dw-late" if days_w and days_w > expected_tat else
            "dw-warn" if days_w and days_w > 20 else "dw-ok"
        )
    pending_dr.sort(key=lambda s: s.get("stage_index") or 0)

    # Project color map
    proj_nos = [p["sub_project_no"] for p in run_data["projects"]]
    proj_color = {pno: PROJ_COLORS[i % len(PROJ_COLORS)] for i, pno in enumerate(proj_nos)}
    proj_alias = {pno: aliases.get(pno, pno) for pno in proj_nos}

    # Enrich samples for template
    color_map = dict(zip(STAGE_KEYS, STAGE_COLORS))
    for p in run_data["projects"]:
        for s in p["samples"]:
            st = s["current_status"]
            s["has_final_report"] = bool(s["timeline"].get("FINAL_REPORT"))
            s["in_process_view"] = _in_process_view(s)
            s["badge_color"] = color_map.get(st, "var(--pico-muted-color)")
            s["proj_color"]  = proj_color.get(s["sub_project_no"], "var(--pico-muted-color)")
            s["proj_alias"]  = proj_alias.get(s["sub_project_no"], s["sub_project_no"])
            tat = s["tat"]
            if tat is None:
                s["tat_cls"], s["tat_lbl"] = "pend", "en curso"
            elif tat <= expected_tat:
                s["tat_cls"], s["tat_lbl"] = "ok", f"{tat}d"
            elif tat <= expected_tat + 10:
                s["tat_cls"], s["tat_lbl"] = "warn", f"{tat}d"
            else:
                s["tat_cls"], s["tat_lbl"] = "late", f"{tat}d"
            needs_dr = s.get("needs_data_release", False)
            if s["is_complete"]:
                s["row_class"] = "row-complete"
            elif s["is_delayed"]:
                s["row_class"] = "row-delayed"
            elif needs_dr and (s["stage_index"] or 0) >= 0:
                s["row_class"] = "row-norel"
            else:
                s["row_class"] = ""
            # Per-stage dot info
            s["dots"] = [
                {
                    "key": sk, "color": sc,
                    "has": s["timeline"].get(sk) is not None,
                    "is_cur": s["current_status"] == sk,
                    "date": s["timeline"].get(sk) or "pendiente",
                }
                for sk, sc in zip(STAGE_KEYS, STAGE_COLORS)
            ]
            # Timeline rows
            s["tl_rows"] = []
            for idx, sk in enumerate(STAGE_KEYS):
                dt = s["timeline"].get(sk)
                dur_key = f"{STAGE_KEYS[idx-1]}→{sk}" if idx > 0 else None
                dur_val = s.get("stage_durations", {}).get(dur_key) if dur_key else None
                s["tl_rows"].append({
                    "stage": sk.replace("_", " ").title(),
                    "color": color_map.get(sk),
                    "date": dt[:10] if dt else "—",
                    "dur": dur_val,
                })
        p["sorted_samples"] = sorted(
            p["samples"],
            key=lambda x: (x["is_complete"], -(x.get("stage_index") or 0))
        )
        p["complete_count"] = sum(1 for s in p["samples"] if s["is_complete"])
        p["alias"] = aliases.get(p["sub_project_no"], p["sub_project_no"])

    return {
        "total": len(all_samples),
        "complete": sum(1 for s in all_samples if s["is_complete"]),
        "inprog": len(all_samples) - sum(1 for s in all_samples if s["is_complete"]),
        "need_dr": len(pending_dr),
        "delayed": sum(1 for s in all_samples if s["is_delayed"]),
        "avg_tat": round(sum(tats) / len(tats)) if tats else None,
        "min_tat": min(tats) if tats else None,
        "max_tat": max(tats) if tats else None,
        "bk_data": bk_data,
        "pending_dr": pending_dr,
        "proj_color": proj_color,
        "proj_alias": proj_alias,
        "stage_keys": STAGE_KEYS,
        "stage_colors": STAGE_COLORS,
        "color_map": color_map,
        "expected_tat": expected_tat,
        "boxplot": [
            {
                "label": p["sub_project_no"],
                "short": aliases.get(p["sub_project_no"], p["sub_project_no"]),
                "color": proj_color.get(p["sub_project_no"], "var(--c-received)"),
                "stats": _boxplot_stats([
                    s["tat"]
                    for s in p["samples"]
                    if s.get("tat") is not None
                ]),
            }
            for p in run_data["projects"]
        ],
    }


def _render_dashboard(run_data, current_file=None):
    from storage import run_summaries
    summaries = run_summaries()
    ctx = _prepare_ctx(run_data)
    return template(
        "dashboard",
        run=run_data,
        ctx=ctx,
        summaries=summaries,
        current_file=current_file,
    )


@route("/")
def index():
    from storage import load_latest_run
    run_data = load_latest_run()
    latest_file = None
    if run_data:
        from storage import list_runs
        runs = list_runs()
        latest_file = runs[0].name if runs else None
    return _render_dashboard(run_data, current_file=latest_file)


@route("/run/<filename>")
def show_run(filename):
    from storage import load_run
    try:
        run_data = load_run(filename)
    except FileNotFoundError:
        bottle.abort(404, "Run not found")
    return _render_dashboard(run_data, current_file=filename)


@route("/api/runs")
def api_runs():
    from storage import run_summaries
    return _json(run_summaries())


@route("/api/run/latest")
def api_latest():
    from storage import load_latest_run
    data = load_latest_run()
    if not data:
        return _json({"error": "no runs yet"}, 404)
    return _json(data)


@route("/api/run/<filename>")
def api_run(filename):
    from storage import load_run
    try:
        return _json(load_run(filename))
    except FileNotFoundError:
        return _json({"error": "not found"}, 404)


@post("/api/scrape")
def api_scrape():
    try:
        from scraper import run_scrape
        from storage import save_run
        run_data = run_scrape()
        path = save_run(run_data)
        total = sum(p["sample_count"] for p in run_data["projects"])
        return _json({
            "ok": True,
            "filename": path.name,
            "sample_count": total,
            "timestamp": run_data["timestamp"],
        })
    except Exception as e:
        traceback.print_exc()
        return _json({"ok": False, "error": str(e)}, 500)


def main():
    port = int(os.getenv("SERVER_PORT", 8080))
    print(f"Novogene dashboard → http://localhost:{port}")
    run(host="0.0.0.0", port=port, debug=True, reloader=True)


if __name__ == "__main__":
    main()
