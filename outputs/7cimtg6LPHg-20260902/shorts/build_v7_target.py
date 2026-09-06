"""Compatibility entrypoint. The preserved implementation lives in Git history.

New work must explicitly pass --root and uses the common content lineage gate.
"""
from pathlib import Path
import sys
PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from shorts_v7_builder import main
if __name__ == "__main__":
    raise SystemExit(main())
