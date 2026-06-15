"""
Novogene CSS America scraper.

Auth: SSO via portal-global.novogene.com → OAuth2 token from ocssamerica.
Data: REST endpoint /service/v1/0/nsrv-sample-infos/selectSampleInfoBySubjectCode
"""

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CDMX = ZoneInfo("America/Mexico_City")

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://ocssamerica.novogene.com"
PORTAL_BASE = "https://portal-global.novogene.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0"
STAGE_ORDER = [
    "PENDING_ARRIVAL",
    "RECEIVED",
    "SAMPLE_QC",
    "LIB_PREP_OR_SEQUENCING",
    "DATA_QC",
    "FINAL_REPORT",
    "DATA_RELEASE",
]
STAGE_LABELS = {
    "PENDING_ARRIVAL": "Pending Arrival",
    "RECEIVED": "Received",
    "SAMPLE_QC": "Sample QC",
    "LIB_PREP_OR_SEQUENCING": "Lib Prep / Sequencing",
    "DATA_QC": "Data QC",
    "FINAL_REPORT": "Final Report",
    "DATA_RELEASE": "Data Release",
}


def _get_token_via_portal(username: str, password: str) -> str:
    """Login via portal-global SSO and return bearer token for ocssamerica.

    Flow:
    1. POST portal/login → session + HSKP_TOKEN
    2. GET portal/oauth2/authorize → redirect with auth code
    3. Follow: ocssamerica/oauth/sso/paas → ocssamerica/oauth/oauth/authorize
    4. Extract access_token from final redirect fragment
    """
    session = requests.Session()
    session.headers.update({"User-Agent": _UA})

    # Step 1: portal login (plain password; portal does NOT use RSA here)
    session.get(f"{PORTAL_BASE}/login", headers={"Accept": "text/html,*/*"})
    r = session.post(
        f"{PORTAL_BASE}/login",
        data={"username": f"GLOBAL_{username}", "password": password},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": PORTAL_BASE,
            "Referer": f"{PORTAL_BASE}/login",
        },
        allow_redirects=True,
    )
    r.raise_for_status()

    # Step 2: get authorization code from portal (authenticated session auto-approves)
    r = session.get(
        f"{PORTAL_BASE}/oauth2/authorize",
        params={
            "response_type": "code",
            "client_id": "Qba3WPly",
            "redirect_uri": f"{API_BASE}/oauth/sso/paas",
            "scope": "openid",
            "state": "https://cssamerica.novogene.com",
        },
        headers={"Accept": "text/html,*/*"},
        allow_redirects=False,
    )
    if r.status_code != 302:
        raise RuntimeError(f"Portal oauth2/authorize failed: {r.status_code} {r.text[:200]}")

    # Steps 3-4: follow redirect chain until access_token appears in URL fragment
    url = r.headers["Location"]
    for _ in range(6):
        resp = session.get(url, headers={"Accept": "text/html,*/*"}, allow_redirects=False)
        loc = resp.headers.get("Location", "")
        m = re.search(r"access_token=([^&#\s]+)", url + loc)
        if m:
            return m.group(1)
        if resp.status_code != 302 or not loc:
            raise RuntimeError(
                f"Token not found in redirect chain. Last URL: {url} | "
                f"Status: {resp.status_code} | Body: {resp.text[:200]}"
            )
        url = loc

    raise RuntimeError("Token not found after 6 redirect steps")


def get_token() -> str:
    """Return a valid bearer token — login if credentials available, else fall back to static token."""
    username = os.getenv("NOVOGENE_USERNAME", "").strip()
    password = os.getenv("NOVOGENE_PASSWORD", "").strip()
    static_token = os.getenv("NOVOGENE_TOKEN", "").strip()

    if username and password:
        print(f"  Logging in as {username}...")
        try:
            return _get_token_via_portal(username, password)
        except Exception as e:
            if static_token:
                print(f"  Login failed ({e}), using NOVOGENE_TOKEN as fallback.")
                return static_token
            raise

    if static_token:
        return static_token

    raise RuntimeError(
        "No credentials available. Set NOVOGENE_USERNAME+NOVOGENE_PASSWORD (or NOVOGENE_TOKEN) in .env"
    )


