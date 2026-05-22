"""
logger.py  —  Logging to console + timestamped log file
=========================================================
Provides a TeeLogger that simultaneously writes all output to:
  1. The console (stdout) — exactly as before, with colours and emoji
  2. A timestamped log file — every line prefixed with date and time

Usage (called once in migrate.py and test_connection.py at startup):

  from logger import start_logging, stop_logging

  start_logging("migration.log")   # begins capturing all print() output
  ...run migration...
  stop_logging()                    # flushes and closes the log file

Log file path is set in config.json as "log_file". Default: migration.log
Each run APPENDS to the log file so you can review history across runs.
A run header is written at the start of each session.

Example log file content:
  [2025-10-14 09:23:41]  ============================================================
  [2025-10-14 09:23:41]    Azure DevOps Wiki → Confluence Migration Tool
  [2025-10-14 09:23:41]  ============================================================
  [2025-10-14 09:23:41]  PASS 1 — Creating 42 page stubs in Confluence
  [2025-10-14 09:23:42]    [  1/ 42]  Creating stub  →  Home  (/Home)
  [2025-10-14 09:23:42]           ✓ Mapped  /Home  →  Confluence ID 223456789
  [2025-10-14 09:24:05]    ✓ Pass 1 complete. 42 pages processed.
  [2025-10-14 09:24:05]  PASS 2 — Converting & uploading content for 42 pages
  ...
"""

import datetime
import sys
from pathlib import Path


# Module-level reference to the active logger (if any)
_active_logger: "TeeLogger | None" = None


class TeeLogger:
    """
    Replaces sys.stdout to write every print() call to both the console
    and a log file simultaneously.

    Every line written to the log file is prefixed with a timestamp:
      [2025-10-14 09:23:41]  <original line>

    Lines are buffered internally until a newline is received so that
    multi-part prints (e.g. print("a", end=""); print("b")) are handled
    correctly — they appear as a single timestamped line.
    """

    def __init__(self, log_path: str):
        self._console  = sys.__stdout__          # always the real terminal
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._logfile  = open(log_path, "a", encoding="utf-8")
        self._buffer   = ""

        # Write a session header so log files are easy to navigate
        self._write_header()

    def _write_header(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        divider = "=" * 60
        self._logfile.write(f"\n{divider}\n")
        self._logfile.write(f"  Session started: {now}\n")
        self._logfile.write(f"  Log file: {self._log_path.resolve()}\n")
        self._logfile.write(f"{divider}\n\n")
        self._logfile.flush()

    # ── stdout protocol ───────────────────────────────────────────────────────

    def write(self, text: str):
        """Write to both console and log file (with timestamps on the log)."""
        # Always mirror to console unchanged
        self._console.write(text)

        # Accumulate in buffer; flush a timestamped line on each newline
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_log_line(line)

    def flush(self):
        self._console.flush()
        self._logfile.flush()

    def fileno(self):
        """
        Return the real stdout file descriptor.
        Required by subprocess and some C extensions that call fileno().
        """
        return self._console.fileno()

    def isatty(self):
        return self._console.isatty()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write_log_line(self, line: str):
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._logfile.write(f"[{ts}]  {line}\n")
        self._logfile.flush()

    def close(self):
        """
        Flush remaining buffer, write a session footer, close the file,
        and restore sys.stdout to the real console.
        """
        # Flush any partial line not yet terminated with \n
        if self._buffer.strip():
            self._write_log_line(self._buffer)
            self._buffer = ""

        # Session footer
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._logfile.write(f"\n  Session ended: {now}\n")
        self._logfile.flush()
        self._logfile.close()

        # Restore real stdout
        sys.stdout = self._console

    @property
    def log_path(self) -> Path:
        return self._log_path


# ── Public API ────────────────────────────────────────────────────────────────

def start_logging(log_file: str):
    """
    Begin capturing all print() output to both console and log_file.
    Call once at the start of the migration (or test_connection.py).
    Subsequent calls replace the existing logger.

    Parameters
    ----------
    log_file : str
        Path to the log file. Created if it does not exist.
        Appends to existing file if it does (so history is preserved).
    """
    global _active_logger

    # Stop any previous logger cleanly
    if _active_logger is not None:
        _active_logger.close()

    _active_logger = TeeLogger(log_file)
    sys.stdout     = _active_logger

    # Confirm logging is active (this line itself appears in both console + log)
    print(f"  📋 Logging to: {Path(log_file).resolve()}")


def stop_logging():
    """
    Stop logging: flush the log file and restore normal stdout.
    Safe to call even if logging was never started.
    """
    global _active_logger
    if _active_logger is not None:
        _active_logger.close()
        _active_logger = None


def get_log_path() -> str | None:
    """Return the current log file path, or None if logging is not active."""
    if _active_logger is not None:
        return str(_active_logger.log_path)
    return None
