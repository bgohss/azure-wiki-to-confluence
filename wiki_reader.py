"""
wiki_reader.py  —  Read the Git-cloned Azure DevOps Wiki directory
==================================================================
Azure DevOps Wiki stores pages as Markdown files in a Git repository.
The folder structure IS the page hierarchy:

  /                              ← wiki root
  ├── .order                     ← defines display order of root pages
  ├── Home.md
  ├── Getting-Started.md
  ├── Getting-Started/
  │   ├── .order                 ← defines order within this folder
  │   ├── Overview.md
  │   ├── Installation.md
  │   └── Configuration.md
  ├── Products.md
  └── .attachments/
      └── diagram.png

PAGE ORDERING (.order files)
----------------------------
ADO Wiki uses a hidden ".order" file in each folder to control the sidebar
display order.  Each line is a page filename stem (no .md extension) in the
desired order.  Pages missing from .order appear AFTER ordered pages.

This script reads every .order file and sorts pages accordingly, so
Confluence receives pages in the correct creation order — which Confluence
uses as its default display order.

SPECIAL CHARACTERS IN FILENAMES
---------------------------------
ADO encodes page titles in filenames:
  - Spaces       → hyphens         (Getting Started → Getting-Started.md)
  - Percent-encoded chars:
      (  → %28    )  → %29
      &  → %26    #  → %23
      ?  → %3F    '  → %27
  - Literal hyphens in titles are indistinguishable from space-hyphens
    (both stored as "-" in filenames)

This script URL-decodes all percent-encoded sequences so titles display
correctly: "Data-%26-AI" becomes "Data & AI".

SECTION FILTERING
-----------------
See build_manifest() parameters for details.
"""

import os
import re
import urllib.parse
from pathlib import Path


# ── Public entry point ────────────────────────────────────────────────────────

def build_manifest(
    wiki_root:     Path,
    limit:         int       = None,
    include_paths: list[str] = None,
    exclude_paths: list[str] = None,
) -> list[dict]:
    """
    Walk the wiki directory and return a flat list of page dicts in the correct
    display order (as defined by .order files), with parents before children.

    Parameters
    ----------
    wiki_root     : Path to the Git-cloned wiki directory.
    limit         : If set, return only the first N pages (PoC mode).
    include_paths : If set, only include pages whose ado_path starts with
                    one of these prefixes.  e.g. ["/Products", "/Architecture"]
                    Pass None or [] to include everything.
    exclude_paths : Paths to skip even if they match an include prefix.
                    e.g. ["/Products/Archive"]

    Returns a list of dicts, each with:
      ado_path        — e.g. /Products/API-Reference
      title           — human-readable page title (URL-decoded, hyphens→spaces)
      parent_ado_path — parent's ado_path, or None for root-level pages
      md_path         — pathlib.Path to the .md file
      depth           — nesting depth (0 = top-level)
      attachments     — list of {"filename": str} dicts referenced in the page
      order_position  — integer position within its parent (0 = first)
    """
    wiki_root = Path(wiki_root)

    if not wiki_root.exists():
        raise FileNotFoundError(
            f"Wiki directory not found: {wiki_root}\n"
            "  Make sure you have run:  git clone <your-ado-wiki-url> my-wiki"
        )

    includes = _normalise_paths(include_paths or [])
    excludes = _normalise_paths(exclude_paths or [])
    section_roots = _derive_section_roots(includes, wiki_root)

    # ── Collect all pages with their order information ────────────────────────
    all_pages: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(wiki_root):
        # Never descend into .attachments or hidden/underscore directories
        dirnames[:] = sorted([
            d for d in dirnames
            if not d.startswith(".") and not d.startswith("_")
        ])

        current_dir = Path(dirpath)

        # Read the .order file for this directory (defines sidebar order)
        ordered_stems = _read_order_file(current_dir)

        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            if filename.startswith(".") or filename.startswith("_"):
                continue

            md_file  = current_dir / filename
            ado_path = _ado_path_from_file(md_file, wiki_root)
            stem     = Path(filename).stem

            # Apply include/exclude filters
            if not _is_included(ado_path, includes, excludes, section_roots):
                continue

            title = _title_from_filename(stem)
            parts     = ado_path.strip("/").split("/")
            raw_depth = len(parts) - 1

            parent_ado_path = _parent_within_scope(
                ado_path, raw_depth, includes, section_roots
            )

            # Determine the display order position within the parent
            # Pages in .order file get their index; unlisted pages sort last
            try:
                order_pos = ordered_stems.index(stem)
            except ValueError:
                order_pos = 10_000 + sorted(filenames).index(filename)

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                content = ""

            attachments = _find_attachments_for_page(content)

            all_pages.append({
                "ado_path":        ado_path,
                "title":           title,
                "parent_ado_path": parent_ado_path,
                "md_path":         md_file,
                "depth":           raw_depth,
                "attachments":     attachments,
                "order_position":  order_pos,
            })

    # ── Sort: respect .order files, parents before children ───────────────────
    # Primary sort: depth (parents first)
    # Secondary sort: order_position within the same parent (respects .order)
    # Tertiary sort: ado_path alphabetically (stable fallback)
    all_pages.sort(key=lambda p: (
        p["depth"],
        p["parent_ado_path"] or "",
        p["order_position"],
        p["ado_path"],
    ))

    # ── Print summary ─────────────────────────────────────────────────────────
    _print_filter_summary(includes, excludes, len(all_pages))

    if limit and limit < len(all_pages):
        all_pages = all_pages[:limit]
        print(f"  PoC mode: showing first {limit} pages")

    _print_manifest(all_pages)
    return all_pages



