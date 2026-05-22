# ADO Wiki → Confluence Migration Tool

A Python tool for migrating Azure DevOps (ADO) Wiki pages to Confluence Cloud.
Preserves page hierarchy, formatting, internal links, images, sidebar order, and
special characters — with multi-wiki support and full logging.

**Version:** 1.10  
**Requires:** Python 3.10+, Pandoc 2.11+, Git

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill in config
copy config.example.json config.json
# Edit config.json with your Confluence credentials

# 3. Clone your ADO wiki (Windows: enable long paths first — see below)
git config --global core.longpaths true
git clone https://org@dev.azure.com/org/Project/_git/Project.wiki my-wiki

# 4. Test everything
python test_connection.py --config config.json

# 5. PoC — migrate 5 pages first
python migrate.py --config config.json --limit 5

# 6. Full migration
python migrate.py --config config.json
```

---

## Features

| Feature | Detail |
|---|---|
| **Page hierarchy** | Reconstructed from Git folder structure in Confluence |
| **Sidebar order** | `.order` files read and respected — pages created in correct order |
| **Internal links** | Two-pass resolution — all links rewritten to Confluence URLs |
| **Images** | Attachments uploaded and re-linked automatically |
| **Formatting** | Tables, code blocks, task lists, callout panels, superscript, strikethrough |
| **Line breaks** | Single newlines preserved (matches ADO Wiki rendering) |
| **Special characters** | `%28 → (`, `%26 → &`, `%23 → #` etc. decoded in titles and links |
| **Title conflicts** | Duplicate titles auto-resolved with numbered suffix (`Title (2)`) |
| **Multi-wiki** | Run multiple migrations with `--wiki-dir` and `--mapping-file` overrides |
| **Section migration** | Target a specific section with `--include /Path/To/Section` |
| **Path discovery** | `--find-path` finds the real nested path when the URL doesn't match |
| **Logging** | All output written to a timestamped log file (configurable) |
| **SSL support** | Corporate proxy/certificate support via `fix_ssl.py` |
| **Resume-safe** | Progress saved to `mapping.json` — re-runs skip completed pages |

---

## File Reference

| File | Purpose |
|---|---|
| `migrate.py` | Main script — orchestrates Pass 1 (stubs), Pass 2 (content), Pass 3 (validation) |
| `test_connection.py` | Prerequisites checker — run before starting any migration |
| `fix_ssl.py` | SSL certificate diagnosis and auto-fix for corporate networks |
| `config_loader.py` | Loads and validates `config.json` |
| `wiki_reader.py` | Walks the cloned wiki directory, reads `.order` files, builds manifest |
| `confluence_client.py` | Confluence Cloud REST API v2 wrapper with retry and SSL support |
| `transformer.py` | Markdown → Confluence Storage Format (HTML5 via Pandoc + post-processing) |
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
| `confluence_base_url` | `https://yourorg.atlassian.net` | Atlassian domain (no `/wiki`) |
| `confluence_space_key` | `DOCS` | Space key from URL after `/spaces/` |
| `confluence_email` | `you@company.com` | Your Atlassian login email |
| `confluence_api_token` | `ATATxxxx...` | Generate at: [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `confluence_parent_page_id` | `123456789` | Page ID from Confluence URL `/pages/XXXXXXXXX/` |
| `mapping_file` | `./mapping.json` | Progress log — use one file per wiki |

### Optional settings

| Setting | Default | Description |
|---|---|---|
| `log_file` | `migration.log` | Log file path. Set to `null` to disable. |
| `include_paths` | `[]` | Only migrate these ADO paths |
| `exclude_paths` | `[]` | Skip these ADO paths |
| `ssl_verify` | `true` | Set `false` for corporate proxy networks |
| `ssl_cert_path` | *(none)* | Path to corporate CA certificate (run `fix_ssl.py`) |

> ⚠ **Never commit `config.json`** — it contains your API token. It is listed in `.gitignore`.

---

## Windows: Enable Long Path Support

ADO wikis with deeply nested pages will fail to clone on Windows unless long path support is enabled. **Do this before cloning.**

```bat
# Tell Git to allow long paths (no admin needed)
git config --global core.longpaths true

# Enable long paths in Windows (run as Administrator)
reg add HKLM\SYSTEM\CurrentControlSet\Control\FileSystem /v LongPathsEnabled /t REG_DWORD /d 1 /f
```

No admin rights? Clone to a short path: `cd C:\ && git clone <url> w`

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

# Resume after interruption (skips already-migrated pages)
python migrate.py --config config.json

# Re-run content upload only (skip page creation)
python migrate.py --config config.json --pass2-only

# Validate links after migration
python migrate.py --config config.json --validate-only
```

### Section migration

```bash
# Find the real path of a page (ADO URL ≠ filesystem path)
python migrate.py --config config.json --find-path TargetPageName

# List all pages in the wiki
python migrate.py --config config.json --list-pages

# Migrate a specific section
python migrate.py --config config.json --include /Parent-Section/Target-Section

# Migrate multiple sections
python migrate.py --config config.json --include /Section-A --include /Section-B

# Exclude a subfolder
python migrate.py --config config.json --include /Section --exclude /Section/Archive
```

### Multi-wiki migration

```bash
# Method A: separate config files
python migrate.py --config wiki-products.json
python migrate.py --config wiki-engineering.json

# Method B: shared credentials config + CLI overrides
python migrate.py --config config.json \
  --wiki-dir ./wiki-products \
  --mapping-file mapping-products.json \
  --parent-page-id 111111111

python migrate.py --config config.json \
  --wiki-dir ./wiki-engineering \
  --mapping-file mapping-engineering.json \
  --parent-page-id 222222222
```

> **Important:** every wiki must have its own `mapping.json` file.

---

## SSL / Corporate Network

If you see `SSLCertVerificationError`:

```bash
# Option 1: quickest fix
# Add to config.json:  "ssl_verify": false

# Option 2: auto-extract corporate certificate (recommended)
python fix_ssl.py --config config.json

# Option 3: Windows — use OS certificate store
pip install pip-system-certs
```

---

## How It Works

The migration runs in three passes:

1. **Pass 1 — Structure:** Creates all Confluence page stubs in the correct hierarchy order (parents before children, respecting `.order` files). Uploads all attachments. Saves every ADO path → Confluence page ID mapping to `mapping.json`.

2. **Pass 2 — Content:** Converts each Markdown file to Confluence Storage Format via Pandoc → BeautifulSoup post-processing. Rewrites all internal links using `mapping.json`. Uploads content to each stub page.

3. **Pass 3 — Validation:** Scans every migrated page for residual broken links. Produces `validation_report.txt`.

The two-pass design is the key to reliable internal link resolution — all pages exist in Confluence before any links are rewritten.

---

## Folder Layout

```
ado-wiki-migration/
├── migrate.py                   # main script
├── test_connection.py           # prerequisites checker
├── fix_ssl.py                   # SSL fix tool
├── config.example.json          # config template (copy → config.json)
├── requirements.txt             # pip dependencies
├── config_loader.py             # internal
├── confluence_client.py         # internal
├── transformer.py               # internal
├── wiki_reader.py               # internal
├── link_rewriter.py             # internal
├── mapping_store.py             # internal
├── validator.py                 # internal
├── logger.py                    # internal
├── logs/                        # log files (git-ignored)
│   └── .gitkeep
└── docs/
    ├── ADO_Wiki_Confluence_Installation_Guide.docx
    └── ADO_Wiki_Confluence_Migration_Strategy.docx
```

Files created at runtime (all git-ignored):

```
config.json                      # your credentials — NEVER commit
my-wiki/                         # cloned ADO wiki — NEVER commit
mapping.json                     # migration progress
migration.log                    # log file
validation_report.txt            # post-migration report
company-ca.crt                   # SSL certificate (if fix_ssl.py was run)
```

---

## Dependencies

| Dependency | Install | Purpose |
|---|---|---|
| Python 3.10+ | [python.org](https://python.org/downloads) | Runtime |
| Git | [git-scm.com](https://git-scm.com) | Clone ADO wiki |
| Pandoc 2.11+ | [pandoc.org](https://pandoc.org/installing.html) | Markdown conversion |
| requests | `pip install -r requirements.txt` | HTTP client for Confluence API |
| beautifulsoup4 | `pip install -r requirements.txt` | HTML post-processing |
| lxml | `pip install -r requirements.txt` | HTML parser |

---

## Documentation

Full installation guide and technical architecture report are in the `docs/` folder.

- **Installation Guide** (`ADO_Wiki_Confluence_Installation_Guide.docx`) — Step-by-step setup for beginners, all CLI options, troubleshooting
- **Migration Strategy** (`ADO_Wiki_Confluence_Migration_Strategy.docx`) — Technical architecture, option comparison, design decisions

---

## Changelog

| Version | Summary |
|---|---|
| 1.10 | File logging — all output written to timestamped log file |
| 1.9 | Title conflict round-trip fix; internal link URLs now include space key |
| 1.8 | Title duplicate auto-retry with suffix; `--find-path` NameError fixed |
| 1.7 | Windows long-path fix; `test_connection.py` detects `core.longpaths` |
| 1.6 | `--find-path` for path discovery; improved 0-match error message |
| 1.5 | Multi-wiki CLI overrides; line break preservation fix |
| 1.4 | Formatting: task lists, callout panels, table alignment, superscript |
| 1.3 | Section migration (`--include`/`--exclude`); `.order` file support |
| 1.2 | Pandoc 3.x fix; `[[_TOC_]]` / `[[_TOSP_]]` macro conversion |
| 1.1 | SSL/corporate network support; `fix_ssl.py` |
| 1.0 | Initial release |
