import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, RefreshCw, Search } from "lucide-react";
import { pipelineApi, type PipelineRecord, type PipelineRecords, type PipelineSummary, type PipelineTrace, type StageInfo } from "../lib/api";
import { CLAIM_TYPE_COLORS, colorOf, compact, humanize, pct, titleCase } from "../lib/claims";
import { cn } from "../lib/presentation";
import { Badge, Button, Card, CardHeader, ExtLink } from "./ui";

const PAGE = 20;
const STAGE_COLORS: Record<string, string> = {
  not_eligible: "#cbd5e1", waiting_for_intake: "#94a3b8", skipped: "#a8a29e", waiting_for_search: "#fbbf24", searching: "#f59e0b",
  no_evidence: "#fda4af", no_usable_evidence: "#f87171", waiting_to_send: "#38bdf8", send_failed: "#b91c1c",
  sent_unconfirmed: "#818cf8", in_pastdev: "#15803d",
};
const STANCE_TONE: Record<string, "ok" | "bad" | "accent" | "default"> = { supports: "ok", contradicts: "bad", context: "accent", irrelevant: "default" };
const FETCH_TONE: Record<string, "ok" | "bad" | "warn" | "default"> = { fetched: "ok", blocked: "bad", robots_disallowed: "warn", failed: "bad", queued: "default" };
const fmtTime = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
const num = (n: number | null | undefined) => (n ?? 0).toLocaleString();

const intakeNote = (q: PipelineTrace["queue"]) => {
  if (!q) return "never entered the evidence queue";
  if (q.status === "skipped") return `skipped: ${humanize(q.skip_reason)}`;
  if (q.status === "mapped") return "accepted; evidence check finished";
  if (q.status === "mapping") return "accepted; evidence check running";
  return q.skip_reason === "pastdev_pending" ? "accepted; evidence judged, queued again to finish the past.dev write" : "accepted; waiting for evidence search";
};

function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: "ok" | "bad" }) {
  return <div className="rounded-lg border border-line p-3" title={hint}>
    <p className="text-[11px] text-muted">{label}</p>
    <p className={cn("text-lg font-semibold tabular-nums", tone === "ok" && "text-ok", tone === "bad" && "text-bad")}>{value}</p>
  </div>;
}

