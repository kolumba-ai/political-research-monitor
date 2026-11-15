import { useEffect, useState, type ReactNode } from "react";
import { ArrowRight, Building2, CalendarDays, Landmark, MessageSquareQuote, Scale, ShieldAlert, Vote } from "lucide-react";
import { api, claimsApi, type CountryDetail, type CountryRow } from "../lib/api";
import { flagOf } from "../lib/geo";
import { fmtDate } from "../lib/presentation";
import { Badge, Button, Card, RegimeDot, Skeleton } from "./ui";

type Tone = "ok" | "warn" | "bad" | "accent" | "default";
const toneOf = (v: string | null | undefined): Tone => {
  const s = (v || "").toLowerCase();
  if (/(^free$|full|liberal|allowed|^democratic)/.test(s)) return "ok";
  if (/(partly|flawed|electoral|hybrid|restricted|pending)/.test(s)) return "warn";
  if (/(not free|authoritarian|autocracy|prohibited|closed)/.test(s)) return "bad";
  return "default";
};

function latest(data: CountryDetail, index: string) {
  const a = data.assessments.filter((x) => x.index === index).sort((x, y) => (y.year ?? 0) - (x.year ?? 0))[0];
  if (a) return { label: a.label || a.classification, year: a.year };
  const s = data.regime.by_source[index];
  return s ? { label: s.class, year: s.year } : null;
}

function Row({ icon: Icon, label, children }: { icon: typeof Landmark; label: string; children: ReactNode }) {
  return <div className="flex items-center gap-3 border-b border-line py-2.5 text-sm last:border-0">
    <Icon className="size-4 shrink-0 text-muted" /><span className="text-muted">{label}</span>
    <span className="ml-auto min-w-0 text-right">{children}</span>
  </div>;
}

/** Compact country summary shown beside the globe. */
export default function CountryCard({ iso2, row, onExplore, onOverview }: {
  iso2: string; row?: CountryRow; onExplore: () => void; onOverview: () => void;
}) {
  const [state, setState] = useState<{ iso2: string; data: CountryDetail | null; error: string | null } | null>(null);
  const [claims, setClaims] = useState<{ iso2: string; n: number; politicians: number } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    api.country(iso2, ctl.signal).then((data) => { if (!ctl.signal.aborted) setState({ iso2, data, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setState({ iso2, data: null, error: String(e.message || e) }); });
    claimsApi.summary(iso2, ctl.signal).then((s) => { if (!ctl.signal.aborted) setClaims({ iso2, n: s.stats.claims, politicians: s.politicians.length }); }).catch(() => {});
    return () => ctl.abort();
  }, [iso2]);
  const data = state?.iso2 === iso2 ? state.data : null;
  const c = data?.country;
  const vdem = data && latest(data, "vdem");
  const fh = data && latest(data, "freedom_house");
  const eiu = data && latest(data, "eiu");
  const hasStructure = data ? data.structure.tiers.length > 0 : !!row?.has_structure;
  const nClaims = claims?.iso2 === iso2 ? claims : null;

  return <Card className="w-full max-w-md p-5 shadow-xl shadow-slate-900/10" data-testid="country-card">
    <div className="flex items-start gap-3">
      <span className="text-5xl leading-none" aria-hidden>{flagOf(iso2)}</span>
      <div className="min-w-0 flex-1">
        <h2 className="text-2xl font-semibold tracking-tight">{c?.name ?? row?.name ?? iso2}</h2>
        <p className="text-sm text-muted">{[c?.continent ?? row?.continent, c?.un_sub_region].filter(Boolean).join(" · ")}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge><RegimeDot group={data?.regime.group ?? row?.regime_group ?? "Unclassified"} />{data?.regime.group ?? row?.regime_group ?? "Unclassified"}</Badge>
          <Badge>ISO {iso2}{c?.iso3 ? ` / ${c.iso3}` : ""}</Badge>
        </div>
      </div>
    </div>
    {state?.error && <p className="mt-4 text-sm text-bad">Could not load this country: {state.error}</p>}
    {!data && !state?.error && <div className="mt-4 space-y-2"><Skeleton className="h-5" /><Skeleton className="h-5" /><Skeleton className="h-5" /></div>}
    {data && <div className="mt-4">
      <Row icon={Landmark} label="Government">{c?.system_of_government ?? <span className="text-muted">Not researched</span>}</Row>
      {vdem && <Row icon={Vote} label={`Democracy (V-Dem${vdem.year ? ` ${vdem.year}` : ""})`}><Badge tone={toneOf(vdem.label)}>{vdem.label}</Badge></Row>}
      {fh && <Row icon={Scale} label={`Freedom House${fh.year ? ` ${fh.year}` : ""}`}><Badge tone={toneOf(fh.label)}>{fh.label}</Badge></Row>}
      {eiu && <Row icon={Building2} label={`EIU${eiu.year ? ` ${eiu.year}` : ""}`}><Badge tone={toneOf(eiu.label)}>{eiu.label}</Badge></Row>}
      {data.contact_rules && <Row icon={ShieldAlert} label="Political contact"><Badge tone={toneOf(data.contact_rules.status)} className="capitalize">{data.contact_rules.status}{data.contact_rules.review_status === "pending_human_review" ? " · review pending" : ""}</Badge></Row>}
      {!!nClaims?.n && <Row icon={MessageSquareQuote} label="Social claims">{nClaims.n} · {nClaims.politicians} politician{nClaims.politicians > 1 ? "s" : ""}</Row>}
      <Row icon={CalendarDays} label="Last verified">{fmtDate(data.last_verified ?? row?.last_verified)}</Row>
    </div>}
    <Button variant="primary" className="mt-4 w-full py-2.5 text-[15px]" disabled={!hasStructure} onClick={onExplore}>
      {hasStructure ? <>Explore government structure <ArrowRight className="size-4" /></> : "Government structure not researched yet"}
    </Button>
    <button className="mt-2 w-full text-center text-xs text-accent hover:underline" onClick={onOverview}>Full country profile, assessments and sources</button>
  </Card>;
}
