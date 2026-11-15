import { useEffect, useState } from "react";
import { ShieldAlert, Landmark, Scale, ExternalLink, Network, Info } from "lucide-react";
import { api, type Assessment, type CountryDetail, type CountryRow, type SourceRef } from "../lib/api";
import { flagOf, type CountryFeature } from "../lib/geo";
import { Badge, Button, Card, CardHeader, ExtLink, RegimeDot, Skeleton } from "./ui";
import { fmtDate } from "../lib/presentation";

const INDEX_NAMES: Record<string, { name: string; publisher: string; url: string }> = {
  eiu: { name: "Democracy Index", publisher: "Economist Intelligence Unit", url: "https://www.eiu.com/n/campaigns/democracy-index-2025/" },
  freedom_house: { name: "Freedom in the World", publisher: "Freedom House", url: "https://freedomhouse.org/report/freedom-world" },
  vdem: { name: "Regimes of the World", publisher: "V-Dem Institute", url: "https://www.v-dem.net/" },
};

function verificationBadge(v: string) {
  if (v === "verified") return <Badge tone="ok">publisher page</Badge>;
  if (v === "secondary_compilation") return <Badge tone="accent">index table</Badge>;
  return <Badge tone="warn">unverified</Badge>;
}

function statusTone(s: string | null | undefined): "ok" | "warn" | "bad" | "default" {
  if (s === "allowed" || s === "approved") return "ok";
  if (s === "restricted" || s === "pending_human_review") return "warn";
  if (s === "prohibited" || s === "unknown" || s === "rejected") return "bad";
  return "default";
}

function sourceUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return ["https:", "http:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

function SourceList({ sources }: { sources: SourceRef[] }) {
  const unique = [...new Map(sources.filter((s) => sourceUrl(s.url)).map((s) => [s.url, s])).values()];
  if (!unique.length) return null;
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted hover:text-fg">Source documents ({unique.length})</summary>
      <ul className="mt-2 space-y-2">
        {unique.map((s) => (
          <li key={s.url} className="min-w-0 break-words">
            <ExtLink href={sourceUrl(s.url)!}>{s.title || s.domain || new URL(s.url).hostname}</ExtLink>
            {s.retrieved_at && <p className="text-muted">Retrieved {fmtDate(s.retrieved_at)}</p>}
          </li>
        ))}
      </ul>
    </details>
  );
}

