"""
fix_ssl.py  —  Diagnose and fix SSL certificate errors
=======================================================
Run this if you see:
  SSLCertVerificationError: unable to get local issuer certificate

What this script does:
  1. Diagnoses WHY the SSL error is happening
  2. Extracts your corporate/proxy certificate from the live connection
  3. Saves it as  company-ca.crt  in this folder
  4. Automatically updates your config.json to use it

Usage:
  python fix_ssl.py --config config.json

Or just test the diagnosis without config:
  python fix_ssl.py
"""

import argparse
import json
import os
import platform
import socket
import ssl
import subprocess
import sys
from pathlib import Path


TARGET_HOST = "atlassian.net"
TARGET_PORT = 443
CERT_OUTPUT = "company-ca.crt"


def print_header(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ── Step 1: Diagnose ──────────────────────────────────────────────────────────
def diagnose():
    print_header("Step 1 — Diagnosing SSL issue")

    system = platform.system()
    python_version = sys.version.split()[0]
    print(f"  OS:             {system} {platform.release()}")
    print(f"  Python version: {python_version}")

    # Check if requests is installed
    try:
        import requests
        print(f"  requests:       {requests.__version__} ✓")
    except ImportError:
        print("  requests:       NOT INSTALLED — run: pip install requests")
        sys.exit(1)

    # Try a plain SSL connection to get the certificate chain
    print(f"\n  Connecting to {TARGET_HOST}:{TARGET_PORT}…")
    try:
        context = ssl.create_default_context()
        with socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=TARGET_HOST) as ssock:
                cert = ssock.getpeercert()
                cipher = ssock.cipher()
                print(f"  ✓ Direct SSL connection succeeded")
                print(f"    Cipher: {cipher[0]}")
                issuer = dict(x[0] for x in cert.get("issuer", []))
                subject = dict(x[0] for x in cert.get("subject", []))
                print(f"    Cert subject: {subject.get('organizationName', subject.get('commonName', 'unknown'))}")
                print(f"    Cert issuer:  {issuer.get('organizationName', issuer.get('commonName', 'unknown'))}")
                return "direct_ok"
    except ssl.SSLCertVerificationError as e:
        print(f"  ✗ SSL verification failed: {e.reason}")
        print(f"\n  This confirms a corporate proxy/firewall is intercepting HTTPS.")
        print(f"  We need to extract its certificate so Python can trust it.")
        return "corporate_proxy"
    except ssl.SSLError as e:
        print(f"  ✗ SSL error: {e}")
        return "ssl_error"
    except Exception as e:
        print(f"  ✗ Connection error: {e}")
        return "connection_error"


