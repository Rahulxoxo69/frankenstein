"""JSON dump of the irrigation bundle — for the demo / README / external consumers."""

import json
from pathlib import Path

from frankenstein.mocks import IRRIGATION_BUNDLE

OUT = Path(__file__).parent / "irrigation_manifest.json"

if __name__ == "__main__":
    OUT.write_text(IRRIGATION_BUNDLE.model_dump_json(indent=2))
    print(f"Wrote {OUT}")