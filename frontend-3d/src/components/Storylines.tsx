// "Storylines by week": how often a topic and each specific claim under it is repeated, week by week, and everything
// behind it. Rows are topics; a topic expands into its storylines. The grid only counts; the detail section under
// it keeps every post, its link and account, each statement and each evidence document, so grouping never hides a
// source.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, X } from "lucide-react";
import {
  claimsApi, type ClaimEvidence, type StanceCounts, type StorylineDetail, type StorylineGrid, type StorylinePost,
  type OpinionResult, type OpinionRun, type StorylineOpinion, type StorylineRow, type StorylineStatement, type StorylineTopic, type StorylineVersion, type WeekCell,
} from "../lib/api";
import { CLAIM_TYPE_COLORS, colorOf, compact, PLATFORM_META, SPEAKER_NAMES, titleCase } from "../lib/claims";
import { cn, fmtDate } from "../lib/presentation";
import { Badge, Button, ExtLink } from "./ui";

type Metric = "statements" | "posts";
type Side = 0 | 1 | 2; // index into a WeekCell: everyone, the politician, others
const SIDE_NAMES = ["Everyone", "The politician", "Other accounts"];
const SELF = new Set(["politician", "politician_quoted"]);
const SELF_COLOR = "var(--color-accent)", OTHERS_COLOR = "#d97706";
const STANCES = [["supports", "support", "ok"], ["contradicts", "contradict", "bad"], ["context", "context", "default"]] as const;
const TIERS: Record<number, string> = { 1: "Official source", 2: "Press or reference", 3: "Other source" };
const RELATIONS: Record<string, string> = {
  same_version: "Same claim, same figures", same_storyline_different_version: "Same claim, different figures or scope",
  counter_claim: "Disputes this claim", unrelated: "Not related to an existing storyline",
};
const selectCls = "rounded-lg border border-line bg-white px-2.5 py-1.5 text-xs focus-visible:outline-2 focus-visible:outline-accent";

const weekLabel = (w: string) => new Date(`${w}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
function isoWeek(w: string) {
  const d = new Date(`${w}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 3); // the Thursday decides the ISO week's year
  return 1 + Math.round((d.getTime() - Date.UTC(d.getUTCFullYear(), 0, 4)) / 604_800_000);
}
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const count = (cell: WeekCell | undefined, metric: Metric, side: Side) => cell?.[metric][side] ?? 0;
const rowTotal = (r: { cells: Record<string, WeekCell> }, metric: Metric, side: Side) =>
  Object.values(r.cells).reduce((a, c) => a + c[metric][side], 0);

export function StanceChips({ counts, className }: { counts: StanceCounts | undefined; className?: string }) {
  if (!counts || !STANCES.some(([k]) => counts[k])) return null;
  return <span className={cn("inline-flex flex-wrap gap-1", className)}>
    {STANCES.map(([k, word, tone]) => counts[k] > 0 && <Badge key={k} tone={tone}>{counts[k]} {word}{k !== "context" && counts[k] === 1 ? "s" : ""}</Badge>)}
  </span>;
}

function Dot({ cell, metric, side, max }: { cell: WeekCell | undefined; metric: Metric; side: Side; max: number }) {
  const n = count(cell, metric, side);
  if (!cell || !n) return null;
  const mostlySelf = side === 1 || (side === 0 && cell[metric][1] >= cell[metric][2]);
  const size = 14 + 18 * Math.sqrt(n / max);
  return <span className="inline-flex items-center justify-center rounded-full text-[10px] font-semibold text-white tabular-nums"
    style={{ width: size, height: size, background: mostlySelf ? SELF_COLOR : OTHERS_COLOR, opacity: 0.55 + 0.45 * (n / max) }}>{n}</span>;
}

function WeekStrip({ weeks, cells, metric }: { weeks: string[]; cells: Record<string, WeekCell>; metric: Metric }) {
  const max = Math.max(1, ...weeks.map((w) => count(cells[w], metric, 0)));
  return <div className="flex items-end gap-0.5" role="img" aria-label={`${metric} per week`}>
    {weeks.map((w) => {
      const n = count(cells[w], metric, 0);
      return <span key={w} title={`Week of ${weekLabel(w)}: ${n}`} className={cn("w-2.5 rounded-sm", n ? "bg-accent" : "bg-ink-700")}
        style={{ height: n ? 6 + 14 * (n / max) : 4 }} />;
    })}
  </div>;
}

