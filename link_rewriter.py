"""
link_rewriter.py  —  Rewrite internal links and image references
================================================================
This is the most important post-processing step.

After Pandoc converts Markdown → Confluence Storage Format, all internal
wiki links still point to ADO wiki paths like /Getting-Started/Installation.

This module:
  1. Rewrites internal links → Confluence page URLs (using mapping.json)
  2. Rewrites image src attributes → Confluence attachment URLs (using mapping.json)
  3. Converts <img> tags → Confluence <ac:image> macro format (for native rendering)
  4. Reports any links that could NOT be resolved (so you can fix them manually)
"""

import re
import urllib.parse
from mapping_store import MappingStore


# ── Link rewriting ────────────────────────────────────────────────────────────

def rewrite_links(xml: str, store: MappingStore) -> str:
    """
    Find all <a href="..."> tags in the Confluence Storage Format XML
    and replace internal wiki hrefs with real Confluence page URLs.

    Handles these ADO link formats:
      /Getting-Started/Installation          (absolute path)
      ./Sibling-Page                         (relative to current)
      ../Other-Section/Page                  (parent-relative)
      /wiki/Getting-Started/Installation     (with /wiki prefix)
      /Getting%20Started/Installation        (URL-encoded spaces)
    """
    unresolved = []

    def replace_href(match):
        full_tag  = match.group(0)
        href      = match.group(1)
        link_text = match.group(2)

        # Skip external links (http/https/mailto)
        if href.startswith(("http://", "https://", "mailto:", "#", "ftp://")):
            return full_tag

        # Skip already-converted Confluence links
        if "/wiki/spaces/" in href:
            return full_tag

        # Skip ADO special directives — these should have been converted to
        # Confluence macros by transformer.py, but filter here as a safety net.
        # Showing them as unresolved links would be misleading.
        _ADO_SPECIAL = ("_TOC_", "_TOSP_", "_TSLM_", "_TSLP_")
        if any(d in href for d in _ADO_SPECIAL):
            # Return just the link text, stripping the unresolvable href
            return f"<em>[Table of contents — see Confluence TOC macro]</em>"

        # Normalise the path
        normalised = _normalise_ado_path(href)

        # Look up in mapping store
        cf_id = store.get_page_id(normalised)

        # Try title-based lookup as fallback (for [[WikiLink]] style)
        if not cf_id:
            title_guess = normalised.split("/")[-1].replace("-", " ")
            cf_id = store.get_page_by_title(title_guess)

        if cf_id:
            # Use the full stored URL if available (includes space key)
            # Fall back to just the page ID path if URL not stored
            stored_url = store.get_page_url(cf_id)
            cf_url = stored_url if stored_url else f"/wiki/pages/{cf_id}"
            return f'<a href="{cf_url}">{link_text}</a>'
        else:
            unresolved.append(href)
            # Leave the link but add a visual marker so it's easy to spot
            return f'<a href="{href}" title="⚠ UNRESOLVED — update after migration">{link_text} ⚠</a>'

    # Match <a href="...">...</a> — non-greedy to handle multiple links per line
    xml = re.sub(
        r'<a href="([^"]*)">(.*?)</a>',
        replace_href,
        xml,
        flags=re.DOTALL
    )

    if unresolved:
        print(f"         ⚠ {len(unresolved)} unresolved link(s):")
        for u in set(unresolved):
            print(f"            - {u}")

    return xml


def _normalise_ado_path(href: str) -> str:
    """
    Normalise an ADO wiki href to the canonical form stored in mapping.json.

    Handles all ADO percent-encoding and relative path formats:
      ./Sibling-Page             → /Sibling-Page
      ../Parent/Other            → /Parent/Other
      /wiki/Getting%20Started    → /Getting Started
      /API-Reference-%28v2%29    → /API-Reference-(v2)
      /Data-%26-AI/Overview      → /Data-&-AI/Overview
      /C%23-Guide                → /C#-Guide        (# is part of the page name)
      /Page-Title#section-one    → /Page-Title       (lowercase anchor stripped)
      /Page-Title#Introduction   → /Page-Title#Introduction  (uppercase = name char)
    """
    # URL-decode all percent-encoded sequences
    # %20→space  %28→(  %29→)  %26→&  %23→#  %3F→?  %27→'  etc.
    href = urllib.parse.unquote(href)

    # Strip /wiki prefix that ADO sometimes includes in generated links
    href = re.sub(r'^/wiki(?=/|$)', '', href)

    # Normalise relative path prefixes
    href = re.sub(r'^\./+', '/', href)    # ./Page  →  /Page
    href = re.sub(r'^\.\./', '/', href)   # ../Page  →  /Page  (simplistic)

    # Ensure leading slash
    if not href.startswith('/'):
        href = '/' + href

    # Remove trailing slash
    href = href.rstrip('/')

    # Smart anchor stripping:
    # ADO/Pandoc heading anchors are all-lowercase-with-hyphens (e.g. #getting-started)
    # Page names can contain # (e.g. C# Programming) — these are NOT anchors
    # Rule: only strip the fragment if it is all-lowercase letters, digits, and hyphens
    if '#' in href:
        path_part, fragment = href.split('#', 1)
        is_heading_anchor = bool(re.match(r'^[a-z0-9][a-z0-9\-]*$', fragment))
        if is_heading_anchor and path_part:
            href = path_part
        # else: the # is part of the page path (e.g. "C# Guide") — preserve it

    return href


