import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("API_KEY", None)
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
