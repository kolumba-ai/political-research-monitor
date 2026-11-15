from system_d.agents.aggregator import aggregate_pending, best_match
from system_d.config import Settings
from system_d.llm import Usage
from system_d.models import CanonicalClaim


def test_best_match_requires_similarity_strictly_above_threshold():
    assert best_match([1, 0], [("near", [0.91, 0.4146])], 0.90) == "near"
    assert best_match([1, 0], [("boundary", [0.9, (1 - 0.9**2) ** 0.5])], 0.90) is None


def test_best_match_selects_highest_candidate_and_handles_empty_candidates():
    candidates = [
        ("lower", [0.91, (1 - 0.91**2) ** 0.5]),
        ("best", [0.98, (1 - 0.98**2) ** 0.5]),
    ]
    assert best_match([1, 0], candidates, 0.90) == "best"
    assert best_match([1, 0], [], 0.90) is None
    assert best_match([0, 0], [("zero", [1, 0])], 0.90) is None


class FakeStore:
    def __init__(self):
        self.embeddings = []
        self.links = []
        self.increments = []
        self.new_aggregates = []
        self.pending = [
            {
                "id": "claim-1",
                "politician_id": "politician-1",
                "politician_name": "Politician",
                "claim_text": "first claim",
                "embedding": [1.0, 0.0],
            },
            {
                "id": "claim-2",
                "politician_id": "politician-1",
                "politician_name": "Politician",
                "claim_text": "second claim",
                "embedding": [0.0, 1.0],
            },
        ]

    def list_claims_without_embedding(self):
        return [{"id": "claim-1", "claim_text": "first claim"}]

    def update_claim_embedding(self, claim_id, embedding):
        self.embeddings.append((claim_id, embedding))

    def pending_claims_for_aggregation(self):
        return self.pending

    def nearest_aggregates(self, politician_id, embedding):
        if embedding == [1.0, 0.0]:
            return [("existing", [0.95, (1 - 0.95**2) ** 0.5])]
        return []

    def link_claim_to_aggregate(self, claim_id, aggregate_id):
        self.links.append((claim_id, aggregate_id))

    def increment_aggregate_count(self, aggregate_id):
        self.increments.append(aggregate_id)

    def add_aggregated_claim(self, politician_id, canonical_text, testable, embedding):
        self.new_aggregates.append((politician_id, canonical_text, testable, embedding))
        return "created"


class FakeEmbedder:
    def embed(self, texts):
        assert texts == ["first claim"]
        return [[1.0, 0.0]], Usage("text-embedding-3-small", 3, 0)


class FakeLLM:
    def complete_json(self, **kwargs):
        assert kwargs["output_model"] is CanonicalClaim
        return CanonicalClaim(canonical_text="Canonical claim", testable=True), Usage("claude-sonnet-5-5", 5, 2)


def test_aggregate_pending_embeds_merges_and_canonicalizes_with_fake_store():
    store = FakeStore()
    result = aggregate_pending(store, FakeLLM(), FakeEmbedder(), Settings())
    assert result.merged == 1
    assert result.created == 1
    assert store.embeddings == [("claim-1", [1.0, 0.0])]
    assert store.increments == ["existing"]
    assert store.links == [("claim-1", "existing"), ("claim-2", "created")]
    assert store.new_aggregates == [("politician-1", "Canonical claim", True, [0.0, 1.0])]
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 2
