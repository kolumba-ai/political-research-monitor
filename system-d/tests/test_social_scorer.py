import re

import pytest

from system_d.agents.social_scorer import score_claim, score_unscored
from system_d.models import CommentScore, CommentScores
from system_d.prompts import SOCIAL_SCORER_SYSTEM


class SequentialLLM:
    def __init__(self, invalid=False):
        self.calls = []
        self.invalid = invalid

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        indexes = [int(value) for value in re.findall(r"^\[(\d+)\]", kwargs["user"], flags=re.MULTILINE)]
        if self.invalid:
            scores = [CommentScore(index=0, sentiment=0, stance="neutral") for _ in indexes]
        else:
            scores = [
                CommentScore(index=index, sentiment=index / 100, stance="believes") for index in reversed(indexes)
            ]
        assert kwargs["system"] == SOCIAL_SCORER_SYSTEM
        return CommentScores(scores=scores), None


def test_score_claim_batches_no_more_than_40_and_offsets_indexes():
    llm = SequentialLLM()
    comments = [f"Comentario {index}" for index in range(41)]
    scores = score_claim(llm, "Politician", "Claim", comments)
    assert len(llm.calls) == 2
    assert [score.index for score in scores] == list(range(41))
    assert llm.calls[0]["user"].split("Comments:\n", maxsplit=1)[1].count("\n") == 39
    assert "[0] Comentario 0" in llm.calls[0]["user"]
    assert "[0] Comentario 40" in llm.calls[1]["user"]


def test_score_claim_retries_invalid_indexes_then_raises():
    llm = SequentialLLM(invalid=True)
    with pytest.raises(ValueError, match="exactly once"):
        score_claim(llm, "Politician", "Claim", ["one", "two"])
    assert len(llm.calls) == 3


class FakeStore:
    def __init__(self):
        self.updates = []

    def get_unscored_claim_comments(self):
        return [
            {
                "politician_name": "Politician",
                "claim_text": "Claim",
                "reaction_ids": ["r1", "r2"],
                "comments": ["first", "second"],
            }
        ]

    def update_social_reaction(self, reaction_id, sentiment, stance):
        self.updates.append((reaction_id, sentiment, stance))


def test_score_unscored_persists_scores():
    store = FakeStore()
    assert score_unscored(store, SequentialLLM()) == 2
    assert store.updates == [("r1", 0.0, "believes"), ("r2", 0.01, "believes")]
