"""Command-line operations for the EvidenceGraph M0 skeleton."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime

from .config import get_settings
from .db import connect, migrate
from .fetch import Fetcher
from .intake import queue_rows, run_intake
from .registry import exclude_domains, load_registry, lookup


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2,
                      default=lambda item: item.isoformat() if isinstance(item, (date, datetime)) else str(item))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evidencegraph")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    load = commands.add_parser("load-registry")
    load.add_argument("--country", default="MX")
    lookup_cmd = commands.add_parser("registry-lookup")
    lookup_cmd.add_argument("url")
    intake_cmd = commands.add_parser("intake")
    intake_cmd.add_argument("ids", nargs="*")
    intake_cmd.add_argument("--dry-run", action="store_true")
    queue_cmd = commands.add_parser("queue")
    queue_cmd.add_argument("--status", default="queued")
    queue_cmd.add_argument("--limit", type=int, default=20)
    enqueue = commands.add_parser("enqueue-url")
    enqueue.add_argument("urls", nargs="+")
    fetch_cmd = commands.add_parser("fetch")
    fetch_cmd.add_argument("urls", nargs="*")
    fetch_cmd.add_argument("--from-queue", action="store_true")
    fetch_cmd.add_argument("--limit", type=int, default=20)
    enrich_cmd = commands.add_parser(
        "enrich", help="Exa search, fetch, summarise and judge evidence for queued claims; write them to past.dev")
    enrich_cmd.add_argument("ids", nargs="*", help="politician ids (default: every queued proposition)")
    enrich_cmd.add_argument("--limit", type=_positive_int, default=10, help="propositions to enrich, by priority")
    enrich_cmd.add_argument("--country", default="MX")
    enrich_cmd.add_argument("--dry-run", action="store_true", help="list what would run and the cost ceiling; free")
    enrich_cmd.add_argument("--confirm-spend", action="store_true", help="required: Exa, Anthropic and past.dev bill")
    return parser


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _enricher(conn, settings, country: str):
    from socialclaims.claims import AnthropicLLM
    from socialclaims.pastdev import PastDevClient

    from .config import exa_gateway
    from .enrich import Enricher

    llm = AnthropicLLM(settings.anthropic_api_key, settings.enrich_model, settings.anthropic_base_url,
                       max_tokens=2000, rate_limiter_database_url=settings.database_url,
                       requests_per_second=settings.anthropic_requests_per_second)
    client = PastDevClient(settings.past_dev_api_key, settings.past_dev_base_url,
                           rate_limiter_database_url=settings.database_url,
                           requests_per_second=settings.pastdev_requests_per_second)
    return Enricher(conn, settings, llm=llm, gateway=exa_gateway(settings), fetcher=Fetcher(conn, settings),
                    client=client, country=country)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    conn = connect(settings.database_url)
    try:
        if args.command == "migrate":
            print(_json({"applied": migrate(conn)}))
        elif args.command == "load-registry":
            print(_json({"loaded": load_registry(conn, args.country), "country": args.country.upper()}))
        elif args.command == "registry-lookup":
            print(_json({"registry": lookup(conn, args.url), "excluded_domains": exclude_domains(conn)}))
        elif args.command == "intake":
            result = run_intake(conn, args.ids or None, settings.intake_min_checkworthiness,
                                settings.mapper_version, args.dry_run)
            print(_json(result))
        elif args.command == "queue":
            print(_json(queue_rows(conn, args.status, args.limit)))
        elif args.command == "enqueue-url":
            fetcher = Fetcher(conn, settings)
            try:
                print(_json([{"url": url, "url_hash": fetcher.enqueue(url)} for url in args.urls]))
            finally:
                fetcher.close()
        elif args.command == "fetch":
            if args.from_queue and args.urls:
                parser.error("provide URLs or --from-queue, not both")
            if not args.from_queue and not args.urls:
                parser.error("provide at least one URL or use --from-queue")
            fetcher = Fetcher(conn, settings)
            try:
                results = fetcher.fetch_queue(args.limit) if args.from_queue else [fetcher.fetch(url) for url in args.urls]
                print(_json(results))
            finally:
                fetcher.close()
        elif args.command == "enrich":
            from .enrich import dry_run

            if args.dry_run:
                print(_json(dry_run(conn, settings, args.ids or None, args.limit)))
                return 0
            if not args.confirm_spend:
                parser.error("enrich calls Exa, Anthropic and past.dev (billed): run --dry-run first, "
                             "then pass --confirm-spend")
            missing = [name for name, value in (("EXA_API_KEY", settings.exa_api_key),
                                                ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
                                                ("PAST_DEV_API_KEY", settings.past_dev_api_key)) if not value]
            if missing:
                parser.error(f"missing {', '.join(missing)}")
            enricher = _enricher(conn, settings, args.country)
            try:
                print(_json(enricher.run(args.ids or None, args.limit)))
            finally:
                enricher.fetcher.close()
    finally:
        conn.close()
    return 0