function StageBar({ stages, info }: { stages: Record<string, number>; info: StageInfo[] }) {
  const total = Object.values(stages).reduce((a, b) => a + b, 0) || 1;
  return <span className="flex h-2.5 w-full overflow-hidden rounded-full bg-ink-700">
    {info.filter((s) => stages[s.key]).map((s) => <span key={s.key} title={`${s.label}: ${stages[s.key]}`} style={{ width: `${(100 * stages[s.key]) / total}%`, background: STAGE_COLORS[s.key] }} />)}
  </span>;
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return <div className="overflow-x-auto"><table className="w-full text-left text-xs">
    <thead><tr className="text-[11px] text-muted">{head.map((h, i) => <th key={h} className={cn("px-2 py-1 font-medium", i > 0 && "text-right")}>{h}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-line">{r.map((c, j) => <td key={j} className={cn("px-2 py-1.5", j > 0 && "text-right tabular-nums")}>{c}</td>)}</tr>)}</tbody>
  </table></div>;
}

function Step({ n, title, note, children }: { n: number; title: string; note?: ReactNode; children?: ReactNode }) {
  return <li className="relative pl-8">
    <span className="absolute left-0 top-0 flex size-5 items-center justify-center rounded-full bg-accent text-[10px] font-semibold text-white">{n}</span>
    <p className="text-xs font-semibold">{title} {note && <span className="font-normal text-muted">· {note}</span>}</p>
    <div className="mt-1.5 space-y-1.5 text-xs">{children}</div>
  </li>;
}

/** Every recorded step of one claim for one politician, from the post to the past.dev record. */
function Trace({ iso2, rec }: { iso2: string; rec: PipelineRecord }) {
  const [t, setT] = useState<PipelineTrace | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    pipelineApi.trace(iso2, rec.proposition_id, rec.politician_id, ctl.signal).then((d) => { if (!ctl.signal.aborted) setT(d); }).catch(() => {});
    return () => ctl.abort();
  }, [iso2, rec.proposition_id, rec.politician_id]);
  if (!t) return <p className="p-3 text-xs text-muted">Loading trace…</p>;
  if (!t.available) return <p className="p-3 text-xs text-bad">Trace unavailable: {t.reason}</p>;
  const r = t.records[0];
  const stage = t.stage_info.find((s) => s.key === r?.stage);
  const usable = t.evidence.filter((e) => e.usable).length;
  return <div className="space-y-3 bg-ink-850 p-4" data-testid="pipeline-trace">
    <p className="rounded-lg border border-line bg-white p-3 text-xs"><span className="font-semibold">Outcome: {stage?.label ?? r?.stage}.</span> {stage?.meaning}
      {r?.skip_reason && <> Reason recorded: <b>{humanize(r.skip_reason)}</b>.</>}
      {r?.in_explorer === true && " This claim is in the explorer's past.dev export."}
      {r?.in_explorer === false && r.datapoint_id && " It is in Neon's send log but not in the explorer's last past.dev pull."}</p>
    <ol className="space-y-4">
      <Step n={1} title="Captured and extracted" note={`${t.claims.length} extracted claim${t.claims.length === 1 ? "" : "s"} state this`}>
        {t.claims.slice(0, 5).map((c) => <div key={c.claim_id + c.extracted_at} className="rounded-lg border border-line bg-white p-2.5">
          <p>“{c.quote}”</p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
            <Badge>{titleCase(c.type)}</Badge><Badge tone={c.grounding === "exact" ? "ok" : "warn"}>quote match: {c.grounding}</Badge>
            <Badge>{humanize(c.modality)}</Badge><Badge>politician is {humanize(c.relation_to_politician)}</Badge>
            <span>check-worthiness {c.checkworthiness ?? "—"}</span><span>stated {fmtTime(c.stated_at)}</span><span>extracted {fmtTime(c.extracted_at)}</span>
            {c.url && <ExtLink href={c.url}>{c.platform} post{c.profile_handle && ` @${c.profile_handle}`}</ExtLink>}
          </p>
        </div>)}
        {t.claims.length > 5 && <p className="text-muted">and {t.claims.length - 5} more</p>}
      </Step>
      <Step n={2} title="Evidence intake" note={intakeNote(t.queue)}>
        {t.queue && <p className="text-muted">Queued {fmtTime(t.queue.queued_at)} · priority {t.queue.priority?.toFixed(1) ?? "—"} · reach {compact(t.queue.reach)} views · check-worthiness {t.queue.max_checkworthiness ?? "—"} · last update {fmtTime(t.queue.updated_at)}</p>}
      </Step>
      <Step n={3} title="Evidence search" note={`${t.searches.length} searches, ${t.searches.reduce((a, s) => a + s.urls.length, 0)} pages found`}>
        {t.searches.map((s) => <div key={s.request_id} className="rounded-lg border border-line bg-white p-2.5">
          <p className="font-medium">“{s.query}”</p>
          <p className="text-[11px] text-muted">{fmtTime(s.requested_at)} · {s.result_count ?? 0} results · ${s.cost_usd ?? 0}{s.domains?.length ? ` · limited to ${s.domains.length} official domains` : ""}</p>
          <ul className="mt-1 space-y-0.5">{s.urls.map((u) => <li key={u.url} className="flex items-center gap-2">
            <Badge tone={FETCH_TONE[u.fetch_status ?? ""] ?? "default"} className="shrink-0">{u.fetch_status ? humanize(u.fetch_status) : "not fetched"}</Badge>
            <ExtLink href={u.url} className="min-w-0 truncate">{u.title || u.url}</ExtLink>
            {u.last_error && <span className="shrink-0 text-bad">{u.last_error}</span>}
          </li>)}</ul>
        </div>)}
      </Step>
      <Step n={4} title="Evidence judged" note={`${t.evidence.length} pages judged, ${usable} usable`}>
        {t.evidence.map((e) => <div key={e.link_id} className={cn("rounded-lg border border-line bg-white p-2.5", !e.usable && "opacity-70")}>
          <p className="flex flex-wrap items-center gap-1.5"><Badge tone={STANCE_TONE[e.stance] ?? "default"}>{e.stance}</Badge>
            <span className="text-muted">confidence {e.confidence ?? "—"}</span>
            <Badge tone={e.quote_verified ? "ok" : "warn"}>{e.quote_verified ? "quote found in page" : "quote not found in page"}</Badge>
            <ExtLink href={e.url} className="min-w-0 truncate">{e.publisher_domain}{e.title ? `: ${e.title}` : ""}</ExtLink></p>
          {e.quote && <p className="mt-1">“{e.quote}”</p>}
          {e.rationale && <p className="mt-1 text-muted">{e.rationale}</p>}
          {e.not_sent_because && <p className="mt-1 text-warn">Not sent to past.dev: {e.not_sent_because}.</p>}
        </div>)}
      </Step>
      <Step n={5} title="Sent to past.dev" note={t.sent.length ? `${t.sent.length} records` : "nothing sent"}>
        {!!t.sent.length && <Table head={["past.dev record", "Kind", "Status", "Sent", "Confirmed"]} rows={t.sent.map((s) => [
          <code className="break-all text-[11px]">{s.datapoint_id}</code>, humanize(s.kind),
          <Badge tone={s.status === "completed" ? "ok" : s.status === "failed" ? "bad" : "accent"}>{s.status === "completed" ? "confirmed" : s.status === "queued" ? "processing" : s.status}</Badge>,
          fmtTime(s.sent_at), s.error ? <span className="text-bad">{s.error}</span> : fmtTime(s.completed_at)])} />}
      </Step>
    </ol>
  </div>;
}

