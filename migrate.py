"""
migrate.py  —  Azure DevOps Wiki → Confluence Cloud Migration Tool
==================================================================
MULTI-WIKI SUPPORT
------------------
Each wiki uses its own config file and its own mapping file.

  Method A — Separate config files (recommended):
    python migrate.py --config wiki-products.json
    python migrate.py --config wiki-engineering.json

  Method B — Shared credentials + CLI overrides:
    python migrate.py --config config.json --wiki-dir ./wiki-products    --mapping-file mapping-products.json
    python migrate.py --config config.json --wiki-dir ./wiki-engineering --mapping-file mapping-engineering.json

FINDING THE RIGHT --include PATH
---------------------------------
The ADO Wiki browser URL shows the page title but NOT the full filesystem path.
A page at URL .../12345/Target-Page-Name may actually live at
/Parent-Section/Target-Page-Name in the cloned repo.

  Step 1: Find the real path
    python migrate.py --config config.json --find-path Target-Page-Name

  Step 2: Migrate using the real path
    python migrate.py --config config.json --include /Parent-Section/Target-Page-Name

  Or list all pages to browse the full tree:
    python migrate.py --config config.json --list-pages
"""

import argparse
import sys
import time

from config_loader     import load_config
from logger            import start_logging, stop_logging
from wiki_reader       import build_manifest, find_paths
from confluence_client import ConfluenceClient
from transformer       import convert_markdown
from link_rewriter     import rewrite_links, rewrite_images
from mapping_store     import MappingStore
from validator         import run_validation


# ── CLI arguments ─────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Migrate Azure DevOps Wiki pages to Confluence Cloud",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--config",         required=True,
                   help="Path to config.json")

    # ── Multi-wiki overrides ──────────────────────────────────────────────────
    p.add_argument("--wiki-dir",       default=None, metavar="PATH",
                   help="Override wiki_clone_dir from config.json. "
                        "Use for multiple wikis with one shared credentials config.")
    p.add_argument("--mapping-file",   default=None, metavar="PATH",
                   help="Override mapping_file from config.json. "
                        "Each wiki MUST have its own mapping file.")
    p.add_argument("--parent-page-id", default=None, metavar="ID",
                   help="Override confluence_parent_page_id from config.json.")

    # ── Path discovery ────────────────────────────────────────────────────────
    p.add_argument("--find-path",      default=None, metavar="TEXT",
                   help="Search the wiki for pages whose path or title contains TEXT. "
                        "Prints matching ADO paths and exits — no migration runs. "
                        "Use this when the ADO Wiki browser URL does not reveal the "
                        "true nested filesystem path. "
                        "Example: --find-path Target-Page-Name")

    # ── Migration scope ───────────────────────────────────────────────────────
    p.add_argument("--limit",          type=int, default=None,
                   help="Limit to N pages (PoC mode).")
    p.add_argument("--include",        action="append", default=[], metavar="PATH",
                   help="Only migrate pages under this ADO path. Repeatable. "
                        "Also reads include_paths from config.json.")
    p.add_argument("--exclude",        action="append", default=[], metavar="PATH",
                   help="Skip pages under this ADO path. Repeatable. "
                        "Also reads exclude_paths from config.json.")

    # ── Run modes ─────────────────────────────────────────────────────────────
    p.add_argument("--dry-run",        action="store_true",
                   help="Convert pages but do NOT upload to Confluence.")
    p.add_argument("--pass1-only",     action="store_true",
                   help="Only run Pass 1 (create stubs + upload attachments).")
    p.add_argument("--pass2-only",     action="store_true",
                   help="Only run Pass 2 (content upload). Requires Pass 1 done.")
    p.add_argument("--validate-only",  action="store_true",
                   help="Only run validation against existing Confluence pages.")
    p.add_argument("--list-pages",     action="store_true",
                   help="Print pages that would be migrated, then exit. No uploads.")
    return p.parse_args()