def rewrite_images(xml: str, store: MappingStore) -> str:
    """
    Replace <img src=".attachments/filename.png"> tags with Confluence's
    native <ac:image> macro format, using the uploaded attachment URLs
    stored in mapping.json.

    Two formats handled:
      1. Standard HTML img tag from Pandoc output
      2. Already-converted ac:image macros (left alone)
    """
    unresolved_images = []

    def replace_img(match):
        full_tag = match.group(0)
        src      = match.group(1)
        alt      = match.group(2) if match.group(2) else ""

        # Skip external images
        if src.startswith(("http://", "https://")):
            return full_tag

        # Extract filename from .attachments/filename.png
        filename = _extract_attachment_filename(src)
        if not filename:
            return full_tag

        # Look up the Confluence attachment URL
        cf_url = store.get_attachment_url(filename)

        if cf_url:
            # Use Confluence ac:image macro for native rendering
            # This is better than a plain <img> tag in Confluence
            return _make_confluence_image_macro(filename, alt)
        else:
            unresolved_images.append(filename)
            # Leave as plain img with a warning comment
            return f'<!-- ⚠ Attachment not found: {filename} --><img src="{src}" alt="{alt}" />'

    # Match <img src="..." alt="..." /> and variations
    xml = re.sub(
        r'<img\s+src="([^"]*)"(?:\s+alt="([^"]*)")?[^>]*/?>',
        replace_img,
        xml
    )

    # Also handle <img alt="..." src="..."> (attribute order reversed)
    xml = re.sub(
        r'<img\s+alt="([^"]*)"(?:\s+src="([^"]*)")?[^>]*/?>',
        lambda m: replace_img_alt_first(m, store, unresolved_images),
        xml
    )

    if unresolved_images:
        print(f"         ⚠ {len(unresolved_images)} unresolved image(s):")
        for u in set(unresolved_images):
            print(f"            - {u}")

    return xml


def replace_img_alt_first(match, store, unresolved_images):
    """Handle <img alt='...' src='...'> format (alt before src)."""
    alt = match.group(1) or ""
    src = match.group(2) or ""
    filename = _extract_attachment_filename(src)
    if not filename:
        return match.group(0)
    cf_url = store.get_attachment_url(filename)
    if cf_url:
        return _make_confluence_image_macro(filename, alt)
    else:
        unresolved_images.append(filename)
        return f'<!-- ⚠ Attachment not found: {filename} --><img src="{src}" alt="{alt}" />'


def _extract_attachment_filename(src: str) -> str | None:
    """
    Extract just the filename from an attachment src path.
    '.attachments/diagram.png' → 'diagram.png'
    './.attachments/my file.png' → 'my file.png'
    """
    # URL-decode
    src = urllib.parse.unquote(src)

    # Match .attachments/ pattern
    match = re.search(r'\.?attachments/(.+)$', src)
    if match:
        return match.group(1).strip()

    # If the src is just a filename with no path (sometimes happens)
    if "/" not in src and "." in src:
        return src

    return None


def _make_confluence_image_macro(filename: str, alt: str = "") -> str:
    """
    Build a Confluence ac:image macro for an attached image.
    This renders natively in Confluence with proper sizing controls.
    """
    alt_attr = f' ac:alt="{alt}"' if alt else ""
    return (
        f'<ac:image{alt_attr}>'
        f'<ri:attachment ri:filename="{filename}" />'
        f'</ac:image>'
    )
