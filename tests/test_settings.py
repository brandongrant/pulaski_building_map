"""pipeline/common/settings.py: defaults and environment overrides."""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_paths(env=None):
    """Import settings in a subprocess so env overrides apply at import time."""
    full_env = dict(os.environ)
    full_env.pop("PULASKI_DATA_ROOT", None)
    if env:
        full_env.update(env)
    out = subprocess.check_output(
        [sys.executable, "-c",
         "from common.settings import DATA_ROOT, RAW_DIR, WEB_DATA_DIR;"
         "print(DATA_ROOT); print(RAW_DIR); print(WEB_DATA_DIR)"],
        cwd=REPO_ROOT / "pipeline", env=full_env, text=True)
    return out.strip().splitlines()


def test_defaults_are_repo_relative():
    data_root, raw_dir, web_data = _read_paths()
    assert data_root == str(REPO_ROOT / "data")
    assert raw_dir == str(REPO_ROOT / "data" / "raw")
    assert web_data == str(REPO_ROOT / "web" / "data")


def test_data_root_env_override(tmp_path):
    data_root, raw_dir, web_data = _read_paths({"PULASKI_DATA_ROOT": str(tmp_path)})
    assert data_root == str(tmp_path)
    assert raw_dir == str(tmp_path / "raw")
    # web output always belongs to the checkout, not the data tree
    assert web_data == str(REPO_ROOT / "web" / "data")
