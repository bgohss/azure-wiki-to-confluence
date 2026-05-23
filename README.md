# ADO Wiki → Confluence Migration Tool

A Python tool for migrating Azure DevOps (ADO) Wiki pages to Confluence Cloud.
Preserves page hierarchy, formatting, internal links, images, sidebar order, and
special characters — with multi-wiki support and full logging.

**Version:** 1.11  
**Requires:** Python 3.10+, Pandoc 2.11+, Git

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill in config
copy config.example.json config.json
# Edit config.json with your Confluence credentials

# 3. Windows only — enable long paths BEFORE cloning (see below)
git config --global core.longpaths true

# 4. Clone your ADO wiki
git clone https://org@dev.azure.com/org/Project/_git/Project.wiki my-wiki

# 5. Test everything
python test_connection.py --config config.json

# 6. PoC — migrate 5 pages first, check the result in Confluence
python migrate.py --config config.json --limit 5

# 7. Full migration
python migrate.py --config config.json
```

---

## Features

| Feature | Detail |
|---|---|
| **Page hierarchy** | Reconstructed from Git folder structure; parents always before children |
| **Folder-only nodes** | ADO folders with no .md file get a synthetic placeholder page — hierarchy preserved |
| **Sidebar order** | `.order` files read and respected — pages appear in correct sidebar order |
| **Internal links** | Two-pass resolution — all links rewritten to correct Confluence URLs |
| **Images** | Attachments uploaded per page and re-linked automatically |
| **Formatting** | Tables (with alignment), code blocks, task lists, callout panels, superscript, strikethrough |
| **Line breaks** | Single newlines preserved as `<br>` — matches ADO Wiki rendering |
| **Special characters** | `%28→(`, `%26→&`, `%23→#` etc. decoded in titles and links |
| **Title conflicts** | Duplicate titles auto-resolved with suffix (`Title (2)`) — round-trips to Pass 2 |
| **Multi-wiki** | Migrate multiple wikis with `--wiki-dir` and `--mapping-file` CLI overrides |
| **Section migration** | Target a specific section with `--include /Path/To/Section` |
| **Path discovery** | `--find-path` finds real nested path when the ADO URL doesn't match |
| **Logging** | All output written to a timestamped log file — appends across runs |
| **SSL support** | Corporate proxy/certificate support via `fix_ssl.py` |
| **Resume-safe** | Progress saved to `mapping.json` — interrupted runs re-start from where they stopped |

---

## File Reference

| File | Purpose |
|---|---|
| `migrate.py` | Main script — Pass 1 (stubs), Pass 2 (content), Pass 3 (validation) |
| `test_connection.py` | Prerequisites checker — run before any migration |
| `fix_ssl.py` | SSL certificate diagnosis and auto-fix for corporate networks |
| `config_loader.py` | Loads and validates `config.json` |
| `wiki_reader.py` | Walks the cloned wiki; reads `.order` files; detects folder-only nodes |
| `confluence_client.py` | Confluence Cloud REST API v2 wrapper with retry and SSL support |
| `transformer.py` | Markdown → Confluence Storage Format via Pandoc + BeautifulSoup |
| `link_rewriter.py` | Rewrites internal wiki links and image `src` paths |
| `mapping_store.py` | JSON-based progress store (`mapping.json`) |
| `validator.py` | Post-migration broken-link sweep |
| `logger.py` | TeeLogger — writes all output to console and timestamped log file |
| `config.example.json` | Config template — copy to `config.json` and fill in |
| `requirements.txt` | `pip install -r requirements.txt` |

---

## Configuration

Copy `config.example.json` to `config.json` and fill in your values.

### Required settings

| Setting | Example | Description |
|---|---|---|
| `wiki_clone_dir` | `./my-wiki` | Path to the Git-cloned wiki folder |
| `confluence_base_url` | `https://yourorg.atlassian.net` | Atlassian domain — no `/wiki` at the end |
| `confluence_space_key` | `DOCS` | Space key from URL after `/spaces/` |
| `confluence_email` | `you@company.com` | Your Atlassian login email |
| `confluence_api_token` | `ATATxxxx...` | Generate at: [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `confluence_parent_page_id` | `123456789` | Page ID from Confluence URL `/pages/XXXXXXXXX/` |
| `mapping_file` | `./mapping.json` | Progress log — use one file per wiki |

### Optional settings

| Setting | Default | Description |
|---|---|---|
| `log_file` | `migration.log` | Log file path. Set to `null` to disable. |
| `include_paths` | `[]` | Only migrate these ADO paths. |
| `exclude_paths` | `[]` | Skip these ADO paths. |
| `ssl_verify` | `true` | Set `false` for corporate proxy networks. |
| `ssl_cert_path` | *(none)* | Path to corporate CA certificate (run `fix_ssl.py`). |

> ⚠ **Never commit `config.json`** — it contains your API token. It is listed in `.gitignore`.

---

## Windows: Enable Long Path Support

ADO wikis with deeply nested pages will fail to clone on Windows unless long path support is enabled. **Do this before cloning — the resulting partial clone is missing pages and causes hierarchy errors.**

```bat
:: Tell Git to allow long paths (no admin needed)
git config --global core.longpaths true

:: Enable long paths in Windows — requires Administrator terminal
reg add HKLM\SYSTEM\CurrentControlSet\Control\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f
```

No admin rights? Clone to the shortest possible path: `cd C:\ && git clone <url> w`  
Then set `"wiki_clone_dir": "C:\\w"` in config.json.

---

## How It Works

The migration runs in three passes:

**Pass 1 — Structure**  
Creates all Confluence page stubs in the correct hierarchy order (depth-first, parents before children, respecting `.order` files). Uploads all attachments. Saves every `ADO path → Confluence page ID` mapping to `mapping.json`.

Folder-only nodes — ADO Wiki folders that have child pages but no corresponding `.md` file — are automatically detected and given synthetic placeholder pages in Confluence so children appear in the correct location.

**Pass 2 — Content**  
Converts each Markdown file to Confluence Storage Format via Pandoc → BeautifulSoup post-processing. Rewrites all internal links using `mapping.json` (which already has every page ID from Pass 1). Uploads content to each stub page.

**Pass 3 — Validation**  
Scans every migrated page for residual broken links. Produces `validation_report.txt`.

The two-pass design is the key to reliable internal link resolution — all pages exist in Confluence before any link is rewritten.

---

## CLI Reference

### Common commands

```bash
# Check everything is ready
python test_connection.py --config config.json

# Dry run — preview without uploading
python migrate.py --config config.json --limit 5 --dry-run

# PoC — first 5 pages
python migrate.py --config config.json --limit 5

# Full migration
python migrate.py --config config.json

# Resume after interruption (skips already-done pages automatically)
python migrate.py --config config.json

# Re-run content upload only — skip page creation (Pass 1)
python migrate.py --config config.json --pass2-only

# Validate links after migration
python migrate.py --config config.json --validate-only
```

### Section migration

```bash
# Step 1: find the real filesystem path (ADO URL ≠ git clone path)
python migrate.py --config config.json --find-path TargetPageName

# Step 2: list all pages to browse the full tree
python migrate.py --config config.json --list-pages

# Migrate a specific section
python migrate.py --config config.json --include /Parent-Section/Target-Section

# Migrate multiple sections
python migrate.py --config config.json --include /Section-A --include /Section-B

# Exclude a subfolder
python migrate.py --config config.json --include /Section --exclude /Section/Archive

# Exclude pipeline-generated release note pages
python migrate.py --config config.json --exclude /Release-Process/Release-Notes
```

### Multi-wiki migration

```bash
# Method A: separate config file per wiki (recommended)
python migrate.py --config wiki-products.json
python migrate.py --config wiki-engineering.json

# Method B: shared credentials config + CLI overrides per run
python migrate.py --config config.json \
  --wiki-dir ./wiki-products \
  --mapping-file mapping-products.json \
  --parent-page-id 111111111

python migrate.py --config config.json \
  --wiki-dir ./wiki-engineering \
  --mapping-file mapping-engineering.json \
  --parent-page-id 222222222
```

> **Important:** every wiki must have its own `mapping.json` file. Sharing one mapping file between two wikis corrupts both.

---

## Known ADO Wiki Patterns

### Folder-only nodes
ADO Wiki allows a folder to contain child pages without a corresponding page file for the folder itself. Example:

```
Release-Notes/
└── BusinessCyberBarometer/        ← folder, no BusinessCyberBarometer.md
    ├── BusinessCyberBarometer-1.364.md
    └── BusinessCyberBarometer-1.365.md
```

Without handling, all children land under the Confluence root page. The tool detects these automatically and creates a synthetic placeholder page so children appear in the correct location. You will see:

```
⚠ 1 folder(s) with no .md file — creating synthetic stub page(s):
  + /Release-Notes/BusinessCyberBarometer  (folder-only, no .md file in clone)
```

### Pipeline variable pages
Azure DevOps release pipelines sometimes auto-create wiki pages using unresolved variable expressions like `$(major).$(minor)`. These pages migrate successfully but rarely contain useful documentation. Exclude them with:

```json
"exclude_paths": ["/Release-Process/Release-Notes/-$(major).$(minor)"]
```

---

## SSL / Corporate Network

If you see `SSLCertVerificationError`:

```bash
# Option 1 — quickest: add to config.json
"ssl_verify": false

# Option 2 — auto-extract corporate certificate (recommended)
python fix_ssl.py --config config.json

# Option 3 — Windows: use the OS certificate store
pip install pip-system-certs
```

---

## Log File

All output is written to a timestamped log file alongside the console. Each run appends a new session block:

```
============================================================
  Session started: 2025-10-14 09:23:41
============================================================

[2025-10-14 09:23:42]    [  1/ 42]  Creating stub  →  Home  (/Home)
[2025-10-14 09:23:42]           ✓ Mapped  /Home  →  Confluence ID 223456789
[2025-10-14 09:24:07]           ⚠ 2 unresolved link(s):
[2025-10-14 09:24:52]    ✓ Pass 2 complete. Errors: 0

  Session ended: 2025-10-14 09:24:52
```

Configure in `config.json`:
```json
"log_file": "migration.log"           // default
"log_file": "logs/wiki-products.log"  // custom path (folder created if needed)
"log_file": null                       // disable
```

Search the log for issues: look for `✗` or `⚠` or `ERROR`.

---

## Folder Layout

```
ado-wiki-migration/
├── migrate.py                   # main script
├── test_connection.py           # prerequisites checker
├── fix_ssl.py                   # SSL fix tool
├── config.example.json          # config template → copy to config.json
├── requirements.txt             # pip install -r requirements.txt
├── config_loader.py
├── confluence_client.py
├── transformer.py
├── wiki_reader.py
├── link_rewriter.py
├── mapping_store.py
├── validator.py
├── logger.py
├── logs/                        # log files (git-ignored)
│   └── .gitkeep
└── docs/
    ├── ADO_Wiki_Confluence_Installation_Guide.docx
    └── ADO_Wiki_Confluence_Migration_Strategy.docx
```

Files created at runtime — **all listed in `.gitignore`**:

```
config.json              # your credentials — NEVER commit
my-wiki/                 # cloned ADO wiki — NEVER commit
mapping.json             # migration progress
migration.log            # timestamped log
validation_report.txt    # post-migration link report
company-ca.crt           # SSL certificate (if fix_ssl.py was run)
```

---

## Dependencies

| Dependency | Install | Purpose |
|---|---|---|
| Python 3.10+ | [python.org](https://python.org/downloads) | Runtime |
| Git | [git-scm.com](https://git-scm.com) | Clone ADO wiki |
| Pandoc 2.11+ | [pandoc.org](https://pandoc.org/installing.html) | Markdown → HTML conversion |
| requests | `pip install -r requirements.txt` | HTTP client for Confluence API |
| beautifulsoup4 | `pip install -r requirements.txt` | HTML post-processing |
| lxml | `pip install -r requirements.txt` | HTML parser |

---

## Documentation

Full guides are in the `docs/` folder:

- **Installation Guide** (`ADO_Wiki_Confluence_Installation_Guide.docx`) — Step-by-step setup for beginners, all CLI options, troubleshooting, full changelog
- **Migration Strategy** (`ADO_Wiki_Confluence_Migration_Strategy.docx`) — Technical architecture, option comparison, design decisions

---

## Changelog

| Version | Summary |
|---|---|
| **1.11** | **Folder-only node fix** — ADO folders with no .md file now get synthetic placeholder pages; children placed correctly instead of at root |
| 1.10 | File logging — all output written to timestamped log file via TeeLogger |
| 1.9 | Title conflict round-trip fix; internal link URLs now include space key |
| 1.8 | Title duplicate auto-retry; `--find-path` NameError fixed |
| 1.7 | Windows long-path fix; `test_connection.py` detects `core.longpaths` |
| 1.6 | `--find-path` for path discovery; improved 0-match error message |
| 1.5 | Multi-wiki CLI overrides (`--wiki-dir`, `--mapping-file`, `--parent-page-id`); line break fix |
| 1.4 | Formatting: task lists, callout panels, table alignment, superscript/subscript |
| 1.3 | Section migration (`--include`/`--exclude`/`--list-pages`); `.order` file support |
| 1.2 | Pandoc 3.x fix (was: `--to confluence`); `[[_TOC_]]` / `[[_TOSP_]]` macro conversion |
| 1.1 | SSL/corporate network support; `fix_ssl.py` |
| 1.0 | Initial release |