/** Claim-level list with filters; each row opens its trace. */
function Records({ iso2, summary, stage, politician, onStage, onPolitician }: {
  iso2: string; summary: PipelineSummary; stage: string; politician: string; onStage: (s: string) => void; onPolitician: (p: string) => void;
}) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [data, setData] = useState<PipelineRecords | null>(null);
  useEffect(() => { setPage(0); setOpen(null); }, [stage, politician, q]);
  useEffect(() => {
    const ctl = new AbortController();
    const timer = setTimeout(() => pipelineApi.records(iso2, { stage, politician_id: politician, q, limit: PAGE, offset: page * PAGE }, ctl.signal)
      .then((d) => { if (!ctl.signal.aborted) setData(d); }).catch(() => {}), 250);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [iso2, stage, politician, q, page]);
  const label = useMemo(() => Object.fromEntries(summary.stage_info.map((s) => [s.key, s.label])), [summary]);
  const names = useMemo(() => Object.fromEntries(summary.politicians.map((p) => [p.politician_id, p.name ?? p.politician_id])), [summary]);
  const pages = data ? Math.ceil(data.total / PAGE) : 0;
  const select = "rounded-lg border border-line bg-white px-2 py-1.5 text-xs";
  return <Card data-testid="pipeline-records">
    <CardHeader title="Claim trace" aside={data && <span className="text-[11px] text-muted">{num(data.total)} claims</span>} />
    <div className="px-4 pb-4">
      <p className="mb-2 text-xs text-muted">One row per distinct claim per politician. Select a row to see every recorded step and why it did or did not reach past.dev.</p>
      <div className="flex flex-wrap gap-2">
        <label className="relative min-w-40 flex-1"><Search className="absolute left-2 top-2 size-3.5 text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search claim text" aria-label="Search claim text" className="w-full rounded-lg border border-line bg-white py-1.5 pl-7 pr-2 text-xs" /></label>
        <select aria-label="Stage" value={stage} onChange={(e) => onStage(e.target.value)} className={select}>
          <option value="">All stages</option>{summary.stage_info.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
        <select aria-label="Politician" value={politician} onChange={(e) => onPolitician(e.target.value)} className={cn(select, "max-w-52")}>
          <option value="">All politicians</option>{summary.politicians.map((p) => <option key={p.politician_id} value={p.politician_id}>{p.name ?? p.politician_id}</option>)}</select>
      </div>
      {!data && <p className="mt-3 text-xs text-muted">Loading claims…</p>}
      {data && !data.available && <p className="mt-3 text-xs text-bad">Unavailable: {data.reason}</p>}
      {data?.available && <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
        {data.records.map((r) => {
          const id = `${r.proposition_id}|${r.politician_id}`;
          return <li key={id}>
            <button onClick={() => setOpen(open === id ? null : id)} aria-expanded={open === id} className="flex w-full items-start gap-2 px-3 py-2 text-left text-xs hover:bg-ink-850">
              {open === id ? <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-muted" /> : <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-muted" />}
              <span className="min-w-0 flex-1"><span className="line-clamp-2">{r.canonical_text}</span>
                <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                  <span className="font-medium text-fg">{names[r.politician_id] ?? r.politician_id}</span>
                  {r.types.map((t) => <span key={t} className="flex items-center gap-1"><span className="size-2 rounded-full" style={{ background: colorOf(CLAIM_TYPE_COLORS, t) }} />{titleCase(t)}</span>)}
                  {r.judged > 0 && <span>{r.supports} support · {r.contradicts} contradict · {r.context} context · {r.judged - r.usable} unusable</span>}
                  {r.skip_reason && <span>{humanize(r.skip_reason)}</span>}
                </span></span>
              <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-line px-2 py-0.5 text-[11px]"><span className="size-2 rounded-full" style={{ background: STAGE_COLORS[r.stage] }} />{label[r.stage] ?? r.stage}</span>
            </button>
            {open === id && <Trace iso2={iso2} rec={r} />}
          </li>;
        })}
        {!data.records.length && <li className="p-4 text-center text-xs text-muted">No claims match these filters.</li>}
      </ul>}
      {pages > 1 && <div className="mt-3 flex items-center justify-between text-xs text-muted">
        <Button aria-label="Previous page" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
        <span>Page {page + 1} of {num(pages)}</span>
        <Button aria-label="Next page" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</Button>
      </div>}
    </div>
  </Card>;
}

export default function Pipeline({ iso2 }: { iso2: string }) {
  const [state, setState] = useState<{ iso2: string; data: PipelineSummary | null; error?: boolean } | null>(null);
  const [reload, setReload] = useState(0);
  const [stage, setStage] = useState("");
  const [politician, setPolitician] = useState("");
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    const ctl = new AbortController();
    pipelineApi.summary(iso2, ctl.signal).then((data) => { if (!ctl.signal.aborted) setState({ iso2, data }); })
      .catch(() => { if (!ctl.signal.aborted) setState({ iso2, data: null, error: true }); });
    return () => ctl.abort();
  }, [iso2, reload]);
  const s = state?.iso2 === iso2 ? state.data : null;
  if (!state || state.iso2 !== iso2) return <p className="p-6 text-sm text-muted" role="status">Loading the pipeline…</p>;
  if (!s?.available) return <Card className="m-4 p-6 text-sm text-muted lg:mx-6" data-testid="pipeline-unavailable">
    The pipeline database is not connected{s?.reason ? ` (${s.reason})` : ""}. Set <code>PIPELINE_DATABASE_URL</code> in <code>.env</code> to the Systems B and C Postgres and restart the server. The page only reads from it.
  </Card>;
  const top = Math.max(1, ...s.funnel.slice(2).map((f) => f.count));
  const maxHour = Math.max(1, ...s.hourly.map((h) => Math.max(h.extracted, h.judged, h.sent, h.confirmed)));
  const rc = s.reconciliation;
  const pols = showAll ? s.politicians : s.politicians.slice(0, 12);
  const focus = (st: string, pid = "") => { setStage(st); setPolitician(pid); document.getElementById("pipeline-records")?.scrollIntoView({ behavior: "smooth" }); };
  const sentErrors = s.sent.reduce((a, x) => a + x.errors, 0);
  return <div className="space-y-4 p-4 lg:px-6" data-testid="pipeline-page">
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      <span>Read-only view of what the pipeline database (Neon) has recorded, as of {fmtTime(s.generated_at)}. Figures refresh at most once a minute.</span>
      <Button className="ml-auto" onClick={() => setReload(reload + 1)}><RefreshCw className="size-3.5" />Refresh</Button>
    </div>

    <Card data-testid="pipeline-funnel"><CardHeader title="From post to past.dev" />
      <div className="space-y-3 px-4 pb-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label={s.funnel[0].label} value={num(s.funnel[0].count)} /><Stat label={s.funnel[1].label} value={num(s.funnel[1].count)} />
          <Stat label="Sent to past.dev" value={num(s.funnel[8].count)} hint="Claims in Neon's send log" />
          <Stat label="Confirmed by past.dev" value={num(s.funnel[9].count)} tone="ok" hint="past.dev reported the record as fully processed" />
        </div>
        <ul className="space-y-1.5 text-xs">{s.funnel.slice(2).map((f, i, all) => <li key={f.key} className="grid grid-cols-[minmax(0,13rem)_1fr_4rem_3rem] items-center gap-2">
          <span className="truncate">{f.label}</span>
          <span className="h-3 overflow-hidden rounded-full bg-ink-700"><span className="block h-full rounded-full bg-accent" style={{ width: `${(100 * f.count) / top}%` }} /></span>
          <span className="text-right font-medium tabular-nums">{num(f.count)}</span>
          <span className="text-right text-muted tabular-nums" title="Share of the previous step">{i ? `${pct(f.count, all[i - 1].count)}%` : ""}</span>
        </li>)}</ul>
        <p className="text-[11px] text-muted">Counted per distinct claim per politician, using each post's latest successful extraction. The percentage is the share kept from the step above.</p>
      </div>
    </Card>

    <div className="grid gap-4 lg:grid-cols-2">
      <Card data-testid="pipeline-stages"><CardHeader title="Where every claim stands now" />
        <ul className="px-2 pb-3 text-xs">{s.stage_info.map((st) => <li key={st.key}>
          <button onClick={() => focus(st.key)} title={st.meaning} className="grid w-full grid-cols-[0.6rem_minmax(0,1fr)_4rem] items-center gap-2 rounded-md px-2 py-1 text-left hover:bg-ink-850">
            <span className="size-2.5 rounded-full" style={{ background: STAGE_COLORS[st.key] }} />
            <span className="min-w-0"><span className="block truncate">{st.label}</span><span className="block truncate text-[11px] text-muted">{st.meaning}</span></span>
            <span className="text-right font-medium tabular-nums">{num(s.stages[st.key])}</span>
          </button></li>)}</ul>
        {!!s.skip_reasons.length && <p className="border-t border-line px-4 py-2 text-[11px] text-muted">Skipped at intake: {s.skip_reasons.map((x) => `${num(x.records)} ${humanize(x.reason)}`).join(" · ")}</p>}
      </Card>

      <div className="space-y-4">
        <Card data-testid="pipeline-reconciliation"><CardHeader title="Neon send log vs explorer's past.dev pull" />
          <div className="space-y-2 px-4 pb-4 text-xs">
            {rc.explorer_available ? <>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Extracted claims sent (Neon log)" value={num(rc.neon_sent_claims)} />
                <Stat label="Claims read back from past.dev" value={num(rc.explorer_claims)} />
                <Stat label="Sent but not in the last pull" value={num(rc.sent_not_in_explorer)} tone={rc.sent_not_in_explorer ? "bad" : "ok"} hint="Sent after the last pull, or missing in past.dev" />
                <Stat label="In past.dev but not in the log" value={num(rc.in_explorer_not_in_log)} tone={rc.in_explorer_not_in_log ? "bad" : "ok"} hint="Claims past.dev returned that Neon has no send record for" />
              </div>
              <p className="text-[11px] text-muted">Compares claim IDs in Neon's send log with the explorer's last pull from {rc.explorer_source ?? "past.dev"} ({fmtTime(rc.explorer_pulled_at)}).
                {rc.explorer_evidence_records != null && ` The pull also saw ${num(rc.explorer_evidence_records)} document and evidence-link records in past.dev.`} Run <code>make sc-pull</code> to refresh it.</p>
            </> : <p className="text-muted">No past.dev pull on this machine yet. Run <code>make sc-pull</code> to compare.</p>}
          </div>
        </Card>
        <Card><CardHeader title="Records sent to past.dev" />
          <div className="px-2 pb-3"><Table head={["Kind", "Status", "Records", "MB", "Last sent", "Last confirmed"]} rows={s.sent.map((x) => [
            humanize(x.kind), x.status === "completed" ? "confirmed" : x.status === "queued" ? "processing" : x.status, num(x.records), (x.bytes / 1e6).toFixed(1), fmtTime(x.last_sent), fmtTime(x.last_confirmed)])} /></div>
        </Card>
      </div>
    </div>

    <div className="grid gap-4 lg:grid-cols-2">
      <Card data-testid="pipeline-activity"><CardHeader title="Activity, last 48 hours" />
        <div className="space-y-3 px-4 pb-4 text-xs">
          {(["extracted", "judged", "sent", "confirmed"] as const).map((k) => <div key={k} className="grid grid-cols-[6.5rem_1fr_3.5rem] items-end gap-2">
            <span className="text-muted">{{ extracted: "Claims extracted", judged: "Pages judged", sent: "Records sent", confirmed: "Confirmed" }[k]}</span>
            <span className="flex h-8 items-end gap-px">{s.hourly.map((h) => <span key={h.hour} title={`${fmtTime(h.hour)}: ${h[k]}`} className="min-w-0 flex-1 rounded-t-sm bg-accent/75" style={{ height: `${h[k] ? Math.max(6, (100 * h[k]) / maxHour) : 0}%` }} />)}</span>
            <span className="text-right tabular-nums">{num(s.hourly.reduce((a, h) => a + h[k], 0))}</span>
          </div>)}
          {!!s.hourly.length && <p className="flex justify-between text-[10px] text-muted"><span>{fmtTime(s.hourly[0].hour)}</span><span>{fmtTime(s.hourly.at(-1)!.hour)}</span></p>}
          <Table head={["Step", "Last recorded"]} rows={[["Capture from Oriane", fmtTime(s.last.capture)], ["Claim extraction", fmtTime(s.last.extraction)], ["Evidence search", fmtTime(s.last.search)],
            ["Evidence judgement", fmtTime(s.last.judgement)], ["Sent to past.dev", fmtTime(s.last.sent)], ["Confirmed by past.dev", fmtTime(s.last.confirmed)]]} />
          <p className="text-[11px] text-muted">Hourly totals count every write: claims extracted include re-runs of the same post, and records sent include documents and evidence links.</p>
          <p className="text-[11px] text-muted">Spend recorded so far: ${s.spend.exa_usd.toFixed(2)} on {num(s.spend.exa_requests)} Exa searches · {num(s.spend.oriane_credits)} Oriane credits · {(s.spend.pastdev_bytes / 1e6).toFixed(1)} MB sent to past.dev.</p>
        </div>
      </Card>

      <Card data-testid="pipeline-errors"><CardHeader title="Errors and blocked work" />
        <div className="space-y-3 px-4 pb-4 text-xs">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Failed sends" value={num(s.stages.send_failed + sentErrors)} tone={s.stages.send_failed + sentErrors ? "bad" : "ok"} />
            <Stat label="Failed extractions" value={num(s.extraction.find((x) => x.status === "failed")?.extractions)} />
            <Stat label="Capture runs out of credits" value={num(s.capture_runs.find((x) => x.status === "credits_exhausted")?.runs)} />
          </div>
          <p><span className="font-medium">Evidence pages:</span> {s.fetch.map((f) => `${num(f.urls)} ${humanize(f.status)}`).join(" · ")}</p>
          <Table head={["Page fetch problem", "Status", "Pages"]} rows={s.fetch_errors.map((e) => [e.error, humanize(e.status), num(e.urls)])} />
          <p><span className="font-medium">Evidence judgements:</span> {s.evidence.map((e) => `${num(e.links)} ${e.stance}${e.quote_verified ? "" : " (quote not found)"}`).join(" · ")}</p>
          <p><span className="font-medium">Capture runs:</span> {s.capture_runs.map((r) => `${r.runs} ${humanize(r.status)}`).join(" · ")}</p>
        </div>
      </Card>
    </div>

    <Card data-testid="pipeline-politicians"><CardHeader title={`By politician · ${s.politicians.length}`} />
      <div className="px-2 pb-3"><div className="overflow-x-auto"><table className="w-full text-left text-xs">
        <thead><tr className="text-[11px] text-muted"><th className="px-2 py-1 font-medium">Politician</th><th className="px-2 py-1 text-right font-medium">Posts</th><th className="px-2 py-1 text-right font-medium">Claims</th>
          <th className="w-1/3 px-2 py-1 font-medium">Stages</th><th className="px-2 py-1 text-right font-medium">With evidence</th><th className="px-2 py-1 text-right font-medium">Sent</th><th className="px-2 py-1 text-right font-medium">Confirmed</th></tr></thead>
        <tbody>{pols.map((p) => {
          const sent = p.stages.sent_unconfirmed + p.stages.in_pastdev + p.stages.send_failed;
          return <Fragment key={p.politician_id}><tr className="cursor-pointer border-t border-line hover:bg-ink-850" onClick={() => focus("", p.politician_id)}>
            <td className="px-2 py-1.5 font-medium text-accent">{p.name ?? p.politician_id}</td>
            <td className="px-2 py-1.5 text-right tabular-nums" title={`${p.posts_extracted} of ${p.posts_captured} posts have claims extracted`}>{num(p.posts_captured)}</td>
            <td className="px-2 py-1.5 text-right tabular-nums">{num(p.records)}</td>
            <td className="px-2 py-1.5"><StageBar stages={p.stages} info={s.stage_info} /></td>
            <td className="px-2 py-1.5 text-right tabular-nums">{num(sent + p.stages.waiting_to_send)}</td>
            <td className="px-2 py-1.5 text-right tabular-nums">{num(sent)}</td>
            <td className="px-2 py-1.5 text-right tabular-nums">{num(p.stages.in_pastdev)}</td>
          </tr></Fragment>;
        })}</tbody>
      </table></div>
        {s.politicians.length > 12 && <Button variant="ghost" className="mt-2" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer" : `Show all ${s.politicians.length}`}</Button>}
        <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 px-2 text-[11px] text-muted">{s.stage_info.map((st) => <span key={st.key} className="flex items-center gap-1"><span className="size-2 rounded-full" style={{ background: STAGE_COLORS[st.key] }} />{st.label}</span>)}</p>
      </div>
    </Card>

    <div id="pipeline-records"><Records iso2={iso2} summary={s} stage={stage} politician={politician} onStage={setStage} onPolitician={setPolitician} /></div>
  </div>;
}
