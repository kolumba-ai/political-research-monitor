import { useEffect, useMemo, useState } from "react";
import { ChevronRight, ExternalLink, MessageSquareQuote, Search } from "lucide-react";
import { claimsApi, type ClaimStats, type ClaimsSummary, type PoliticianClaims, type SocialClaim } from "../lib/api";
import { CLAIM_TYPE_COLORS, colorOf, compact, humanize, PLATFORM_META, pct, SPEAKER_NAMES, titleCase } from "../lib/claims";
import { fmtDate } from "../lib/presentation";
import { Badge, Button, Card, CardHeader } from "./ui";

const PAGE = 15;

export function Donut({ counts, colors, centerLabel, size = 132 }: {
  counts: Record<string, number>; colors: Record<string, string>; centerLabel: string; size?: number;
}) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const r = 42, c = 2 * Math.PI * r;
  let offset = 0;
  return <div className="flex items-center gap-4">
    <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={`${total} ${centerLabel}`} className="shrink-0 -rotate-90">
      <circle cx="50" cy="50" r={r} fill="none" stroke="var(--color-ink-700)" strokeWidth="12" />
      {Object.entries(counts).map(([k, v]) => {
        const len = (v / (total || 1)) * c;
        const seg = <circle key={k} cx="50" cy="50" r={r} fill="none" stroke={colorOf(colors, k)} strokeWidth="12"
          strokeDasharray={`${Math.max(len - 1.2, 0)} ${c - Math.max(len - 1.2, 0)}`} strokeDashoffset={-offset}><title>{`${humanize(k)}: ${v}`}</title></circle>;
        offset += len;
        return seg;
      })}
      <g className="rotate-90" style={{ transformOrigin: "50px 50px" }}>
        <text x="50" y="49" textAnchor="middle" className="fill-fg text-[19px] font-bold">{total}</text>
        <text x="50" y="62" textAnchor="middle" className="fill-muted text-[8px]">{centerLabel}</text>
      </g>
    </svg>
    <ul className="min-w-0 flex-1 space-y-1.5 text-xs">
      {Object.entries(counts).map(([k, v]) => <li key={k} className="flex items-center gap-2">
        <span className="size-2.5 shrink-0 rounded-full" style={{ background: colorOf(colors, k) }} />
        <span className="min-w-0 flex-1 truncate">{titleCase(k)}</span><span className="text-muted tabular-nums">{pct(v, total)}%</span>
      </li>)}
    </ul>
  </div>;
}

