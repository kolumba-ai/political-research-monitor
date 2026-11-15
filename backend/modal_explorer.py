"""Public, read-only explorer on Modal: the API and the built 3D explorer behind one HTTPS URL.

Separate from modal_app.py on purpose: this container is reachable by anyone, so it gets no API keys
(`prm-secrets` is not attached) and its own volume, which holds only what `make modal-explorer-data` published.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import modal

REPO_ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT = Path("/root/app")
REMOTE_BACKEND = REMOTE_ROOT / "backend"
EXPLORER_DIST = REPO_ROOT / "frontend-3d" / "dist"
SCALEDOWN_WINDOW_S = int(os.environ.get("MODAL_EXPLORER_SCALEDOWN_S", "300"))

if modal.is_local() and not (EXPLORER_DIST / "index.html").exists():
    raise SystemExit("frontend-3d/dist is missing: run `make explorer-build` before deploying the explorer")

app = modal.App("prm-explorer")
DATA_VOLUME = modal.Volume.from_name("prm-explorer-data", create_if_missing=True)
IMAGE = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements(str(REPO_ROOT / "requirements.txt"))
    .add_local_dir(str(REPO_ROOT / "backend"), remote_path=str(REMOTE_BACKEND), copy=True,
                   ignore=["**/__pycache__", "tests", ".pytest_cache"])
    .add_local_dir(str(REPO_ROOT / "structures"), remote_path=str(REMOTE_ROOT / "structures"), copy=True)
    .add_local_dir(str(EXPLORER_DIST), remote_path=str(REMOTE_ROOT / "frontend-3d" / "dist"), copy=True)
)


@app.function(image=IMAGE, volumes={str(REMOTE_ROOT / "var"): DATA_VOLUME}, max_containers=2,
              scaledown_window=SCALEDOWN_WINDOW_S)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def explorer():
    os.environ["GRAPH_BACKEND"] = "sqlite"
    sys.path.insert(0, str(REMOTE_BACKEND))
    from fastapi.responses import RedirectResponse
    from polgraph.api import app as api_app

    api_app.add_api_route("/", lambda: RedirectResponse("/explorer/"), include_in_schema=False)
    return api_app
