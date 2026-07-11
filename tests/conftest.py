"""Make pipeline modules importable from tests (scripts import each other
assuming pipeline/ is on sys.path, the way run_all.py invokes them)."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "pipeline"))
