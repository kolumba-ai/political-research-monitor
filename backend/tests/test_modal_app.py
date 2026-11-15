import pytest

pytest.importorskip("modal")

import modal_app
from evidencegraph.config import REPO_ROOT as EVIDENCEGRAPH_ROOT
from polgraph.config import REPO_ROOT as POLGRAPH_ROOT
from socialclaims.config import REPO_ROOT as SOCIALCLAIMS_ROOT


def test_modal_caps_and_worker_clamping():
    assert modal_app.IMAGE_PYTHON_VERSION == "3.11"
    assert modal_app.TIMEOUT_S == 6 * 3600
    assert modal_app.CAPS == {
        "systema": 1,
        "systemb": 1,
        "systemb_extract_shard": 10,
        "systemc": 1,
        "systemc_fetch_worker": 4,
    }
    assert modal_app._clamp_workers(12, modal_app.CAPS["systemb_extract_shard"]) == 10
    assert modal_app._clamp_workers(3, modal_app.CAPS["systemc_fetch_worker"]) == 3
    assert modal_app._clamp_workers(0, modal_app.CAPS["systemc_fetch_worker"]) == 1


def test_exit_code_returns_first_nonzero_result():
    assert modal_app._exit_code([{"exit_code": 0}, {"exit_code": 7}, {"exit_code": 2}]) == 7
    assert modal_app._exit_code([{"exit_code": 0}]) == 0
    assert modal_app._exit_code([]) == 0


def test_packages_resolve_repository_root_from_modal_layout():
    assert POLGRAPH_ROOT == SOCIALCLAIMS_ROOT == EVIDENCEGRAPH_ROOT == modal_app.REPO_ROOT
    assert (modal_app.REMOTE_ROOT / "backend" / "polgraph" / "config.py").parents[2] == modal_app.REMOTE_ROOT
    assert (modal_app.REMOTE_ROOT / "backend" / "socialclaims" / "config.py").parents[2] == modal_app.REMOTE_ROOT
    assert (modal_app.REMOTE_ROOT / "backend" / "evidencegraph" / "config.py").parents[2] == modal_app.REMOTE_ROOT