def find_paths(wiki_root: Path, search_term: str) -> list[tuple[str, str]]:
    """
    Search the cloned wiki directory for pages whose path or title
    contains search_term (case-insensitive).

    Use this to discover the REAL filesystem path of a page when you only
    know the page title from the ADO Wiki browser URL.

    The ADO Wiki URL shows the page title (e.g. /Target-Page-Name) but
    the actual path in the Git repo may be nested deeper
    (e.g. /Parent-Section/Target-Page-Name).

    Returns a list of (ado_path, title) tuples for matching pages.
    """
    wiki_root = Path(wiki_root)
    matches   = []

    for dirpath, dirnames, filenames in os.walk(wiki_root):
        dirnames[:] = sorted([d for d in dirnames if not d.startswith(".")])
        for fn in sorted(filenames):
            if not fn.endswith(".md") or fn.startswith("."):
                continue
            md_file  = Path(dirpath) / fn
            ado_path = _ado_path_from_file(md_file, wiki_root)
            title    = _title_from_filename(Path(fn).stem)
            if (search_term.lower() in ado_path.lower()
                    or search_term.lower() in title.lower()):
                matches.append((ado_path, title))

    return matches


# ── .order file parsing ───────────────────────────────────────────────────────

def _read_order_file(directory: Path) -> list[str]:
    """
    Read the .order file from a wiki directory.
    Returns a list of filename stems in display order.
    Returns an empty list if the file does not exist.

    .order file format (from ADO):
      Home
      Getting-Started
      Architecture
      Products
    """
    order_file = directory / ".order"
    if not order_file.exists():
        return []
    try:
        lines = order_file.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip()]
    except Exception:
        return []


# ── Title and path helpers ────────────────────────────────────────────────────

def _title_from_filename(stem: str) -> str:
    """
    Convert an ADO wiki filename stem to a human-readable page title.

    ADO encoding rules:
      Spaces        → hyphens in filename  (e.g. "Getting Started" → "Getting-Started")
      Special chars → percent-encoded      (e.g. "Data & AI" → "Data-%26-AI")

    This function reverses both transformations:
      Step 1: URL-decode percent sequences  (%28→(  %26→&  %23→#  %3F→?  %27→')
      Step 2: Replace remaining hyphens with spaces  (hyphens = spaces in ADO)
      Step 3: Collapse multiple consecutive spaces   (triple hyphens → single space)

    Examples:
      "Getting-Started"        → "Getting Started"
      "API-Reference-%28v2%29" → "API Reference (v2)"
      "Data-%26-AI"            → "Data & AI"
      "C%23-Guide"             → "C# Guide"
      "FAQ%3F"                 → "FAQ?"
      "BP-Digital---Overview"  → "BP Digital Overview"
    """
    decoded = urllib.parse.unquote(stem)         # decode %XX sequences
    title   = decoded.replace("-", " ")           # hyphens → spaces
    title   = re.sub(r' {2,}', ' ', title).strip()  # collapse multiple spaces
    return title


