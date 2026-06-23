"""
process_and_vault.py
====================
Batch CLI: process a folder of PCB photos through the Vision Triage Pipeline
and store the USEFUL parts (functional + repairable) into the Recycle Vault
for later reuse in Frankenstein builds.

Usage:
    python process_and_vault.py path/to/photos/ [--device cuda] [--store-all]

The vault database is `vault.db` at the project root by default.

What it does:
  1. Loads both YOLO models (component-ID + board-damage)
  2. Runs Gemini (or CLIP-mock) condition assessment per detected crop
  3. Applies damage-inference rules (critical defects → unsafe, etc.)
  4. Filters to USEFUL parts only (skips unsafe by default)
  5. Stores each useful part in the SQLite vault with a sentence-transformer
     embedding for later semantic search
  6. Prints vault stats before and after, so you can see what got added
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Make the project importable when run as a script
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from frankenstein.schema import TeardownContext
from frankenstein.vision.triage_pipeline import VisionTriagePipeline
from frankenstein.vision.vault.repository import VaultRepository


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}


def find_images(folder: Path) -> list[Path]:
    """Return all image files in `folder` (non-recursive)."""
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def print_stats(label: str, stats: dict) -> None:
    """Pretty-print a vault stats dict."""
    print(f"\n  {label}:")
    print(f"    Total parts in vault:  {stats['total_parts']}")
    print(f"    Available:             {stats['available']}")
    print(f"    ├─ Functional:         {stats['functional']}")
    print(f"    └─ Repairable:         {stats['repairable']}")
    print(f"    Unsafe (not available):{stats['unsafe']}")
    print(f"    Teardown sessions:     {stats['teardown_sessions']}")
    cats = stats.get("categories", {})
    if cats:
        print(f"    Categories:            {len(cats)}")
        for cat, count in sorted(cats.items(), key=lambda kv: -kv[1])[:8]:
            print(f"      {cat:24s} {count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process PCB photos and vault useful parts for later reuse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Folder containing PCB / hardware photos",
    )
    parser.add_argument(
        "--device-model",
        default="Batch import",
        help="Device model name to record in the teardown context (default: 'Batch import')",
    )
    parser.add_argument(
        "--failure-cause",
        default="Salvaged from e-waste batch",
        help="Failure cause to record in the teardown context",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Torch device for YOLO (cuda/cpu). Auto-detected if omitted.",
    )
    parser.add_argument(
        "--component-conf",
        type=float,
        default=0.40,
        help="YOLO component confidence threshold (default 0.40)",
    )
    parser.add_argument(
        "--damage-conf",
        type=float,
        default=0.35,
        help="YOLO damage confidence threshold (default 0.35)",
    )
    parser.add_argument(
        "--store-all",
        action="store_true",
        help="Store ALL parts including unsafe (default: useful only)",
    )
    parser.add_argument(
        "--vault-db",
        default="vault.db",
        help="Path to vault SQLite database (default: vault.db)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N images (useful for testing)",
    )
    args = parser.parse_args()

    folder: Path = args.folder
    if not folder.exists() or not folder.is_dir():
        print(f"[ERROR] Folder not found or not a directory: {folder}")
        return 2

    images = find_images(folder)
    if args.limit:
        images = images[: args.limit]
    if not images:
        print(f"[ERROR] No images found in {folder}")
        print(f"        Supported extensions: {sorted(IMAGE_EXTENSIONS)}")
        return 2

    print("=" * 60)
    print(f"Frankenstein Batch Triage → Recycle Vault")
    print("=" * 60)
    print(f"  Folder:           {folder}")
    print(f"  Images found:     {len(images)}")
    print(f"  Device model:     {args.device_model}")
    print(f"  Component conf:   {args.component_conf}")
    print(f"  Damage conf:      {args.damage_conf}")
    print(f"  Store useful only:{not args.store_all}")
    print(f"  Vault DB:         {args.vault_db}")

    # 1. Open vault (shows stats before)
    vault = VaultRepository(db_path=args.vault_db)
    print_stats("Vault BEFORE", vault.stats())

    # 2. Build pipeline with vault wired in
    pipeline = VisionTriagePipeline(
        component_weights=PROJECT_ROOT / "weights" / "component_id_best.pt",
        damage_weights=PROJECT_ROOT / "weights" / "board_damage_best.pt",
        device=args.device,
        component_conf=args.component_conf,
        damage_conf=args.damage_conf,
        vault=vault,
        store_useful_only=not args.store_all,
    )

    # 3. Process each image as its own teardown session so vault stays organised
    #    (one teardown_id per image). If you want one session for the whole
    #    batch, pass all paths to process_teardown in a single call.
    context = TeardownContext(
        device_model=args.device_model,
        failure_cause=args.failure_cause,
        available_tools=["multimeter"],
        skill_level=3,
    )

    failures = 0
    for i, img in enumerate(images, start=1):
        print(f"\n[{i}/{len(images)}] Processing {img.name} ...")
        try:
            pipeline.process_teardown(
                image_paths=[img],
                context=context,
            )
        except Exception as e:
            failures += 1
            print(f"  [ERROR] {img.name}: {e}")
            continue

    # 4. Show vault stats after
    print_stats("\nVault AFTER", vault.stats())

    if failures:
        print(f"\n  {failures} image(s) failed to process (see above).")
    print(f"\n[OK] Done. Search your vault with:")
    print(f"    python -c \"from frankenstein.vision.vault.repository import VaultRepository; "
          f"v = VaultRepository('{args.vault_db}'); "
          f"[print(r) for r in v.search('5V microcontroller')]\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
