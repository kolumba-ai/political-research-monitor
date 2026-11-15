import { lazy, Suspense, useEffect, useState } from "react";
import { ChevronRight, LayoutDashboard, LoaderCircle, MessageSquareQuote, Network, Users } from "lucide-react";
import { claimsApi, type ClaimsSummary, type CountryRow } from "../lib/api";
import { flagOf } from "../lib/geo";
import Avatar from "./Avatar";
import { cn, fmtDate } from "../lib/presentation";
import CountryPanel from "./CountryPanel";
import PoliticianPage from "./PoliticianPage";
import { SocialClaimsCard, Timeline } from "./SocialClaims";
import { Card } from "./ui";

const GovernmentGraph = lazy(() => import("./GovernmentGraph"));

export type Section = "structure" | "overview" | "politicians" | "claims" | "politician";
const NAV: { id: Section; label: string; icon: typeof Network }[] = [
  { id: "structure", label: "Government structure", icon: Network },
  { id: "overview", label: "Country overview", icon: LayoutDashboard },
  { id: "politicians", label: "Politicians", icon: Users },
  { id: "claims", label: "Claims & analysis", icon: MessageSquareQuote },
];

function useClaimsSummary(iso2: string) {
  const [s, setS] = useState<{ iso2: string; data: ClaimsSummary | null } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.summary(iso2, ctl.signal).then((data) => { if (!ctl.signal.aborted) setS({ iso2, data }); })
      .catch(() => { if (!ctl.signal.aborted) setS({ iso2, data: null }); });
    return () => ctl.abort();
  }, [iso2]);
  return s?.iso2 === iso2 ? s.data : null;
}

function Loading({ text }: { text: string }) {
  return <div className="flex h-full min-h-40 items-center justify-center gap-2 p-6 text-sm text-muted" role="status"><LoaderCircle className="size-4 animate-spin" />{text}</div>;
}

function PageHeader({ crumbs, title, subtitle }: { crumbs: string[]; title: string; subtitle?: string | null }) {
  return <div className="px-4 pt-4 lg:px-6">
    <p className="flex flex-wrap items-center gap-1 text-xs text-muted">{crumbs.map((c, i) => <span key={c} className="flex items-center gap-1">{i > 0 && <ChevronRight className="size-3" />}{c}</span>)}</p>
    <h2 className="mt-1 text-xl font-semibold tracking-tight">{title}</h2>
    {subtitle && <p className="text-sm text-muted">{subtitle}</p>}
  </div>;
}