export default function CountryPanel({ iso2, row, feature, onExplore, exploring }: {
  iso2: string | null; row?: CountryRow; feature?: CountryFeature; onExplore: () => void; exploring: boolean;
}) {
  const [request, setRequest] = useState<{
    key: string | null; data: CountryDetail | null; error: string | null;
  }>({ key: null, data: null, error: null });
  const [retry, setRetry] = useState(0);
  const requestKey = `${iso2}:${retry}`;
  const data = request.key === requestKey ? request.data : null;
  const error = request.key === requestKey ? request.error : null;
  const loading = !!iso2 && request.key !== requestKey;

  useEffect(() => {
    if (!iso2) return;
    const ctl = new AbortController();
    api.country(iso2, ctl.signal)
      .then((result) => {
        if (!ctl.signal.aborted) setRequest({ key: requestKey, data: result, error: null });
      })
      .catch((e) => {
        if (!ctl.signal.aborted) setRequest({ key: requestKey, data: null, error: String(e.message || e) });
      });
    return () => ctl.abort();
  }, [iso2, requestKey]);

  if (!iso2) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center text-muted">
        <Landmark className="size-8 text-accent" />
        <p className="text-sm">Select a country on the globe or search for one to see its government, democracy
          assessments and verified sources.</p>
      </div>
    );
  }

  const c = data?.country;
  const stats = data?.structure.stats;
  // Some countries only have the stored global-index entries; show those individually too.
  const assessments: Assessment[] = data ? [
    ...data.assessments,
    ...Object.entries(data.regime.by_source)
      .filter(([index]) => !data.assessments.some((a) => a.index === index))
      .map(([index, a]) => ({
        id: `summary:${index}`, index, label: a.class, classification: a.class,
        year: a.year, verification: a.verification, evidence_url: a.evidence_url,
        score: null, score_scale: null, grey_zone: null, sources: [],
      })),
  ] : [];
  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      {/* Header */}
      <Card className="p-4">
        <div className="flex items-start gap-3">
          <span className="text-4xl leading-none" aria-hidden>{flagOf(iso2)}</span>
          <div className="min-w-0 flex-1">
            <h2 className="break-words text-xl font-semibold">{c?.name ?? row?.name ?? iso2}</h2>
            <p className="text-xs text-muted">
              {[c?.continent ?? row?.continent, c?.un_sub_region].filter(Boolean).join(" · ")}
              {feature?.__centroid && ` · ${feature.__centroid.lat.toFixed(1)}°, ${feature.__centroid.lng.toFixed(1)}°`}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge><RegimeDot group={data?.regime.group ?? row?.regime_group ?? "Unclassified"} />
                {data?.regime.group ?? row?.regime_group ?? "Unclassified"}</Badge>
              <Badge>ISO {iso2}{c?.iso3 ? ` / ${c.iso3}` : ""}</Badge>
              <Badge>Last checked {fmtDate(data?.last_verified ?? row?.last_verified)}</Badge>
            </div>
          </div>
        </div>
      </Card>

      {loading && <Card role="status" aria-label="Loading country information" className="space-y-2 p-4"><Skeleton className="h-4 w-2/3" /><Skeleton className="h-4 w-1/2" /><Skeleton className="h-20 w-full" /></Card>}
      {error && <Card role="alert" className="space-y-2 p-4 text-sm">
        <p className="break-words text-bad">Could not load this country: {error}</p>
        <Button onClick={() => setRetry((n) => n + 1)}>Try again</Button>
      </Card>}

      {data && (
        <>
          {/* Government */}
          <Card>
            <CardHeader title="Government" aside={<Landmark className="size-4 text-muted" />} />
            <div className="space-y-2 px-4 pb-4 text-sm">
              <p>{c?.system_of_government ?? <span className="text-muted">System of government not researched yet.</span>}</p>
              {data.structure.spec && (
                <p className="text-xs text-muted">
                  Structure from the {data.structure.spec.status?.toLowerCase().startsWith("approved") ? "approved" : "draft"}{" "}
                  constitutional structure{data.structure.spec.version ? ` (${data.structure.spec.version})` : ""}.{" "}
                  {sourceUrl(data.structure.spec.official_text_url) && <ExtLink href={sourceUrl(data.structure.spec.official_text_url)!}>Official text</ExtLink>}
                </p>
              )}
              {data.structure.tiers.length > 0 ? (
                <ul className="space-y-1">
                  {data.structure.tiers.map((t) => (
                    <li key={t.id} className="space-y-1 rounded-md bg-ink-850 px-2.5 py-2">
                      <span className="block break-words">{t.name}</span>
                      <span className="block break-words text-xs text-muted">{t.child_count} entries · {t.article ?? "Citation not recorded"}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="rounded-md border border-dashed border-line p-3 text-xs text-muted">
                  Government structure is not available yet. New research must use the constitution or official
                  government documents and be reviewed before institutions and officeholders are added.
                </div>
              )}
              <Button variant="primary" className="mt-1 w-full" disabled={!data.structure.tiers.length} onClick={onExplore}>
                <Network className="size-4" /> {exploring ? "Viewing government structure" : "Explore government structure"}
              </Button>
            </div>
          </Card>

          {/* Democracy assessments: each index separately, never merged into one status */}
          <Card>
            <CardHeader title="Democracy assessments" aside={<Scale className="size-4 text-muted" />} />
            <div className="px-4 pb-4 text-sm">
              <p className="mb-2 text-xs text-muted">
                Group: <b className="text-fg">{data.regime.group}</b>{data.regime.class && data.regime.class !== data.regime.group ? ` (${data.regime.class})` : ""}
                {data.regime.basis ? `, based on ${data.regime.basis}` : ""}.
                {data.regime.sources_agree === false && <span className="text-warn"> The indices disagree; each is listed below.</span>}
              </p>
              {assessments.length === 0 && (
                <p className="text-xs text-muted">No democracy assessments are recorded for this country or territory.</p>
              )}
              <ul className="space-y-2">
                {assessments.map((a) => {
                  const meta = INDEX_NAMES[a.index] ?? { name: a.index, publisher: a.index, url: "" };
                  return (
                    <li key={a.id} className="rounded-md bg-ink-850 px-3 py-2">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="break-words font-medium">{a.label}</span>
                        {verificationBadge(a.verification)}
                      </div>
                      <div className="mt-0.5 text-xs text-muted">
                        {meta.url ? <ExtLink href={meta.url}>{meta.publisher}</ExtLink> : meta.publisher}, {meta.name}
                        {a.year ? ` ${a.year}` : ""}{a.score != null ? ` · score ${a.score}${a.score_scale ? ` (${a.score_scale})` : ""}` : ""}
                        {a.grey_zone ? " · grey zone" : ""}
                      </div>
                      {sourceUrl(a.evidence_url) && <div className="mt-0.5 text-xs"><ExtLink href={sourceUrl(a.evidence_url)!}>Evidence <ExternalLink className="inline size-3" /></ExtLink></div>}
                      {"evidence_quote" in a && typeof a.evidence_quote === "string" && a.evidence_quote && (
                        <blockquote className="mt-2 border-l-2 border-line pl-2 text-xs text-muted">{a.evidence_quote}</blockquote>
                      )}
                      <SourceList sources={a.sources} />
                    </li>
                  );
                })}
              </ul>
            </div>
          </Card>

          {/* Political contact rules */}
          {data.contact_rules && (
            <Card>
              <CardHeader title="Political contact & verification rules" aside={<ShieldAlert className="size-4 text-muted" />} />
              <div className="space-y-2 px-4 pb-4 text-sm">
                <div className="flex flex-wrap gap-1.5">
                  <Badge tone={statusTone(data.contact_rules.status)}>Status: {data.contact_rules.status}</Badge>
                  <Badge tone={statusTone(data.contact_rules.review_status)}>Review: {data.contact_rules.review_status?.replaceAll("_", " ")}</Badge>
                  <Badge>{data.contact_rules.dimensions.length} rules · {data.contact_rules.laws.length} laws</Badge>
                </div>
                {data.contact_rules.status_reasons?.length ? (
                  <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">
                    {data.contact_rules.status_reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                ) : null}
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted hover:text-fg">Rules, scope and legal evidence</summary>
                  <ol className="mt-2 space-y-2">
                    {data.contact_rules.dimensions.map((d, i) => (
                      <li key={i} className="space-y-1.5 rounded-md bg-ink-850 p-2.5 break-words">
                        <div className="flex flex-wrap items-start justify-between gap-1.5">
                          <span className="font-medium">{d.purpose.replaceAll("_", " ")}</span>
                          <Badge tone={statusTone(d.status)}>{d.status}</Badge>
                        </div>
                        <p className="text-muted">{d.sender.replaceAll("_", " ")} → {d.recipient.replaceAll("_", " ")} · {d.channel.replaceAll("_", " ")}</p>
                        {d.conditions && <p><span className="text-muted">Conditions: </span>{d.conditions}</p>}
                        {d.legal_basis && <p><span className="text-muted">Legal basis: </span>{d.legal_basis}</p>}
                        {sourceUrl(d.evidence_url) && <p><ExtLink href={sourceUrl(d.evidence_url)!}>Supporting document</ExtLink>{d.evidence_fetched === false && <span className="text-muted"> · not retrieved</span>}</p>}
                      </li>
                    ))}
                  </ol>
                </details>
                {data.contact_rules.laws.length > 0 && <details className="text-xs">
                  <summary className="cursor-pointer text-muted hover:text-fg">Referenced laws ({data.contact_rules.laws.length})</summary>
                  <ul className="mt-2 space-y-2 break-words">
                    {data.contact_rules.laws.map((law, i) => <li key={i}>
                      {sourceUrl(law.url) ? <ExtLink href={sourceUrl(law.url)!}>{law.name}</ExtLink> : law.name}
                      {law.category && <p className="text-muted">{law.category.replaceAll("_", " ")}</p>}
                    </li>)}
                  </ul>
                </details>}
                <SourceList sources={data.contact_rules.sources} />
                <p className="text-xs text-muted">Rules last checked {fmtDate(data.contact_rules.last_verified)}</p>
                <p className="flex gap-1.5 rounded-md bg-ink-850 p-2 text-xs text-muted">
                  <Info className="mt-0.5 size-3.5 shrink-0" />
                  {data.contact_rules.disclaimer ?? "Research and compliance assessment only. Not legal advice."} This interface
                  does not authorize or perform any political outreach.
                </p>
              </div>
            </Card>
          )}

          {/* Completeness */}
          {stats && (
            <Card>
              <CardHeader title="Data coverage" />
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 px-4 pb-4 text-sm">
                <dt className="text-muted">Government units</dt><dd className="text-right">{stats.levels.toLocaleString()}</dd>
                <dt className="text-muted">Institutions</dt><dd className="text-right">{stats.bodies.toLocaleString()}</dd>
                <dt className="text-muted">Offices (elected)</dt><dd className="text-right">{stats.offices.toLocaleString()} ({stats.elected_offices.toLocaleString()})</dd>
                <dt className="text-muted">Offices with a recorded current holder</dt><dd className="text-right">{stats.offices_with_current_holder.toLocaleString()} of {stats.offices.toLocaleString()}</dd>
                <dt className="text-muted">Popularly elected officials</dt><dd className="text-right">{stats.popularly_elected_officeholders.toLocaleString()}</dd>
                <dt className="text-muted">Evidence</dt>
                <dd className="text-right text-xs">
                  {Object.entries(stats.holdings_by_evidence).map(([k, v]) => `${v} ${k.replaceAll("_", " ")}`).join(" · ")}
                </dd>
              </dl>
              <p className="px-4 pb-4 text-xs text-muted">Counts describe stored records. An office can contain multiple seats; these figures do not imply that every seat or local authority has been researched.</p>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
