"""
config_loader.py  —  Load and validate config.json
"""

import json
import sys
from pathlib import Path


REQUIRED_KEYS = [
    "wiki_clone_dir",
    "confluence_base_url",
    "confluence_space_key",
    "confluence_email",
    "confluence_api_token",
    "confluence_parent_page_id",
    "mapping_file",
]


def load_config(path: str) -> dict:
    """Load config.json and validate all required keys are present."""
    config_path = Path(path)
    if not config_path.exists():
        print(f"\n  ERROR: Config file not found: {path}")
        print("  Copy config.example.json → config.json and fill in your values.\n")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        print(f"\n  ERROR: Missing required config keys: {missing}")
        print(f"  Check your config.json against config.example.json\n")
        sys.exit(1)

    # Normalise paths
    cfg["wiki_clone_dir"] = str(Path(cfg["wiki_clone_dir"]).expanduser().resolve())
    cfg["mapping_file"]   = str(Path(cfg["mapping_file"]).expanduser().resolve())

    # Strip trailing slash from Confluence URL
    cfg["confluence_base_url"] = cfg["confluence_base_url"].rstrip("/")

    return cfg
