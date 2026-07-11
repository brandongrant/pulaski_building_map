"""Central paths and environment settings for all pipeline scripts.

Every pipeline script imports its input/output locations from here instead
of building its own ``Path`` constants, so a clean clone works on any
machine. Two knobs, both optional, both read from the environment (put them
in your shell profile or a ``.env`` you source — see ``.env.example``):

  PULASKI_DATA_ROOT   where the (gitignored) data tree lives. Defaults to
                      ``<repo>/data``. Point it elsewhere when the raw
                      archives live on a different drive than the checkout.
  PULASKI_CAMA_URL    current CAMA export download URL (it rotates; see
                      https://pulaskicountyassessor.net/services/raw-data-export/)

Layout under DATA_ROOT:
  raw/         immutable source downloads (never edited after retrieval)
  staging/     source-shaped intermediate tables (Parquet as of Phase 0B)
  processed/   joined/derived outputs consumed by later stages

WEB_DATA_DIR always points at this checkout's ``web/data`` — published
artifacts belong to the repo regardless of where the data tree lives.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get("PULASKI_DATA_ROOT", REPO_ROOT / "data")).resolve()
RAW_DIR = DATA_ROOT / "raw"
STAGING_DIR = DATA_ROOT / "staging"
PROCESSED_DIR = DATA_ROOT / "processed"
WEB_DATA_DIR = REPO_ROOT / "web" / "data"

# Rotating source URLs, overridable without code edits.
CAMA_URL = os.environ.get(
    "PULASKI_CAMA_URL",
    "https://www.dropbox.com/scl/fi/iogswewv3za77ocqcznj4/CamaExport.zip"
    "?dl=1&rlkey=8yh1qcm4ckw8y3t5oe5mlxdu3&st=byptnjxq",
)
PP_DUMP_URLS = {
    "PP_Dump1.xlsx": os.environ.get(
        "PULASKI_PP_DUMP1_URL",
        "https://www.dropbox.com/s/2q59jy8xs1j97ql/PP_Dump1.xlsx?dl=1"),
    "PP_Dump2.xlsx": os.environ.get(
        "PULASKI_PP_DUMP2_URL",
        "https://www.dropbox.com/s/hyucgbvkii5secf/PP_Dump2.xlsx?dl=1"),
}

PAGIS_BASE = "https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer"
