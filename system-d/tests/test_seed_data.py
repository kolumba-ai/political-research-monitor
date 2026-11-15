import json
import math
from collections import Counter
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parents[1] / "db" / "seed"


def read_seed(filename):
    return json.loads((SEED_DIR / filename).read_text(encoding="utf-8"))


def test_politician_seed_has_requested_context_and_current_offices():
    politicians = read_seed("politicians.json")
    assert [(person["name"], person["party"], person["region"]) for person in politicians] == [
        ("Claudia Sheinbaum Pardo", "Morena", "Nacional"),
        ("Clara Brugada Molina", "Morena", "Ciudad de México"),
        ("Samuel García Sepúlveda", "Movimiento Ciudadano", "Nuevo León"),
    ]
    assert [person["context"]["office"] for person in politicians] == [
        "Presidenta de México",
        "Jefa de Gobierno",
        "Gobernador",
    ]
    assert all(person["context"]["seed"] == "hand-curated demo seed" for person in politicians)


def test_claim_seed_has_ten_unverified_claims_and_six_to_eight_mixed_comments_each():
    claims = read_seed("claims.json")
    assert len(claims) == 10
    assert Counter(claim["politician"] for claim in claims) == {
        "Claudia Sheinbaum Pardo": 4,
        "Clara Brugada Molina": 3,
        "Samuel García Sepúlveda": 3,
    }
    allowed_platforms = {"tiktok", "x", "facebook", "youtube"}
    assert all(claim["source_url"] is None for claim in claims)
    assert all(claim["source_platform"] in allowed_platforms for claim in claims)
    assert all(6 <= len(claim["comments"]) <= 8 for claim in claims)
    assert len({(claim["politician"], claim["claim_text"]) for claim in claims}) == 10
    assert claims[0]["politician"] == claims[1]["politician"]
    assert claims[4]["politician"] == claims[5]["politician"]
    assert claims[0]["claim_text"] != claims[1]["claim_text"]
    assert claims[4]["claim_text"] != claims[5]["claim_text"]


def test_stratum_seed_is_the_rounded_product_design_with_exact_total():
    data = read_seed("strata.json")
    strata = data["strata"]
    assert data["note"] == "estimated shares (rough INEGI 2020 proportions), not official"
    assert len(strata) == 12
    assert len({(item["region"], item["age_band"], item["gender"]) for item in strata}) == 12
    assert all(item["urban_rural"] is None for item in strata)
    assert math.fsum(item["share"] for item in strata) == 1.0
    region_shares = {"Norte": 0.28, "Centro": 0.45, "Sur-Sureste": 0.27}
    age_shares = {"18-39": 0.47, "40+": 0.53}
    gender_shares = {"F": 0.52, "M": 0.48}
    expected = {
        (region, age, gender): round(region_share * age_share * gender_share, 4)
        for region, region_share in region_shares.items()
        for age, age_share in age_shares.items()
        for gender, gender_share in gender_shares.items()
    }
    for item in strata:
        assert item["share"] == expected[(item["region"], item["age_band"], item["gender"])]