# ── Apply CLI overrides to config ─────────────────────────────────────────────
def _apply_overrides(cfg: dict, args) -> dict:
    """
    CLI flags --wiki-dir, --mapping-file, --parent-page-id override
    the corresponding config.json values. This enables multi-wiki runs
    from a single shared credentials config.
    """
    from pathlib import Path
    if args.wiki_dir:
        cfg["wiki_clone_dir"] = str(Path(args.wiki_dir).expanduser().resolve())
        print(f"  ↳ wiki-dir override:       {cfg['wiki_clone_dir']}")
    if args.mapping_file:
        cfg["mapping_file"] = str(Path(args.mapping_file).expanduser().resolve())
        print(f"  ↳ mapping-file override:   {cfg['mapping_file']}")
    if args.parent_page_id:
        cfg["confluence_parent_page_id"] = args.parent_page_id
        print(f"  ↳ parent-page-id override: {cfg['confluence_parent_page_id']}")
    return cfg


# ── Merge config + CLI section filters ────────────────────────────────────────
def _resolve_filters(cfg: dict, args) -> tuple[list[str], list[str]]:
    cfg_includes = cfg.get("include_paths", []) or []
    cfg_excludes = cfg.get("exclude_paths", []) or []
    includes = list(dict.fromkeys(list(cfg_includes) + list(args.include)))
    excludes = list(dict.fromkeys(list(cfg_excludes) + list(args.exclude)))
    return includes, excludes


# ── Helpers ───────────────────────────────────────────────────────────────────
def print_header(text):
    line = "─" * 60
    print(f"\n{line}\n  {text}\n{line}")


def print_step(n, total, text):
    print(f"  [{n:>3}/{total}]  {text}")


# ── Path discovery ─────────────────────────────────────────────────────────────
def _run_find_path(wiki_dir, search_term: str):
    """
    Search the cloned wiki for pages matching search_term and print their
    real ADO filesystem paths.

    WHY THIS IS NEEDED
    ------------------
    The ADO Wiki browser URL shows the page title as the last URL segment:
      https://dev.azure.com/your-org/your-project/_wiki/wikis/wiki/12345/Target-Page-Name

    But the actual path in the cloned Git repo is often nested much deeper:
      /Parent-Section/Target-Page-Name   ← the REAL path to use in --include

    The URL does not reveal the nesting. This command searches the filesystem.
    """
    from pathlib import Path
    matches = find_paths(Path(wiki_dir), search_term)

    print_header(f'Path search: "{search_term}"')

    if not matches:
        print(f"\n  No pages found containing '{search_term}'.")
        print(f"\n  Suggestions:")
        print(f"  • Try a shorter or partial search term:")
        print(f"      --find-path IAM")
        print(f"  • List ALL pages in the wiki to browse the full tree:")
        print(f"      python migrate.py --config <config> --list-pages")
        return

    print(f"\n  Found {len(matches)} matching page(s):\n")
    for ado_path, title in matches:
        print(f"    Path:   {ado_path}")
        print(f"    Title:  {title}")
        print()

    # Suggest the shallowest match as the likely section root
    shallowest = min(matches, key=lambda m: m[0].count("/"))
    print(f"  Suggested --include value (section root):")
    print(f'    --include "{shallowest[0]}"')
    print()
    print(f"  Or add permanently to config.json:")
    print(f'    "include_paths": ["{shallowest[0]}"]')