export default function Investigate({ iso2, row, section, onSection, graphTarget, onGraphNode, personId, onOpenPerson, lowMotion }: {
  iso2: string; row?: CountryRow; section: Section; onSection: (s: Section) => void;
  graphTarget?: string; onGraphNode: (id: string) => void; personId?: string;
  onOpenPerson: (id: string) => void; lowMotion: boolean;
}) {
  const summary = useClaimsSummary(iso2);
  const name = row?.name ?? iso2;
  const crumbs = ["Earth", row?.continent ?? "", name].filter(Boolean);
  const viewInGraph = (id: string) => { onGraphNode(id); onSection("structure"); };

  return <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[232px_minmax(0,1fr)]">
    <aside className="border-b border-line bg-ink-850/60 lg:border-r lg:border-b-0" aria-label="Country sections">
      <div className="flex items-center gap-2.5 px-4 py-4"><span className="text-3xl leading-none" aria-hidden>{flagOf(iso2)}</span>
        <div className="min-w-0"><p className="truncate text-base font-semibold">{name}</p><p className="text-[11px] text-muted">{row?.regime_group}</p></div></div>
      <nav className="flex gap-1 overflow-x-auto px-2 pb-2 lg:flex-col lg:overflow-visible">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = section === id || (id === "politicians" && section === "politician");
          return <button key={id} onClick={() => onSection(id)} aria-current={active ? "page" : undefined}
            className={cn("flex items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors",
              active ? "bg-accent text-white shadow-sm" : "text-fg hover:bg-ink-700")}><Icon className="size-4" />{label}
            {id === "claims" && !!summary?.stats.claims && <span className={cn("ml-auto rounded-full px-1.5 text-[10px]", active ? "bg-white/20" : "bg-ink-700 text-muted")}>{summary.stats.claims}</span>}
          </button>;
        })}
      </nav>
      {summary?.available && <div className="mx-3 mt-3 hidden rounded-xl border border-line bg-white p-3 lg:block">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Tracked on social media</p>
        {summary.politicians.map((p) => p.politician_id && <button key={p.politician_id} onClick={() => onOpenPerson(p.politician_id!)} className="mt-2 flex w-full items-center gap-2 text-left text-xs hover:text-accent">
          <Avatar name={p.name} src={p.image_url} className="size-7 text-[10px]" />
          <span className="min-w-0 flex-1 truncate">{p.name}</span><span className="text-muted">{p.claims}</span></button>)}
      </div>}
    </aside>

    <section className="flex min-h-0 min-w-0 flex-col overflow-y-auto" aria-label={NAV.find((n) => n.id === section)?.label ?? "Politician"}>
      {section === "structure" && <div className="flex min-h-[640px] flex-1 flex-col">
        <PageHeader crumbs={[...crumbs, "Government structure"]} title={`Government structure of ${name}`} subtitle="Constitutional hierarchy from the knowledge graph. Select a node to expand it; select a person to open their profile." />
        <div className="m-4 flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-line bg-white shadow-sm lg:mx-6">
          <Suspense fallback={<Loading text="Preparing the government graph…" />}>
            <GovernmentGraph key={iso2} iso2={iso2} countryName={name} lowMotion={lowMotion} initialNode={graphTarget}
              onNodeChange={onGraphNode} onOpenPerson={onOpenPerson} />
          </Suspense>
        </div>
      </div>}
      {section === "overview" && <div className="mx-auto w-full max-w-3xl">
        <PageHeader crumbs={[...crumbs, "Overview"]} title={`${name}: country overview`} subtitle="Democracy assessments, contact rules and data coverage, each with its sources." />
        <CountryPanel iso2={iso2} row={row} exploring={false} onExplore={() => onSection("structure")} onOpenPerson={onOpenPerson} />
      </div>}
      {section === "politicians" && <div>
        <PageHeader crumbs={[...crumbs, "Politicians"]} title="Politicians" subtitle="Officeholders with collected social media claims. Use search for anyone else in the graph." />
        <div className="grid gap-3 p-4 sm:grid-cols-2 lg:px-6 xl:grid-cols-3">
          {(summary?.politicians ?? []).filter((p) => p.politician_id).map((p) => <Card key={p.politician_id} className="p-4 shadow-sm transition-shadow hover:shadow-md">
            <button className="w-full text-left" onClick={() => onOpenPerson(p.politician_id!)}>
              <div className="flex items-center gap-3">
                <Avatar name={p.name} src={p.image_url} rounded="rounded-xl" className="size-12 text-lg" />
                <div className="min-w-0"><p className="truncate font-semibold">{p.name}</p><p className="text-xs text-muted">{p.claims} claims · {p.posts} posts</p></div>
                <ChevronRight className="ml-auto size-4 text-muted" />
              </div>
              <div className="mt-3"><Timeline days={p.by_day} height="h-10" /></div>
              <p className="mt-1 text-[11px] text-muted">{fmtDate(p.first)} – {fmtDate(p.last)}</p>
            </button>
          </Card>)}
          {!summary?.politicians.length && <Card className="p-6 text-sm text-muted sm:col-span-2">No politicians have collected claims yet. Explore the government structure to browse officeholders.</Card>}
        </div>
      </div>}
      {section === "claims" && <div className="mx-auto w-full max-w-3xl">
        <PageHeader crumbs={[...crumbs, "Claims & analysis"]} title="Claims & analysis" subtitle="Social media claims collected for this country's politicians." />
        <div className="p-4 lg:px-6"><SocialClaimsCard iso2={iso2} onOpenPerson={onOpenPerson} /></div>
      </div>}
      {section === "politician" && personId && <PoliticianPage key={personId} iso2={iso2} personId={personId} onViewInGraph={viewInGraph}
        onNavigate={(id) => id.startsWith("person:") ? onOpenPerson(id) : /^country:/.test(id) ? onSection("overview") : viewInGraph(id)} />}
    </section>
  </div>;
}
