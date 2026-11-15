import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { BadgeCheck, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import {
  metricsApi, type EngagementSummary, type FeedbackCounts, type MetricsMethod, type MetricsPolitician, type MetricsSources,
  type MetricsSummary, type MetricsVideo, type Perception, type PoliticianMetrics as PoliticianMetricsData,
  type PresentationSummary, type Tone3, type VideoReading,
} from "../lib/api";
import { compact, PLATFORM_META } from "../lib/claims";
import { cn, fmtDate } from "../lib/presentation";
import Avatar from "./Avatar";
import { Badge, Button, Card, CardHeader } from "./ui";

const PAGE = 25;
const TONE: Record<Tone3, { text: string; bar: string; badge: "ok" | "bad" | "default" }> = {
  positive: { text: "text-ok", bar: "bg-ok", badge: "ok" },
  neutral: { text: "text-muted", bar: "bg-ink-600", badge: "default" },
  negative: { text: "text-bad", bar: "bg-bad", badge: "bad" },
};
const PRESENTATION_NAMES: Record<Tone3, string> = { positive: "Positive", neutral: "Neutral", negative: "Negative" };
const READING: Record<VideoReading, { name: string; hint: string }> = {
  official_communication: { name: "Official communication", hint: "Presented positively, posted from the politician's own account." },
  favourable_third_party: { name: "Favourable third-party content", hint: "Presented positively by an account that is not the politician's own." },
  critical_content: { name: "Critical content", hint: "Presented negatively." },
};
const signed = (n: number | null | undefined) => (n == null ? "—" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(Math.round(n))}`);
const num = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());
const selectCls = "rounded-lg border border-line bg-white px-2.5 py-1.5 text-xs focus-visible:outline-2 focus-visible:outline-accent";

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <div className="rounded-lg border border-line bg-white p-3" title={hint}>
    <p className="text-[11px] text-muted">{label}</p>
    <p className="text-lg font-semibold tabular-nums">{value}</p>
  </div>;
}

function SplitBar({ parts, label }: { parts: [Tone3, number][]; label: string }) {
  const total = parts.reduce((t, [, n]) => t + n, 0);
  return <div className="flex h-2 w-full overflow-hidden rounded-full bg-ink-700" role="img" aria-label={label}>
    {total > 0 && parts.map(([k, n]) => <span key={k} className={TONE[k].bar} style={{ width: `${(100 * n) / total}%` }} />)}
  </div>;
}

/** Index score as a small pill, e.g. "+33". */
export function IndexPill({ index, label, title }: { index: number | null | undefined; label: Tone3 | null | undefined; title?: string }) {
  if (index == null) return <span className="text-muted">—</span>;
  return <Badge tone={label ? TONE[label].badge : "default"} title={title} className="tabular-nums">{signed(index)}</Badge>;
}

/** Presentation and feedback labels of one video; used on claim rows and in the video table. */
export function PerceptionPills({ p, className }: { p: Perception | null | undefined; className?: string }) {
  if (!p || (!p.presentation && !p.feedback)) return null;
  const f = p.feedback;
  return <span className={cn("flex flex-wrap items-center gap-1", className)}>
    {p.presentation && <Badge tone={TONE[p.presentation].badge} title={p.presentation_reason ? `How the video presents the politician: ${p.presentation_reason}` : "How the video presents the politician"}>
      {PRESENTATION_NAMES[p.presentation]} portrayal</Badge>}
    {f && f.humans > 0 && <Badge tone={f.label ? TONE[f.label].badge : "default"} className="tabular-nums"
      title={`Comments: ${f.for} for, ${f.against} against, ${f.neutral} neutral${f.bots ? `, ${f.bots} likely bots excluded` : ""}`}>
      Comments {signed(f.index)}</Badge>}
  </span>;
}

function PresentationCard({ p, method }: { p: PresentationSummary | null | undefined; method: MetricsMethod }) {
  return <Card className="shadow-sm">
    <CardHeader title="Presentation index" aside={p && <span className={cn("text-2xl font-semibold tabular-nums", TONE[p.label].text)}>{signed(p.index)}</span>} />
    <div className="space-y-2 px-4 pb-4 text-xs">
      <p className="text-muted">{method.presentation}</p>
      {p ? <>
        <SplitBar parts={[["positive", p.positive], ["neutral", p.neutral], ["negative", p.negative]]}
          label={`${p.positive} positive, ${p.neutral} neutral, ${p.negative} negative videos`} />
        <p className="flex flex-wrap gap-x-4 gap-y-1"><span className="text-ok">{p.positive} positive</span><span className="text-muted">{p.neutral} neutral</span>
          <span className="text-bad">{p.negative} negative</span><span className="text-muted">{p.total} labelled videos</span></p>
        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 border-t border-line pt-2">
          {([["official_communication", p.official], ["favourable_third_party", p.favourable_third_party], ["critical_content", p.critical]] as const).map(([k, n]) =>
            <Fragment key={k}><dt className="text-muted" title={READING[k].hint}>{READING[k].name}</dt><dd className="tabular-nums">{n}</dd></Fragment>)}
        </dl>
      </> : <p className="rounded-lg border border-dashed border-line p-3 text-muted">No videos have been labelled for this yet.</p>}
    </div>
  </Card>;
}

function FeedbackCard({ f, method }: { f: FeedbackCounts | null | undefined; method: MetricsMethod }) {
  return <Card className="shadow-sm">
    <CardHeader title="Feedback index" aside={f && f.index != null && <span className={cn("text-2xl font-semibold tabular-nums", f.label ? TONE[f.label].text : "text-muted")}>{signed(f.index)}</span>} />
    <div className="space-y-2 px-4 pb-4 text-xs">
      <p className="text-muted">{method.feedback}</p>
      {f ? <>
        <SplitBar parts={[["positive", f.for], ["neutral", f.neutral], ["negative", f.against]]}
          label={`${f.for} comments for, ${f.neutral} neutral, ${f.against} against`} />
        <p className="flex flex-wrap gap-x-4 gap-y-1"><span className="text-ok">{f.for} for</span><span className="text-muted">{f.neutral} neutral</span>
          <span className="text-bad">{f.against} against</span><span className="text-muted">{f.humans} human comments</span></p>
        <p className="border-t border-dashed border-line pt-2 text-muted">Likely bots: <b className="text-warn">{f.analyzed ? Math.round((100 * f.bots) / f.analyzed) : 0}%</b> of {num(f.analyzed)} comments
          analysed{f.videos != null && ` on ${num(f.videos)} videos`} ({f.bot_for} for, {f.bot_against} against, {f.bot_neutral} neutral).</p>
      </> : <p className="rounded-lg border border-dashed border-line p-3 text-muted">No comments have been labelled for this yet.</p>}
    </div>
  </Card>;
}

function EngagementStats({ e }: { e: EngagementSummary }) {
  return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
    <Stat label="Posts captured" value={num(e.posts)} hint={Object.entries(e.platforms).map(([k, v]) => `${PLATFORM_META[k]?.name ?? k}: ${v}`).join(", ")} />
    <Stat label="Views" value={compact(e.views)} hint={`${num(e.views)} views on the ${num(e.posts_with_views)} posts that report views`} />
    <Stat label="Likes" value={compact(e.likes)} hint={num(e.likes)} />
    <Stat label="Comments" value={compact(e.comments)} hint={num(e.comments)} />
    <Stat label="Shares" value={compact(e.shares)} hint={num(e.shares)} />
    <Stat label="Engagement rate" value={e.engagement_rate == null ? "—" : `${e.engagement_rate}%`} hint="Likes, comments and shares per 100 views, over posts that report views" />
  </div>;
}

function SourcesNote({ s, method }: { s: MetricsSources; method: MetricsMethod }) {
  return <p className="text-[11px] leading-relaxed text-muted">
    {s.engagement && <>Engagement: {num(s.engagement.posts)} posts captured with Oriane, published {fmtDate(s.engagement.first)} – {fmtDate(s.engagement.last)}, counts as of {fmtDate(s.engagement.exported_at)}. </>}
    {s.perception && <>Indices: {num(s.perception.videos)} {PLATFORM_META[s.perception.platform]?.name ?? s.perception.platform} videos labelled in {s.perception.source}
      {s.perception.range && <>, published {fmtDate(s.perception.range.after)} – {fmtDate(s.perception.range.before)}</>}. </>}
    {method.limits}
  </p>;
}

function VideoRow({ v }: { v: MetricsVideo }) {
  const meta = PLATFORM_META[v.platform || ""];
  const title = (v.caption || "").split("\n")[0].trim() || `${meta?.name ?? "Post"} by @${v.uploader_handle ?? "unknown"}`;
  return <tr className="border-t border-line align-top">
    <td className="py-2 pr-3">
      <p className="line-clamp-2 max-w-md font-medium" title={v.caption ?? undefined}>{title}</p>
      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
        <span style={{ color: meta?.color }}>{meta?.name ?? v.platform}</span>
        {v.uploader_handle && <span>@{v.uploader_handle}</span>}<span>{fmtDate(v.published_at)}</span>
        {v.own_account && <Badge tone="accent">Own account</Badge>}
        {v.claims > 0 && <span>{v.claims} claim{v.claims > 1 ? "s" : ""} extracted</span>}
        {v.url && <a href={v.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-accent hover:underline">Open <ExternalLink className="size-3" /></a>}
      </p>
    </td>
    <td className="py-2 pr-3 text-right tabular-nums">{v.views == null ? "—" : compact(v.views)}</td>
    <td className="py-2 pr-3 text-right tabular-nums">{v.likes == null ? "—" : compact(v.likes)}</td>
    <td className="py-2 pr-3 text-right tabular-nums">{v.comments == null ? "—" : compact(v.comments)}</td>
    <td className="py-2 pr-3 text-right tabular-nums">{v.shares == null ? "—" : compact(v.shares)}</td>
    <td className="py-2">
      {v.labelled ? <><PerceptionPills p={v} />
        {v.reading && <p className="mt-1 text-[11px] text-muted" title={READING[v.reading].hint}>{READING[v.reading].name}</p>}
        {v.presentation_reason && <p className="mt-0.5 max-w-52 text-[11px] text-muted">{v.presentation_reason}</p>}</> : <span className="text-[11px] text-muted">Not labelled</span>}
    </td>
  </tr>;
}

/** One politician's metrics: engagement, the two indices and their videos. `politicianKey` is a person id or a monitor key. */
export function PoliticianMetrics({ iso2, politicianKey, compactView = false }: { iso2: string; politicianKey: string; compactView?: boolean }) {
  const [sort, setSort] = useState("views");
  const [show, setShow] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const query = `${iso2}|${politicianKey}|${sort}|${show}|${limit}`;
  const [res, setRes] = useState<{ query: string; data: PoliticianMetricsData | null; error: string | null } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    const filters = { sort, limit, labelled: show === "labelled" ? true : undefined, presentation: ["positive", "neutral", "negative"].includes(show) ? show : undefined };
    metricsApi.politician(iso2, politicianKey, filters, ctl.signal)
      .then((data) => { if (!ctl.signal.aborted) setRes({ query, data, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setRes({ query, data: null, error: String(e.message || e) }); });
    return () => ctl.abort();
  }, [iso2, politicianKey, sort, show, limit, query]);

  const d = res?.data;
  if (res?.error) return <p className="text-sm text-bad">Could not load metrics: {res.error}</p>;
  if (!d) return <p className="text-sm text-muted" role="status">Loading metrics…</p>;
  if (!d.available) return <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">
    No social media metrics have been collected for this politician yet.</p>;
  return <div className="space-y-4" data-testid="politician-metrics">
    {d.engagement ? <EngagementStats e={d.engagement} /> : <p className="rounded-lg border border-dashed border-line p-3 text-xs text-muted">
      No posts have been captured for this politician in our own pipeline; the videos below come from the labelled set only.</p>}
    <div className={cn("grid gap-3", !compactView && "lg:grid-cols-2")}>
      <PresentationCard p={d.presentation} method={d.method} />
      <FeedbackCard f={d.feedback} method={d.method} />
    </div>
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="mr-auto text-[11px] font-semibold uppercase tracking-wider text-muted">Posts and videos ({num(d.total)})</h4>
        <select aria-label="Show" className={selectCls} value={show} onChange={(e) => { setShow(e.target.value); setLimit(PAGE); }}>
          <option value="">All posts</option><option value="labelled">Labelled only</option>
          <option value="positive">Positive portrayal</option><option value="neutral">Neutral portrayal</option><option value="negative">Negative portrayal</option>
        </select>
        <select aria-label="Sort by" className={selectCls} value={sort} onChange={(e) => { setSort(e.target.value); setLimit(PAGE); }}>
          <option value="views">Most viewed</option><option value="likes">Most liked</option><option value="comments">Most commented</option>
          <option value="shares">Most shared</option><option value="published_at">Newest</option>
        </select>
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[640px] text-xs">
          <thead><tr className="text-left text-[11px] text-muted"><th className="pb-1.5 font-medium">Post</th><th className="pb-1.5 pr-3 text-right font-medium">Views</th>
            <th className="pb-1.5 pr-3 text-right font-medium">Likes</th><th className="pb-1.5 pr-3 text-right font-medium">Comments</th>
            <th className="pb-1.5 pr-3 text-right font-medium">Shares</th><th className="pb-1.5 font-medium">Portrayal and comments</th></tr></thead>
          <tbody>{d.videos.map((v) => <VideoRow key={`${v.platform}:${v.platform_id}`} v={v} />)}</tbody>
        </table>
        {!d.videos.length && <p className="p-4 text-center text-xs text-muted">No posts match this filter.</p>}
      </div>
      {d.total > d.videos.length && <div className="mt-2 text-center"><Button onClick={() => setLimit((n) => Math.min(n + PAGE, 200))} disabled={res?.query !== query}>Show more</Button></div>}
    </div>
    <SourcesNote s={d.sources} method={d.method} />
  </div>;
}

type SortKey = "views" | "posts" | "likes" | "comments" | "shares" | "engagement_rate" | "presentation" | "feedback";
const sortValue = (p: MetricsPolitician, k: SortKey) =>
  k === "presentation" ? p.presentation?.index : k === "feedback" ? p.feedback?.index : p.engagement?.[k];

/** Country page: every tracked politician with engagement and the two indices; each row leads to that politician. */
export default function Metrics({ iso2, onOpenPerson }: { iso2: string; onOpenPerson: (id: string) => void }) {
  const [res, setRes] = useState<{ iso2: string; data: MetricsSummary | null; error: string | null } | null>(null);
  const [sort, setSort] = useState<SortKey>("views");
  const [only, setOnly] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    metricsApi.summary(iso2, ctl.signal).then((data) => { if (!ctl.signal.aborted) setRes({ iso2, data, error: null }); })
      .catch((e) => { if (!ctl.signal.aborted) setRes({ iso2, data: null, error: String(e.message || e) }); });
    return () => ctl.abort();
  }, [iso2]);
  const d = res?.iso2 === iso2 ? res.data : null;
  const rows = useMemo(() => (d?.politicians ?? [])
    .filter((p) => only === "" || (only === "indices" ? !!(p.presentation || p.feedback) : !!p.engagement))
    .sort((a, b) => (sortValue(b, sort) ?? -Infinity) - (sortValue(a, sort) ?? -Infinity)), [d, sort, only]);

  if (res?.error) return <div className="p-6 text-sm text-bad">Could not load metrics: {res.error}</div>;
  if (!d) return <div className="p-6 text-sm text-muted" role="status">Loading metrics…</div>;
  if (!d.available) return <div className="p-4 lg:px-6"><Card className="p-6 text-sm text-muted">
    No metrics have been exported for this country yet. Run <code>make sc-metrics</code> to export post engagement from the pipeline database.</Card></div>;
  const t = d.totals;
  const th = (k: SortKey, text: string, right = true) => <th className={cn("pb-2 font-medium", right && "pr-3 text-right")}>
    <button className={cn("hover:text-fg", sort === k && "font-semibold text-fg")} onClick={() => setSort(k)} aria-pressed={sort === k}>{text}{sort === k && " ↓"}</button></th>;
  return <div className="space-y-4 p-4 lg:px-6" data-testid="metrics-page">
    {t?.engagement && <EngagementStats e={t.engagement} />}
    <div className="grid gap-3 lg:grid-cols-2">
      <PresentationCard p={t?.presentation} method={d.method} />
      <FeedbackCard f={t?.feedback} method={d.method} />
    </div>
    <Card className="shadow-sm">
      <CardHeader title={`By politician (${rows.length})`} aside={<select aria-label="Show politicians" className={selectCls} value={only} onChange={(e) => setOnly(e.target.value)}>
        <option value="">All politicians</option><option value="indices">With presentation or feedback index</option><option value="engagement">With captured posts</option></select>} />
      <div className="overflow-x-auto px-4 pb-4">
        <table className="w-full min-w-[820px] text-xs">
          <thead><tr className="text-left text-[11px] text-muted"><th className="pb-2 font-medium">Politician</th>
            {th("posts", "Posts")}{th("views", "Views")}{th("likes", "Likes")}{th("comments", "Comments")}{th("shares", "Shares")}
            {th("engagement_rate", "Eng. rate")}{th("presentation", "Presentation")}{th("feedback", "Feedback")}<th /></tr></thead>
          <tbody>{rows.map((p) => {
            const e = p.engagement, expanded = open === p.key;
            return <Fragment key={p.key}>
              <tr className="cursor-pointer border-t border-line hover:bg-ink-850" onClick={() => setOpen(expanded ? null : p.key)}>
                <td className="py-2 pr-3"><div className="flex items-center gap-2">
                  <Avatar name={p.name} src={p.image_url} className="size-7 text-[10px]" />
                  <div className="min-w-0">
                    {p.politician_id && p.in_graph
                      ? <button className="flex items-center gap-1 text-left font-medium text-accent hover:underline" onClick={(ev) => { ev.stopPropagation(); onOpenPerson(p.politician_id!); }}>
                        {p.name}<BadgeCheck className="size-3.5" aria-label="In the knowledge graph" /></button>
                      : <span className="font-medium">{p.name}</span>}
                    <p className="max-w-64 truncate text-[11px] text-muted">{p.politician_id && p.in_graph ? p.role ?? "Open profile and metrics" : `${p.role ? `${p.role} · ` : ""}not in the knowledge graph yet`}</p>
                  </div></div></td>
                <td className="py-2 pr-3 text-right tabular-nums">{e ? num(e.posts) : "—"}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{e ? compact(e.views) : "—"}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{e ? compact(e.likes) : "—"}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{e ? compact(e.comments) : "—"}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{e ? compact(e.shares) : "—"}</td>
                <td className="py-2 pr-3 text-right tabular-nums">{e?.engagement_rate != null ? `${e.engagement_rate}%` : "—"}</td>
                <td className="py-2 pr-3 text-right"><IndexPill index={p.presentation?.index} label={p.presentation?.label} title={p.presentation ? `${p.presentation.total} labelled videos` : undefined} /></td>
                <td className="py-2 pr-3 text-right"><IndexPill index={p.feedback?.index} label={p.feedback?.label} title={p.feedback ? `${p.feedback.humans} human comments` : undefined} /></td>
                <td className="py-2 text-muted">{expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</td>
              </tr>
              {expanded && <tr><td colSpan={10} className="border-t border-line bg-ink-850/60 p-3">
                {p.politician_id && p.in_graph && <div className="mb-3"><Button onClick={() => onOpenPerson(p.politician_id!)}>Open {p.name}'s profile and metrics <ChevronRight className="size-4" /></Button></div>}
                <PoliticianMetrics iso2={iso2} politicianKey={p.key} />
              </td></tr>}
            </Fragment>;
          })}</tbody>
        </table>
      </div>
    </Card>
    <SourcesNote s={d.sources} method={d.method} />
    {!!d.sources.perception?.unmatched.length && <p className="text-[11px] text-muted">Labelled but not in the knowledge graph yet, so they have no profile page: {d.sources.perception.unmatched.join(", ")}.</p>}
  </div>;
}
