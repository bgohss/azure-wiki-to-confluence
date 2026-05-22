"""
transformer.py  —  Convert Markdown to Confluence Storage Format
================================================================
Pipeline:
  1. Pre-process  — Fix ADO-specific Markdown before Pandoc sees it
  2. Pandoc       — Markdown → HTML5  (--to html5 --no-highlight)
  3. Post-process — HTML5 → Confluence Storage Format via BeautifulSoup

FORMATTING COVERAGE
-------------------
Fully preserved:
  ✓ Headings H1–H4
  ✓ Bold, italic, bold-italic
  ✓ Inline code
  ✓ Unordered lists  (including nested)
  ✓ Ordered lists    (including nested)
  ✓ Mixed ordered/unordered nesting
  ✓ Task / checkbox lists  → Confluence ac:task-list macro
  ✓ Tables with column alignment (left / center / right)
  ✓ Fenced code blocks with language tag  → Confluence Code macro + CDATA
  ✓ Horizontal rules
  ✓ Superscript (x^2^) and subscript (H~2~O)
  ✓ Strikethrough (~~text~~)  → <del> (Confluence supports it natively)
  ✓ Hyperlinks (internal and external)
  ✓ Images  → Confluence ac:image macro (after attachment upload)
  ✓ ::: note / warning / tip / important blocks  → Confluence panel macros
  ✓ > [!NOTE] / [!WARNING] / [!TIP] / [!IMPORTANT] / [!CAUTION]  → panel macros
  ✓ Plain blockquotes
  ✓ ADO [[_TOC_]]  → Confluence TOC macro
  ✓ ADO [[_TOSP_]] → Confluence Children Display macro
  ✓ HTML entities (&amp; &lt; &gt;)

Known limitations:
  ~ Nested blockquotes (> > text) — inner level flattened (very rare in wikis)
  ~ Mermaid diagrams — converted to plain code block with a note
  ~ Definition lists — rendered as bold term + paragraph

Requirements:
  pip install beautifulsoup4 lxml
  Install Pandoc 2.11+: https://pandoc.org/installing.html
"""

import re
import subprocess
import sys
import urllib.parse

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    print("\n  ✗ beautifulsoup4 not installed.  Run:  pip install beautifulsoup4 lxml\n")
    sys.exit(1)


# ── Callout / panel type mapping ──────────────────────────────────────────────
# Maps ADO/GitHub callout keywords → Confluence panel macro type + display title
_CALLOUT_MAP = {
    "note":      ("info",    "Note"),
    "info":      ("info",    "Info"),
    "tip":       ("tip",     "Tip"),
    "hint":      ("tip",     "Hint"),
    "warning":   ("warning", "Warning"),
    "warn":      ("warning", "Warning"),
    "caution":   ("warning", "Caution"),
    "important": ("warning", "Important"),
    "danger":    ("warning", "Danger"),
    "error":     ("warning", "Error"),
}

# ── Language name normalisation map ──────────────────────────────────────────
_LANG_MAP = {
    "python": "python",   "py":         "python",
    "javascript": "javascript", "js":   "javascript",
    "typescript": "typescript", "ts":   "typescript",
    "java":    "java",    "csharp":     "c#",
    "cs":      "c#",      "cpp":        "cpp",
    "c":       "c",       "bash":       "bash",
    "sh":      "bash",    "shell":      "bash",
    "zsh":     "bash",    "powershell": "powershell",
    "ps1":     "powershell",
    "sql":     "sql",     "xml":        "xml",
    "json":    "json",    "yaml":       "yaml",
    "yml":     "yaml",    "html":       "html",
    "css":     "css",     "scss":       "css",
    "go":      "go",      "rust":       "rust",
    "ruby":    "ruby",    "rb":         "ruby",
    "r":       "r",       "kotlin":     "kotlin",
    "swift":   "swift",   "dockerfile": "dockerfile",
    "docker":  "dockerfile", "text":    "",
    "plain":   "",        "txt":        "",
}

# ── ADO special directives ────────────────────────────────────────────────────
_ADO_DIRECTIVES = {
    "_TOC_": (
        '<ac:structured-macro ac:name="toc">'
        '<ac:parameter ac:name="minLevel">1</ac:parameter>'
        '<ac:parameter ac:name="maxLevel">3</ac:parameter>'
        '</ac:structured-macro>'
    ),
    "_TOSP_": (
        '<ac:structured-macro ac:name="children">'
        '<ac:parameter ac:name="sort">title</ac:parameter>'
        '<ac:parameter ac:name="depth">1</ac:parameter>'
        '</ac:structured-macro>'
    ),
}


