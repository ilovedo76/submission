"""Shared app plumbing: data dir and store."""
from __future__ import annotations

import os
from pathlib import Path

from qreport.store.db import Store

ROOT = Path(__file__).resolve().parents[3]   # project root (qreport/)


def data_dir() -> Path:
    """QREPORT_DATA, else %LOCALAPPDATA%/qreport/data (kept out of OneDrive), else ~/.qreport/data."""
    if os.environ.get("QREPORT_DATA"):
        return Path(os.environ["QREPORT_DATA"])
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "qreport" / "data"
    return Path.home() / ".qreport" / "data"


def get_store() -> Store:
    store = Store(data_dir() / "qreport.db")
    store.init()
    return store
