"""Optional Modal deployment for the Political Research Monitor systems."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import modal

REPO_ROOT = Path(__file__).resolve().parents[1]
REMOTE_ROOT = Path("/root/app")
REMOTE_BACKEND = REMOTE_ROOT / "backend"
REMOTE_VAR = REMOTE_ROOT / "var"
IMAGE_PYTHON_VERSION = "3.11"
TIMEOUT_S = int(os.environ.get("MODAL_TIMEOUT_S", str(6 * 3600)))

# Shared PostgreSQL limiters enforce Exa (8 rps), Anthropic (2 rps), Oriane (1 rps),
# past.dev (2 rps), and per-host fetch spacing; these caps bound concurrent containers.
CAPS = {
    "systema": 1,  # SQLite on the shared Volume must have a single writer.
    "systemb": 1,  # Oriane capture is low-rate; limiter also covers past.dev.
    "systemb_extract_shard": 10,  # Anthropic calls share a 2-rps limiter.
    "systemc": 1,  # Exa requests share the 8-rps limiter.
    "systemc_fetch_worker": 4,  # Fetch tasks retain per-host limiter spacing.
}


def _cap(env_name: str, key: str) -> int:
    return max(1, int(os.environ.get(env_name, CAPS[key])))


def _clamp_workers(requested: int, maximum: int) -> int:
    return min(max(1, int(requested)), max(1, int(maximum)))


def _exit_code(results: Iterable[dict]) -> int:
    for result in results:
        code = int(result.get("exit_code", 0))
        if code:
            return code
    return 0


def _run_cli(module: str, args: str | Iterable[str], *, sqlite_default: bool = False) -> dict:
    if sqlite_default:
        os.environ.setdefault("GRAPH_BACKEND", "sqlite")
    argv = shlex.split(args) if isinstance(args, str) else list(args)
    try:
        result = subprocess.run(
            [sys.executable, "-m", module, *argv],
            cwd=str(REMOTE_BACKEND),
            capture_output=True,
            text=True,
            check=False,
        )
        output = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-100:])
        return {"exit_code": result.returncode, "output_tail": output}
    except OSError as exc:
        return {"exit_code": 127, "output_tail": str(exc)}
    finally:
        VAR_VOLUME.commit()


app = modal.App("political-research-monitor")
VAR_VOLUME = modal.Volume.from_name("prm-var", create_if_missing=True)
SECRETS = modal.Secret.from_name("prm-secrets")
IMAGE = (
    modal.Image.debian_slim(python_version=IMAGE_PYTHON_VERSION)
    .pip_install_from_requirements(str(REPO_ROOT / "requirements.txt"))
    .add_local_dir(str(REPO_ROOT / "backend"), remote_path=str(REMOTE_BACKEND), copy=True)
    .add_local_dir(str(REPO_ROOT / "knowledge_base"), remote_path=str(REMOTE_ROOT / "knowledge_base"), copy=True)
    .add_local_dir(str(REPO_ROOT / "structures"), remote_path=str(REMOTE_ROOT / "structures"), copy=True)
)


@app.function(image=IMAGE, volumes={str(REMOTE_VAR): VAR_VOLUME}, secrets=[SECRETS], max_containers=1,
              timeout=TIMEOUT_S)
def systema(args: str) -> dict:
    return _run_cli("polgraph", args, sqlite_default=True)


@app.function(image=IMAGE, volumes={str(REMOTE_VAR): VAR_VOLUME}, secrets=[SECRETS],
              max_containers=_cap("MODAL_SYSTEMB_MAX_CONTAINERS", "systemb"), timeout=TIMEOUT_S)
def systemb(args: str) -> dict:
    return _run_cli("socialclaims", args)


@app.function(image=IMAGE, volumes={str(REMOTE_VAR): VAR_VOLUME}, secrets=[SECRETS],
              max_containers=_cap("MODAL_CLAUDE_MAX_CONTAINERS", "systemb_extract_shard"), timeout=TIMEOUT_S)
def systemb_extract_shard(shard: str, extra_args: str) -> dict:
    return _run_cli("socialclaims", ["extract-claims", "--shard", shard, *shlex.split(extra_args)])


@app.function(image=IMAGE, volumes={str(REMOTE_VAR): VAR_VOLUME}, secrets=[SECRETS],
              max_containers=_cap("MODAL_SYSTEMC_MAX_CONTAINERS", "systemc"), timeout=TIMEOUT_S)
def systemc(args: str) -> dict:
    return _run_cli("evidencegraph", args)


@app.function(image=IMAGE, volumes={str(REMOTE_VAR): VAR_VOLUME}, secrets=[SECRETS],
              max_containers=_cap("MODAL_FETCH_MAX_CONTAINERS", "systemc_fetch_worker"), timeout=TIMEOUT_S)
def systemc_fetch_worker(extra_args: str) -> dict:
    return _run_cli("evidencegraph", ["fetch", *shlex.split(extra_args)])


@app.local_entrypoint()
def systema_entrypoint(args: str = "build --country MX"):
    result = systema.remote(args)
    print(result)
    code = _exit_code([result])
    if code:
        raise SystemExit(code)


@app.local_entrypoint()
def systemb_entrypoint(args: str = "list --country MX"):
    result = systemb.remote(args)
    print(result)
    code = _exit_code([result])
    if code:
        raise SystemExit(code)


@app.local_entrypoint()
def systemc_entrypoint(args: str = "queue"):
    result = systemc.remote(args)
    print(result)
    code = _exit_code([result])
    if code:
        raise SystemExit(code)


@app.local_entrypoint()
def extract(shards: int = 1, args: str = ""):
    count = _clamp_workers(shards, _cap("MODAL_CLAUDE_MAX_CONTAINERS", "systemb_extract_shard"))
    outputs = list(systemb_extract_shard.starmap((f"{index}/{count}", args) for index in range(count)))
    for output in outputs:
        print(output)
    code = _exit_code(outputs)
    if code:
        raise SystemExit(code)


@app.local_entrypoint()
def fetch(workers: int = 1, extra_args: str = "--from-queue --limit 20"):
    count = _clamp_workers(workers, _cap("MODAL_FETCH_MAX_CONTAINERS", "systemc_fetch_worker"))
    outputs = list(systemc_fetch_worker.map([extra_args] * count))
    for output in outputs:
        print(output)
    code = _exit_code(outputs)
    if code:
        raise SystemExit(code)


@app.function(image=IMAGE, volumes={str(REMOTE_VAR): VAR_VOLUME}, secrets=[SECRETS], max_containers=1)
@modal.asgi_app()
def api():
    os.environ.setdefault("GRAPH_BACKEND", "sqlite")
    sys.path.insert(0, str(REMOTE_BACKEND))
    from polgraph.api import app as api_app

    return api_app
