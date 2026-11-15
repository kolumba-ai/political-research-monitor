from evidencegraph.cli import build_parser


def test_cli_parses_all_m0_commands():
    parser = build_parser()
    assert parser.parse_args(["migrate"]).command == "migrate"
    assert parser.parse_args(["load-registry", "--country", "MX"]).country == "MX"
    assert parser.parse_args(["registry-lookup", "https://www.gob.mx/presidencia"]).url.endswith("presidencia")
    intake = parser.parse_args(["intake", "person:MX:one", "--dry-run"])
    assert intake.ids == ["person:MX:one"] and intake.dry_run
    assert parser.parse_args(["queue", "--status", "queued", "--limit", "8"]).limit == 8
    assert parser.parse_args(["enqueue-url", "https://www.inegi.org.mx/"]).urls
    fetch = parser.parse_args(["fetch", "--from-queue", "--limit", "5"])
    assert fetch.from_queue and fetch.limit == 5
