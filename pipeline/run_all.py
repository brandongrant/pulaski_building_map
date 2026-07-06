"""Run the full data pipeline end-to-end (download -> extract -> join -> tiles).

Usage: python pipeline/run_all.py [--skip-downloads]
Note: the CAMA zip URL occasionally changes; get the current one from
https://pulaskicountyassessor.net/services/raw-data-export/ (Real Property).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPE = ROOT / "pipeline"
RAW = ROOT / "data" / "raw"
CAMA_URL = ("https://www.dropbox.com/scl/fi/iogswewv3za77ocqcznj4/CamaExport.zip"
            "?dl=1&rlkey=8yh1qcm4ckw8y3t5oe5mlxdu3&st=byptnjxq")
PAGIS = "https://www.pagis.org/arcgis/rest/services/MAPS/BaseMap/MapServer"


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

run(sys.executable, PIPE / "build_cama_attrs.py")
run(sys.executable, PIPE / "join_buildings.py")
run(sys.executable, PIPE / "make_tiles.py")
print("\nPipeline complete. Start the map with:  python serve.py")