def _parse_dt(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            pass
    return None


def _build_timeline(processes: list[dict]) -> dict:
    """
    Return a dict mapping each stage to its earliest processDate.
    Uses processesDetails (ordered) when available, falls back to processes.
    """
    timeline: dict[str, datetime | None] = {s: None for s in STAGE_ORDER}
    for p in processes:
        status = p.get("sampleStatus")
        if status in timeline:
            dt = _parse_dt(p.get("processDate"))
            if dt and (timeline[status] is None or dt < timeline[status]):
                timeline[status] = dt
    return timeline


def _tat_days(start: datetime | None, end: datetime | None) -> int | None:
    if start and end and end >= start:
        return (end - start).days
    return None


def _stage_durations(timeline: dict) -> dict:
    """Days elapsed between each consecutive stage pair, for samples that have both dates."""
    durations = {}
    for i in range(len(STAGE_ORDER) - 1):
        a, b = STAGE_ORDER[i], STAGE_ORDER[i + 1]
        durations[f"{a}→{b}"] = _tat_days(timeline.get(a), timeline.get(b))
    return durations


def fetch_project(sub_project_no: str, token: str) -> dict:
    """Fetch and normalize all sample data for a sub-project."""
    r = requests.get(
        f"{API_BASE}/service/v1/0/nsrv-sample-infos/selectSampleInfoBySubjectCode",
        params={"subProjectNo": sub_project_no},
        headers={
            "Accept": "application/json",
            "authorization": f"bearer {token}",
            "h-menu-id": "-1",
            "Origin": "https://cssamerica.novogene.com",
            "Referer": "https://cssamerica.novogene.com/",
        },
    )
    r.raise_for_status()
    raw_samples = r.json()

    expected_tat = int(os.getenv("NOVOGENE_EXPECTED_TAT_DAYS", "30"))
    samples = []
    prj_name = ""

    for s in raw_samples:
        prj_name = prj_name or s.get("prjName", "")
        processes = s.get("processesDetails") or s.get("processes") or []
        timeline = _build_timeline(processes)

        received = timeline.get("RECEIVED")
        data_release = timeline.get("DATA_RELEASE")
        pending = timeline.get("PENDING_ARRIVAL")

        tat_received = _tat_days(received, data_release)
        tat_pending = _tat_days(pending, data_release)

        is_complete = data_release is not None
        is_delayed = (
            not is_complete
            and pending is not None
            and (_tat_days(pending, datetime.now()) or 0) > expected_tat
        )

        current_status = s.get("sampleStatus", "")
        stage_index = STAGE_ORDER.index(current_status) if current_status in STAGE_ORDER else -1

        tl_str = {k: v.strftime("%Y-%m-%d %H:%M:%S") if v else None for k, v in timeline.items()}
        has_data_release = data_release is not None

        samples.append({
            "sub_project_no": sub_project_no,
            "sample_name": s.get("sampleName", ""),
            "novo_id": s.get("novoId", ""),
            "product_name": s.get("productName", ""),
            "current_status": current_status,
            "current_status_meaning": s.get("sampleStatusMeaning", current_status),
            "stage_index": stage_index,
            "qc_arrived_date": s.get("qcArrivedDate", ""),
            "timeline": tl_str,
            "stage_durations": _stage_durations(timeline),
            "tat_received_to_release": tat_received,
            "tat_pending_to_release": tat_pending,
            "tat": tat_pending,
            "is_complete": is_complete,
            "is_delayed": is_delayed,
            "needs_data_release": not has_data_release,
        })

    return {
        "sub_project_no": sub_project_no,
        "prj_name": prj_name,
        "sample_count": len(samples),
        "samples": samples,
    }


def run_scrape() -> dict:
    """Run a full scrape of all configured projects. Returns a run dict."""
    projects_env = os.getenv("NOVOGENE_PROJECTS", "")
    project_nos = [p.strip() for p in projects_env.split(",") if p.strip()]
    if not project_nos:
        raise RuntimeError("NOVOGENE_PROJECTS is empty. Add at least one sub-project number.")

    print(f"Scraping {len(project_nos)} project(s)...")
    token = get_token()

    projects = []
    for pno in project_nos:
        print(f"  Fetching {pno}...")
        project = fetch_project(pno, token)
        projects.append(project)
        print(f"    {project['sample_count']} samples")

    ts = datetime.now(CDMX)
    return {
        "timestamp": ts.isoformat(),
        "timestamp_display": ts.strftime("%Y-%m-%d %H:%M CDMX"),
        "project_count": len(projects),
        "projects": projects,
    }
