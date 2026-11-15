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
    return OrianeClient(
        s.oriane_api_key, s.oriane_base_url, s.oriane_timeout_s, s.oriane_max_retries,
        rate_limiter_database_url=s.rate_limiter_database_url,
        requests_per_second=s.oriane_requests_per_second,
    )


def _parse_shard(value: str) -> tuple[int, int]:
    parts = value.split("/")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("shard must be I/N with 0 <= I < N")
    try:
        index, count = (int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("shard must be I/N with 0 <= I < N") from exc
    if count <= 0 or index < 0 or index >= count:
        raise argparse.ArgumentTypeError("shard must be I/N with 0 <= I < N")
    return index, count


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


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
    p.add_argument("--branches", help="comma-separated subset of query branches, e.g. own,coauthored")
    p.add_argument("--allow-review", action="store_true", help="also capture single_source officeholders")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm-spend", action="store_true")
    p = sub.add_parser("replay-raw", help="ingest pages saved by a failed capture, without calling Oriane")
    p.add_argument("run_dir")
    p.add_argument("id")
    p.add_argument("--country", default="MX")
    p.add_argument("--allow-review", action="store_true")
    p = sub.add_parser("rematch", help="move name-only matches lacking a current name variant to 'low' (no API call)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--country", default="MX")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("segment", help="Phase 2: segments + speaker roles from saved snapshots (no API call)")
    p.add_argument("ids", nargs="*", help="politicians (default: everyone with captured posts)")
    p.add_argument("--country", default="MX")
    p = sub.add_parser("extract-claims", help="Phase 3: LLM claim extraction from segments (paid unless --dry-run)")
    p.add_argument("ids", nargs="+")
    p.add_argument("--country", default="MX")
    p.add_argument("--max-posts", type=int, help="per politician (default: CLAIM_MAX_POSTS_PER_RUN)")
    p.add_argument("--max-tokens", type=_positive_int, default=8000,
                   help="maximum Anthropic output tokens (default: 8000)")
    p.add_argument("--dry-run", action="store_true", help="print pending counts and the first prompt; no LLM call")
    p.add_argument("--confirm-spend", action="store_true")
    p.add_argument("--shard", type=_parse_shard, default=(0, 1),
                   help="I/N: only posts whose id hashes to share I of N (parallel workers)")
    p = sub.add_parser("regrade-claims", help="re-check stored claim responses without an LLM call")
    p.add_argument("ids", nargs="+")
    p.add_argument("--country", default="MX")
    p.add_argument("--from-checks", required=True)
    p = sub.add_parser("pull-pastdev", help="export the country's claim data points from past.dev (read-only, free)")
    p.add_argument("--country", default="MX")
    p.add_argument("--out", help="export directory (default: CLAIMS_EXPORT_DIR)")
    p = sub.add_parser("push-pastdev", help="send current claims from Postgres to past.dev (billed in past.dev credits)")
    p.add_argument("ids", nargs="*", help="politician ids or names (default: every politician with claims)")
    p.add_argument("--country", default="MX")
    p.add_argument("--extractor-version", help="only claims of this version (default: latest extraction per post)")
    p.add_argument("--dry-run", action="store_true",
                   help="count data points and estimate credits; past.dev is only read (if a key is set)")
    p.add_argument("--include-legacy", action="store_true",
                   help="also send posts already in past.dev under the earlier pipeline's oriane:<id>:v<n> ids")
    p.add_argument("--confirm-spend", action="store_true")
    p.add_argument("--max-credits", type=_positive_int, help="refuse when the estimate exceeds this many credits")
    p = sub.add_parser("thumbnails", help="backfill thumbnail URLs from raw payloads and download them (no Oriane API call)")
    p.add_argument("--format", action="append", dest="formats", help="only these post formats (repeatable), e.g. video")
    p.add_argument("--no-download", action="store_true", help="only backfill thumbnail_url")
    p.add_argument("--redownload", action="store_true")
    p.add_argument("--out", default=None, help="directory for images (default THUMBNAIL_DIR)")
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

    if args.cmd == "pull-pastdev":
        from .pastdev import PastDevClient, pull
        client = PastDevClient(
            s.past_dev_api_key, s.past_dev_base_url, s.past_dev_timeout_s,
            rate_limiter_database_url=s.rate_limiter_database_url,
            requests_per_second=s.pastdev_requests_per_second,
        )
        _out(pull(client, _graph(s), args.country, args.out or s.claims_export_dir))
        return 0

    conn = connect(s.database_url)
    if args.cmd == "push-pastdev":
        from .pastdev import PastDevClient
        from .pastdev_push import push
        if not args.dry_run and not args.confirm_spend:
            print("refusing: past.dev ingest is billed; pass --confirm-spend (or --dry-run)", file=sys.stderr)
            return 2
        ids = [_pid(_graph(s), i, args.country) for i in args.ids] or None
        client = None if args.dry_run and not s.past_dev_api_key else PastDevClient(
            s.past_dev_api_key, s.past_dev_base_url, s.past_dev_timeout_s,
            rate_limiter_database_url=s.rate_limiter_database_url,
            requests_per_second=s.pastdev_requests_per_second,
        )
        out = push(conn, client, args.country, graph=_graph(s), politician_ids=ids,
                   extractor_version=args.extractor_version, dry_run=args.dry_run, max_credits=args.max_credits,
                   skip_legacy=not args.include_legacy)
        _out(out)
        return 1 if out.get("error") or out.get("errors") else 0
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
    if args.cmd == "thumbnails":
        from . import thumbnails
        out = {"backfill": thumbnails.backfill(conn, s.raw_storage_dir)}
        if not args.no_download:
            out["download"] = thumbnails.download(conn, args.out or s.thumbnail_dir, formats=args.formats,
                                                  redownload=args.redownload)
        _out(out)
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
        _out(accounts.discover(conn, _client(s), snap, platforms, limit=args.limit,
                               monthly_ceiling=s.oriane_monthly_credit_ceiling))
    elif args.cmd == "capture":
        cap = Capturer(conn, graph, None if args.dry_run else (_client(s) if args.confirm_spend else None), s)
        if not args.dry_run and not args.confirm_spend:
            print("refusing: Oriane capture is billed; pass --confirm-spend (or --dry-run)", file=sys.stderr)
            return 2
        from .query import BRANCHES
        branches = [b.strip() for b in args.branches.split(",")] if args.branches else None
        if branches and set(branches) - set(BRANCHES):
            print(f"unknown branches: {sorted(set(branches) - set(BRANCHES))}", file=sys.stderr)
            return 2
        kw = dict(date_from=args.date_from, date_to=args.date_to, max_posts=args.max_posts,
                  allow_review=args.allow_review, branches=branches)
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
    elif args.cmd == "rematch":
        from .capture import rematch
        _out([rematch(conn, graph, s, _pid(graph, i, args.country), apply=args.apply) for i in args.ids])
    elif args.cmd == "segment":
        from .segments import process_politician
        ids = [_pid(graph, i, args.country) for i in args.ids] or [r["politician_id"] for r in conn.execute(
            "SELECT DISTINCT politician_id FROM run_post ORDER BY 1").fetchall()]
        _out([process_politician(conn, graph, s.raw_storage_dir, i) for i in ids])
    elif args.cmd == "extract-claims":
        from .claims import AnthropicLLM, Extractor, build_prompt
        shard = args.shard
        if not args.dry_run and not args.confirm_spend:
            print("refusing: LLM extraction is billed; pass --confirm-spend (or --dry-run)", file=sys.stderr)
            return 2
        if not args.dry_run and not s.anthropic_api_key:
            print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
            return 2
        llm = AnthropicLLM(s.anthropic_api_key or "", s.claim_model, s.anthropic_base_url,
                           max_tokens=args.max_tokens, rate_limiter_database_url=s.rate_limiter_database_url,
                           requests_per_second=s.anthropic_requests_per_second)
        ex = Extractor(conn, graph, llm, args.country)
        cap = min(args.max_posts or s.claim_max_posts_per_run, s.claim_max_posts_per_run)
        results = []
        for i in args.ids:
            pid = _pid(graph, i, args.country)
            if args.dry_run:
                todo = ex.pending(pid, shard)
                results.append({"politician_id": pid, "extractor_version": ex.version, "pending": len(todo),
                                "would_extract": min(len(todo), cap)})
            else:
                results.append(ex.run(pid, cap, shard))
        _out(results)
    elif args.cmd == "regrade-claims":
        from .claims import Extractor

        class NoCallLLM:
            def __init__(self, model):
                self.model = model

            def extract(self, system, prompt, tool):
                raise RuntimeError("regrade-claims must not call the LLM")

        ex = Extractor(conn, graph, NoCallLLM(s.claim_model), args.country)
        _out([ex.regrade(_pid(graph, i, args.country), args.from_checks) for i in args.ids])
    elif args.cmd == "replay-raw":
        from pathlib import Path
        from .capture import RawReplayClient
        rc = RawReplayClient(Path(args.run_dir))
        window = rc.body["filters"]["publishedAt"]
        cap = Capturer(conn, graph, rc, s, clock=lambda: rc.captured_at)
        cap.usage_purpose = "capture-replay"
        _out(cap.capture(_pid(graph, args.id, args.country), date_from=window["after"], date_to=window["before"],
                         max_posts=rc.max_posts, allow_review=args.allow_review,
                         exclude_ids=rc.exclude_ids,
                         branches=sorted({q["name"].split(":")[0] for q in rc.body["queries"][0]["queries"]})))
    return 0
