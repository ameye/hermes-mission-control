# 🛸 Hermes Mission Control

A live, read-only dashboard for your Hermes Agent fleet. Shows all 8 agent profiles, session activity, cron jobs, gateway health, and the content pipeline — in a dark glassmorphism UI with auto-refresh.

## Quick Start — Python (standalone)

```bash
# Copy to your Contabo VPS
rsync -avz ./hermes-mission-control user@contabo:/opt/data/

# SSH in and run
ssh user@contabo
cd /opt/data/hermes-mission-control
pip install fastapi uvicorn sse-starlette
python server.py
```

Then from your local machine:
```bash
ssh -L 2800:localhost:2800 user@contabo
# Open http://localhost:2800
```

## Docker

Since Hermes itself likely runs in Docker on your VPS, the dashboard works best as a sibling container:

```bash
# Build and run with docker-compose
cd /opt/data/hermes-mission-control
docker compose up -d

# Or build manually
docker build -t hermes-mission-control .
docker run -d \
  --name hermes-mission-control \
  -p 2800:2800 \
  -v ~/.hermes:/data/hermes:ro \
  hermes-mission-control
```

### Docker on Coolify

If you're using Coolify on your Contabo VPS:

1. Add a new **Service** → **Docker Compose**
2. Paste the `docker-compose.yml` content
3. Add a volume mount: `~/.hermes:/data/hermes:ro` (Coolify path picker)
4. Deploy — Coolify auto-restarts and handles HTTPS via the proxy

> ⚠️ The container mounts your Hermes data **read-only** (`:ro`). It cannot modify anything — safe to run alongside your live Hermes agent.

## Custom Hermes Home

If Hermes data lives at a non-standard path:

```bash
# Standalone
HERMES_HOME=/opt/hermes/data python server.py

# Docker
docker run -d -p 2800:2800 -v /opt/hermes/data:/data/hermes:ro hermes-mission-control
```

## Access via SSH Tunnel

```bash
ssh -L 2800:localhost:2800 user@your-contabo-vps
```

Then open **http://localhost:2800**.

## As a systemd service (optional)

```ini
[Unit]
Description=Hermes Mission Control
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/opt/data/hermes-mission-control
ExecStart=/usr/bin/python3 server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable --now hermes-mission-control
```

## Dashboard Tabs

| Tab | What it shows |
|-----|--------------|
| 🚀 **Fleet** | All 8 agent profiles — sessions, tokens, costs, last activity. Click any card for detail drawer |
| 🔗 **Pipeline** | Visual workflow: Clinical → Dissector → Infographic/Blog/Video/Social |
| ⏰ **Cron** | All scheduled jobs across every profile |
| 📡 **Gateway** | Platform connections (Telegram, Teams), active channels |
| ⚙️ **System** | Disk usage, DB stats, version info, live activity feed |

## Data Sources

The dashboard reads **read-only** from:
- `~/.hermes/state.db` — session store (SQLite)
- `~/.hermes/profiles/<name>/state.db` — per-profile sessions
- `~/.hermes/gateway_state.json` — gateway status
- `~/.hermes/channel_directory.json` — connected channels
- `~/.hermes/cron/jobs.json` — scheduled jobs
- `~/.hermes/profiles/<name>/cron/jobs.json` — per-profile cron jobs
- `~/.hermes/AGENT_PROFILES.md` — pipeline definition
- `~/.hermes/profiles/<name>/SOUL.md` — agent identities
- `~/.hermes/kanban.db` — task board

## Tech Stack

- **FastAPI** — async Python backend with SSE for live updates
- **Tailwind CSS** CDN — utility-first styling
- **Alpine.js** — lightweight reactive frontend
- **SQLite** — read-only queries on Hermes's own session store