export function EvidenceCard({ e }: { e: ClaimEvidence }) {
  const doc = e.document;
  const [, , tone] = STANCES.find(([k]) => k === e.stance) ?? STANCES[2];
  const tier = e.authority_tier ?? doc?.authority_tier;
  const when = doc?.published_at ?? doc?.retrieved_at;
  return <li className="rounded-lg border border-line bg-white p-2.5" data-testid="evidence-card">
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone={tone}>{titleCase(e.stance)}</Badge>
      {tier != null && <Badge tone={tier === 1 ? "accent" : "default"}>{TIERS[tier] ?? `Tier ${tier}`}</Badge>}
      <span className="text-muted">{[doc?.publisher, when && `${doc?.published_at && !doc.date_is_retrieval ? "" : "retrieved "}${fmtDate(when)}`,
        e.confidence != null && `${Math.round(100 * e.confidence)}% confidence`].filter(Boolean).join(" · ")}</span>
    </div>
    {doc?.url ? <ExtLink href={doc.url} className="mt-1.5 inline-flex items-start gap-1 font-medium">{doc.title || doc.url}<ExternalLink className="mt-0.5 size-3 shrink-0" /></ExtLink>
      : <p className="mt-1.5 text-muted">{doc?.title || "The source document for this evidence is not in the export."}</p>}
    {e.quote && <blockquote className="mt-1.5 border-l-2 border-accent/50 pl-2">“{e.quote}”</blockquote>}
    {e.rationale && <p className="mt-1.5 text-muted"><span className="font-medium text-fg">Why: </span>{e.rationale}</p>}
  </li>;
}

function Statement({ s, showEvidence }: { s: StorylineStatement; showEvidence: boolean }) {
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? showEvidence;
  const color = colorOf(CLAIM_TYPE_COLORS, s.type);
  const p = s.placement;
  return <li className="rounded-lg bg-ink-850 p-2.5" data-testid="storyline-statement">
    <div className="flex items-start gap-2">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-snug">“{s.text}”</p>
        {s.text_en && <p className="mt-0.5 leading-snug text-muted">{s.text_en}</p>}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        <Badge style={{ background: `${color}1a`, color }}>{titleCase(s.type)}</Badge>
        {s.speaker && <Badge>{SPEAKER_NAMES[s.speaker] ?? titleCase(s.speaker)}</Badge>}
      </div>
    </div>
    {s.evidence_span && s.evidence_span !== s.text && <blockquote className="mt-1.5 border-l-2 border-ink-600 pl-2 text-muted">Heard in the post: “{s.evidence_span}”</blockquote>}
    {p?.relation && <p className="mt-1.5 text-[11px] text-muted">
      {p.status === "needs_review" ? "Waiting for review, not grouped yet. Proposed: " : "Grouped as: "}{RELATIONS[p.relation] ?? titleCase(p.relation)}
      {p.confidence != null && ` (${Math.round(100 * p.confidence)}% confidence, ${p.judge === "human" ? "set by a reviewer" : "judge model"})`}
      {!!p.rule_blocks?.length && `. Kept as a separate version because the ${p.rule_blocks.join(", ")} differ`}
      {p.rationale && `. ${p.rationale}`}
    </p>}
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {s.evidence.length ? <button className="inline-flex items-center gap-1 text-accent hover:underline" aria-expanded={expanded} onClick={() => setOpen(!expanded)}>
        <ChevronRight className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />{expanded ? "Hide" : "Show"} {plural(s.evidence.length, "evidence document")}
      </button> : <span className="text-muted">No evidence documents for this statement.</span>}
      <StanceChips counts={s.evidence_counts} />
    </div>
    {expanded && !!s.evidence.length && <ul className="mt-2 space-y-1.5">{s.evidence.map((e) => <EvidenceCard key={e.link_id} e={e} />)}</ul>}
  </li>;
}

