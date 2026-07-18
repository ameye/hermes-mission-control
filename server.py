#!/usr/bin/env python3
"""
Hermes Mission Control — read-only dashboard server for Hermes Agent fleets.

Run:   python server.py
Then:  open http://localhost:2800

To tunnel from your local machine: ssh -L 2800:localhost:2800 user@your-vps
"""

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Hermes Mission Control")

# ── Config ──────────────────────────────────────────────────────────────
# Change this to your Hermes home directory
HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
PROFILES_DIR = HERMES_HOME / "profiles"
REFRESH_INTERVAL = 10  # seconds for SSE heartbeat

# ── Helpers ─────────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    """Read a JSON file, return {} if missing or broken."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _read_text(path: Path, max_len: int = 2000) -> str:
    """Read a text file, return '' if missing."""
    if not path.exists():
        return ""
    try:
        text = path.read_text()
        return text[:max_len]
    except OSError:
        return ""


def _query_db(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Read-only SQLite query. Returns list of dicts."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


def _ts_to_iso(ts: Optional[float]) -> str:
    """Convert unix timestamp to ISO string, or '—'."""
    if ts is None:
        return "—"
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OSError, ValueError):
        return "—"


def _ts_to_rel(ts: Optional[float]) -> str:
    """Convert unix timestamp to relative time like '2h ago'."""
    if ts is None:
        return "—"
    now = time.time()
    diff = now - ts
    if diff < 0:
        return "just now"
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"
    days = int(diff // 86400)
    return f"{days}d ago" if days < 30 else f"{days // 30}mo ago"


# ── API Endpoints ───────────────────────────────────────────────────────


@app.get("/api/diagnose")
async def api_diagnose():
    """Diagnose why data isn't loading — helpful when volume mount is wrong."""
    issues = []
    warnings = []

    # Check HERMES_HOME
    hermes_home = str(HERMES_HOME)
    home_exists = HERMES_HOME.exists()

    if not home_exists:
        issues.append(f"Directory {hermes_home} does not exist inside the container.")
        issues.append("This means the volume mount is not working or pointing to the wrong path.")
    else:
        # Check what's in the directory
        items = list(HERMES_HOME.iterdir())
        if not items:
            issues.append(f"Directory {hermes_home} is empty.")
            issues.append("The volume mount is pointing to an empty directory on the host.")
        else:
            warnings.append(f"Found {len(items)} items in {hermes_home}, but some data may be missing.")

        # Check for state.db
        state_db = HERMES_HOME / "state.db"
        if not state_db.exists():
            issues.append(f"Missing state.db — no session data available.")
        
        # Check for profiles
        if not PROFILES_DIR.exists():
            issues.append(f"Missing profiles directory — no multi-agent data.")
        
        # Check for gateway state
        gw = HERMES_HOME / "gateway_state.json"
        if not gw.exists():
            warnings.append(f"Missing gateway_state.json — gateway status unknown.")

    return {
        "hermes_home": hermes_home,
        "directory_exists": home_exists,
        "is_empty": home_exists and len(list(HERMES_HOME.iterdir())) == 0 if home_exists else True,
        "issues": issues,
        "warnings": warnings,
        "fix_hint": "In Coolify, go to your service → Persistent Storage → add a volume: "
                    "Host path = /home/hermeswebui/.hermes  |  Container path = /data/hermes  |  Read-only = ON"
                    if not home_exists or (home_exists and len(list(HERMES_HOME.iterdir())) == 0) else None,
    }
@app.get("/health")
async def health():
    """Fast health check — no DB queries, just confirms server is alive."""
    return {
        "status": "ok",
        "hermes_home": str(HERMES_HOME),
        "profiles_dir_exists": PROFILES_DIR.exists(),
    }


@app.get("/")
async def root():
    """Serve the dashboard HTML."""
    static_dir = Path(__file__).parent / "static"
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"error": "Dashboard not built. Run the build script first."}, 500)


