"""
test_connection.py  —  Verify your credentials before starting migration
=========================================================================
Run this FIRST to confirm your config.json is correct.

Usage:
  python test_connection.py --config config.json

What it checks:
  1. Confluence API token + SSL connection
  2. Confluence space key exists
  3. Confluence parent page ID is valid
  4. Azure DevOps wiki directory exists and contains .md files
  5. Pandoc is installed
"""

import argparse
import base64
import subprocess
import sys
from pathlib import Path

import requests
import urllib3

from config_loader import load_config
from confluence_client import ConfluenceClient
from logger import start_logging, stop_logging


def _get_ssl_verify(cfg: dict):
    """Return the ssl_verify value from config (True / False / path string)."""
    v = cfg.get("ssl_verify", True)
    if isinstance(v, bool):
        return v
    if str(v).lower() in ("false", "0", "no"):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return False
    if str(v).lower() in ("true", "1", "yes"):
        return True
    # It's a path string
    p = Path(v).expanduser().resolve()
    return str(p) if p.exists() else True


def check_pandoc():
    print("\n── Pandoc ──────────────────────────────────")
    try:
        result = subprocess.run(["pandoc", "--version"], capture_output=True, text=True)
        version = result.stdout.split("\n")[0]
        print(f"  ✓ {version}")
        return True
    except FileNotFoundError:
        print("  ✗ Pandoc NOT FOUND on PATH")
        print("    Install: https://pandoc.org/installing.html")
        print("    Windows: winget install JohnMacFarlane.Pandoc")
        print("    macOS:   brew install pandoc")
        return False


def _check_git_longpaths() -> bool:
    """
    Check whether git core.longpaths is enabled.
    On Windows this must be true or deep wiki paths will fail to clone.
    Returns True if enabled or not on Windows.
    """
    import platform
    if platform.system() != "Windows":
        return True   # not an issue on macOS / Linux
    try:
        result = subprocess.run(
            ["git", "config", "--global", "core.longpaths"],
            capture_output=True, text=True
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False  # git not found — Pandoc check will catch that


def check_wiki_dir(cfg):
    print("\n── Wiki Directory ──────────────────────────")
    wiki_dir = Path(cfg["wiki_clone_dir"])

    if not wiki_dir.exists():
        print(f"  ✗ Directory not found: {wiki_dir}")
        print(f"    Run:  git clone <your-ado-wiki-url> {wiki_dir.name}")
        return False

    md_files  = list(wiki_dir.rglob("*.md"))
    att_dir   = wiki_dir / ".attachments"
    att_files = list(att_dir.iterdir()) if att_dir.exists() else []

    print(f"  ✓ Directory exists: {wiki_dir}")
    print(f"  ✓ Found {len(md_files)} Markdown (.md) page(s)")

    # ── Long path check (Windows only) ───────────────────────────────────────
    import platform
    if platform.system() == "Windows":
        if _check_git_longpaths():
            print("  ✓ git core.longpaths = true (long filenames supported)")
        else:
            print("  ⚠ git core.longpaths is NOT enabled")
            print("    ADO Wiki pages with long nested paths will be MISSING from")
            print("    the clone, causing --find-path and --include to return 0 results.")
            print()
            print("    Fix (run both commands, then re-clone):")
            print("      git config --global core.longpaths true")
            print("      Run as Administrator: reg add HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f")
            print()
            print("    No admin rights? Use a very short clone path instead:")
            print("      cd C:\\ && git clone <url> w")
            print("      Then set wiki_clone_dir to C:\\w in config.json")

    if att_dir.exists():
        print(f"  ✓ .attachments/ folder: {len(att_files)} file(s)")
    else:
        print(f"  ℹ No .attachments/ folder (ok if your wiki has no images)")

    if md_files:
        print(f"\n  Pages found (first 5):")
        for f in sorted(md_files)[:5]:
            print(f"    • {f.relative_to(wiki_dir)}")
        if len(md_files) > 5:
            print(f"    … and {len(md_files) - 5} more")

    return len(md_files) > 0


def check_confluence(cfg):
    print("\n── Confluence Connection ───────────────────")
    ssl_verify = _get_ssl_verify(cfg)

    if ssl_verify is False:
        print("  ℹ SSL verification disabled (ssl_verify=false in config.json)")

    client = ConfluenceClient(cfg)
    ok     = client.test_connection()

    if not ok:
        # Check if it looks like an SSL error vs auth error
        print("\n  ── SSL Troubleshooting ──────────────────")
        print("  If the error mentions SSL or certificate, run:")
        print("    python fix_ssl.py --config config.json")
        print()
        print("  Or for a quick fix, add this to config.json and retry:")
        print('    "ssl_verify": false')
        return False

    # Test parent page access using the same ssl_verify setting
    try:
        creds   = f"{cfg['confluence_email']}:{cfg['confluence_api_token']}"
        encoded = base64.b64encode(creds.encode()).decode()
        headers = {"Authorization": f"Basic {encoded}", "Accept": "application/json"}
        url     = f"{cfg['confluence_base_url']}/wiki/api/v2/pages/{cfg['confluence_parent_page_id']}"
        resp    = requests.get(url, headers=headers, verify=ssl_verify, timeout=15)

        if resp.status_code == 200:
            page_title = resp.json().get("title", "Unknown")
            print(f"  ✓ Parent page: '{page_title}' (ID: {cfg['confluence_parent_page_id']})")
        elif resp.status_code == 404:
            print(f"  ✗ Parent page NOT FOUND (ID: {cfg['confluence_parent_page_id']})")
            print(f"    Open the target page in Confluence.")
            print(f"    Copy the number from the URL:  /pages/XXXXXXXXX/")
            print(f"    Paste it into confluence_parent_page_id in config.json.")
            ok = False
        else:
            print(f"  ⚠ Parent page check returned HTTP {resp.status_code}")

    except requests.exceptions.SSLError:
        print(f"  ⚠ SSL error on parent page check — run: python fix_ssl.py --config config.json")

    except Exception as e:
        print(f"  ⚠ Could not verify parent page: {e}")

    return ok


def main():
    p = argparse.ArgumentParser(description="Test migration prerequisites")
    p.add_argument("--config", required=True, help="Path to config.json")
    args = p.parse_args()

    print("=" * 60)
    print("  ADO Wiki → Confluence — Prerequisites Check")
    print("=" * 60)

    cfg = load_config(args.config)

    # Start logging if configured
    log_file = cfg.get("log_file", "migration.log")
    if log_file:
        start_logging(log_file)

    results = {
        "Pandoc":     check_pandoc(),
        "Wiki dir":   check_wiki_dir(cfg),
        "Confluence": check_confluence(cfg),
    }

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        icon = "✓" if ok else "✗"
        print(f"  {icon}  {name}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\n  ✓ All checks passed — ready to migrate!\n")
        print(f"  Dry run (5 pages, no upload):")
        print(f"    python migrate.py --config {args.config} --limit 5 --dry-run\n")
        print(f"  PoC (5 pages, real upload):")
        print(f"    python migrate.py --config {args.config} --limit 5\n")
        print(f"  Full migration:")
        print(f"    python migrate.py --config {args.config}")
    else:
        print("\n  ✗ Some checks failed. See details above.")
        if not results.get("Confluence"):
            print("\n  SSL issue? Run:")
            print(f"    python fix_ssl.py --config {args.config}")

    print("=" * 60 + "\n")
    stop_logging()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
