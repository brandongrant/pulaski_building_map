"""Run the full data pipeline end-to-end (download -> extract -> join -> tiles).

Usage: python pipeline/run_all.py [--skip-downloads]
Note: the CAMA zip URL occasionally changes; get the current one from
https://pulaskicountyassessor.net/services/raw-data-export/ (Real Property).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common.settings import (CAMA_URL, PAGIS_BASE as PAGIS, PP_DUMP_URLS,
                             RAW_DIR as RAW, REPO_ROOT as ROOT)

PIPE = ROOT / "pipeline"


def run(*args):
    print("\n>>>", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


skip_dl = "--skip-downloads" in sys.argv
if not skip_dl:
    RAW.mkdir(parents=True, exist_ok=True)
    if not (RAW / "CamaExport.zip").exists():
        run("curl", "-SL", "-o", RAW / "CamaExport.zip", CAMA_URL)
    run(sys.executable, PIPE / "download_layer.py", f"{PAGIS}/21", "buildings", RAW,
        "OBJECTID,BO_UNIQ,BO_NAME,STR_CODE,BO_CODE,SASC", "1000")
    run(sys.executable, PIPE / "download_layer.py", f"{PAGIS}/68", "parcels", RAW,
        "OBJECTID,PARCELID,CAMA_PIN,PROPLOOKUP,ADRLABEL,ADRCITY,PARCELTYPE,IMPVALUE,LANDVALUE,TOTALVALUE", "1000")

if not skip_dl:
    for n, u in PP_DUMP_URLS.items():
        if not (RAW / n).exists():
            run("curl", "-SL", "-o", RAW / n, u)

run(sys.executable, PIPE / "build_cama_attrs.py")
run(sys.executable, PIPE / "join_buildings.py")
run(sys.executable, PIPE / "extract_pp.py")
run(sys.executable, PIPE / "enrich_pp.py")
run(sys.executable, PIPE / "make_tiles.py")
# owner/address search index — streams the parcel layer itself (~5 min),
# independent of the raw downloads above
run(sys.executable, PIPE / "build_owner_index.py")
run(sys.executable, PIPE / "build_vehicle_index.py")
print("\nPipeline complete. Start the map with:  python serve.py")
