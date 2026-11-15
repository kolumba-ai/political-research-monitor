"""Extract checkable claims and persist them with their comments."""

from __future__ import annotations

import unicodedata
from typing import Any

from ..db import Store
from ..llm import LLM
from ..models import ExtractedClaim, ExtractionResult
from ..prompts import CLAIM_EXTRACTOR_SYSTEM, CLAIM_EXTRACTOR_USER


def _normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def extract(llm: LLM, post: dict[str, Any]) -> list[ExtractedClaim]:
    politicians = list(post["politicians"])
    result, _ = llm.complete_json(
        system=CLAIM_EXTRACTOR_SYSTEM,
        user=CLAIM_EXTRACTOR_USER.format(
            politicians=", ".join(politicians),
            platform=post.get("platform", ""),
            source_url=post.get("source_url") or "null",
            content=post["content"],
        ),
        output_model=ExtractionResult,
        max_tokens=2048,
    )
    canonical_names = {_normalize_name(name): name for name in politicians}
    extracted = []
    for claim in result.claims:
        canonical_name = canonical_names.get(_normalize_name(claim.about_politician))
        if claim.is_factual_claim and canonical_name:
            extracted.append(claim.model_copy(update={"about_politician": canonical_name}))
    return extracted


def store_extracted(store: Store, llm: LLM, post: dict[str, Any]) -> list[str]:
    extracted = extract(llm, post)
    comments = post.get("comments") or []
    claim_ids = []
    for claim in extracted:
        politician = store.get_politician(claim.about_politician)
        if politician is None:
            continue
        claim_id, _ = store.add_claim(
            str(politician["id"]),
            claim.claim_text,
            post.get("platform"),
            claim.source_url or post.get("source_url"),
        )
        claim_ids.append(claim_id)
        for comment in comments:
            if isinstance(comment, dict):
                comment_text = comment.get("text") or comment.get("comment_text")
                platform = comment.get("platform") or post.get("platform")
            else:
                comment_text = str(comment)
                platform = post.get("platform")
            if comment_text:
                store.add_social_reaction(claim_id, platform, comment_text)
    return claim_ids