@app.get("/api/fleet")
async def api_fleet():
    """Return all profiles with session counts, last activity, models, costs."""
    profiles = []
    # Default profile
    default_db = HERMES_HOME / "state.db"
    default_sessions = _query_db(
        default_db,
        "SELECT COUNT(*) as count, COALESCE(SUM(input_tokens+output_tokens),0) as tokens, "
        "COALESCE(SUM(estimated_cost_usd),0) as cost, "
        "MAX(started_at) as last_active, "
        "COALESCE(SUM(message_count),0) as messages "
        "FROM sessions"
    )
    ds = default_sessions[0] if default_sessions else {}
    profiles.append({
        "name": "default",
        "icon": "🧠",
        "sessions": ds.get("count", 0),
        "messages": ds.get("messages", 0),
        "tokens": ds.get("tokens", 0),
        "cost": round(ds.get("cost", 0), 4),
        "last_active": _ts_to_rel(ds.get("last_active")),
        "model": _get_default_model(default_db),
    })

    # Profile definitions
    PROFILE_META = {
        "clinical":      {"icon": "🩺", "color": "emerald"},
        "research-2-content": {"icon": "🔬", "color": "violet"},
        "infographic":   {"icon": "📊", "color": "amber"},
        "blog":          {"icon": "✍️", "color": "sky"},
        "video":         {"icon": "🎬", "color": "rose"},
        "social":        {"icon": "🐦", "color": "blue"},
        "brain":         {"icon": "🧠", "color": "cyan"},
        "medical_ai_visibility": {"icon": "👁️", "color": "purple"},
    }

    for name in sorted(os.listdir(PROFILES_DIR)):
        prof_dir = PROFILES_DIR / name
        if not prof_dir.is_dir():
            continue
        meta = PROFILE_META.get(name, {"icon": "⚙️", "color": "slate"})

        db_path = prof_dir / "state.db"
        sessions = _query_db(
            db_path,
            "SELECT COUNT(*) as count, COALESCE(SUM(input_tokens+output_tokens),0) as tokens, "
            "COALESCE(SUM(estimated_cost_usd),0) as cost, "
            "MAX(started_at) as last_active, "
            "COALESCE(SUM(message_count),0) as messages "
            "FROM sessions"
        )
        s = sessions[0] if sessions else {}

        # Read SOUL.md for purpose
        soul = _read_text(prof_dir / "SOUL.md", 200)
        purpose = soul.strip().split("\n")[:3]
        purpose = " ".join(p.strip() for p in purpose if p.strip() and not p.startswith("#") and not p.startswith("---"))[:150]

        # Count cron jobs
        cron_dir = prof_dir / "cron"
        cron_count = len([f for f in cron_dir.iterdir() if f.suffix in (".yaml", ".json", ".yml")]) if cron_dir.exists() else 0

        profiles.append({
            "name": name,
            "icon": meta["icon"],
            "color": meta["color"],
            "sessions": s.get("count", 0),
            "messages": s.get("messages", 0),
            "tokens": s.get("tokens", 0),
            "cost": round(s.get("cost", 0), 4),
            "last_active": _ts_to_rel(s.get("last_active")),
            "purpose": purpose or "—",
            "cron_count": cron_count,
            "model": _get_default_model(db_path),
        })

    return {"profiles": profiles}


def _get_default_model(db_path: Path) -> str:
    """Get the most common model used across sessions."""
    rows = _query_db(
        db_path,
        "SELECT model, COUNT(*) as cnt FROM sessions WHERE model IS NOT NULL AND model != '' "
        "GROUP BY model ORDER BY cnt DESC LIMIT 1"
    )
    if rows:
        return rows[0].get("model", "—")
    return "—"


