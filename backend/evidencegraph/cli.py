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
    return parser


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
    finally:
        conn.close()
    return 0
