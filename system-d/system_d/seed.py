"""Idempotently seed the System D demo database."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Store, get_pool

SEED_DIR = Path(__file__).resolve().parents[1] / "db" / "seed"


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def seed_data(store: Store, seed_dir: Path = SEED_DIR) -> dict[str, int]:
    politicians = _load_json(seed_dir / "politicians.json")
    claims = _load_json(seed_dir / "claims.json")
    strata_data = _load_json(seed_dir / "strata.json")
    shares = [row["share"] for row in strata_data["strata"]]
    share_sum = math.fsum(shares)
    if share_sum != 1.0:
        raise ValueError(f"stratum shares must sum exactly to 1.0, got {share_sum}")

    counts = {"politicians": 0, "claims": 0, "reactions": 0, "strata": 0}
    politician_ids: dict[str, str] = {}
    for politician in politicians:
        existing = store.get_politician(politician["name"])
        politician_ids[politician["name"]] = (
            str(existing["id"])
            if existing
            else store.add_politician(
                politician["name"],
                politician["party"],
                politician["region"],
                politician["context"],
            )
        )
        counts["politicians"] += int(existing is None)

    for claim in claims:
        politician_id = politician_ids[claim["politician"]]
        claim_id, inserted = store.add_claim(
            politician_id,
            claim["claim_text"],
            claim["source_platform"],
            claim["source_url"],
        )
        counts["claims"] += int(inserted)
        for comment in claim["comments"]:
            counts["reactions"] += int(store.add_social_reaction(claim_id, claim["source_platform"], comment))

    for stratum in strata_data["strata"]:
        _, inserted = store.add_stratum(
            stratum["region"],
            stratum["age_band"],
            stratum["gender"],
            stratum["urban_rural"],
            stratum["share"],
        )
        counts["strata"] += int(inserted)
    return counts


def main() -> None:
    settings = Settings.from_env()
    pool = get_pool(settings)
    try:
        with pool.connection() as conn:
            counts = seed_data(Store(conn))
    finally:
        pool.close()
    print(f"seeded {counts}")


if __name__ == "__main__":
    main()