function Post({ post, side, showEvidence }: { post: StorylinePost; side: Side; showEvidence: boolean }) {
  const meta = PLATFORM_META[post.platform || ""] ?? { name: post.platform || "Post", color: "#64748b", short: "•" };
  const statements = post.statements.filter((s) => side === 0 || SELF.has(s.speaker || "") === (side === 1));
  if (!statements.length) return null;
  const id = post.platform_id || post.oriane_content_id || post.data_point_id;
  return <li className="rounded-xl border border-line bg-white p-3" data-testid="storyline-post">
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="rounded px-1.5 py-0.5 text-[10px] font-bold text-white" style={{ background: meta.color }}>{meta.name}</span>
      <span className="font-medium">{fmtDate(post.published_at)}</span>
      {post.uploader_handle ? <span>@{post.uploader_handle}{post.uploader_name && post.uploader_name !== post.uploader_handle && <span className="text-muted"> ({post.uploader_name})</span>}</span>
        : <span className="text-muted">Account not stored</span>}
      {post.engagement?.views != null && <span className="text-muted">{compact(post.engagement.views)} views</span>}
      {post.url ? <ExtLink href={post.url} className="ml-auto inline-flex items-center gap-1 font-medium">Open original post<ExternalLink className="size-3" /></ExtLink>
        : <span className="ml-auto text-muted" title="The capture did not store a link for this post.">No link stored · post id {id}</span>}
    </div>
    <ul className="mt-2 space-y-2">{statements.map((s) => <Statement key={s.claim_id} s={s} showEvidence={showEvidence} />)}</ul>
  </li>;
}

function Version({ v, index, weeks, metric, side, showEvidence, single }: {
  v: StorylineVersion; index: number; weeks: string[]; metric: Metric; side: Side; showEvidence: boolean; single: boolean;
}) {
  const [open, setOpen] = useState(true);
  const counter = v.kind === "counter_claim";
  return <li className={cn("rounded-xl border", counter ? "border-warn/40 bg-warn/5" : "border-line bg-ink-900")} data-testid="storyline-version">
    <button className="flex w-full items-center gap-3 p-3 text-left" aria-expanded={open} onClick={() => setOpen(!open)}>
      <ChevronRight className={cn("size-4 shrink-0 text-muted transition-transform", open && "rotate-90")} />
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-1.5 text-sm font-semibold">
          {!single && <span className="text-muted">{counter ? "Counter-claim" : `Version ${index + 1}`}</span>}
          <span>{v.label || v.text_en || v.text}</span>
        </p>
        <p className="mt-0.5 text-[11px] text-muted">First seen {fmtDate(v.first_seen)} · {plural(v.statements, "statement")} in {plural(v.posts, "post")}</p>
      </div>
      <WeekStrip weeks={weeks} cells={v.cells} metric={metric} />
    </button>
    {open && <ul className="space-y-2 px-3 pb-3 text-xs">{v.post_list.map((p) => <Post key={p.data_point_id} post={p} side={side} showEvidence={showEvidence} />)}</ul>}
  </li>;
}

function Detail({ iso2, politicianId, weeks, selected, metric, side, onClearWeek, onLoaded }: {
  iso2: string; politicianId: string; weeks: string[]; selected: { id: string; week: string | null }; metric: Metric; side: Side;
  onClearWeek: () => void; onLoaded: () => void;
}) {
  const key = `${iso2}|${politicianId}|${selected.id}|${selected.week ?? ""}`;
  const [state, setState] = useState<{ key: string; data: StorylineDetail | null; error: string | null } | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.storyline(iso2, politicianId, selected.id, selected.week, ctl.signal)
      .then((d) => { if (!ctl.signal.aborted) setState({ key, data: d.storyline, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setState({ key, data: null, error: String(e.message || e) }); });
    return () => ctl.abort();
  }, [iso2, politicianId, selected.id, selected.week, key]);
  const d = state?.key === key ? state.data : null;
  const loaded = !!d;
  // The page is only tall enough to bring the detail to the top once its posts are rendered.
  useEffect(() => { if (loaded) onLoaded(); }, [loaded, key, onLoaded]);
  if (state?.key === key && state.error) return <p className="text-xs text-bad">This storyline could not be loaded: {state.error}</p>;
  if (!d) return <p className="text-xs text-muted">Loading storyline…</p>;
  return <div>
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">Storyline{d.topic.judged && <> in the topic <span className="text-fg" data-testid="detail-topic">{d.topic.label}</span></>}</p>
        <h3 className="text-lg font-bold leading-tight">{d.label}</h3>
        <p className="mt-1 text-xs text-muted">
          {plural(d.statements, "statement")} in {plural(d.posts, "post")} · {plural(d.versions.length, "version")}
          {d.accounts.length > 0 && ` · ${plural(d.accounts.length, "account")}`}
          {d.documents > 0 && ` · ${plural(d.documents, "evidence document")}`}
        </p>
      </div>
      <Button onClick={() => setShowEvidence((v) => !v)} aria-pressed={showEvidence}>{showEvidence ? "Hide all evidence" : "Show all evidence"}</Button>
    </div>
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
      {d.area && <Badge tone="accent">{titleCase(d.area)}</Badge>}
      {d.week && <Badge tone="warn">Week of {weekLabel(d.week)} only<button aria-label="Show every week" className="ml-0.5 rounded-full hover:bg-warn/20" onClick={onClearWeek}><X className="size-3" /></button></Badge>}
      {side !== 0 && <Badge tone="warn">Said by: {SIDE_NAMES[side].toLowerCase()}</Badge>}
      {Object.entries(d.speakers).map(([k, n]) => <Badge key={k}>{SPEAKER_NAMES[k] ?? titleCase(k)}: {n}</Badge>)}
      <StanceChips counts={d.evidence_counts} />
      {!d.judged && <Badge tone="warn">Not grouped yet</Badge>}
    </div>
    {d.accounts.length > 0 && <p className="mt-2 text-xs text-muted">Accounts: {d.accounts.map((a) => `@${a}`).join(", ")}</p>}
    {!d.week && <div className="mt-3 flex items-center gap-3 text-[11px] text-muted"><WeekStrip weeks={weeks} cells={d.cells} metric={metric} />
      <span>{weeks.length > 0 && `${weekLabel(weeks[0])} to ${weekLabel(weeks.at(-1)!)}, ${metric} per week`}</span></div>}
    {d.opinion && <OpinionPanel opinion={d.opinion} updatedAt={d.opinion_updated_at} />}
    {d.pending_review.length > 0 && <p className="mt-3 rounded-lg border border-warn/40 bg-warn/5 p-2.5 text-xs">
      {plural(d.pending_review.length, "statement")} here {d.pending_review.length === 1 ? "is" : "are"} waiting for review: the judge was not sure where {d.pending_review.length === 1 ? "it belongs" : "they belong"}, so nothing was merged.
      Resolve with <code>python -m socialclaims storylines-review</code>.
    </p>}
    <ul className="mt-3 space-y-2">
      {d.versions.map((v, i) => <Version key={v.version_id} v={v} index={i} weeks={weeks} metric={metric} side={side} showEvidence={showEvidence} single={d.versions.length === 1} />)}
    </ul>
  </div>;
}