# ── PASS 1: Create page stubs + upload attachments ────────────────────────────
def run_pass1(manifest, confluence, store, attachments_dir, dry_run, base_url="", space_key=""):
    print_header(f"PASS 1 — Creating {len(manifest)} page stubs in Confluence")

    # ── Pre-flight: detect pages whose parent is missing ──────────────────────
    # A page's parent can be missing if:
    #   - The parent .md was not cloned (Windows long-path issue)
    #   - The parent was outside the --include scope
    #   - The parent save failed in a previous run (not in mapping.json)
    manifest_paths = {p["ado_path"] for p in manifest}
    orphans = []
    for page in manifest:
        parent = page["parent_ado_path"]
        if parent and parent not in manifest_paths:
            already_mapped = store.get_page_id(parent) is not None
            if not already_mapped:
                orphans.append((page["ado_path"], parent))

    if orphans:
        print(f"\n  ⚠ PRE-FLIGHT WARNING: {len(orphans)} page(s) have a missing parent.")
        print(f"  These pages will be placed under the ROOT page instead of their correct parent.")
        print(f"  Most likely cause: parent .md file missing from git clone (Windows long-path).")
        print()
        for child, missing_parent in orphans[:10]:   # show first 10
            print(f"    Child:          {child}")
            print(f"    Missing parent: {missing_parent}")
            print()
        if len(orphans) > 10:
            print(f"    ... and {len(orphans) - 10} more.")
        print(f"  Recommended fix:")
        print(f"    1. Run:  git config --global core.longpaths true")
        print(f"    2. Enable Windows long paths (see installation guide Section 2B)")
        print(f"    3. Delete the partial wiki clone and re-clone")
        print(f"    4. Delete mapping.json and re-run the migration")
        print()

    for i, page in enumerate(manifest):
        label = f"{page['title']}  ({page['ado_path']})"

        if store.get_page_id(page["ado_path"]):
            print_step(i + 1, len(manifest), f"SKIP (already migrated)  {label}")
            continue

        print_step(i + 1, len(manifest), f"Creating stub  →  {label}")

        if not dry_run:
            parent_cf_id = None
            if page["parent_ado_path"]:
                parent_cf_id = store.get_page_id(page["parent_ado_path"])
                if parent_cf_id is None:
                    # Parent not in mapping.json — it was either not migrated yet,
                    # its save failed in a previous run, or its .md file was missing
                    # from the git clone (Windows long-path issue).
                    # Confluence will place this page under the root parent page
                    # instead of under its correct parent — hierarchy will be wrong.
                    print(f"         ⚠ PARENT MISSING from mapping.json: {page['parent_ado_path']}")
                    print(f"           This page will be created under the ROOT parent page.")
                    print(f"           Causes: (1) parent not yet migrated in this run,")
                    print(f"                   (2) parent .md file missing from git clone (Windows long-path),")
                    print(f"                   (3) parent save failed in a previous run.")
                    print(f"           Fix: check mapping.json for '{page['parent_ado_path']}'")
                    print(f"                If missing: delete mapping.json and re-run from scratch,")
                    print(f"                or move the misplaced page manually in Confluence.")

            # Synthetic pages (folder-only nodes with no .md file) get a
            # descriptive body explaining they are section containers.
            if page.get("synthetic"):
                stub_body = (
                    f"<p><em>This page was automatically created as a section container. "
                    f"The original Azure DevOps Wiki folder "
                    f"<code>{page['ado_path']}</code> existed as a directory "
                    f"without a corresponding page file.</em></p>"
                )
            else:
                stub_body = "<p><em>Migration in progress…</em></p>"

            cf_id, actual_title = confluence.create_page(
                title=page["title"],
                parent_id=parent_cf_id,
                body=stub_body,
            )
            # Save the ACTUAL title used (may have a suffix if there was a conflict)
            # Pass base_url + space_key so the stored URL includes the space key
            store.save_page(
                page["ado_path"], cf_id, actual_title,
                base_url=base_url,
                space_key=space_key,
            )

            for att in page.get("attachments", []):
                att_path = attachments_dir / att["filename"]
                if att_path.exists():
                    print(f"         ↳ Uploading attachment: {att['filename']}")
                    url = confluence.upload_attachment(cf_id, att_path)
                    store.save_attachment(att["filename"], url, cf_id)
                else:
                    print(f"         ↳ WARNING: attachment not found: {att_path}")

            time.sleep(0.3)
        else:
            print(f"         [DRY RUN — would create stub and upload attachments]")

    print(f"\n  ✓ Pass 1 complete. {len(manifest)} pages processed.")


