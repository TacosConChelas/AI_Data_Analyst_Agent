"""
Shared dataset store.

Owns the loaded DataFrames and the RAG index. There is ONE DataStore for the
whole server: uploaded files are a shared library (they all live in uploads/),
so the data of a given file is the same regardless of which session views it.

Datasets persist across restarts: on startup the server calls load_from_disk()
to rescan uploads/ and re-index every supported file.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import pandas as pd

from rag import RAGSystem

SUPPORTED = {".csv", ".xlsx", ".xls"}


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Convert text columns that are overwhelmingly parseable dates into real
    datetime columns.

    Small models reliably write `df[df['col'].dt.year == 2013]` for datetime
    columns, but guess wrong (e.g. `str[:4]`) when a date is stored as text like
    '17-09-2013'. Normalizing at load time removes that guesswork. Uses
    dayfirst=True (handles DD-MM-YYYY; pandas ignores it for unambiguous ISO
    dates). Numeric-as-text columns fail to parse and are left untouched."""
    for col in df.select_dtypes(include=["object", "string"]).columns:
        non_null = int(df[col].notna().sum())
        if non_null == 0:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
        # Require ≥90% of originally non-null values to parse as real dates.
        if int(parsed.notna().sum()) / non_null >= 0.9:
            df[col] = parsed
    return df


class DataStore:
    def __init__(self):
        self.dataframes: dict[str, pd.DataFrame] = {}
        self.rag = RAGSystem()

    # ──────────────────────────────────────────────────────
    def load_file(self, file_path) -> dict:
        """Read a CSV/Excel file, store the DataFrame and index it in RAG."""
        path = Path(file_path)
        fname = path.name
        suffix = path.suffix.lower()

        if suffix == ".csv":
            df = pd.read_csv(file_path)
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(file_path)
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        df = _normalize_dates(df)
        self.dataframes[fname] = df
        self.rag.index_dataframe(fname, df)
        return self._info(fname, df)

    def load_from_disk(self, upload_dir) -> list[str]:
        """Reload+index every supported file already present in upload_dir.

        Called once at startup so datasets survive a restart. A file that fails
        to parse is skipped with a warning — it never crashes the server."""
        loaded: list[str] = []
        for p in sorted(Path(upload_dir).glob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED:
                try:
                    self.load_file(p)
                    loaded.append(p.name)
                except Exception as exc:
                    print(f"[DataStore] Skipped '{p.name}': {exc}")
        if loaded:
            print(f"[DataStore] Reloaded {len(loaded)} dataset(s) from disk: {', '.join(loaded)}")
        else:
            print("[DataStore] No datasets found on disk.")
        return loaded

    # ──────────────────────────────────────────────────────
    def has(self, filename: str) -> bool:
        return filename in self.dataframes

    def get_df(self, filename: str | None) -> pd.DataFrame | None:
        if not filename:
            return None
        return self.dataframes.get(filename)

    def delete_file(self, filename: str) -> bool:
        if filename not in self.dataframes:
            return False
        del self.dataframes[filename]
        return True

    def list_files(self) -> list[dict]:
        return [
            {"filename": fn, "rows": len(df), "columns": len(df.columns)}
            for fn, df in self.dataframes.items()
        ]

    # ──────────────────────────────────────────────────────
    @staticmethod
    def _info(fname: str, df: pd.DataFrame) -> dict:
        return {
            "filename": fname,
            "rows": len(df),
            "columns": list(df.columns),
            "dtypes": {c: str(t) for c, t in df.dtypes.items()},
            "preview": json.loads(df.head(3).to_json(orient="records", date_format="iso")),
        }