const OPINION_STATUS: Record<OpinionResult["status"], string> = {
  converged: "converged", decisive: "decisive", capped: "inconclusive", polling: "still polling",
};
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const signed = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`);

/** Belief share with its 95% CI on a 0–100% track. */
function BeliefBar({ o, wide }: { o: OpinionResult; wide?: boolean }) {
  if (o.p_believe == null) return null;
  const lo = (o.ci_low ?? o.p_believe) * 100, hi = (o.ci_high ?? o.p_believe) * 100;
  return <span className={cn("relative inline-block h-1.5 rounded-full bg-line align-middle", wide ? "w-40" : "w-14")} aria-hidden>
    <span className="absolute inset-y-0 rounded-full bg-accent/35" style={{ left: `${lo}%`, width: `${Math.max(hi - lo, 1)}%` }} />
    <span className="absolute -top-0.5 size-2.5 -translate-x-1/2 rounded-full bg-accent" style={{ left: `${o.p_believe * 100}%` }} />
  </span>;
}

function OpinionChip({ o }: { o: OpinionResult & { sim_run: string } }) {
  return <span className="mt-1 flex items-center gap-1.5 text-[11px] text-muted" data-testid="opinion-chip"
    title={`Simulated respondents (${o.sim_run}): ${pct(o.p_believe)} believe it (95% CI ${pct(o.ci_low)}–${pct(o.ci_high)}), n=${o.n_completed}, ${OPINION_STATUS[o.status]}. Not a real poll.`}>
    <span className="font-medium text-fg">Simulated belief {pct(o.p_believe)}</span><BeliefBar o={o} /><span>n={o.n_completed}</span>
  </span>;
}

/** Diverging −1..+1 axis: polled sentiment with its CI against the social media sentiment. */
function SentimentAxis({ run }: { run: OpinionRun }) {
  const x = (v: number) => `${((v + 1) / 2) * 100}%`;
  return <div className="relative mt-5 h-6 w-full max-w-md" role="img" aria-label={`Polled sentiment ${signed(run.sentiment_mean)}, social sentiment ${signed(run.social_sentiment)}`}>
    <div className="absolute inset-x-0 top-3 h-px bg-line" /><div className="absolute top-1 h-4 w-px bg-muted/50" style={{ left: "50%" }} />
    {run.sentiment_mean != null && run.sentiment_ci_low != null && run.sentiment_ci_high != null &&
      <div className="absolute top-2.5 h-1 rounded-full bg-accent/35" style={{ left: x(run.sentiment_ci_low), width: `${((run.sentiment_ci_high - run.sentiment_ci_low) / 2) * 100}%` }} />}
    {run.sentiment_mean != null && <span className="absolute top-1.5 size-3 -translate-x-1/2 rounded-full bg-accent" style={{ left: x(run.sentiment_mean) }} title="Polled (simulated)" />}
    {run.social_sentiment != null && <span className="absolute top-1.5 size-3 -translate-x-1/2 rotate-45 bg-[#d97706]" style={{ left: x(run.social_sentiment) }} title="Social media comments" />}
    <span className="absolute -bottom-3 left-0 text-[10px] text-muted">−1 negative</span><span className="absolute -bottom-3 right-0 text-[10px] text-muted">+1 positive</span>
  </div>;
}

function OpinionPanel({ opinion, updatedAt }: { opinion: StorylineOpinion; updatedAt: string | null | undefined }) {
  const runs = Object.keys(opinion.runs);
  const [pick, setPick] = useState(opinion.latest);
  const name = runs.includes(pick) ? pick : opinion.latest;
  const run = opinion.runs[name];
  const strata = [...run.strata].sort((a, b) => b.n - a.n);
  return <section className="mt-4 rounded-xl border border-line bg-ink-850/40 p-3" data-testid="opinion-panel">
    <div className="flex flex-wrap items-center gap-2">
      <h4 className="text-sm font-semibold">Simulated population opinion</h4>
      <Badge tone="warn">Simulated respondents, not a real poll</Badge>
      <Badge tone={run.status === "polling" || run.status === "capped" ? "default" : "accent"}>{OPINION_STATUS[run.status]}</Badge>
      {runs.length > 1 ? <select aria-label="Simulation run" value={name} onChange={(e) => setPick(e.target.value)} className={cn(selectCls, "ml-auto")}>
        {runs.map((r) => <option key={r} value={r}>{r}</option>)}</select> : <span className="ml-auto text-[11px] text-muted">{name}</span>}
    </div>
    <p className="mt-1 text-xs text-muted">Asked: «{opinion.polled_claim}»</p>
    <div className="mt-3 grid gap-4 sm:grid-cols-2">
      <div>
        <p className="text-[11px] uppercase tracking-[0.08em] text-muted">Believe the claim</p>
        <p className="text-2xl font-bold tabular-nums">{pct(run.p_believe)} <span className="text-xs font-normal text-muted">95% CI {pct(run.ci_low)}–{pct(run.ci_high)} · n={run.n_completed}</span></p>
        <BeliefBar o={run} wide />
      </div>
      <div>
        <p className="text-[11px] uppercase tracking-[0.08em] text-muted">Sentiment: <span className="text-accent">● polled {signed(run.sentiment_mean)}</span> · <span className="text-[#d97706]">◆ social {signed(run.social_sentiment)}</span>{run.gap != null && <> · gap {signed(run.gap)}</>}</p>
        <SentimentAxis run={run} />
        <p className="mt-4 text-[11px] text-muted">Social sentiment from {run.social_comments} real comments on the source posts.</p>
      </div>
    </div>
    {strata.length > 0 && <details className="mt-3 text-xs"><summary className="cursor-pointer text-muted">By population stratum ({strata.length})</summary>
      <table className="mt-2 w-full text-left text-[11px]"><thead><tr className="text-muted"><th>Region</th><th>Age</th><th>Gender</th><th className="text-right">n</th><th className="text-right">Believe</th><th className="text-right">Sentiment</th></tr></thead>
        <tbody>{strata.map((s) => <tr key={`${s.region}|${s.age_band}|${s.gender}`} className="border-t border-line"><td>{s.region}</td><td>{s.age_band}</td><td>{s.gender}</td>
          <td className="text-right tabular-nums">{s.n}</td><td className="text-right tabular-nums">{pct(s.share_believe)}</td><td className="text-right tabular-nums">{signed(s.sentiment_mean)}</td></tr>)}</tbody></table>
      <p className="mt-1 text-[11px] text-muted">Unweighted shares per stratum; the headline figure is post-stratified.</p></details>}
    <p className="mt-2 text-[11px] text-muted">LLM-generated personas answering a survey script (System D){updatedAt && `, exported ${fmtDate(updatedAt)}`}. They show how a synthetic sample reacts, not measured Mexican public opinion.</p>
  </section>;
}

function WeekCells({ row, label, weeks, metric, side, max, activeWeek, onPick, hint }: {
  row: { cells: Record<string, WeekCell>; posts: number }; label: string | null; weeks: string[]; metric: Metric; side: Side; max: number;
  activeWeek: string | null; onPick: (week: string) => void; hint: string;
}) {
  return <>
    {weeks.map((w) => {
      const n = count(row.cells[w], metric, side);
      return <td key={w} className={cn("border-b border-line p-0 text-center", activeWeek === w && "bg-accent/15")}>
        {n > 0 && <button className="flex h-11 w-full items-center justify-center hover:bg-accent/10" onClick={() => onPick(w)}
          aria-label={`${label}: ${plural(n, metric === "posts" ? "post" : "statement")} in the week of ${weekLabel(w)}`}
          title={`${plural(count(row.cells[w], "statements", side), "statement")} in ${plural(count(row.cells[w], "posts", side), "post")}, week of ${weekLabel(w)}. ${hint}`}>
          <Dot cell={row.cells[w]} metric={metric} side={side} max={max} />
        </button>}
      </td>;
    })}
    <td className="border-b border-line px-3 text-right font-semibold tabular-nums">{metric === "posts" && side === 0 ? row.posts : rowTotal(row, metric, side)}</td>
  </>;
}

/** Week grid of one politician's topics and the storylines under them, with the full detail of the selected storyline underneath. */
export function StorylinesView({ iso2, politicianId }: { iso2: string; politicianId: string }) {
  const key = `${iso2}|${politicianId}`;
  const [state, setState] = useState<{ key: string; data: StorylineGrid | null; error: string | null } | null>(null);
  const [metric, setMetric] = useState<Metric>("statements");
  const [side, setSide] = useState<Side>(0);
  const [area, setArea] = useState("");
  const [evidence, setEvidence] = useState("");
  const [recurring, setRecurring] = useState(false);
  const [open, setOpen] = useState<{ key: string; ids: Set<string> }>({ key, ids: new Set() });
  const [selected, setSelected] = useState<{ key: string; id: string; week: string | null } | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const toDetail = useCallback(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), []);

  // The filters that change which storylines a topic holds are applied by the API, so the topic counts stay exact.
  useEffect(() => {
    const ctl = new AbortController();
    claimsApi.storylines(iso2, politicianId, { area, evidence, recurring }, ctl.signal)
      .then((data) => { if (!ctl.signal.aborted) setState({ key, data, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setState({ key, data: null, error: String(e.message || e) }); });
    return () => ctl.abort();
  }, [iso2, politicianId, key, area, evidence, recurring]);

  const data = state?.key === key ? state.data : null;
  const groups = useMemo(() => {
    const members = new Map<string, StorylineRow[]>();
    for (const r of data?.storylines ?? []) {
      if (rowTotal(r, metric, side) > 0) members.set(r.topic_id, [...(members.get(r.topic_id) ?? []), r]);
    }
    return (data?.topics ?? []).filter((t: StorylineTopic) => members.has(t.topic_id))
      .map((t) => ({ topic: t, total: rowTotal(t, metric, side), rows: members.get(t.topic_id)! }))
      .sort((a, b) => b.total - a.total);
  }, [data, metric, side]);
  const max = useMemo(() => Math.max(1, ...groups.flatMap((g) => Object.values(g.topic.cells).map((c) => c[metric][side]))), [groups, metric, side]);
  const current = selected?.key === key ? selected : null;
  const expanded = open.key === key ? open.ids : new Set<string>();
  const loaded = !!data;
  // Open on the most recent weeks; older ones are a scroll to the left.
  useEffect(() => { if (loaded && gridRef.current) gridRef.current.scrollLeft = gridRef.current.scrollWidth; }, [loaded, key]);

  const select = (id: string, week: string | null) => {
    setSelected({ key, id, week });
    requestAnimationFrame(toDetail);
  };
  const toggle = (id: string, show?: boolean) => {
    const ids = new Set(expanded);
    if (show ?? !ids.has(id)) ids.add(id); else ids.delete(id);
    setOpen({ key, ids });
  };

  if (state?.key === key && state.error) return <p className="text-xs text-bad">Storylines could not be loaded: {state.error}</p>;
  if (!data) return <p className="text-xs text-muted">Loading storylines…</p>;
  if (!data.totals.statements) return <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">No social media claims have been collected for this politician yet.</p>;
  const { totals, grouping, weeks } = data;
  const ungrouped = grouping.units - grouping.placed;
  const shown = groups.reduce((n, g) => n + g.rows.length, 0);
  const allOpen = groups.length > 0 && groups.every((g) => expanded.has(g.topic.topic_id));
  return <div data-testid="storylines">
    <p className="text-xs text-muted">
      {plural(totals.statements, "statement")} from {plural(totals.posts, "post")}, in {plural(totals.storylines, "storyline")} under {plural(totals.topics, "topic")}.
      {" "}{totals.repeated} storylines were said more than once ({totals.recurring} of them in more than one week).
      A topic is the subject several storylines share; a storyline is one specific claim, and its versions differ in figures, period or place.
      {" "}{totals.with_post_link} of {totals.statements} statements link to their original post.
    </p>
    {(ungrouped > 0 || grouping.needs_review > 0 || grouping.without_topic > 0) && <p className="mt-2 rounded-lg border border-warn/40 bg-warn/5 p-2.5 text-xs" data-testid="grouping-note">
      {ungrouped > 0 && <>{grouping.placed ? `${ungrouped} of ${grouping.units} claims have not been grouped yet` : "These claims have not been grouped into storylines yet"}, so
        each is listed on its own and only identical claims share a row. Run <code>python -m socialclaims storylines</code> to group them. </>}
      {grouping.needs_review > 0 && <>{plural(grouping.needs_review, "claim")} {grouping.needs_review === 1 ? "is waiting for review and stays" : "are waiting for review and stay"} ungrouped until then. </>}
      {grouping.without_topic > 0 && <>{plural(grouping.without_topic, "storyline")} {grouping.without_topic === 1 ? "has" : "have"} no topic yet and {grouping.without_topic === 1 ? "is" : "are"} listed
        under {grouping.without_topic === 1 ? "its" : "their"} subject area instead. Run <code>python -m socialclaims topics</code> to group them.</>}
    </p>}
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <div className="inline-flex overflow-hidden rounded-lg border border-line text-xs" role="group" aria-label="Count by">
        {(["statements", "posts"] as const).map((m) => <button key={m} aria-pressed={metric === m} onClick={() => setMetric(m)}
          className={cn("px-2.5 py-1.5", metric === m ? "bg-accent font-medium text-white" : "bg-white text-muted hover:text-fg")}>{m === "statements" ? "Statements" : "Unique posts"}</button>)}
      </div>
      <select aria-label="Said by" value={side} onChange={(e) => setSide(Number(e.target.value) as Side)} className={selectCls}>
        {SIDE_NAMES.map((n, i) => <option key={n} value={i}>Said by: {n.toLowerCase()}</option>)}
      </select>
      {data.areas.length > 0 && <select aria-label="Subject area" value={area} onChange={(e) => setArea(e.target.value)} className={selectCls}>
        <option value="">All subject areas</option>{data.areas.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
      </select>}
      {data.has_evidence && <select aria-label="Evidence" value={evidence} onChange={(e) => setEvidence(e.target.value)} className={selectCls}>
        <option value="">Any evidence</option><option value="contradicts">Has contradicting evidence</option>
        <option value="supports">Has supporting evidence</option><option value="none">No evidence yet</option>
      </select>}
      <label className="inline-flex items-center gap-1.5 text-xs"><input type="checkbox" checked={recurring} onChange={(e) => setRecurring(e.target.checked)} className="accent-[var(--color-accent)]" />Only storylines seen in more than one week</label>
      <Button onClick={() => setOpen({ key, ids: allOpen ? new Set() : new Set(groups.map((g) => g.topic.topic_id)) })} disabled={!groups.length}>{allOpen ? "Collapse all topics" : "Expand all topics"}</Button>
      <span className="ml-auto inline-flex items-center gap-3 text-[11px] text-muted">
        <span className="inline-flex items-center gap-1"><span className="size-2.5 rounded-full" style={{ background: SELF_COLOR }} />Mostly the politician</span>
        <span className="inline-flex items-center gap-1"><span className="size-2.5 rounded-full" style={{ background: OTHERS_COLOR }} />Mostly other accounts</span>
      </span>
    </div>
    <div ref={gridRef} className="mt-3 max-h-[38rem] overflow-auto rounded-xl border border-line">
      <table className="w-full border-separate border-spacing-0 text-xs" data-testid="storyline-grid">
        <thead>
          <tr>
            <th scope="col" className="sticky left-0 top-0 z-20 min-w-64 border-b border-line bg-ink-850 px-3 py-2 text-left font-semibold">Topic <span className="font-normal text-muted">({groups.length}, with {plural(shown, "storyline")})</span></th>
            {weeks.map((w) => <th key={w} scope="col" className="sticky top-0 z-10 w-12 min-w-12 border-b border-line bg-ink-850 px-1 py-2 text-center font-medium" title={`Week starting ${fmtDate(w)}`}>
              <span className="block text-[10px] text-muted">W{isoWeek(w)}</span>{weekLabel(w)}</th>)}
            <th scope="col" className="sticky top-0 z-10 border-b border-line bg-ink-850 px-3 py-2 text-right font-medium">Total</th>
          </tr>
        </thead>
        {groups.map(({ topic: t, rows }) => {
          const isOpen = expanded.has(t.topic_id);
          const holdsCurrent = rows.some((r) => r.storyline_id === current?.id);
          const said = rows.filter((r) => r.statements > 1).length;
          return <tbody key={t.topic_id} data-testid="topic-group">
            <tr className="group" data-testid="topic-row">
              <th scope="row" className={cn("sticky left-0 z-10 border-b border-line p-0 text-left font-normal", isOpen ? "bg-ink-850" : "bg-white group-hover:bg-ink-850")}>
                <button className="flex w-full items-start gap-1.5 px-2 py-2 text-left" onClick={() => toggle(t.topic_id)} aria-expanded={isOpen}>
                  {isOpen ? <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted" /> : <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted" />}
                  <span className="min-w-0">
                    <span className={cn("line-clamp-2 text-[13px] font-semibold leading-snug", holdsCurrent && "text-accent")}>{t.label}</span>
                    <span className="mt-0.5 block text-[11px] text-muted">{[
                      plural(rows.length, "storyline"), said > 0 && `${said} said more than once`,
                      t.counter_claims > 0 && plural(t.counter_claims, "counter-claim"), t.accounts > 0 && plural(t.accounts, "account"),
                      !t.judged && "subject area, no topic yet", t.needs_review > 0 && `${t.needs_review} to review`,
                      rows.some((r) => r.opinion) && `${rows.filter((r) => r.opinion).length} with simulated opinion`].filter(Boolean).join(" · ")}</span>
                  </span>
                </button>
              </th>
              <WeekCells row={t} label={t.label} weeks={weeks} metric={metric} side={side} max={max} activeWeek={null}
                onPick={() => toggle(t.topic_id, true)} hint={isOpen ? "" : "Click to list the storylines."} />
            </tr>
            {isOpen && rows.map((r) => {
              const active = current?.id === r.storyline_id;
              return <tr key={r.storyline_id} className={cn("group", active && "bg-accent/5")} data-testid="storyline-row">
                <th scope="row" className={cn("sticky left-0 z-10 border-b border-line p-0 text-left font-normal", active ? "bg-[#eef0fd]" : "bg-white group-hover:bg-ink-850")}>
                  <button className="block w-full py-1.5 pl-8 pr-3 text-left" onClick={() => select(r.storyline_id, null)} aria-pressed={active && !current?.week}>
                    <span className={cn("line-clamp-2 text-xs leading-snug", active && "font-medium text-accent")}>{r.label}</span>
                    <span className="mt-0.5 block text-[11px] text-muted">{[
                      plural(r.versions, "version"), r.counter_claims > 0 && plural(r.counter_claims, "counter-claim"),
                      r.accounts > 0 && plural(r.accounts, "account"), r.area && titleCase(r.area), !r.judged && "not grouped yet",
                      r.needs_review > 0 && `${r.needs_review} to review`].filter(Boolean).join(" · ")}</span>
                    {r.opinion && <OpinionChip o={r.opinion} />}
                  </button>
                </th>
                <WeekCells row={r} label={r.label} weeks={weeks} metric={metric} side={side} max={max} activeWeek={active ? current.week : null}
                  onPick={(w) => select(r.storyline_id, w)} hint="Click to see the posts and evidence." />
              </tr>;
            })}
          </tbody>;
        })}
      </table>
      {!groups.length && <p className="p-6 text-center text-xs text-muted">No storylines match these filters.</p>}
    </div>
    <p className="mt-1.5 text-[11px] text-muted">Circle size is the number of {metric === "posts" ? "posts" : "statements"} that week. Open a topic to list its storylines; click a storyline or one of its circles to see every post, statement and evidence document behind it.</p>
    <div ref={detailRef} className="mt-5 scroll-mt-4 border-t border-line pt-4" data-testid="storyline-detail">
      {current ? <Detail iso2={iso2} politicianId={politicianId} weeks={weeks} selected={current} metric={metric} side={side} onClearWeek={() => setSelected({ ...current, week: null })} onLoaded={toDetail} />
        : <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">Open a topic and select a storyline to see its versions, every post with its link and account, and the evidence documents.</p>}
    </div>
  </div>;
}
