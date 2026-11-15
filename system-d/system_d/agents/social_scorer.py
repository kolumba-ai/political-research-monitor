"""Score claim-related comments in bounded batches."""

from __future__ import annotations

from ..db import Store
from ..llm import LLM
from ..models import CommentScore, CommentScores
from ..prompts import SOCIAL_SCORER_SYSTEM, SOCIAL_SCORER_USER

MAX_COMMENTS_PER_CALL = 40
MAX_INDEX_ATTEMPTS = 3


def _score_batch(llm: LLM, politician: str, claim: str, comments: list[str], offset: int) -> list[CommentScore]:
    numbered = "\n".join(f"[{index}] {text}" for index, text in enumerate(comments))
    user = SOCIAL_SCORER_USER.format(politician=politician, claim=claim, comments=numbered)
    expected = list(range(len(comments)))
    for _ in range(MAX_INDEX_ATTEMPTS):
        result, _ = llm.complete_json(
            system=SOCIAL_SCORER_SYSTEM,
            user=user,
            output_model=CommentScores,
            max_tokens=2048,
        )
        indexes = [score.index for score in result.scores]
        if sorted(indexes) == expected:
            return [
                CommentScore(index=score.index + offset, sentiment=score.sentiment, stance=score.stance)
                for score in sorted(result.scores, key=lambda item: item.index)
            ]
    raise ValueError(f"scorer indexes must contain each index exactly once from 0 to {len(comments) - 1}")


def score_claim(llm: LLM, politician: str, claim: str, comments: list[str]) -> list[CommentScore]:
    scores = []
    for offset in range(0, len(comments), MAX_COMMENTS_PER_CALL):
        batch = comments[offset : offset + MAX_COMMENTS_PER_CALL]
        scores.extend(_score_batch(llm, politician, claim, batch, offset))
    return scores


def score_unscored(store: Store, llm: LLM) -> int:
    updated = 0
    for group in store.get_unscored_claim_comments():
        scores = score_claim(
            llm,
            group["politician_name"],
            group["claim_text"],
            group["comments"],
        )
        for reaction_id, score in zip(group["reaction_ids"], scores, strict=True):
            store.update_social_reaction(str(reaction_id), score.sentiment, score.stance)
            updated += 1
    return updated
