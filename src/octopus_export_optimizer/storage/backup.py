"""SQLite database backup utility.

Creates timestamped backup copies of the optimizer database
to a configurable directory, with automatic retention management.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def create_backup(
    db_path: str,
    backup_dir: str,
    retention_days: int = 7,
) -> Path | None:
    """Create a backup of the SQLite database using the backup API.

    Args:
        db_path: Path to the live database file.
        backup_dir: Directory to store backups.
        retention_days: Number of daily backups to keep.

    Returns:
        Path to the backup file, or None if backup failed.
    """
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    dest = backup_path / f"optimizer_{timestamp}.db"

    try:
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(str(dest))
        source.backup(target)
        target.close()
        source.close()
        logger.info("Database backup created: %s", dest)
    except Exception:
        logger.exception("Failed to create database backup")
        return None

    # Clean up old backups
    _cleanup_old_backups(backup_path, retention_days)

    return dest


def _cleanup_old_backups(backup_dir: Path, keep: int) -> None:
    """Remove old backups, keeping the most recent `keep` files."""
    backups = sorted(
        backup_dir.glob("optimizer_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[keep:]:
        old.unlink()
        logger.debug("Removed old backup: %s", old)
