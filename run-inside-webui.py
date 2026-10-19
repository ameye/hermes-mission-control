#!/usr/bin/env python3
"""Launch Mission Control on alternative port (to avoid conflict with Coolify container on 2800)."""
import sys, os
os.environ["HERMES_HOME"] = "/home/hermeswebui/.hermes"
sys.path.insert(0, "/workspace/hermes-mission-control")

import uvicorn
from server import app

uvicorn.run(app, host="0.0.0.0", port=2803)
