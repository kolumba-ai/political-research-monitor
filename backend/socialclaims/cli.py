"""`python -m socialclaims <command>` — System B Phase 0/1 operations. Paid Oriane calls need --confirm-spend."""
from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings
from .db import connect, migrate


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _graph(s):
    from .systema import open_reader
    return open_reader(s)


def _pid(graph, value: str, country: str | None) -> str:
    from .systema import resolve_name
    return value if value.startswith("person:") else resolve_name(graph, value, country)


def _client(s):
    from .oriane import OrianeClient
    return OrianeClient(s.oriane_api_key, s.oriane_base_url, s.oriane_timeout_s, s.oriane_max_retries)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="socialclaims")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    p = sub.add_parser("list", help="System A persons with their tracking status (no DB writes)")
    p.add_argument("--country", default="MX")
    p.add_argument("--all", action="store_true", help="include ineligible persons")
    p = sub.add_parser("sync-refs", help="write politician_ref versions from System A")
    p.add_argument("ids", nargs="*")
    p.add_argument("--country", default="MX")
    p = sub.add_parser("show-ref")
    p.add_argument("id")
    p.add_argument("--country", default="MX")
    p = sub.add_parser("query", help="print the Oriane request for a politician (no API call)")
    p.add_argument("id")
    p.add_argument("--country", default="MX")
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p = sub.add_parser("add-term")
    p.add_argument("id")
    p.add_argument("kind", choices=["variant", "hashtag", "exclude_variant"])
    p.add_argument("value")
    p.add_argument("--by", required=True)
    p.add_argument("--note")
    p = sub.add_parser("redirect", help="record a System A identity merge")
    p.add_argument("old_id")
    p.add_argument("new_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    p = sub.add_parser("discover-accounts", help="propose handles via Oriane profile search (paid)")
    p.add_argument("id")
    p.add_argument("--country", default="MX")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--confirm-spend", action="store_true")
    p = sub.add_parser("approve-account")
    p.add_argument("id")
    p.add_argument("platform", choices=["tiktok", "instagram", "youtube"])
    p.add_argument("handle")
    p.add_argument("--by", required=True)
    p.add_argument("--source", action="append", required=True, help="independent source URL (repeatable)")
    p.add_argument("--profile-id")
    p = sub.add_parser("reject-account")
    p.add_argument("id")
    p.add_argument("platform")
    p.add_argument("handle")
    p.add_argument("--by", required=True)
    p.add_argument("--note", default="")
    p = sub.add_parser("capture", help="capture Oriane posts for politicians (paid unless --dry-run)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--country", default="MX")
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("--max-posts", type=int)
    p.add_argument("--allow-review", action="store_true", help="also capture single_source officeholders")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm-spend", action="store_true")
    sub.add_parser("usage", help="Oriane credits used this month vs ceiling")
    sub.add_parser("purge-expired", help="delete raw payloads past retention")
    args = ap.parse_args(argv)
    s = get_settings()

    if args.cmd == "list":
        from .systema import list_persons
        snaps = list_persons(_graph(s), args.country, include_ineligible=args.all)
        _out([{"politician_id": x.politician_id, "name": x.name, "tracking_status": x.tracking_status,
               "evidence_status": x.evidence_status, "pipeline_mode": x.pipeline_mode,
               "offices": [o.name for o in x.current_offices]} for x in snaps])
        return 0

    conn = connect(s.database_url)
    if args.cmd == "migrate":
        _out({"applied": migrate(conn)})
        return 0
    from . import accounts, identity
    from .capture import Capturer, month_credits_used, purge_expired, utcnow

    if args.cmd == "usage":
        _out({"month_credits_used": month_credits_used(conn, utcnow()),
              "monthly_ceiling": s.oriane_monthly_credit_ceiling})
        return 0
    if args.cmd == "purge-expired":
        _out({"purged": purge_expired(conn, s)})
        return 0
    if args.cmd == "redirect":
        identity.add_redirect(conn, args.old_id, args.new_id, args.reason, args.by)
        return 0

    graph = _graph(s)
    platforms = s.csv(s.platforms)
    if args.cmd == "sync-refs":
        from .systema import list_persons
        ids = [_pid(graph, i, args.country) for i in args.ids] or \
            [x.politician_id for x in list_persons(graph, args.country)]
        _out([identity.sync_ref(conn, graph, i, platforms) for i in ids])
    elif args.cmd == "show-ref":
        _out(identity.current_ref(conn, identity.resolve_redirect(conn, _pid(graph, args.id, args.country))))
    elif args.cmd == "query":
        plan = Capturer(conn, graph, None, s).plan(_pid(graph, args.id, args.country),
                                                   date_from=args.date_from, date_to=args.date_to)
        plan.pop("ref")
        _out(plan)
    elif args.cmd == "add-term":
        identity.add_term(conn, args.id, args.kind, args.value, args.by, args.note)
    elif args.cmd == "approve-account":
        accounts.approve(conn, args.id, args.platform, args.handle, args.by, args.source, args.profile_id)
    elif args.cmd == "reject-account":
        accounts.reject(conn, args.id, args.platform, args.handle, args.by, args.note)
    elif args.cmd == "discover-accounts":
        if not args.confirm_spend:
            print("refusing: Oriane profile search is billed; pass --confirm-spend", file=sys.stderr)
            return 2
        from .systema import read_person
        snap = read_person(graph, _pid(graph, args.id, args.country))
        _out(accounts.discover(conn, _client(s), snap, platforms, limit=args.limit))
    elif args.cmd == "capture":
        cap = Capturer(conn, graph, None if args.dry_run else (_client(s) if args.confirm_spend else None), s)
        if not args.dry_run and not args.confirm_spend:
            print("refusing: Oriane capture is billed; pass --confirm-spend (or --dry-run)", file=sys.stderr)
            return 2
        kw = dict(date_from=args.date_from, date_to=args.date_to, max_posts=args.max_posts,
                  allow_review=args.allow_review)
        results = []
        for i in args.ids:
            pid = _pid(graph, i, args.country)
            if args.dry_run:
                plan = cap.plan(pid, **kw)
                plan.pop("ref")
                results.append(plan)
            else:
                results.append(cap.capture(pid, **kw))
        _out(results)
    return 0
