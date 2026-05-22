"""
validator.py  —  Post-migration validation
==========================================
After migration, this module:
  1. Checks every page in the mapping store was actually created in Confluence
  2. Scans page content for residual ADO-style links (broken links)
  3. Checks for unresolved ⚠ markers left by link_rewriter.py
  4. Produces a clear report of what needs manual attention

Run standalone:
  python migrate.py --config config.json --validate-only
"""

import re
from mapping_store import MappingStore
from confluence_client import ConfluenceClient


def run_validation(store: MappingStore, confluence: ConfluenceClient):
    """
    Run all validation checks and print a report.
    Returns True if everything is clean, False if issues were found.
    """
    print("\n" + "─" * 60)
    print("  PASS 3 — Validation")
    print("─" * 60)

    pages = store.all_pages()
    total = len(pages)

    issues = []
    ok_count = 0

    for i, (ado_path, entry) in enumerate(pages.items(), 1):
        cf_id    = entry["confluence_id"]
        title    = entry["title"]
        progress = f"[{i:>3}/{total}]"

        # Check 1: Fetch the page (confirms it exists)
        try:
            content = confluence.get_page_content(cf_id)
        except Exception as e:
            issues.append({
                "type":    "PAGE_NOT_FOUND",
                "path":    ado_path,
                "title":   title,
                "cf_id":   cf_id,
                "detail":  str(e),
            })
            print(f"  {progress}  ✗ NOT FOUND  {title}  (ID: {cf_id})")
            continue

        # Check 2: Look for residual /wiki/ links that weren't rewritten
        ado_links = re.findall(r'href="(/wiki/[^"]*)"', content)
        ado_links = [l for l in ado_links if "/spaces/" not in l]  # exclude already-converted

        # Check 3: Look for ⚠ markers left by link_rewriter.py
        unresolved_markers = re.findall(
            r'href="([^"]*)"[^>]*title="⚠ UNRESOLVED[^"]*"',
            content
        )

        # Check 4: Look for broken attachment references
        broken_attachments = re.findall(r'<!-- ⚠ Attachment not found: ([^-]+) -->', content)

        page_issues = []
        if ado_links:
            page_issues.append(f"{len(ado_links)} residual ADO link(s): {ado_links[:3]}")
        if unresolved_markers:
            page_issues.append(f"{len(unresolved_markers)} unresolved link(s): {unresolved_markers[:3]}")
        if broken_attachments:
            page_issues.append(f"{len(broken_attachments)} missing attachment(s): {broken_attachments[:3]}")

        if page_issues:
            issues.append({
                "type":   "CONTENT_ISSUES",
                "path":   ado_path,
                "title":  title,
                "cf_id":  cf_id,
                "detail": " | ".join(page_issues),
            })
            print(f"  {progress}  ⚠ ISSUES  {title}")
            for issue in page_issues:
                print(f"              → {issue}")
        else:
            ok_count += 1
            print(f"  {progress}  ✓ OK  {title}")

    # ── Summary report ────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  VALIDATION REPORT")
    print("─" * 60)
    print(f"  Total pages checked:  {total}")
    print(f"  ✓ Clean pages:        {ok_count}")
    print(f"  ⚠ Pages with issues:  {len(issues)}")

    if issues:
        print(f"\n  Issues requiring attention:")
        for issue in issues:
            cf_url = confluence.page_url(issue["cf_id"])
            print(f"\n  • [{issue['type']}] {issue['title']}")
            print(f"    ADO path:       {issue['path']}")
            print(f"    Confluence URL: {cf_url}")
            print(f"    Detail:         {issue['detail']}")

        # Save report to file
        _save_report(issues, confluence)
        print(f"\n  Full report saved to: validation_report.txt")
    else:
        print("\n  ✓ All pages are clean — no issues found!")

    print("─" * 60)
    return len(issues) == 0


def _save_report(issues: list, confluence: ConfluenceClient):
    """Write validation issues to a plain text file for easy sharing."""
    with open("validation_report.txt", "w", encoding="utf-8") as f:
        f.write("ADO Wiki → Confluence Migration — Validation Report\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Issues found: {len(issues)}\n\n")

        for i, issue in enumerate(issues, 1):
            cf_url = confluence.page_url(issue["cf_id"])
            f.write(f"{i}. [{issue['type']}] {issue['title']}\n")
            f.write(f"   ADO path:       {issue['path']}\n")
            f.write(f"   Confluence URL: {cf_url}\n")
            f.write(f"   Detail:         {issue['detail']}\n\n")
