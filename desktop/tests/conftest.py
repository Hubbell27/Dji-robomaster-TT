import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "desktop")]
os.environ["DENTAL_INTAKE_HOME"] = tempfile.mkdtemp(prefix="di-test-")