@app.get("/api/profile/{name}")
async def api_profile_detail(name: str, limit: int = Query(20, le=100)):
    """Return detailed info for a specific profile, including recent sessions."""
    if name == "default":
        db_path = HERMES_HOME / "state.db"
        prof_dir = HERMES_HOME
    else:
        db_path = PROFILES_DIR / name / "state.db"
        prof_dir = PROFILES_DIR / name
        if not prof_dir.exists():
            return JSONResponse({"error": "Profile not found"}, 404)

    # Recent sessions
    sessions = _query_db(
        db_path,
        "SELECT id, title, source, model, started_at, ended_at, message_count, tool_call_count, "
        "input_tokens, output_tokens, estimated_cost_usd, end_reason "
        "FROM sessions ORDER BY started_at DESC LIMIT ?",
        (limit,)
    )

    # Parse session timestamps
    for s in sessions:
        s["started_at"] = _ts_to_iso(s.get("started_at"))
        s["ended_at"] = _ts_to_iso(s.get("ended_at"))
        s["cost"] = round(s.get("estimated_cost_usd", 0), 4) if s.get("estimated_cost_usd") else 0
        s["end_reason"] = s.get("end_reason") or "active"

    # Stats
    stats = _query_db(
        db_path,
        "SELECT "
        "COUNT(*) as total_sessions, "
        "COALESCE(SUM(message_count),0) as total_messages, "
        "COALESCE(SUM(tool_call_count),0) as total_tool_calls, "
        "COALESCE(SUM(input_tokens+output_tokens),0) as total_tokens, "
        "COALESCE(SUM(estimated_cost_usd),0) as total_cost, "
        "COALESCE(SUM(input_tokens),0) as total_input_tokens, "
        "COALESCE(SUM(output_tokens),0) as total_output_tokens, "
        "COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens "
        "FROM sessions"
    )
    stats_summary = stats[0] if stats else {}

    # Cron jobs
    cron_jobs = []
    cron_file = prof_dir / "cron" / "jobs.json"
    if cron_file.exists():
        data = _read_json(cron_file)
        for job in data.get("jobs", []):
            cron_jobs.append({
                "id": job.get("id"),
                "name": job.get("name", "unnamed"),
                "schedule": job.get("schedule", "—"),
                "enabled": job.get("enabled", True),
                "last_run": job.get("last_run_at", "—"),
                "last_status": job.get("last_result", "—"),
            })

    # SOUL.md
    soul = _read_text(prof_dir / "SOUL.md", 5000)

    # Skills list
    skills_dir = prof_dir / "skills"
    skills = []
    if skills_dir.exists():
        for cat_dir in skills_dir.iterdir():
            if cat_dir.is_dir():
                for sk_file in cat_dir.iterdir():
                    if sk_file.name == "SKILL.md":
                        skills.append(sk_file.parent.name)

    return {
        "name": name,
        "stats": stats_summary,
        "sessions": sessions,
        "cron_jobs": cron_jobs,
        "soul": soul[:3000],
        "skills": sorted(skills),
        "model": _get_default_model(db_path),
    }


@app.get("/api/gateway")
async def api_gateway():
    """Return gateway status and channel info."""
    gw = _read_json(HERMES_HOME / "gateway_state.json")
    channels = _read_json(HERMES_HOME / "channel_directory.json")
    return {"gateway": gw, "channels": channels}


@app.get("/api/cron")
async def api_cron():
    """Return all cron jobs across all profiles."""
    jobs = []

    # Default profile
    cron_file = HERMES_HOME / "cron" / "jobs.json"
    if cron_file.exists():
        data = _read_json(cron_file)
        for j in data.get("jobs", []):
            jobs.append({
                "profile": "default",
                "id": j.get("id"),
                "name": j.get("name", "unnamed"),
                "schedule": j.get("schedule", "—"),
                "prompt": (j.get("prompt", "") or "")[:120],
                "enabled": j.get("enabled", True),
                "last_run": j.get("last_run_at", "—"),
                "last_status": (j.get("last_result", "") or "")[:50],
            })

    # Profile cron jobs
    for name in sorted(os.listdir(PROFILES_DIR)):
        cron_file = PROFILES_DIR / name / "cron" / "jobs.json"
        if cron_file.exists():
            data = _read_json(cron_file)
            for j in data.get("jobs", []):
                jobs.append({
                    "profile": name,
                    "id": j.get("id"),
                    "name": j.get("name", "unnamed"),
                    "schedule": j.get("schedule", "—"),
                    "prompt": (j.get("prompt", "") or "")[:120],
                    "enabled": j.get("enabled", True),
                    "last_run": j.get("last_run_at", "—"),
                    "last_status": (j.get("last_result", "") or "")[:50],
                })

    return {"jobs": sorted(jobs, key=lambda j: (j["profile"], j["name"]))}


