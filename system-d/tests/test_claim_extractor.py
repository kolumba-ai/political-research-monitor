from system_d.agents.claim_extractor import extract, store_extracted
from system_d.models import ExtractedClaim, ExtractionResult
from system_d.prompts import CLAIM_EXTRACTOR_SYSTEM


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.system = None

    def complete_json(self, **kwargs):
        self.system = kwargs["system"]
        assert kwargs["output_model"] is ExtractionResult
        return self.result, None


def test_extract_keeps_only_factual_claims_about_listed_name_accent_insensitively():
    llm = FakeLLM(
        ExtractionResult(
            claims=[
                ExtractedClaim(
                    claim_text="Una afirmación verificable.",
                    about_politician="CLÁUDIA SHEINBAUM PARDO",
                    is_factual_claim=True,
                    source_url=None,
                ),
                ExtractedClaim(
                    claim_text="Una opinión.",
                    about_politician="Claudia Sheinbaum Pardo",
                    is_factual_claim=False,
                    source_url=None,
                ),
                ExtractedClaim(
                    claim_text="Una afirmación sobre otra persona.",
                    about_politician="Otra Persona",
                    is_factual_claim=True,
                    source_url=None,
                ),
            ]
        )
    )
    result = extract(
        llm,
        {
            "content": "Texto",
            "platform": "x",
            "source_url": None,
            "politicians": ["Claudia Sheinbaum Pardo"],
        },
    )
    assert len(result) == 1
    assert result[0].about_politician == "Claudia Sheinbaum Pardo"
    assert llm.system == CLAIM_EXTRACTOR_SYSTEM


class FakeStore:
    def __init__(self):
        self.claims = []
        self.reactions = []

    def get_politician(self, name):
        return {"id": "politician-id"} if name == "Claudia Sheinbaum Pardo" else None

    def add_claim(self, politician_id, claim_text, platform, source_url):
        self.claims.append((politician_id, claim_text, platform, source_url))
        return "claim-id", True

    def add_social_reaction(self, claim_id, platform, comment_text):
        self.reactions.append((claim_id, platform, comment_text))
        return True


def test_store_extracted_persists_claim_comments_and_uses_comment_platform():
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                claim_text="Una afirmación verificable.",
                about_politician="Claudia Sheinbaum Pardo",
                is_factual_claim=True,
                source_url=None,
            )
        ]
    )
    store = FakeStore()
    claim_ids = store_extracted(
        store,
        FakeLLM(result),
        {
            "content": "Texto",
            "platform": "facebook",
            "source_url": "https://example.test/post",
            "politicians": ["Claudia Sheinbaum Pardo"],
            "comments": ["Comentario sencillo", {"text": "Comentario de video", "platform": "youtube"}],
        },
    )
    assert claim_ids == ["claim-id"]
    assert store.claims == [("politician-id", "Una afirmación verificable.", "facebook", "https://example.test/post")]
    assert store.reactions == [
        ("claim-id", "facebook", "Comentario sencillo"),
        ("claim-id", "youtube", "Comentario de video"),
    ]