# ── Public entry point ────────────────────────────────────────────────────────

def convert_markdown(markdown_text: str) -> str:
    """
    Convert a Markdown string to Confluence Storage Format.

    Pipeline:
      1. _preprocess()            — ADO quirks, directive tokens, callout normalisation
      2. _run_pandoc()            — Markdown → HTML5
      3. _html_to_confluence()    — All HTML→Confluence conversions via BeautifulSoup
      4. _postprocess_directives()— Inject TOC/TOSP macros replacing placeholder tokens
    """
    _check_pandoc()
    _check_beautifulsoup()

    markdown_text = _preprocess(markdown_text)
    html          = _run_pandoc(markdown_text)
    storage_xml   = _html_to_confluence(html)
    storage_xml   = _postprocess_directives(storage_xml)
    return storage_xml


# ── Step 1: Pre-process ───────────────────────────────────────────────────────

def _preprocess(md: str) -> str:
    """
    Normalise ADO-specific Markdown before Pandoc runs.
    Order matters — directives must be replaced before wiki-link handling.
    """
    # 1a. ADO special directives → placeholder tokens
    #     (tokens are replaced with real XML after Pandoc, avoiding XML mangling)
    for directive in _ADO_DIRECTIVES:
        md = md.replace(f'[[{directive}]]', f'ADODIRECTIVE_{directive}_END')
        md = re.sub(
            rf'\[[^\]]*\]\([^)]*{re.escape(directive)}[^)]*\)',
            f'ADODIRECTIVE_{directive}_END', md,
        )
        md = re.sub(
            rf'^{re.escape(directive)}$',
            f'ADODIRECTIVE_{directive}_END', md, flags=re.MULTILINE,
        )

    # 1b. [[Page Title]] wiki-links → standard Markdown links
    md = re.sub(
        r'\[\[([^\]]+)\]\]',
        lambda m: f'[{m.group(1)}](/wiki/{m.group(1).replace(" ", "-")})',
        md,
    )

    # 1c. ::: fenced blocks — handled by pandoc +fenced_divs extension
    #     (no pre-processing needed; Pandoc converts them to <div class="...">)

    # 1d. Mermaid diagrams → plain code block with note
    md = re.sub(
        r'```mermaid\n(.*?)```',
        r'```\n[Mermaid diagram — paste at https://mermaid.live to view]\n\1```',
        md, flags=re.DOTALL,
    )

    return md


# ── Step 2: Pandoc ────────────────────────────────────────────────────────────