def _ado_path_from_file(md_file: Path, wiki_root: Path) -> str:
    """
    wiki_root/Getting-Started/Installation.md  →  /Getting-Started/Installation

    Note: the path KEEPS the original encoded/hyphenated form from the filesystem.
    This is the canonical key used in mapping.json and for link resolution.
    The human-readable title is stored separately.
    """
    rel      = md_file.relative_to(wiki_root)
    path_str = "/" + str(rel.with_suffix("")).replace("\\", "/")
    return path_str


def _find_attachments_for_page(md_content: str) -> list[dict]:
    """
    Extract all attachment filenames referenced in a Markdown page.
    Handles both image ![]() and link []() syntax.
    URL-decodes filenames so they match the actual files in .attachments/.
    """
    pattern = r'[!\[].+?\]\(\.?\.?/?\.attachments/([^)]+)\)'
    seen, unique = set(), []
    for match in re.finditer(pattern, md_content):
        raw      = match.group(1).strip()
        filename = urllib.parse.unquote(raw)   # decode %20 etc in attachment names
        if filename not in seen:
            seen.add(filename)
            unique.append({"filename": filename})
    return unique


# ── Filtering helpers ─────────────────────────────────────────────────────────

def _normalise_paths(paths: list[str]) -> list[str]:
    result = []
    for p in paths:
        p = p.strip().rstrip("/")
        if not p.startswith("/"):
            p = "/" + p
        result.append(p.lower())
    return result


def _derive_section_roots(includes: list[str], wiki_root: Path) -> set[str]:
    roots: set[str] = set()
    for inc in includes:
        parts = inc.strip("/").split("/")
        for i in range(1, len(parts)):
            roots.add("/" + "/".join(parts[:i]))
        roots.add(inc.lower())
    return roots


def _is_included(
    ado_path: str,
    includes: list[str],
    excludes: list[str],
    section_roots: set[str],
) -> bool:
    path_lower = ado_path.lower()
    for exc in excludes:
        if path_lower == exc or path_lower.startswith(exc + "/"):
            return False
    if not includes:
        return True
    if path_lower in section_roots:
        return True
    for inc in includes:
        if path_lower == inc or path_lower.startswith(inc + "/"):
            return True
    return False


def _parent_within_scope(
    ado_path: str,
    raw_depth: int,
    includes: list[str],
    section_roots: set[str],
) -> str | None:
    if raw_depth == 0:
        return None
    parts            = ado_path.strip("/").split("/")
    immediate_parent = "/" + "/".join(parts[:-1])
    if not includes:
        return immediate_parent
    for inc in includes:
        if immediate_parent.lower() == inc or immediate_parent.lower().startswith(inc + "/"):
            return immediate_parent
    if immediate_parent.lower() in section_roots:
        return immediate_parent
    return immediate_parent


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_filter_summary(includes, excludes, count):
    if includes:
        print(f"\n  Section filter — INCLUDE:")
        for p in includes:
            print(f"    + {p}")
    if excludes:
        print(f"  Section filter — EXCLUDE:")
        for p in excludes:
            print(f"    - {p}")
    label = "matched by filter" if (includes or excludes) else "total"
    print(f"  Pages {label}: {count}")


def _print_manifest(pages: list[dict]):
    print(f"\n  Page manifest ({len(pages)} pages):")
    for p in pages:
        indent    = "  " * p["depth"]
        prefix    = "↳ " if p["depth"] > 0 else "• "
        att_note  = f"  [{len(p['attachments'])} att.]" if p["attachments"] else ""
        pos_note  = f"  #{p['order_position']}" if p["order_position"] < 10_000 else "  (unordered)"
        print(f"    {indent}{prefix}{p['title']}{att_note}{pos_note}")