# ── PASS 2: Convert + rewrite + upload content ────────────────────────────────
def run_pass2(manifest, confluence, store, dry_run):
    print_header(f"PASS 2 — Converting & uploading content for {len(manifest)} pages")

    errors = []
    for i, page in enumerate(manifest):
        print_step(i + 1, len(manifest), f"Converting  →  {page['title']}")

        try:
            # Skip synthetic pages (folder-only nodes) — they have no .md file.
            # Their stub body was already set correctly in Pass 1.
            if page.get("synthetic"):
                print(f"         [synthetic stub — no content to upload]")
                continue

            raw_md      = page["md_path"].read_text(encoding="utf-8")
            storage_xml = convert_markdown(raw_md)
            storage_xml = rewrite_links(storage_xml, store)
            storage_xml = rewrite_images(storage_xml, store)

            if not dry_run:
                cf_id = store.get_page_id(page["ado_path"])
                if not cf_id:
                    print(f"         WARNING: no Confluence ID for {page['ado_path']} — skipping")
                    errors.append(page["ado_path"])
                    continue
                # Use the title stored in mapping.json — may differ from manifest
                # if a suffix was added during Pass 1 to resolve a title conflict
                stored_title = store.get_page_title(page["ado_path"]) or page["title"]
                confluence.update_page(cf_id, stored_title, storage_xml)
                time.sleep(0.3)
            else:
                print(f"         [DRY RUN — converted {len(storage_xml)} chars, would upload]")

        except Exception as e:
            print(f"         ERROR processing {page['ado_path']}: {e}")
            errors.append(page["ado_path"])

    print(f"\n  ✓ Pass 2 complete. Errors: {len(errors)}")
    if errors:
        print("  Pages with errors:")
        for e in errors:
            print(f"    - {e}")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()

    print("\n" + "=" * 60)
    print("  Azure DevOps Wiki → Confluence Migration Tool")
    print("=" * 60)

    cfg = load_config(args.config)
    cfg = _apply_overrides(cfg, args)

    from pathlib import Path
    wiki_dir        = Path(cfg["wiki_clone_dir"])
    attachments_dir = wiki_dir / ".attachments"

    # ── Start logging to file ────────────────────────────────────────────────
    log_file = cfg.get("log_file", "migration.log")
    if log_file:
        start_logging(log_file)

    # ── --find-path: runs immediately — no Confluence connection needed ────────
    # Must be checked BEFORE initialising MappingStore/ConfluenceClient so that
    # the SSL warning and mapping file are not touched for a simple path search.
    if args.find_path:
        _run_find_path(wiki_dir, args.find_path)
        return

    includes, excludes = _resolve_filters(cfg, args)

    if includes:
        print(f"\n  SECTION FILTER (include):")
        for p in includes:
            print(f"    + {p}")
    if excludes:
        print(f"  SECTION FILTER (exclude):")
        for p in excludes:
            print(f"    - {p}")
    if args.limit:
        print(f"\n  PoC MODE: first {args.limit} pages only")
    if args.dry_run:
        print("  DRY RUN: no uploads will occur")

    store      = MappingStore(cfg["mapping_file"])
    confluence = ConfluenceClient(cfg)

    if args.validate_only:
        run_validation(store, confluence)
        return

    # Build manifest
    print_header("Building page manifest")
    manifest = build_manifest(
        wiki_root     = wiki_dir,
        limit         = args.limit,
        include_paths = includes,
        exclude_paths = excludes,
    )

    if not manifest:
        print("\n  ✗ No pages matched your filter.")
        print()
        print("  The ADO Wiki browser URL shows the page title, but the ACTUAL")
        print("  filesystem path in the cloned repo is often nested deeper.")
        print()
        print("  Example:")
        print("    URL shows:     .../12345/Target-Page-Name")
        print("    Real path:     /Parent-Section/Target-Page-Name")
        print()
        print("  Step 1 — Find the real path:")
        search_hint = includes[0].strip("/") if includes else "your-page-name"
        print(f"    python migrate.py --config {args.config} --find-path {search_hint}")
        print()
        print("  Step 2 — Use the real path:")
        print(f"    python migrate.py --config {args.config} --include /Real/Path/Here")
        print()
        print("  Or list ALL pages to browse the full wiki tree:")
        print(f"    python migrate.py --config {args.config} --list-pages")
        sys.exit(1)

    if args.list_pages:
        print(f"\n  Total pages that would be migrated: {len(manifest)}")
        return

    if args.pass2_only:
        run_pass2(manifest, confluence, store, args.dry_run)
    elif args.pass1_only:
        run_pass1(manifest, confluence, store, attachments_dir, args.dry_run,
                  base_url=cfg["confluence_base_url"], space_key=cfg["confluence_space_key"])
    else:
        run_pass1(manifest, confluence, store, attachments_dir, args.dry_run,
                  base_url=cfg["confluence_base_url"], space_key=cfg["confluence_space_key"])
        run_pass2(manifest, confluence, store, args.dry_run)
        if not args.dry_run:
            run_validation(store, confluence)

    print("\n" + "=" * 60)
    print("  Migration complete!")
    print(f"  Config:  {args.config}")
    print(f"  Wiki:    {cfg['wiki_clone_dir']}")
    print(f"  Mapping: {cfg['mapping_file']}")
    log_file = cfg.get("log_file", "migration.log")
    if log_file:
        print(f"  Log:     {log_file}")
    print("=" * 60 + "\n")
    stop_logging()


if __name__ == "__main__":
    main()