@app.get("/api/pipeline")
async def api_pipeline():
    """Return the core workflow pipeline from AGENT_PROFILES.md."""
    pipeline_doc = _read_text(HERMES_HOME / "AGENT_PROFILES.md", 5000)

    # Extract pipeline stages
    stages = [
        {"id": "clinical", "name": "Clinical Research", "icon": "🩺", "output": "Selected paper"},
        {"id": "research-2-content", "name": "Paper Dissector", "icon": "🔬", "output": "Dissection document"},
        {"id": "infographic", "name": "Infographic", "icon": "📊", "output": "Visual summary"},
        {"id": "blog", "name": "Blog Post", "icon": "✍️", "output": "Published article"},
        {"id": "video", "name": "Explainer Video", "icon": "🎬", "output": "MP4 animation"},
        {"id": "social", "name": "Social / X", "icon": "🐦", "output": "Threads & posts"},
    ]

    # Get recent activity per stage
    for stage in stages:
        if stage["id"] == "clinical":
            db_path = PROFILES_DIR / "clinical" / "state.db"
        elif stage["id"] == "research-2-content":
            db_path = PROFILES_DIR / "research-2-content" / "state.db"
        else:
            db_path = PROFILES_DIR / stage["id"] / "state.db"
        
        last = _query_db(
            db_path,
            "SELECT title, started_at, message_count FROM sessions ORDER BY started_at DESC LIMIT 1"
        )
        stage["last_active"] = _ts_to_rel(last[0]["started_at"]) if last else "never"
        stage["sessions"] = _query_db(
            db_path, "SELECT COUNT(*) as c FROM sessions"
        )[0]["c"]

    return {"stages": stages, "document": pipeline_doc}


@app.get("/api/system")
async def api_system():
    """Return system info — disk, memory, hermes version."""
    import shutil
    try:
        total, used, free = shutil.disk_usage(HERMES_HOME)
        disk = {
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "used_pct": round(used / total * 100, 1),
        }
    except OSError:
        disk = {"total_gb": 0, "used_gb": 0, "free_gb": 0, "used_pct": 0}

    # Quick size estimate — just top-level files + known big dirs
    home_size = 0
    big_dirs = ["sessions", "logs", "skills", "plugins", "memories", "cron"]
    for entry in HERMES_HOME.iterdir():
        try:
            if entry.is_file():
                home_size += entry.stat().st_size
        except OSError:
            pass
    for d in big_dirs:
        dpath = HERMES_HOME / d
        if dpath.is_dir():
            try:
                for f in dpath.rglob("*"):
                    if f.is_file():
                        home_size += f.stat().st_size
            except OSError:
                pass
    home_size = home_size / (1024**2)

    # State DB size
    state_db_size = HERMES_HOME.joinpath("state.db").stat().st_size / (1024**2) if HERMES_HOME.joinpath("state.db").exists() else 0

    # Total sessions across all profiles
    total_sessions = 0
    total_tokens = 0
    total_cost = 0.0
    for db_name in ["state.db"] + [f"profiles/{p}/state.db" for p in os.listdir(PROFILES_DIR)]:
        db_path = HERMES_HOME / db_name
        if db_path.exists():
            stats = _query_db(
                db_path,
                "SELECT COUNT(*) as s, COALESCE(SUM(input_tokens+output_tokens),0) as t, "
                "COALESCE(SUM(estimated_cost_usd),0) as c FROM sessions"
            )
            if stats:
                total_sessions += stats[0].get("s", 0)
                total_tokens += stats[0].get("t", 0)
                total_cost += stats[0].get("c", 0)

    # Version
    hermes_bin = HERMES_HOME / "bin" / "hermes"
    version = "?"
    if hermes_bin.exists():
        import subprocess
        try:
            result = subprocess.run([str(hermes_bin), "--version"], capture_output=True, text=True, timeout=5)
            version = result.stdout.strip() or result.stderr.strip() or "?"
        except Exception:
            version = "?"

    return {
        "disk": disk,
        "home_size_mb": round(home_size, 1),
        "state_db_mb": round(state_db_size, 1),
        "total_sessions": total_sessions,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "hermes_version": version[:60],
        "hermes_home": str(HERMES_HOME),
        "profiles_count": len([p for p in PROFILES_DIR.iterdir() if p.is_dir()]) if PROFILES_DIR.exists() else 0,
    }