export function BarList({ counts, colors, names }: { counts: Record<string, number>; colors?: Record<string, string>; names?: Record<string, string> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const max = Math.max(1, ...Object.values(counts));
  return <ul className="space-y-2 text-xs">
    {Object.entries(counts).map(([k, v]) => <li key={k} className="grid grid-cols-[minmax(0,7.5rem)_1fr_2.5rem] items-center gap-2">
      <span className="truncate">{names?.[k] ?? titleCase(k)}</span>
      <span className="h-2 overflow-hidden rounded-full bg-ink-700"><span className="block h-full rounded-full" style={{ width: `${(100 * v) / max}%`, background: colors ? colorOf(colors, k) : "var(--color-accent)" }} /></span>
      <span className="text-right text-muted tabular-nums">{pct(v, total)}%</span>
    </li>)}
  </ul>;
}

export function Timeline({ days, height = "h-14" }: { days: ClaimStats["by_day"]; height?: string }) {
  if (!days.length) return null;
  const max = Math.max(...days.map((d) => d.claims));
  return <div>
    <div className={`flex ${height} items-end gap-px`} role="img" aria-label="Claims per day">
      {days.map((d) => <span key={d.date} title={`${fmtDate(d.date)}: ${d.claims} claims`} className="min-w-0 flex-1 rounded-t-sm bg-accent/75 hover:bg-accent" style={{ height: `${Math.max(8, (100 * d.claims) / max)}%` }} />)}
    </div>
    <div className="mt-1 flex justify-between text-[10px] text-muted"><span>{fmtDate(days[0].date)}</span><span>{fmtDate(days.at(-1)!.date)}</span></div>
  </div>;
}

export function PlatformTile({ platform, date }: { platform: string | null; date?: string | null }) {
  const meta = PLATFORM_META[platform || ""] ?? { name: platform || "Post", color: "#64748b", short: "•" };
  return <div className="flex w-20 shrink-0 flex-col items-center justify-center gap-1 rounded-lg py-2 text-white" style={{ background: `linear-gradient(145deg, ${meta.color}, ${meta.color}cc)` }}>
    <span className="text-sm font-bold tracking-wide">{meta.short}</span>
    <span className="text-[10px] opacity-90">{meta.name}</span>
    {date && <span className="text-[10px] opacity-80">{new Date(date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>}
  </div>;
}

export function ClaimRow({ c }: { c: SocialClaim }) {
  const [open, setOpen] = useState(false);
  const color = colorOf(CLAIM_TYPE_COLORS, c.type);
  return <li className="rounded-xl border border-line bg-white transition-shadow hover:shadow-md">
    <button className="flex w-full items-stretch gap-3 p-3 text-left" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
      <PlatformTile platform={c.platform} date={c.published_at} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-snug">“{c.text}”</p>
        {c.text_en && <p className="mt-1 text-xs leading-snug text-muted">{c.text_en}</p>}
        <p className="mt-1.5 text-[11px] text-muted">@{c.uploader_handle} · {fmtDate(c.published_at)} · {compact(c.engagement?.views)} views</p>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <Badge style={{ background: `${color}1a`, color }}>{titleCase(c.type)}</Badge>
        <Badge>{SPEAKER_NAMES[c.speaker || ""] ?? titleCase(c.speaker)}</Badge>
        {c.about_politician === false && <Badge tone="warn">Context</Badge>}
      </div>
      <ChevronRight className={`size-4 shrink-0 self-center text-muted transition-transform ${open ? "rotate-90" : ""}`} />
    </button>
    {open && <div className="space-y-2 border-t border-line px-3 py-2.5 text-xs">
      {c.evidence_span && <blockquote className="border-l-2 border-accent/50 pl-2 text-muted">“{c.evidence_span}” <span className="text-[10px]">— from the {humanize(c.source_field)}</span></blockquote>}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted">
        <span>{compact(c.engagement?.likes)} likes</span><span>{compact(c.engagement?.comments)} comments</span><span>{compact(c.engagement?.shares)} shares</span>
        <span>Checkable: {c.checkable === false ? "no" : "yes"}</span>
        {c.uploader_name && <span>Account: {c.uploader_name}</span>}
      </div>
      {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">Open original post <ExternalLink className="size-3" /></a>}
    </div>}
  </li>;
}

function useDebounced(value: string, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

const selectCls = "rounded-lg border border-line bg-white px-2.5 py-1.5 text-xs focus-visible:outline-2 focus-visible:outline-accent";

/** Filterable, paged claim list for one politician. */
export function ClaimsBrowser({ iso2, politicianId, stats }: { iso2: string; politicianId: string; stats: ClaimStats }) {
  const [type, setType] = useState("");
  const [speaker, setSpeaker] = useState("");
  const [platform, setPlatform] = useState("");
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim());
  const [page, setPage] = useState(0);
  const [state, setState] = useState<{ key: string; data: PoliticianClaims | null; error: string | null } | null>(null);
  const key = `${iso2}|${politicianId}|${type}|${speaker}|${platform}|${q}|${page}`;

  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.politician(iso2, politicianId, { type, speaker, platform, q, limit: PAGE, offset: page * PAGE }, ctl.signal)
      .then((data) => { if (!ctl.signal.aborted) setState({ key, data, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setState({ key, data: null, error: String(e.message || e) }); });
    return () => ctl.abort();
  }, [iso2, politicianId, type, speaker, platform, q, page, key]);

  const reset = (fn: (v: string) => void) => (v: string) => { fn(v); setPage(0); };
  const data = state?.data;
  const pages = data ? Math.ceil(data.total / PAGE) : 0;
  return <div>
    <div className="flex flex-wrap gap-2">
      <select aria-label="Claim type" value={type} onChange={(e) => reset(setType)(e.target.value)} className={selectCls}>
        <option value="">All types</option>{Object.keys(stats.by_type).map((k) => <option key={k} value={k}>{titleCase(k)}</option>)}
      </select>
      <select aria-label="Speaker" value={speaker} onChange={(e) => reset(setSpeaker)(e.target.value)} className={selectCls}>
        <option value="">All speakers</option>{Object.keys(stats.by_speaker).map((k) => <option key={k} value={k}>{SPEAKER_NAMES[k] ?? titleCase(k)}</option>)}
      </select>
      <select aria-label="Platform" value={platform} onChange={(e) => reset(setPlatform)(e.target.value)} className={selectCls}>
        <option value="">All sources</option>{Object.keys(stats.by_platform).map((k) => <option key={k} value={k}>{PLATFORM_META[k]?.name ?? k}</option>)}
      </select>
      <label className="flex min-w-48 flex-1 items-center gap-1.5 rounded-lg border border-line px-2.5"><Search className="size-3.5 text-muted" />
        <input aria-label="Search claims" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} placeholder="Search claims…" className="min-w-0 flex-1 bg-transparent py-1.5 text-xs outline-none" /></label>
    </div>
    {state?.error && <p className="mt-3 text-xs text-bad">Claims could not be loaded: {state.error}</p>}
    {!data && !state?.error && <p className="mt-3 text-xs text-muted">Loading claims…</p>}
    {data && <>
      <p className="mt-3 text-[11px] text-muted">{data.total} of {stats.claims} claims</p>
      <ul className="mt-2 space-y-2" data-testid="claim-list">{data.claims.map((c) => <ClaimRow key={c.claim_id} c={c} />)}</ul>
      {!data.claims.length && <p className="mt-3 rounded-lg border border-dashed border-line p-4 text-center text-xs text-muted">No claims match these filters.</p>}
      {pages > 1 && <div className="mt-3 flex items-center justify-between text-xs text-muted">
        <Button aria-label="Previous claims" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
        <span>Page {page + 1} of {pages}</span>
        <Button aria-label="Next claims" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</Button>
      </div>}
    </>}
  </div>;
}

/** All claims for one politician, grouped by publication day. */
export function ClaimsTimeline({ iso2, politicianId }: { iso2: string; politicianId: string }) {
  const [data, setData] = useState<PoliticianClaims | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.politician(iso2, politicianId, { limit: 500 }, ctl.signal).then((d) => { if (!ctl.signal.aborted) setData(d); }).catch(() => {});
    return () => ctl.abort();
  }, [iso2, politicianId]);
  const days = useMemo(() => {
    const m = new Map<string, SocialClaim[]>();
    for (const c of data?.claims ?? []) {
      const d = (c.published_at || "").slice(0, 10);
      m.set(d, [...(m.get(d) ?? []), c]);
    }
    return [...m.entries()];
  }, [data]);
  if (!data) return <p className="text-xs text-muted">Loading timeline…</p>;
  return <ol className="relative ml-3 border-l-2 border-ink-700">
    {days.map(([day, claims]) => <li key={day} className="mb-5 pl-5">
      <span className="absolute -left-[7px] mt-1 size-3 rounded-full border-2 border-white bg-accent" />
      <p className="text-xs font-semibold">{fmtDate(day)} <span className="font-normal text-muted">· {claims.length} claim{claims.length > 1 ? "s" : ""}</span></p>
      <ul className="mt-2 space-y-1.5">{claims.map((c) => <li key={c.claim_id} className="flex gap-2 text-xs">
        <span className="mt-1.5 size-2 shrink-0 rounded-full" style={{ background: colorOf(CLAIM_TYPE_COLORS, c.type) }} title={titleCase(c.type)} />
        <span className="min-w-0 flex-1">{c.text} <span className="text-muted">— {PLATFORM_META[c.platform || ""]?.name ?? c.platform} @{c.uploader_handle}</span></span>
      </li>)}</ul>
    </li>)}
  </ol>;
}

/** Country-level overview of tracked politicians. */
export function SocialClaimsCard({ iso2, onOpenPerson, compactView = false }: { iso2: string; onOpenPerson?: (id: string) => void; compactView?: boolean }) {
  const [state, setState] = useState<{ iso2: string; data: ClaimsSummary | null } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.summary(iso2, ctl.signal).then((data) => { if (!ctl.signal.aborted) setState({ iso2, data }); })
      .catch(() => { if (!ctl.signal.aborted) setState({ iso2, data: null }); });
    return () => ctl.abort();
  }, [iso2]);
  const data = state?.iso2 === iso2 ? state.data : null;
  if (!data?.available || !data.stats.claims) return compactView ? null :
    <Card className="p-6 text-sm text-muted">No social media claims have been collected for this country yet. Run <code>make sc-pull</code> after claims are written to past.dev.</Card>;
  return <Card data-testid="social-claims-card">
    <CardHeader title="Social media claims" aside={<MessageSquareQuote className="size-4 text-muted" />} />
    <div className="space-y-4 px-4 pb-4 text-sm">
      <p className="text-xs text-muted">{data.stats.claims} claims from {data.stats.posts} TikTok/Instagram posts, {fmtDate(data.stats.first)} – {fmtDate(data.stats.last)}. Pulled from {data.source ?? "past.dev"} {fmtDate(data.pulled_at)}.</p>
      {!compactView && <Donut counts={data.stats.by_type} colors={CLAIM_TYPE_COLORS} centerLabel="claims" />}
      <Timeline days={data.stats.by_day} />
      <ul className="divide-y divide-line rounded-lg border border-line">
        {data.politicians.map((p) => <li key={p.politician_id ?? p.name}>
          <button disabled={!p.politician_id || !onOpenPerson} onClick={() => p.politician_id && onOpenPerson?.(p.politician_id)} className="flex w-full items-center gap-3 px-3 py-2 text-left text-xs hover:bg-ink-850 disabled:cursor-default">
            <span className="font-medium text-accent">{p.name}</span>
            <span className="ml-auto text-muted">{p.claims} claims · {p.posts} posts</span>
            <ChevronRight className="size-3.5 text-muted" />
          </button>
        </li>)}
      </ul>
      <p className="text-[11px] text-muted">Statements made on social media, extracted automatically. They are context, not verified facts.</p>
    </div>
  </Card>;
}
