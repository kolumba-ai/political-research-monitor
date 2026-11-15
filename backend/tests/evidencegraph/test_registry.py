from datetime import datetime, timezone

from evidencegraph.registry import exclude_domains, load_registry, lookup


def test_registry_seed_defaults_and_longest_path_match(conn):
    assert load_registry(conn) > 60

    inegi = lookup(conn, "https://www.inegi.org.mx/temas/seguridad/")
    assert inegi["source_class"] == "official_statistics"
    assert inegi["licence_class"] == "open_gov"
    assert inegi["can_store_full_text"] is True

    presidency = lookup(conn, "https://www.gob.mx/presidencia/prensa")
    assert presidency["domain"] == "gob.mx/presidencia"
    assert presidency["is_claimant_for"] == [
        "body:MX:national:presidency-and-federal-executive-administration",
        "office:MX:national:presidency-and-federal-executive-administration:president-of-mexico",
    ]
    assert lookup(conn, "https://unknown.example/a") is None
    assert {"tiktok.com", "youtube.com"} <= set(exclude_domains(conn))


def test_reviewed_registry_rows_are_not_overwritten(conn):
    load_registry(conn)
    conn.execute("UPDATE ev_source_registry SET notes = 'reviewed note', reviewed_at = %s "
                 "WHERE domain = 'unam.mx'", (datetime.now(timezone.utc),))

    load_registry(conn)

    row = lookup(conn, "https://unam.mx/")
    assert row["notes"] == "reviewed note"
    assert row["reviewed_at"] is not None