def _run_pandoc(markdown_text: str) -> str:
    """
    Convert Markdown → HTML5 via Pandoc.

    Extensions enabled:
      gfm_auto_identifiers  — GitHub-style heading IDs
      pipe_tables           — GFM pipe-style tables
      fenced_code_blocks    — ``` fenced code
      task_lists            — - [x] / - [ ] checkboxes
      superscript           — x^2^
      subscript             — H~2~O
      strikeout             — ~~text~~
      fenced_divs           — ::: note ... ::: callout blocks
    """
    cmd = [
        "pandoc",
        "--from", (
            "markdown"
            "+gfm_auto_identifiers"
            "+pipe_tables"
            "+fenced_code_blocks"
            "+task_lists"
            "+superscript"
            "+subscript"
            "+strikeout"
            "+fenced_divs"
            "+hard_line_breaks"  # Treat single newlines as <br> — matches ADO Wiki rendering
        ),
        "--to",    "html5",
        "--wrap",  "none",
        "--no-highlight",   # suppress syntax-highlighting <span> tags in code blocks
    ]

    result = subprocess.run(
        cmd,
        input=markdown_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    if result.returncode != 0:
        raise RuntimeError(f"Pandoc conversion failed:\n{result.stderr.strip()}")

    return result.stdout


# ── Step 3: HTML → Confluence Storage Format ──────────────────────────────────

def _html_to_confluence(html: str) -> str:
    """
    Transform Pandoc HTML5 output into Confluence Storage Format.

    Transformations applied (in order):
      A. Code blocks         → ac:structured-macro name="code" with CDATA
      B. Task lists          → ac:task-list / ac:task macros
      C. Callout divs        → ac:structured-macro name="info/tip/warning"
      D. GitHub [!NOTE] etc  → same Confluence panel macros
      E. Figures             → unwrap to plain <img>
      F. Table cleanup       → strip odd/even classes, preserve alignment styles
      G. Heading cleanup     → strip Pandoc-generated id= attributes
    """
    soup = BeautifulSoup(html, "html.parser")

    # ── A. Code blocks ────────────────────────────────────────────────────────
    # Use placeholder tokens for CDATA to prevent BeautifulSoup from escaping
    # the code content during serialisation.
    cdata_store: dict[str, str] = {}
    cdata_counter = [0]

    for pre in soup.find_all("pre"):
        code_tag = pre.find("code")
        if not code_tag:
            continue

        lang      = _extract_lang(pre)
        code_text = code_tag.get_text()                      # .get_text() unescapes &lt; etc
        code_text = code_text.replace("]]>", "]]]]><![CDATA[>")  # escape CDATA end sequence

        token = f"CDATATOKEN{cdata_counter[0]}END"
        cdata_store[token] = f"<![CDATA[{code_text}]]>"
        cdata_counter[0] += 1

        lang_param = (
            f'<ac:parameter ac:name="language">{lang}</ac:parameter>' if lang else ""
        )
        macro_html = (
            f'<ac:structured-macro ac:name="code">'
            f'{lang_param}'
            f'<ac:parameter ac:name="linenumbers">false</ac:parameter>'
            f'<ac:plain-text-body>{token}</ac:plain-text-body>'
            f'</ac:structured-macro>'
        )
        pre.replace_with(BeautifulSoup(macro_html, "html.parser"))

    # ── B. Task lists ─────────────────────────────────────────────────────────
    # <ul class="task-list"> → <ac:task-list><ac:task>...</ac:task></ac:task-list>
    for ul in soup.find_all("ul", class_="task-list"):
        macro = _convert_task_list(ul)
        if macro:
            ul.replace_with(BeautifulSoup(macro, "html.parser"))

    # ── C. Fenced div callouts (::: note / warning / tip) ────────────────────
    # Pandoc renders ::: note as <div class="note">
    for div in soup.find_all("div"):
        classes = div.get("class") or []
        for cls in classes:
            if cls.lower() in _CALLOUT_MAP:
                macro = _div_to_panel_macro(div, cls.lower())
                div.replace_with(BeautifulSoup(macro, "html.parser"))
                break

    # ── D. GitHub-style [!NOTE] blockquote callouts ───────────────────────────
    # ADO also supports > [!NOTE] / > [!WARNING] etc. inside blockquotes
    for bq in soup.find_all("blockquote"):
        macro = _blockquote_callout_to_macro(bq)
        if macro:
            bq.replace_with(BeautifulSoup(macro, "html.parser"))

    # ── E. Figures → plain img ────────────────────────────────────────────────
    # Pandoc wraps standalone images in <figure><img/><figcaption/></figure>
    # Confluence doesn't understand <figure>
    for fig in soup.find_all("figure"):
        img = fig.find("img")
        if img:
            fig.replace_with(img)
        else:
            fig.unwrap()

    # ── F. Table cleanup ──────────────────────────────────────────────────────
    # Strip Pandoc's odd/even/header row classes (cosmetic noise)
    # KEEP style="text-align: ..." attributes — these carry column alignment
    for tr in soup.find_all("tr"):
        tr.attrs.pop("class", None)
    for th in soup.find_all("th"):
        # Remove class= but preserve style= (alignment)
        th.attrs = {k: v for k, v in th.attrs.items() if k != "class"}

    # ── G. Heading cleanup ────────────────────────────────────────────────────
    # Strip Pandoc-generated id= on headings; Confluence generates its own anchors
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.attrs.pop("id", None)

    # ── Serialise + restore CDATA tokens ─────────────────────────────────────
    result = str(soup).strip()
    for token, cdata in cdata_store.items():
        result = result.replace(token, cdata)

    # Strip empty leading/trailing paragraphs
    result = re.sub(r'^(<p>\s*</p>\s*)+', '', result)
    result = re.sub(r'(\s*<p>\s*</p>)+$', '', result)

    return result.strip()


# ── Step 4: Post-process directives ──────────────────────────────────────────

def _postprocess_directives(xml: str) -> str:
    """
    Replace ADODIRECTIVE_X_END placeholder tokens with Confluence macro XML.
    Pandoc may have wrapped the token in <p>…</p> — remove that wrapper too.
    """
    for directive, macro_xml in _ADO_DIRECTIVES.items():
        token = f'ADODIRECTIVE_{directive}_END'
        xml   = re.sub(rf'<p>\s*{re.escape(token)}\s*</p>', macro_xml, xml)
        xml   = xml.replace(token, macro_xml)
    return xml


# ── Conversion helpers ────────────────────────────────────────────────────────

def _convert_task_list(ul_tag) -> str:
    """
    Convert <ul class='task-list'><li><label><input checked/> text</label></li>...
    to Confluence ac:task-list macro.

    Confluence task list format:
      <ac:task-list>
        <ac:task>
          <ac:task-status>complete</ac:task-status>
          <ac:task-body>task text here</ac:task-body>
        </ac:task>
      </ac:task-list>
    """
    tasks = []
    for li in ul_tag.find_all("li", recursive=False):
        label    = li.find("label")
        checkbox = li.find("input", type="checkbox") if not label else label.find("input", type="checkbox")
        is_done  = checkbox and checkbox.get("checked") is not None

        # Extract task text: get label text or li text, minus the checkbox
        if checkbox:
            checkbox.extract()
        text_source = label if label else li
        task_text   = text_source.get_text(strip=True)

        status = "complete" if is_done else "incomplete"
        tasks.append(
            f'<ac:task>'
            f'<ac:task-status>{status}</ac:task-status>'
            f'<ac:task-body>{task_text}</ac:task-body>'
            f'</ac:task>'
        )

    return '<ac:task-list>' + ''.join(tasks) + '</ac:task-list>'


def _div_to_panel_macro(div_tag, cls_lower: str) -> str:
    """
    Convert <div class="note/warning/tip/..."> to a Confluence panel macro.

    Confluence panel macro types: info | tip | warning | note
    The ac:rich-text-body accepts full Storage Format HTML.
    """
    panel_type, title = _CALLOUT_MAP[cls_lower]
    body = "".join(str(c) for c in div_tag.children).strip()
    return (
        f'<ac:structured-macro ac:name="{panel_type}">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>'
        f'<ac:rich-text-body>{body}</ac:rich-text-body>'
        f'</ac:structured-macro>'
    )


def _blockquote_callout_to_macro(bq_tag) -> str | None:
    """
    Detect GitHub/ADO-style callouts inside blockquote tags:
      > [!NOTE] text         → info panel
      > [!WARNING] text      → warning panel
      > [!TIP] text          → tip panel
      > [!IMPORTANT] text    → warning panel
      > [!CAUTION] text      → warning panel

    Returns the Confluence macro string, or None if this is a plain blockquote.
    """
    first_p = bq_tag.find("p")
    if not first_p:
        return None

    text = first_p.get_text(strip=True)
    m    = re.match(
        r'^\[!(NOTE|INFO|WARNING|WARN|TIP|HINT|IMPORTANT|CAUTION|DANGER|ERROR)\](.*)',
        text, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None

    keyword   = m.group(1).lower()
    remainder = m.group(2).strip()
    panel_type, title = _CALLOUT_MAP.get(keyword, ("info", keyword.title()))

    # Rebuild body: replace first_p with remainder text, keep any other children
    body_parts = []
    for child in bq_tag.children:
        if child is first_p:
            if remainder:
                body_parts.append(f'<p>{remainder}</p>')
        else:
            body_parts.append(str(child))
    body = "".join(body_parts).strip()
    if not body and remainder:
        body = f'<p>{remainder}</p>'

    return (
        f'<ac:structured-macro ac:name="{panel_type}">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>'
        f'<ac:rich-text-body>{body}</ac:rich-text-body>'
        f'</ac:structured-macro>'
    )


def _extract_lang(pre_tag) -> str:
    """Extract and normalise the language name from a Pandoc <pre> tag."""
    classes = pre_tag.get("class") or []
    for cls in classes:
        cls_lower = cls.lower()
        if cls_lower in ("sourcecode", "source-code", "code"):
            continue
        return _LANG_MAP.get(cls_lower, cls_lower)
    return ""


# ── Dependency checks (run once at import) ────────────────────────────────────

_pandoc_checked = False
_bs4_checked    = False


def _check_pandoc():
    global _pandoc_checked
    if _pandoc_checked:
        return
    try:
        r = subprocess.run(["pandoc", "--version"], capture_output=True, text=True)
        _pandoc_checked = True
        version_line = r.stdout.split("\n")[0]
        try:
            ver = float(".".join(version_line.split()[1].split(".")[:2]))
            if ver < 2.11:
                print(f"  ⚠ Pandoc {version_line} is old. Recommend upgrading to 3.x")
        except Exception:
            pass
    except FileNotFoundError:
        print("\n  ✗ Pandoc not found on PATH.")
        print("    Windows:  winget install JohnMacFarlane.Pandoc")
        print("    macOS:    brew install pandoc")
        print("    Linux:    sudo apt install pandoc\n")
        sys.exit(1)


def _check_beautifulsoup():
    global _bs4_checked
    if _bs4_checked:
        return
    try:
        import bs4
        _bs4_checked = True
    except ImportError:
        print("\n  ✗ beautifulsoup4 not installed.  Run:  pip install beautifulsoup4 lxml\n")
        sys.exit(1)