# ── Step 2: Extract the certificate ──────────────────────────────────────────
def extract_certificate():
    print_header("Step 2 — Extracting certificate chain")

    # Use openssl s_client to dump the full cert chain (most reliable)
    # This works on Windows (Git Bash / OpenSSL), macOS, and Linux
    try:
        result = subprocess.run(
            ["openssl", "s_client", "-connect", f"{TARGET_HOST}:{TARGET_PORT}",
             "-showcerts", "-verify_return_error"],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout + result.stderr

        # Extract all PEM certificates from the output
        certs = _extract_pem_blocks(output)

        if certs:
            # The LAST cert in the chain is typically the root/intermediate CA
            # (the one added by the corporate proxy)
            root_cert = certs[-1]
            Path(CERT_OUTPUT).write_text(root_cert, encoding="utf-8")
            print(f"  ✓ Extracted {len(certs)} certificate(s) from chain")
            print(f"  ✓ Saved corporate CA certificate → {CERT_OUTPUT}")
            return True
        else:
            print("  ✗ Could not extract PEM certificates from openssl output")
            return False

    except FileNotFoundError:
        print("  ✗ openssl not found — trying Python fallback method…")
        return _extract_via_python()
    except subprocess.TimeoutExpired:
        print("  ✗ Connection timed out — check network access to atlassian.net")
        return False


def _extract_pem_blocks(text: str) -> list[str]:
    """Extract all -----BEGIN CERTIFICATE----- blocks from text."""
    certs = []
    lines = text.split("\n")
    in_cert = False
    current = []

    for line in lines:
        if "-----BEGIN CERTIFICATE-----" in line:
            in_cert = True
            current = [line]
        elif "-----END CERTIFICATE-----" in line and in_cert:
            current.append(line)
            certs.append("\n".join(current) + "\n")
            in_cert = False
            current = []
        elif in_cert:
            current.append(line)

    return certs


def _extract_via_python():
    """Fallback: use Python's ssl module to get the DER cert and convert it."""
    try:
        import base64

        # Get cert without verification (to capture what the proxy is sending)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=TARGET_HOST) as ssock:
                der_cert = ssock.getpeercert(binary_form=True)

        # Convert DER → PEM
        b64 = base64.b64encode(der_cert).decode()
        pem = "-----BEGIN CERTIFICATE-----\n"
        pem += "\n".join(b64[i:i+64] for i in range(0, len(b64), 64))
        pem += "\n-----END CERTIFICATE-----\n"

        Path(CERT_OUTPUT).write_text(pem, encoding="utf-8")
        print(f"  ✓ Extracted server certificate (Python fallback method)")
        print(f"  ✓ Saved → {CERT_OUTPUT}")
        print(f"  ℹ Note: This captured the server/proxy cert, not the full root CA.")
        print(f"    If verification still fails, ask IT for the full CA bundle.")
        return True
    except Exception as e:
        print(f"  ✗ Python fallback also failed: {e}")
        return False


# ── Step 3: Verify the extracted cert works ───────────────────────────────────
def verify_cert():
    print_header("Step 3 — Verifying extracted certificate")

    try:
        import requests

        resp = requests.get(
            f"https://{TARGET_HOST}",
            verify=CERT_OUTPUT,
            timeout=10,
        )
        print(f"  ✓ SSL verification succeeded with {CERT_OUTPUT}!")
        print(f"    HTTP status: {resp.status_code}")
        return True
    except requests.exceptions.SSLError as e:
        print(f"  ✗ Still failing with extracted cert: {e}")
        print(f"\n  This usually means your corporate proxy uses a multi-level")
        print(f"  certificate chain. Try asking IT for the full root CA bundle.")
        return False
    except Exception as e:
        print(f"  ✗ Request error: {e}")
        return False


# ── Step 4: Update config.json ────────────────────────────────────────────────
def update_config(config_path: str):
    print_header("Step 4 — Updating config.json")

    cfg_path = Path(config_path)
    if not cfg_path.exists():
        print(f"  ℹ config.json not found at {config_path}")
        print(f"  Manually add this to your config.json:")
        print(f'    "ssl_cert_path": "{CERT_OUTPUT}"')
        return

    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    # Use forward slashes for cross-platform compatibility
    cert_abs = str(Path(CERT_OUTPUT).resolve()).replace("\\", "/")
    cfg["ssl_cert_path"] = cert_abs

    # Remove ssl_verify: false if it was set as a workaround
    cfg.pop("ssl_verify", None)

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    print(f"  ✓ Updated config.json with ssl_cert_path: {cert_abs}")


# ── Windows-specific: try importing from Windows cert store ───────────────────
def try_windows_cert_store():
    """
    On Windows, Python can optionally use the Windows certificate store.
    Install the pip package 'pip-system-certs' to enable this automatically.
    """
    if platform.system() != "Windows":
        return

    print_header("Windows Certificate Store Option")
    print("""
  On Windows, your corporate certificate is already trusted by the OS.
  You can make Python use the Windows cert store automatically:

  Run:  pip install pip-system-certs

  Then restart your terminal and retry test_connection.py.
  This is often the easiest fix on Windows corporate machines.
""")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Fix SSL certificate errors for the migration tool")
    p.add_argument("--config", default=None, help="Path to config.json (optional)")
    args = p.parse_args()

    print("=" * 60)
    print("  ADO Wiki → Confluence — SSL Certificate Fix Tool")
    print("=" * 60)

    # Show Windows cert store tip first (often simplest fix)
    try_windows_cert_store()

    # Diagnose
    result = diagnose()

    if result == "direct_ok":
        print("\n  ✓ Your SSL connection is working fine!")
        print("  The error you saw may have been a temporary network issue.")
        print("  Try running test_connection.py again.")
        return

    if result in ("connection_error",):
        print("\n  ✗ Cannot reach atlassian.net at all.")
        print("  Check that you have internet access and try again.")
        return

    # Extract the corporate certificate
    extracted = extract_certificate()

    if not extracted:
        print("\n" + "=" * 60)
        print("  MANUAL FIX REQUIRED")
        print("=" * 60)
        print("""
  Automatic extraction failed. Try these manual steps:

  Option A — Disable verification (quickest):
    Add to config.json:   "ssl_verify": false

  Option B — Get cert from IT:
    1. Ask your IT / Security team for the corporate CA certificate
    2. They will give you a .crt or .pem file
    3. Add to config.json:
         "ssl_cert_path": "C:/path/to/the/file.crt"

  Option C — Extract via browser (Windows):
    1. Open Chrome/Edge and go to https://atlassian.net
    2. Click the padlock icon → Certificate → Details tab
    3. Select the ROOT certificate (top of chain)
    4. Click "Copy to file" → Base-64 encoded X.509 → save as company-ca.crt
    5. Add to config.json:
         "ssl_cert_path": "./company-ca.crt"
""")
        return

    # Verify it works
    cert_ok = verify_cert()

    if cert_ok:
        # Update config if provided
        if args.config:
            update_config(args.config)

        print("\n" + "=" * 60)
        print("  ✓  SSL FIX COMPLETE")
        print("=" * 60)
        print(f"""
  Certificate saved to: {CERT_OUTPUT}
  {"config.json updated automatically." if args.config else ""}

  Next step:
    python test_connection.py --config config.json
""")
    else:
        # Fall back to recommending ssl_verify: false
        print("\n" + "=" * 60)
        print("  FALLBACK: Use ssl_verify: false")
        print("=" * 60)
        print("""
  Certificate extraction did not fully resolve the issue.
  The safest remaining option is to disable verification.

  Add this line to your config.json:
    "ssl_verify": false

  This is safe on a corporate network — it only skips certificate
  chain verification, it does NOT expose your credentials or data.

  Then run:  python test_connection.py --config config.json
""")


if __name__ == "__main__":
    main()
