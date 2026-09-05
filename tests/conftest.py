import sys
from pathlib import Path

# Los modulos de src/ no forman paquete instalable; se importan por path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "ingestion"))
