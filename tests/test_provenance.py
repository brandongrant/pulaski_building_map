"""Provenance plumbing: source fingerprints, zip effective dates, manifest merge."""
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(code, data_root):
    """Provenance paths bind to PULASKI_DATA_ROOT at import — run in a
    subprocess per test so each gets a clean tree."""
    env = dict(os.environ, PULASKI_DATA_ROOT=str(data_root))
    return subprocess.check_output(
        [sys.executable, "-c", code], cwd=REPO_ROOT / "pipeline", env=env, text=True)


def test_record_source_fingerprints_file(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "asset.csv").write_text("a,b\n1,2\n")
    _run("from common.provenance import record_source;"
         "from common.settings import RAW_DIR;"
         "record_source('demo', RAW_DIR / 'asset.csv', effective_date='2026-01-15')",
         tmp_path)
    meta = json.loads((raw / "source_meta.json").read_text())
    assert meta["demo"]["bytes"] == 8
    assert len(meta["demo"]["sha256"]) == 64
    assert meta["demo"]["effective_date"] == "2026-01-15"
    assert meta["demo"]["retrieved_at"].endswith("Z")


def test_zip_effective_date_uses_newest_member(tmp_path):
    zp = tmp_path / "export.zip"
    with zipfile.ZipFile(zp, "w") as z:
        old = zipfile.ZipInfo("old.txt", date_time=(2025, 3, 1, 0, 0, 0))
        new = zipfile.ZipInfo("new.txt", date_time=(2026, 6, 28, 12, 0, 0))
        z.writestr(old, "x")
        z.writestr(new, "y")
    out = _run("from common.provenance import zip_effective_date;"
               f"print(zip_effective_date(r'{zp}'))", tmp_path)
    assert out.strip() == "2026-06-28"


def test_zip_effective_date_handles_garbage(tmp_path):
    bad = tmp_path / "not_a_zip.zip"
    bad.write_text("nope")
    out = _run("from common.provenance import zip_effective_date;"
               f"print(repr(zip_effective_date(r'{bad}')))", tmp_path)
    assert out.strip() == "''"


def test_manifest_sections_merge_across_processes(tmp_path):
    (tmp_path / "processed").mkdir()
    _run("from common.provenance import start_build_manifest;"
         "start_build_manifest()", tmp_path)
    _run("from common.provenance import update_build_manifest;"
         "update_build_manifest('quality', building_count=4)", tmp_path)
    _run("from common.provenance import update_build_manifest;"
         "update_build_manifest('quality', building_year_rate=0.5);"
         "update_build_manifest(completed_at='2026-07-11T00:00:00Z')", tmp_path)
    m = json.loads((tmp_path / "processed" / "build_manifest.json").read_text())
    assert m["quality"] == {"building_count": 4, "building_year_rate": 0.5}
    assert m["completed_at"] == "2026-07-11T00:00:00Z"
    assert m["code_commit"]
    assert m["build_id"].endswith(m["code_commit"])
    # started_at survives later merges
    assert datetime.strptime(m["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc) <= datetime.now(timezone.utc)
