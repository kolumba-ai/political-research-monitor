"""Embed claims and merge near-duplicates into canonical aggregates."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..config import Settings
from ..db import Store
from ..llm import LLM, Embedder, Usage, UsageMeter
from ..models import CanonicalClaim
from ..prompts import CANONICALIZER_SYSTEM, CANONICALIZER_USER


@dataclass(frozen=True)
class AggregationStats:
    merged: int
    created: int
    usage: Usage


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def best_match(
    embedding: Sequence[float], candidates: list[tuple[str, Sequence[float]]], threshold: float
) -> str | None:
    if not candidates:
        return None
    candidate_id, similarity = max(
        ((candidate_id, _cosine_similarity(embedding, vector)) for candidate_id, vector in candidates),
        key=lambda item: item[1],
    )
    return candidate_id if similarity > threshold else None


def aggregate_pending(store: Store, llm: LLM, embedder: Embedder, settings: Settings) -> AggregationStats:
    meter = UsageMeter()
    missing_embeddings = store.list_claims_without_embedding()
    for start in range(0, len(missing_embeddings), 64):
        batch = missing_embeddings[start : start + 64]
        vectors, usage = embedder.embed([row["claim_text"] for row in batch])
        meter.add(usage)
        for row, vector in zip(batch, vectors, strict=True):
            store.update_claim_embedding(str(row["id"]), vector)

    merged = 0
    created = 0
    for claim in store.pending_claims_for_aggregation():
        embedding = list(claim["embedding"])
        candidates = store.nearest_aggregates(str(claim["politician_id"]), embedding)
        matched_id = best_match(embedding, candidates, settings.aggregate_similarity)
        if matched_id is not None:
            store.link_claim_to_aggregate(str(claim["id"]), matched_id)
            store.increment_aggregate_count(matched_id)
            merged += 1
            continue
        canonical, usage = llm.complete_json(
            system=CANONICALIZER_SYSTEM,
            user=CANONICALIZER_USER.format(
                politician=claim["politician_name"],
                claim=claim["claim_text"],
            ),
            output_model=CanonicalClaim,
            max_tokens=2048,
        )
        meter.add(usage)
        aggregate_id = store.add_aggregated_claim(
            str(claim["politician_id"]),
            canonical.canonical_text,
            canonical.testable,
            embedding,
        )
        store.link_claim_to_aggregate(str(claim["id"]), aggregate_id)
        created += 1
    return AggregationStats(merged=merged, created=created, usage=meter.usage)
