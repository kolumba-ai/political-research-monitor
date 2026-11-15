import { useEffect, useMemo, useState } from "react";
import { BadgeCheck, BarChart3, CalendarDays, CalendarRange, ChevronRight, Clock, ExternalLink, FileText, Landmark, MapPin, MessageSquareQuote, Network } from "lucide-react";
import { claimsApi, type PoliticianClaims } from "../lib/api";
import { CLAIM_TYPE_COLORS, PLATFORM_META, SPEAKER_COLORS, SPEAKER_NAMES } from "../lib/claims";
import Avatar from "./Avatar";
import { detailUrl, graphGet, type GraphDetail } from "../lib/graph";
import { fmtDate } from "../lib/presentation";
import { BarList, ClaimsBrowser, ClaimsTimeline, Donut, Timeline } from "./SocialClaims";
import { PoliticianMetrics } from "./Metrics";
import { StorylinesView } from "./Storylines";
import { Badge, Button, Card, CardHeader, ExtLink } from "./ui";
import { cn } from "../lib/presentation";

type Tab = "claims" | "storylines" | "metrics" | "timeline" | "sources";
const TABS: Tab[] = ["claims", "storylines", "metrics", "timeline", "sources"];

export default function PoliticianPage({ iso2, personId, initialTab, onViewInGraph, onNavigate }: {
  iso2: string; personId: string; initialTab?: string; onViewInGraph: (id: string) => void; onNavigate: (id: string) => void;
}) {
  const [detail, setDetail] = useState<{ id: string; data: GraphDetail | null; error: string | null } | null>(null);
  const [claims, setClaims] = useState<{ id: string; data: PoliticianClaims | null } | null>(null);
  const [tab, setTab] = useState<Tab>(TABS.includes(initialTab as Tab) ? (initialTab as Tab) : "claims");

  useEffect(() => {
    const ctl = new AbortController();
    graphGet<GraphDetail>(detailUrl(personId), ctl.signal)
      .then((data) => { if (!ctl.signal.aborted) setDetail({ id: personId, data, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setDetail({ id: personId, data: null, error: String(e.message || e) }); });
    claimsApi.politician(iso2, personId, { limit: 1 }, ctl.signal)
      .then((data) => { if (!ctl.signal.aborted) setClaims({ id: personId, data }); })
      .catch(() => { if (!ctl.signal.aborted) setClaims({ id: personId, data: null }); });
    return () => ctl.abort();
  }, [iso2, personId]);

  const d = detail?.id === personId ? detail.data : null;
  const stats = claims?.id === personId ? claims.data?.stats : undefined;
  const current = useMemo(() => d?.offices?.filter((o) => o.edge.current) ?? [], [d]);
  const office = current[0] ?? d?.offices?.[0];
  const levels = d?.path.filter((n) => n.label === "GovernmentLevel") ?? [];
  const body = d?.path.findLast((n) => n.label === "GovernmentBody");
  const sources = useMemo(() => [...new Map((d?.sources ?? []).map((s) => [s.url, s])).values()], [d]);
  const handles = useMemo(() => Object.entries(stats?.by_platform ?? {}), [stats]);

  if (detail?.error) return <div className="p-6 text-sm text-bad">Could not load this politician: {detail.error}</div>;
  if (!d) return <div className="p-6 text-sm text-muted">Loading politician…</div>;

  const name = d.node.name || d.node.id;
  const party = d.parties?.[0]?.party;
  return <div className="space-y-4 p-4 lg:p-6" data-testid="politician-page">
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 text-xs text-muted">
      {d.path.filter((n) => n.label !== "Term").map((n, i) => <span key={n.id} className="flex items-center gap-1">
        {i > 0 && <ChevronRight className="size-3" />}
        <button className={cn("hover:text-fg", n.id === personId && "font-semibold text-fg")} onClick={() => n.id !== personId && onNavigate(n.id)}>{n.name || n.id}</button>
      </span>)}
    </nav>

    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-4">
        <Card className="grid gap-5 p-5 shadow-sm md:grid-cols-[176px_1fr]">
          <div className="w-full max-w-44">
            <Avatar name={name} src={d.node.image_url} rounded="rounded-2xl" className="aspect-[4/5] w-full text-5xl shadow-inner" />
            {typeof d.node.image_source_url === "string" && <p className="mt-1 truncate text-[10px] text-muted">Photo: <ExtLink href={d.node.image_source_url}>{String(d.node.image_source_domain ?? "source")}</ExtLink></p>}
          </div>
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">{name}
              {d.node.evidence_status === "verified" || office?.edge.evidence_status === "verified" ? <BadgeCheck className="size-5 text-accent" aria-label="Verified officeholder" /> : null}</h2>
            <p className="mt-0.5 text-sm text-muted">{office?.office.name ?? "No office recorded"}</p>
            {party && <div className="mt-3 flex items-center gap-2 text-xs"><Badge tone="bad" className="uppercase">{String(party.acronym ?? party.name)}</Badge><span className="text-muted">{party.name}</span></div>}
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2.5 border-t border-line pt-4 text-sm">
              <dt className="flex items-center gap-2 text-muted"><CalendarDays className="size-4" />Term</dt>
              <dd>{office ? `${fmtDate(office.edge.start)} – ${office.edge.end ? fmtDate(office.edge.end) : office.edge.current ? "present" : "not recorded"}` : "—"}</dd>
              <dt className="flex items-center gap-2 text-muted"><Landmark className="size-4" />Government</dt>
              <dd>{body?.name ?? "—"}</dd>
              <dt className="flex items-center gap-2 text-muted"><MapPin className="size-4" />Jurisdiction</dt>
              <dd>{levels.at(-1)?.name ?? d.path.find((n) => n.label === "Country")?.name ?? "—"}</dd>
              {office?.edge.evidence_status && <><dt className="flex items-center gap-2 text-muted"><FileText className="size-4" />Evidence</dt>
                <dd className="capitalize">{String(office.edge.evidence_status).replaceAll("_", " ")} · last verified {fmtDate(d.node.last_verified)}</dd></>}
            </dl>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={() => onViewInGraph(personId)}><Network className="size-4" />View in graph</Button>
              <Button onClick={() => setTab("metrics")}><BarChart3 className="size-4" />Social media metrics</Button>
              <Button onClick={() => setTab("sources")}><ExternalLink className="size-4" />Open sources ({sources.length})</Button>
            </div>
            {current.length > 1 && <p className="mt-3 text-xs text-muted">Also holds: {current.slice(1).map((o) => o.office.name).join(", ")}</p>}
          </div>
        </Card>

        <Card className="shadow-sm">
          <div role="tablist" aria-label="Politician data" className="flex gap-1 overflow-x-auto border-b border-line px-3 pt-2">
            {([["claims", `Collected claims (${stats?.claims ?? 0})`, MessageSquareQuote], ["storylines", "Storylines by week", CalendarRange], ["metrics", "Metrics", BarChart3], ["timeline", "Timeline", Clock], ["sources", `Sources (${sources.length})`, FileText]] as const).map(([k, text, Icon]) =>
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={cn("flex items-center gap-1.5 whitespace-nowrap rounded-t-lg border-b-2 px-3 py-2 text-sm",
                tab === k ? "border-accent bg-accent/5 font-medium text-accent" : "border-transparent text-muted hover:text-fg")}><Icon className="size-4" />{text}</button>)}
          </div>
          <div className="p-4">
            {tab === "claims" && (stats?.claims ? <ClaimsBrowser iso2={iso2} politicianId={personId} stats={stats} /> :
              <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">No social media claims have been collected for this politician yet.</p>)}
            {tab === "storylines" && <StorylinesView iso2={iso2} politicianId={personId} />}
            {tab === "metrics" && <PoliticianMetrics iso2={iso2} politicianKey={personId} />}
            {tab === "timeline" && (stats?.claims ? <ClaimsTimeline iso2={iso2} politicianId={personId} /> : <p className="text-sm text-muted">No claims to place on a timeline yet.</p>)}
            {tab === "sources" && <div className="space-y-4">
              <section>
                <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Officeholding evidence</h4>
                {sources.length ? <ul className="space-y-2">{sources.map((s) => <li key={s.url} className="rounded-lg border border-line p-3 text-xs">
                  <ExtLink href={s.url}>{s.name || s.domain || s.url}</ExtLink>
                  <p className="mt-0.5 text-[10px] text-muted">{s.domain} · retrieved {fmtDate(s.retrieved_at)}</p>
                  {s.edge?.snippet && <blockquote className="mt-1.5 border-l-2 border-accent/40 pl-2 text-muted">{s.edge.snippet}</blockquote>}
                </li>)}</ul> : <p className="text-xs text-muted">No source documents are stored for this record.</p>}
              </section>
              {handles.length > 0 && <section>
                <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">Social media coverage</h4>
                <p className="text-xs text-muted">{stats?.posts} posts on {handles.map(([p, n]) => `${PLATFORM_META[p]?.name ?? p} (${n} claims)`).join(" and ")}, captured with Oriane and stored in past.dev. Open a claim to reach its original post.</p>
              </section>}
            </div>}
          </div>
        </Card>
      </div>

      <aside className="space-y-4">
        <Card className="shadow-sm">
          <CardHeader title="Claim overview" />
          <div className="px-4 pb-4">{stats?.claims ? <Donut counts={stats.by_type} colors={CLAIM_TYPE_COLORS} centerLabel="claims" /> : <p className="text-xs text-muted">No claims yet.</p>}</div>
        </Card>
        {!!stats?.claims && <>
          <Card className="shadow-sm"><CardHeader title="Who is speaking" /><div className="px-4 pb-4"><BarList counts={stats.by_speaker} colors={SPEAKER_COLORS} names={SPEAKER_NAMES} /></div></Card>
          <Card className="shadow-sm"><CardHeader title="Sources" /><div className="px-4 pb-4"><BarList counts={Object.fromEntries(Object.entries(stats.by_platform).map(([k, v]) => [PLATFORM_META[k]?.name ?? k, v]))} /></div></Card>
          <Card className="shadow-sm"><CardHeader title="Activity" /><div className="px-4 pb-4"><Timeline days={stats.by_day} /></div>
            <p className="px-4 pb-4 text-[11px] text-muted">Claims are statements extracted automatically from social media. They are context for research, not verified facts.</p></Card>
        </>}
      </aside>
    </div>
  </div>;
}
