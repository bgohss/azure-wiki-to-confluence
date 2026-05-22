"""
mapping_store.py  —  JSON-based mapping store
==============================================
Saves all ADO path → Confluence ID mappings in a simple JSON file.
Much easier to inspect and edit than SQLite — just open mapping.json
in any text editor to see what has been migrated.

Structure of mapping.json:
{
  "pages": {
    "/Parent/Child-Page": {
      "confluence_id": "12345678",
      "title": "Child Page",
      "confluence_url": "https://org.atlassian.net/wiki/spaces/KEY/pages/12345678"
    }
  },
  "attachments": {
    "diagram.png": {
      "confluence_url": "https://org.atlassian.net/wiki/download/attachments/12345678/diagram.png",
      "page_id": "12345678"
    }
  }
}
"""

import json
from pathlib import Path


class MappingStore:
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self._data = self._load()

    # ── Internal load/save ────────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.filepath.exists():
            with open(self.filepath, encoding="utf-8") as f:
                return json.load(f)
        return {"pages": {}, "attachments": {}}

    def _save(self):
        """Write to disk after every update so progress is never lost."""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # ── Pages ─────────────────────────────────────────────────────────────────
    def save_page(self, ado_path: str, confluence_id: str, title: str,
                  base_url: str = "", space_key: str = ""):
        """Record that an ADO wiki page has been created in Confluence."""
        if base_url and space_key:
            cf_url = f"{base_url}/wiki/spaces/{space_key}/pages/{confluence_id}"
        elif base_url:
            cf_url = f"{base_url}/wiki/pages/{confluence_id}"
        else:
            cf_url = ""
        self._data["pages"][ado_path] = {
            "confluence_id":  confluence_id,
            "title":          title,
            "confluence_url": cf_url,
        }
        self._save()
        print(f"         ✓ Mapped  {ado_path}  →  Confluence ID {confluence_id}")

    def get_page_id(self, ado_path: str) -> str | None:
        """Look up the Confluence page ID for a given ADO path."""
        entry = self._data["pages"].get(ado_path)
        return entry["confluence_id"] if entry else None

    def get_page_url(self, confluence_id: str) -> str | None:
        """
        Look up the stored Confluence URL for a page by its Confluence ID.
        Used by link_rewriter to build correct /wiki/spaces/KEY/pages/ID links.
        """
        for entry in self._data["pages"].values():
            if entry["confluence_id"] == confluence_id:
                url = entry.get("confluence_url", "")
                return url if url else None
        return None

    def get_page_title(self, ado_path: str) -> str | None:
        """
        Look up the ACTUAL Confluence page title for a given ADO path.
        This may differ from the original ADO title if a suffix was added
        during Pass 1 to resolve a title conflict (e.g. "Overview (2)").
        Pass 2 uses this so update_page sends the correct title.
        """
        entry = self._data["pages"].get(ado_path)
        return entry["title"] if entry else None

    def get_page_by_title(self, title: str) -> str | None:
        """Look up a Confluence page ID by page title (fallback for [[WikiLink]] style)."""
        title_lower = title.lower().strip()
        for entry in self._data["pages"].values():
            if entry["title"].lower().strip() == title_lower:
                return entry["confluence_id"]
        return None

    def all_pages(self) -> dict:
        return self._data["pages"]

    # ── Attachments ───────────────────────────────────────────────────────────
    def save_attachment(self, filename: str, confluence_url: str, page_id: str):
        """Record the Confluence URL for an uploaded attachment."""
        self._data["attachments"][filename] = {
            "confluence_url": confluence_url,
            "page_id":        page_id,
        }
        self._save()

    def get_attachment_url(self, filename: str) -> str | None:
        """Look up the Confluence download URL for an attachment by filename."""
        entry = self._data["attachments"].get(filename)
        return entry["confluence_url"] if entry else None

    def all_attachments(self) -> dict:
        return self._data["attachments"]

    # ── Summary ───────────────────────────────────────────────────────────────
    def summary(self) -> str:
        np = len(self._data["pages"])
        na = len(self._data["attachments"])
        return f"{np} pages mapped, {na} attachments mapped"
