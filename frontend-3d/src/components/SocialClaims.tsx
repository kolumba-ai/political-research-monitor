import { useEffect, useMemo, useState } from "react";
import { MessageSquareQuote, Search } from "lucide-react";
import { claimsApi, type ClaimStats, type ClaimsSummary, type PoliticianClaims } from "../lib/api";
import { fmtDate } from "../lib/presentation";
import { Badge, Button, Card, CardHeader, ExtLink } from "./ui";

const PAGE = 20;
const CLAIM_TYPE_COLORS: Record<string, string> = {
  statistic: "#3557e6", event: "#438f85", policy: "#7c3aed", promise: "#b45309", accusation: "#b91c1c", other: "#64708a",
};
const label = (v: string | null | undefined) => (v ? v.replaceAll("_", " ") : "—");
const compact = (n: number | undefined) => (n === undefined ? "—" : Intl.NumberFormat(undefined, { notation: "compact" }).format(n));

export function TypeBar({ counts }: { counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return null;
  return <div>
    <div className="flex h-2 overflow-hidden rounded-full bg-ink-700" role="img" aria-label="Claims by type">
      {Object.entries(counts).map(([k, v]) => <span key={k} title={`${label(k)}: ${v}`} style={{ width: `${(100 * v) / total}%`, background: CLAIM_TYPE_COLORS[k] || "#94a3b8" }} />)}
    </div>
    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
      {Object.entries(counts).map(([k, v]) => <span key={k} className="flex items-center gap-1"><span className="size-2 rounded-full" style={{ background: CLAIM_TYPE_COLORS[k] || "#94a3b8" }} />{label(k)} {v}</span>)}
    </div>
  </div>;
}

export function Timeline({ days }: { days: ClaimStats["by_day"] }) {
  if (!days.length) return null;
  const max = Math.max(...days.map((d) => d.claims));
  return <div>
    <div className="flex h-12 items-end gap-px" role="img" aria-label="Claims per day">
      {days.map((d) => <span key={d.date} title={`${d.date}: ${d.claims} claims`} className="min-w-[3px] flex-1 rounded-t-sm bg-accent/70" style={{ height: `${Math.max(8, (100 * d.claims) / max)}%` }} />)}
    </div>
    <div className="mt-1 flex justify-between text-[10px] text-muted"><span>{fmtDate(days[0].date)}</span><span>{fmtDate(days.at(-1)!.date)}</span></div>
  </div>;
}

/** Social-media claims about one politician, for the graph inspector. */
export function PoliticianSocialClaims({ iso2, politicianId }: { iso2: string; politicianId: string }) {
  const [type, setType] = useState("");
  const [speaker, setSpeaker] = useState("");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [state, setState] = useState<{ key: string; data: PoliticianClaims | null; error: string | null } | null>(null);
  const key = `${iso2}|${politicianId}|${type}|${speaker}|${q}|${page}`;

  useEffect(() => {
    const t = setTimeout(() => { setQ(query.trim()); setPage(0); }, 300);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.politician(iso2, politicianId, { type, speaker, q, limit: PAGE, offset: page * PAGE }, ctl.signal)
      .then((data) => { if (!ctl.signal.aborted) setState({ key, data, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setState({ key, data: null, error: String(e.message || e) }); });
    return () => ctl.abort();
  }, [iso2, politicianId, type, speaker, q, page, key]);

  const data = state?.data;
  if (state?.error) return <p className="mt-4 text-xs text-bad">Social claims could not be loaded: {state.error}</p>;
  if (!data) return <p className="mt-4 text-xs text-muted">Loading social claims…</p>;
  if (!data.stats.claims) return null;
  const pages = Math.ceil(data.total / PAGE);
  return <section className="mt-4 rounded-lg border border-line p-3" aria-label="Social media claims" data-testid="social-claims">
    <h4 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted"><MessageSquareQuote className="size-3.5" />Social media claims · {data.stats.claims}</h4>
    <p className="mt-1 text-[11px] text-muted">{data.stats.posts} posts mentioning this politician, {fmtDate(data.stats.first)} – {fmtDate(data.stats.last)}. Extracted from TikTok/Instagram via past.dev; statements, not verified facts.</p>
    <div className="mt-3 space-y-3"><TypeBar counts={data.stats.by_type} /><Timeline days={data.stats.by_day} /></div>
    <div className="mt-3 flex flex-wrap gap-2 text-xs">
      <select aria-label="Claim type" value={type} onChange={(e) => { setType(e.target.value); setPage(0); }} className="rounded-md border border-line bg-ink-900 px-2 py-1">
        <option value="">All types</option>{Object.keys(data.stats.by_type).map((k) => <option key={k} value={k}>{label(k)}</option>)}
      </select>
      <select aria-label="Speaker" value={speaker} onChange={(e) => { setSpeaker(e.target.value); setPage(0); }} className="rounded-md border border-line bg-ink-900 px-2 py-1">
        <option value="">All speakers</option>{Object.keys(data.stats.by_speaker).map((k) => <option key={k} value={k}>{label(k)}</option>)}
      </select>
      <label className="flex min-w-36 flex-1 items-center gap-1 rounded-md border border-line px-2"><Search className="size-3 text-muted" />
        <input aria-label="Search claims" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search claims" className="min-w-0 flex-1 bg-transparent py-1 outline-none" /></label>
    </div>
    <p className="mt-2 text-[11px] text-muted">{data.total} matching claims</p>
    <ul className="mt-1 space-y-2">
      {data.claims.map((c) => <li key={c.claim_id} className="rounded-lg border border-line p-2.5 text-xs">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge style={{ background: `${CLAIM_TYPE_COLORS[c.type || ""] || "#94a3b8"}22`, color: CLAIM_TYPE_COLORS[c.type || ""] || undefined }}>{label(c.type)}</Badge>
          <Badge>speaker: {label(c.speaker)}</Badge>
          {c.about_politician === false && <Badge tone="warn">not about politician</Badge>}
          <span className="ml-auto text-[10px] text-muted">{fmtDate(c.published_at)}</span>
        </div>
        <p className="mt-1.5 leading-relaxed">{c.text}</p>
        {c.text_en && <p className="mt-1 leading-relaxed text-muted">{c.text_en}</p>}
        {c.evidence_span && <blockquote className="mt-1.5 border-l-2 border-accent/40 pl-2 text-[11px] text-muted">“{c.evidence_span}” <span className="text-[10px]">({label(c.source_field)})</span></blockquote>}
        <p className="mt-1.5 text-[10px] text-muted">
          {c.url ? <ExtLink href={c.url}>{c.platform} @{c.uploader_handle}</ExtLink> : `${c.platform} @${c.uploader_handle}`}
          {" · "}{compact(c.engagement.views)} views · {compact(c.engagement.likes)} likes
        </p>
      </li>)}
    </ul>
    {pages > 1 && <div className="mt-2 flex items-center justify-between text-xs text-muted">
      <Button aria-label="Previous claims" disabled={page === 0} onClick={() => setPage(page - 1)}>Prev</Button>
      <span>Page {page + 1} of {pages}</span>
      <Button aria-label="Next claims" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</Button>
    </div>}
  </section>;
}

/** Country-level overview of tracked politicians, for the country panel. */
export function SocialClaimsCard({ iso2, onOpenPerson }: { iso2: string; onOpenPerson?: (id: string) => void }) {
  const [state, setState] = useState<{ iso2: string; data: ClaimsSummary | null } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.summary(iso2, ctl.signal).then((data) => { if (!ctl.signal.aborted) setState({ iso2, data }); })
      .catch(() => { if (!ctl.signal.aborted) setState({ iso2, data: null }); });
    return () => ctl.abort();
  }, [iso2]);
  const data = state?.iso2 === iso2 ? state.data : null;
  const politicians = useMemo(() => data?.politicians ?? [], [data]);
  if (!data?.available || !data.stats.claims) return null;
  return <Card data-testid="social-claims-card">
    <CardHeader title="Social media claims" aside={<MessageSquareQuote className="size-4 text-muted" />} />
    <div className="space-y-3 px-4 pb-4 text-sm">
      <p className="text-xs text-muted">{data.stats.claims} claims from {data.stats.posts} TikTok/Instagram posts, {fmtDate(data.stats.first)} – {fmtDate(data.stats.last)}. Pulled from past.dev {fmtDate(data.pulled_at)}.</p>
      <TypeBar counts={data.stats.by_type} />
      <Timeline days={data.stats.by_day} />
      <ul className="space-y-1">
        {politicians.map((p) => <li key={p.politician_id ?? p.name} className="flex items-center gap-2 text-xs">
          {p.politician_id && onOpenPerson ? <button className="text-accent hover:underline" onClick={() => onOpenPerson(p.politician_id!)}>{p.name}</button> : <span>{p.name}</span>}
          <span className="ml-auto text-muted">{p.claims} claims · {p.posts} posts</span>
        </li>)}
      </ul>
      <p className="text-[11px] text-muted">Statements made on social media, extracted automatically. They are context, not verified facts.</p>
    </div>
  </Card>;
}
