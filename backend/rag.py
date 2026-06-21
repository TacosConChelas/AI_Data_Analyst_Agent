"""
RAG (Retrieval-Augmented Generation) system.

Indexes CSV/Excel metadata into ChromaDB with sentence-transformer embeddings,
then retrieves the most relevant context chunks for a user query.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd


class RAGSystem:
    """Vector-store backed RAG for DataFrame metadata."""

    def __init__(self):
        self._collections: dict[str, str] = {}  # filename → collection name
        self._fallback_docs: dict[str, list[tuple[str, dict]]] = {}
        self._use_chroma = False
        self._chroma_client = None
        self._embedding_fn = None
        self._init_chroma()

    # ─────────────────────────────────────────────────────────────────
    # Initialisation
    # ─────────────────────────────────────────────────────────────────
    def _init_chroma(self):
        try:
            import chromadb
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )

            self._chroma_client = chromadb.Client()
            self._embedding_fn = SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
            self._use_chroma = True
            print("[RAG] ChromaDB + SentenceTransformer initialised.")
        except Exception as exc:
            print(f"[RAG] ChromaDB not available ({exc}), using keyword fallback.")
            self._use_chroma = False

    # ─────────────────────────────────────────────────────────────────
    # Indexing
    # ─────────────────────────────────────────────────────────────────
    def index_dataframe(self, filename: str, df: pd.DataFrame):
        """Build RAG index from a DataFrame's metadata."""
        docs, metas, ids = [], [], []

        # 1. Schema overview
        docs.append(
            f"File: {filename}\n"
            f"Shape: {df.shape[0]} rows × {df.shape[1]} columns\n"
            f"Columns and data types:\n{df.dtypes.to_string()}"
        )
        metas.append({"type": "schema", "file": filename})
        ids.append(f"{filename}_schema")

        # 2. Statistical summary
        numeric_df = df.select_dtypes(include=[np.number])
        if not numeric_df.empty:
            docs.append(
                f"Statistical summary for {filename}:\n{numeric_df.describe().to_string()}"
            )
            metas.append({"type": "statistics", "file": filename})
            ids.append(f"{filename}_stats")

        # 3. Data sample
        docs.append(
            f"Sample rows from {filename} (first 10):\n{df.head(10).to_string()}"
        )
        metas.append({"type": "sample", "file": filename})
        ids.append(f"{filename}_sample")

        # 4. Per-column metadata
        for col in df.columns:
            col_info = (
                f"Column '{col}' in {filename}:\n"
                f"  Type: {df[col].dtype}\n"
                f"  Non-null: {int(df[col].count())}/{len(df)}\n"
            )
            if pd.api.types.is_numeric_dtype(df[col]):
                col_info += (
                    f"  Min: {df[col].min()}, Max: {df[col].max()}, "
                    f"Mean: {df[col].mean():.4f}, Std: {df[col].std():.4f}"
                )
            else:
                samples = df[col].dropna().unique()[:10].tolist()
                col_info += (
                    f"  Unique values ({int(df[col].nunique())} total): "
                    f"{', '.join(str(v) for v in samples)}"
                )
            docs.append(col_info)
            metas.append({"type": "column", "file": filename, "column": col})
            ids.append(f"{filename}_col_{col[:40]}")

        # 5. Categorical distributions
        for col in df.select_dtypes(include=["object", "category"]).columns:
            vc = df[col].value_counts().head(20)
            docs.append(
                f"Value distribution of '{col}' in {filename}:\n{vc.to_string()}"
            )
            metas.append({"type": "distribution", "file": filename, "column": col})
            ids.append(f"{filename}_dist_{col[:40]}")

        # Store
        if self._use_chroma:
            cname = _safe_collection_name(filename)
            try:
                self._chroma_client.delete_collection(cname)
            except Exception:
                pass
            col_obj = self._chroma_client.create_collection(
                name=cname, embedding_function=self._embedding_fn
            )
            col_obj.add(documents=docs, metadatas=metas, ids=ids)
            self._collections[filename] = cname
        else:
            self._fallback_docs[filename] = list(zip(docs, metas))

    # ─────────────────────────────────────────────────────────────────
    # Retrieval
    # ─────────────────────────────────────────────────────────────────
    def search(
        self, query: str, filename: str | None = None, n_results: int = 5
    ) -> list[str]:
        """Return the top-N most relevant context strings for *query*."""
        if not filename:
            return []

        if self._use_chroma and filename in self._collections:
            cname = self._collections[filename]
            try:
                col_obj = self._chroma_client.get_collection(
                    name=cname, embedding_function=self._embedding_fn
                )
                count = col_obj.count()
                if count == 0:
                    return []
                results = col_obj.query(
                    query_texts=[query], n_results=min(n_results, count)
                )
                return results["documents"][0] if results["documents"] else []
            except Exception as exc:
                print(f"[RAG] ChromaDB query error: {exc}")

        # Keyword fallback
        docs = self._fallback_docs.get(filename, [])
        query_words = set(query.lower().split())
        scored = sorted(
            ((sum(1 for w in query_words if w in doc.lower()), doc) for doc, _ in docs),
            reverse=True,
        )
        return [doc for score, doc in scored[:n_results] if score > 0]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _safe_collection_name(filename: str) -> str:
    """ChromaDB collection names must be 3-63 chars, alphanumeric + hyphens."""
    import re
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", filename)[:50]
    return name if len(name) >= 3 else name + "___"
