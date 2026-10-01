"""Read the exported policy snapshot; do not duplicate numeric values."""
import json
from pathlib import Path
POLICY = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
SHORTS_NARRATION_TARGET_CPS = POLICY["SHORTS_NARRATION_TARGET_CPS"]
