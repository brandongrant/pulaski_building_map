"""Source provenance and build-manifest plumbing (roadmap DATA-003).

Pipeline steps run as separate processes, so state is merged through two
JSON files instead of shared memory:

  RAW_DIR/source_meta.json          one entry per downloaded source asset:
                                    sha256, bytes, retrieved_at, plus
                                    source-specific extras such as the CAMA
                                    export's internal effective date
  PROCESSED_DIR/build_manifest.json one entry per pipeline run: build id,
                                    code commit, sources snapshot, outputs,
                                    and data-quality metrics — every
                                    published dataset is traceable to source
                                    hashes and a git commit

Each step calls record_source()/update_build_manifest() for the parts it
owns; the merge is last-writer-wins per key, which is safe because steps
run sequentially (run_all.py).
"""
import hashlib
import json
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from common.settings import PROCESSED_DIR, RAW_DIR

SOURCE_META = RAW_DIR / "source_meta.json"
BUILD_MANIFEST = PROCESSED_DIR / "build_manifest.json"


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2], text=True).strip()
    except Exception:
        return "unknown"


def record_source(slug, path, **extra):
    """Fingerprint a downloaded source asset. Call after every download
    (or over an existing file — retrieved_at then reflects the file mtime)."""
    path = Path(path)
    meta = _read_json(SOURCE_META)
    st = path.stat()
    meta[slug] = {
        "file": path.name,
        "bytes": st.st_size,
        "sha256": sha256_file(path),
        "retrieved_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recorded_at": _utcnow(),
        **extra,
    }
    _write_json(SOURCE_META, meta)
    return meta[slug]


def read_source_meta():
    return _read_json(SOURCE_META)


def zip_effective_date(path):
    """Newest internal file date in a zip — the CAMA export's real
    effective date, replacing the hard-coded constant (roadmap §5.3)."""
    try:
        with zipfile.ZipFile(path) as z:
            dates = [datetime(*i.date_time) for i in z.infolist() if i.date_time]
        return max(dates).strftime("%Y-%m-%d") if dates else ""
    except Exception:
        return ""


def start_build_manifest():
    """Reset the manifest for a fresh pipeline run (run_all.py calls this)."""
    _write_json(BUILD_MANIFEST, {
        "build_id": f"{_utcnow()}-{git_commit()}",
        "code_commit": git_commit(),
        "started_at": _utcnow(),
        "sources": {},
        "outputs": {},
        "quality": {},
    })


def update_build_manifest(section=None, **entries):
    """Merge entries into a manifest section ('sources'/'outputs'/'quality'),
    or into the top level when section is None. Creates the manifest if a
    step runs standalone outside run_all.py."""
    m = _read_json(BUILD_MANIFEST)
    if not m:
        m = {"build_id": f"{_utcnow()}-{git_commit()}", "code_commit": git_commit(),
             "started_at": _utcnow(), "sources": {}, "outputs": {}, "quality": {}}
    if section:
        m.setdefault(section, {}).update(entries)
    else:
        m.update(entries)
    _write_json(BUILD_MANIFEST, m)
    return m


def record_output(slug, path, **extra):
    """Fingerprint a produced artifact into the manifest's outputs section."""
    path = Path(path)
    update_build_manifest("outputs", **{slug: {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "written_at": _utcnow(),
        **extra,
    }})
