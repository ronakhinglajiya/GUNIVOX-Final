"""Build a BILINGUAL FAISS index from admission_brain.json.

Each program gets two embeddings — one for its English text_field, one for
the Gujarati text_field_gu. Both point back to the same metadata record.
Query "Data Science" hits the English embedding; query "ડેટા સાયન્સ" hits
the Gujarati one. No translate-at-query-time required, so RAG retrieval
costs only a single embedding call (~250ms) instead of translate+embed
(~750ms).

Prerequisite: run `scripts.translate_brain` first to populate text_field_gu
on every record. If text_field_gu is missing, that record is indexed
English-only (so the build still works on a fresh checkout).

Usage (from backend/):
    ../venv/Scripts/python.exe -m scripts.build_index
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import faiss
import numpy as np
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
BATCH_SIZE = 100


def format_program(item: dict) -> str:
    return (
        f"Program: {item.get('program', 'N/A')}. "
        f"Institute: {item.get('institute', 'N/A')}. "
        f"Duration: {item.get('duration', 'N/A')}. "
        f"Fees: {item.get('fees', 'N/A')} INR. "
        f"Eligibility: {item.get('eligibility', 'N/A')}. "
        f"Counsellor: {item.get('counsellor_name', 'N/A')}, "
        f"Phone: {item.get('counsellor_phone', 'N/A')}, "
        f"Email: {item.get('counsellor_email', 'N/A')}."
    )


def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY not set in .env")

    print(f"Reading {settings.admission_brain_json}")
    with open(settings.admission_brain_json, "r", encoding="utf-8") as f:
        data: list[dict] = json.load(f)

    client = OpenAI(api_key=settings.openai_api_key)
    print(f"Loaded {len(data)} programs. Embedding with {EMBED_MODEL}…")

    # Build a flat list of (faiss_idx → record, text, lang) so the same record
    # appears once per language. metadata is keyed by the FAISS row id and
    # carries the full program dict — retrieve() will return that dict.
    metadata: dict[int, dict] = {}
    texts: list[str] = []
    en_count = 0
    gu_count = 0

    for item in data:
        # English entry — always present.
        en_text = format_program(item)
        item["text_field"] = en_text
        faiss_idx = len(texts)
        metadata[faiss_idx] = {**item, "_lang": "en"}
        texts.append(en_text)
        en_count += 1

        # Gujarati entry — only if translate_brain populated it.
        gu_text = item.get("text_field_gu")
        if gu_text:
            faiss_idx = len(texts)
            metadata[faiss_idx] = {**item, "_lang": "gu"}
            texts.append(gu_text)
            gu_count += 1

    print(f"  English entries:  {en_count}")
    print(f"  Gujarati entries: {gu_count}")
    print(f"  Total FAISS rows: {len(texts)}")

    all_vectors: list[np.ndarray] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
        vecs = np.vstack([np.array(d.embedding, dtype="float32") for d in resp.data])
        all_vectors.append(vecs)
        print(f"  embedded {start + len(batch)}/{len(texts)}")

    embeddings = np.vstack(all_vectors)
    faiss.normalize_L2(embeddings)
    index = faiss.IndexFlatIP(EMBED_DIM)
    index.add(embeddings)

    faiss.write_index(index, str(settings.faiss_index_path))
    with open(settings.metadata_mapping_path, "wb") as f:
        pickle.dump(metadata, f)

    print(f"\nWrote FAISS index: {settings.faiss_index_path}")
    print(f"Wrote metadata:    {settings.metadata_mapping_path}")


if __name__ == "__main__":
    main()
