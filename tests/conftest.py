import sys
from pathlib import Path

# Add core directories to python path
root_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir))  # So we can import apps
sys.path.insert(0, str(root_dir / "packages"))  # So we can import adapters / features

# Add all package src directories to path
for p in (root_dir / "packages").glob("**/src"):
    sys.path.insert(0, str(p))
