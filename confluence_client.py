"""
confluence_client.py  —  Confluence Cloud REST API wrapper
===========================================================
Handles all communication with Confluence Cloud using API tokens.
Includes automatic retry with backoff for rate-limit errors (HTTP 429).

SSL / Corporate Network Note
-----------------------------
On corporate networks, a company proxy or firewall intercepts HTTPS traffic
and re-signs it with an internal certificate. Python's requests library
doesn't trust this certificate by default, causing:

  SSLCertVerificationError: unable to get local issuer certificate

Fix options (in order of preference):
  1. Add  "ssl_verify": false  to config.json  (quick fix, less secure)
  2. Add  "ssl_cert_path": "C:/path/to/company.crt"  to config.json
  3. Run:  python fix_ssl.py  to auto-extract and register the certificate

Authentication:
  Confluence Cloud uses HTTP Basic Auth where:
    username = your email address
    password = your API token (NOT your account password)

  Generate a token at: https://id.atlassian.com/manage-profile/security/api-tokens
"""

import base64
import mimetypes
import time
from pathlib import Path

import requests
import urllib3


# How many times to retry on rate limit before giving up
MAX_RETRIES = 5


class ConfluenceClient:
    def __init__(self, cfg: dict):
        self.base_url   = cfg["confluence_base_url"].rstrip("/")
        self.space_key  = cfg["confluence_space_key"]
        self.parent_id  = cfg["confluence_parent_page_id"]
        self.email      = cfg["confluence_email"]
        self.api_token  = cfg["confluence_api_token"]

        # ── SSL verification setting ───────────────────────────────────────────
        # ssl_verify can be:
        #   True  (default)  — verify using system/Python cert store
        #   False            — skip verification (quick fix, not recommended for prod)
        #   "path/to/ca.crt" — verify using a specific certificate bundle file
        ssl_cfg = cfg.get("ssl_verify", True)

        if ssl_cfg is False or str(ssl_cfg).lower() == "false":
            self.ssl_verify = False
            # Suppress the InsecureRequestWarning when ssl_verify=False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            print("  ⚠ SSL verification disabled (ssl_verify=false in config.json)")
            print("    This is acceptable on a trusted corporate network.")
        elif isinstance(ssl_cfg, str) and ssl_cfg not in ("true", "True", "1"):
            # A path to a certificate bundle was provided
            cert_path = Path(ssl_cfg).expanduser().resolve()
            if cert_path.exists():
                self.ssl_verify = str(cert_path)
                print(f"  ✓ Using custom SSL certificate bundle: {cert_path}")
            else:
                print(f"  ✗ ssl_cert_path not found: {cert_path}")
                print(f"    Run: python fix_ssl.py  to generate it automatically.")
                self.ssl_verify = True
        else:
            self.ssl_verify = True

        # Build auth header — Basic Auth with email:api_token base64 encoded
        credentials = f"{self.email}:{self.api_token}"
        encoded     = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }

    # ── Internal helpers ──────────────────────────────────────────────────────
    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        Make an HTTP request with automatic retry on rate limit (HTTP 429).
        On 429, Confluence returns a Retry-After header (seconds to wait).
        ssl_verify is automatically applied to every request.
        """
        kwargs.setdefault("verify", self.ssl_verify)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.request(method, url, headers=self.headers, **kwargs)
            except requests.exceptions.SSLError as e:
                _print_ssl_help(str(e))
                raise

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", 10))
                print(f"         ⚠ Rate limited. Waiting {wait}s before retry {attempt}/{MAX_RETRIES}…")
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                print(f"\n  ✗ HTTP {response.status_code} for {method} {url}")
                try:
                    err = response.json()
                    print(f"    Message: {err.get('message', err)}")
                except Exception:
                    print(f"    Body: {response.text[:300]}")
                response.raise_for_status()

            return response

        raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url}")

    def _api(self, path: str) -> str:
        """Build full API v2 URL."""
        return f"{self.base_url}/wiki/api/v2{path}"

    def _api_v1(self, path: str) -> str:
        """Build full API v1 URL (needed for attachment upload)."""
        return f"{self.base_url}/wiki/rest/api{path}"

    # ── Connection test ───────────────────────────────────────────────────────
    def test_connection(self) -> bool:
        """
        Verify credentials work before starting migration.
        Returns True on success, prints error and returns False on failure.
        """
        print("  Testing Confluence connection…")
        try:
            url  = self._api(f"/spaces?keys={self.space_key}&limit=1")
            resp = self._request("GET", url)
            spaces = resp.json().get("results", [])
            if spaces:
                print(f"  ✓ Connected to Confluence. Space '{self.space_key}' found.")
                return True
            else:
                print(f"  ✗ Space key '{self.space_key}' not found. Check your config.json.")
                return False
        except requests.exceptions.SSLError:
            # SSL help already printed by _request; just return False
            return False
        except Exception as e:
            print(f"  ✗ Connection failed: {e}")
            print("    Check: confluence_base_url, confluence_email, confluence_api_token")
            return False

    # ── Page operations ───────────────────────────────────────────────────────
    def create_page(self, title: str, parent_id: str, body: str) -> str:
        """
        Create a new Confluence page.
        Returns the new page's Confluence ID (string).

        If a page with the same title already exists in the space (HTTP 400),
        automatically retries with a numeric suffix: "Title (2)", "Title (3)" etc.
        This handles ADO wikis that have pages with identical titles in different
        sections — Confluence titles must be unique across the entire space.
        """
        actual_parent = parent_id or self.parent_id
        space_id      = self._get_space_id()

        for attempt in range(1, 10):          # try up to 9 suffixed variants
            candidate_title = title if attempt == 1 else f"{title} ({attempt})"

            payload = {
                "spaceId":  space_id,
                "status":   "current",
                "title":    candidate_title,
                "parentId": actual_parent,
                "body": {
                    "representation": "storage",
                    "value": body,
                },
            }

            try:
                resp    = self._request("POST", self._api("/pages"), json=payload)
                page_id = resp.json()["id"]
                if attempt > 1:
                    print(f"         ℹ Title conflict resolved: using [{candidate_title}]")
                # Return BOTH id and the actual title used (may differ from input if suffix added)
                return str(page_id), candidate_title

            except Exception as e:
                # Detect the title-already-exists 400 error
                err_str = str(e).lower()
                if "400" in err_str and (
                    "title already exists" in err_str
                    or "same title" in err_str
                    or "bad request" in err_str
                ):
                    print(f"         ⚠ Title conflict: [{candidate_title}] already exists — retrying with suffix…")
                    continue    # try next suffix
                raise           # any other error: re-raise immediately

        # Fallback: if all 9 suffixes are taken, use a timestamp suffix
        import time as _time
        ts_title = f"{title} (migrated-{int(_time.time())})"
        print(f"         ⚠ All numeric suffixes taken — using timestamp suffix: [{ts_title}]")
        payload["title"] = ts_title
        resp    = self._request("POST", self._api("/pages"), json=payload)
        page_id = resp.json()["id"]
        return str(page_id), ts_title

    def update_page(self, page_id: str, title: str, body: str):
        """
        Replace a page's content (used in Pass 2 to populate the stub).
        Fetches the current version number first (required by Confluence API).
        """
        resp    = self._request("GET", self._api(f"/pages/{page_id}"))
        current = resp.json()
        version = current["version"]["number"] + 1

        payload = {
            "id":      page_id,
            "status":  "current",
            "title":   title,
            "version": {"number": version},
            "body": {
                "representation": "storage",
                "value": body,
            },
        }
        self._request("PUT", self._api(f"/pages/{page_id}"), json=payload)

    def get_page_content(self, page_id: str) -> str:
        """Fetch the storage format body of a page (used by validator)."""
        url  = self._api(f"/pages/{page_id}?body-format=storage")
        resp = self._request("GET", url)
        return resp.json().get("body", {}).get("storage", {}).get("value", "")

    def page_url(self, page_id: str) -> str:
        """Build the browser URL for a Confluence page."""
        return f"{self.base_url}/wiki/spaces/{self.space_key}/pages/{page_id}"

    # ── Attachment upload ─────────────────────────────────────────────────────
    def upload_attachment(self, page_id: str, file_path: Path) -> str:
        """
        Upload a file as an attachment to a Confluence page.
        Returns the download URL for the attachment.
        """
        url = self._api_v1(f"/content/{page_id}/child/attachment")

        mime_type, _ = mimetypes.guess_type(str(file_path))
        mime_type = mime_type or "application/octet-stream"

        upload_headers = {k: v for k, v in self.headers.items() if k != "Content-Type"}
        upload_headers["X-Atlassian-Token"] = "no-check"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with open(file_path, "rb") as fh:
                    files = {"file": (file_path.name, fh, mime_type)}
                    resp  = requests.post(
                        url,
                        headers=upload_headers,
                        files=files,
                        verify=self.ssl_verify,
                    )
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 10))
                    time.sleep(wait)
                    continue
                break
            except requests.exceptions.SSLError as e:
                _print_ssl_help(str(e))
                return ""

        if resp.status_code not in (200, 201):
            print(f"         ✗ Attachment upload failed ({resp.status_code}): {file_path.name}")
            return ""

        results = resp.json().get("results", [])
        if results:
            download_path = results[0].get("_links", {}).get("download", "")
            return f"{self.base_url}/wiki{download_path}"
        return ""

    # ── Space lookup (cached) ─────────────────────────────────────────────────
    _space_id_cache: str = None

    def _get_space_id(self) -> str:
        """Look up the numeric space ID from the space key. Cached after first call."""
        if self._space_id_cache:
            return self._space_id_cache
        url     = self._api(f"/spaces?keys={self.space_key}&limit=1")
        resp    = self._request("GET", url)
        results = resp.json().get("results", [])
        if not results:
            raise ValueError(
                f"Space '{self.space_key}' not found. Check confluence_space_key in config.json"
            )
        self._space_id_cache = results[0]["id"]
        return self._space_id_cache


# ── SSL error help message ────────────────────────────────────────────────────
def _print_ssl_help(error_msg: str):
    """Print a clear, actionable SSL error message."""
    print("\n" + "=" * 60)
    print("  SSL CERTIFICATE ERROR")
    print("=" * 60)
    print("""
  Your computer's Python cannot verify the SSL certificate for
  Confluence. This is common on corporate networks where a proxy
  or firewall intercepts HTTPS traffic.

  ── QUICK FIX (choose one) ──────────────────────────────────

  Option 1 — Disable SSL verification (easiest, safe on corp network):
    Add this line to your config.json:
      "ssl_verify": false

  Option 2 — Auto-extract your corporate certificate:
    Run this command, then re-run test_connection.py:
      python fix_ssl.py

  Option 3 — Point to your company's certificate file manually:
    Ask your IT team for the corporate CA certificate (.crt or .pem file).
    Then add to config.json:
      "ssl_cert_path": "C:/path/to/corporate-ca.crt"

  ── TECHNICAL DETAIL ────────────────────────────────────────
""")
    # Show only the key part of the SSL error
    for line in error_msg.split("]"):
        line = line.strip().lstrip("[")
        if line:
            print(f"  {line}")
    print("=" * 60 + "\n")