@app.get("/api/activity")
async def api_activity():
    """Return recent activity feed across all profiles."""
    events = []

    for profile_name in ["default"] + sorted(
        p.name for p in PROFILES_DIR.iterdir() if p.is_dir()
    ):
        db_path = HERMES_HOME / "state.db" if profile_name == "default" else PROFILES_DIR / profile_name / "state.db"
        recent = _query_db(
            db_path,
            "SELECT id, title, source, started_at, message_count, model, end_reason, "
            "input_tokens, output_tokens "
            "FROM sessions WHERE title IS NOT NULL AND title != '' "
            "ORDER BY started_at DESC LIMIT 10"
        )
        for s in recent:
            events.append({
                "profile": profile_name,
                "title": s.get("title", "Untitled")[:80],
                "source": s.get("source", "?"),
                "when": _ts_to_rel(s.get("started_at")),
                "messages": s.get("message_count", 0),
                "model": s.get("model", "?")[:30],
                "status": s.get("end_reason", "active") if s.get("end_reason") else "active",
                "tokens": (s.get("input_tokens", 0) or 0) + (s.get("output_tokens", 0) or 0),
            })

    # Sort by recency (approximate by parsing relative time)
    events.sort(key=lambda e: e.get("when", ""), reverse=True)
    return {"events": events[:50]}


@app.get("/api/kanban")
async def api_kanban():
    """Return kanban board data."""
    kanban_db = HERMES_HOME / "kanban.db"
    if not kanban_db.exists():
        return {"items": [], "columns": []}
    
    # Try to get board info
    columns = _query_db(kanban_db, "SELECT * FROM sqlite_master WHERE type='table'")
    items = _query_db(kanban_db, "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50") if any(t["name"] == "tasks" for t in columns) else []
    return {"items": items, "tables": [c["name"] for c in columns]}


# ── SSE endpoint ────────────────────────────────────────────────────────


@app.get("/events")
async def sse_events(request: Request):
    """SSE endpoint that pushes updates every REFRESH_INTERVAL seconds."""

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "fleet": None,
                "gateway": None,
            }
            try:
                # Fetch fleet data
                fleet_resp = await api_fleet()
                if isinstance(fleet_resp, dict):
                    data["fleet"] = fleet_resp

                gw_resp = await api_gateway()
                if isinstance(gw_resp, dict):
                    data["gateway"] = gw_resp

                system_resp = await api_system()
                if isinstance(system_resp, dict):
                    data["system"] = system_resp

                yield {"event": "update", "data": json.dumps(data)}
            except Exception as e:
                yield {"event": "error", "data": str(e)}

            await asyncio.sleep(REFRESH_INTERVAL)

    return EventSourceResponse(event_generator())


# ── Main ────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print(f"🚀 Hermes Mission Control")
    print(f"   Hermes home: {HERMES_HOME}")
    print(f"   Dashboard:   http://localhost:2800")
    print(f"   Refresh:     every {REFRESH_INTERVAL}s (SSE)")
    print()
    print("   To access from your machine:")
    print(f"   ssh -L 2800:localhost:2800 user@your-vps")
    print()
    uvicorn.run(app, host="0.0.0.0", port=2800)
