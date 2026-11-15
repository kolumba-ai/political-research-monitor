// Typed client for the existing FastAPI backend. All political data comes from these calls.

export type RegimeGroup = "Democratic" | "Partly democratic" | "Authoritarian" | "Unclassified";

export interface CountryRow {
  iso2: string;
  iso3: string;
  name: string;
  continent: string;
  regime_group: RegimeGroup;
  regime_class: string | null;
  regime_sources_agree: boolean | null;
  research_status: string;
  has_structure: boolean;
  last_verified: string | null;
}

export interface SourceRef { url: string; title?: string | null; domain?: string | null; retrieved_at?: string | null }

export interface Assessment {
  id: string;
  index: string;
  label: string;
  classification: string;
  year: number | null;
  score: number | null;
  score_scale: string | null;
  grey_zone: boolean | null;
  verification: string;
  evidence_url: string | null;
  evidence_quote?: string | null;
  sources: SourceRef[];
}

export interface RegimeSource { class: string; group: RegimeGroup; year: number | null; verification: string; evidence_url: string | null }

export interface ContactDimension {
  purpose: string; channel: string; sender: string; recipient: string; status: string;
  conditions?: string | null; legal_basis?: string | null; evidence_url?: string | null; evidence_fetched?: boolean;
}

export interface Tier { id: string; name: string; tier: string; article: string | null; child_count: number }

export interface CountryStats {
  levels: number; units_by_tier: Record<string, number>; bodies: number; offices: number; elected_offices: number;
  offices_with_current_holder: number; current_officeholders: number; popularly_elected_officeholders: number;
  holdings_by_evidence: Record<string, number>;
}

export interface CountryDetail {
  country: CountryRow & {
    m49: string; un_region: string; un_sub_region: string;
    system_of_government: string | null; constitution_name: string | null; constitution_url: string | null;
  };
  regime: { group: RegimeGroup; class: string | null; basis: string | null; sources_agree: boolean | null;
            by_source: Record<string, RegimeSource> };
  assessments: Assessment[];
  policy_checks: { democracy: unknown; contact: unknown; note: string };
  contact_rules: null | {
    id: string; status: string; review_status: string; status_reasons: string[] | null; disclaimer: string | null;
    dimensions: ContactDimension[]; laws: { name: string; category: string; url?: string | null }[];
    last_verified: string | null; sources: SourceRef[];
  };
  structure: {
    tiers: Tier[];
    spec: null | { path: string; status: string | null; version: string | null; official_text_url: string | null };
    stats: CountryStats | null;
  };
  last_verified: string | null;
}

export interface Overview { countries: number; by_regime_group: Record<string, number>; researched: string[];
  last_run: null | { run_id: string; country: string; finished_at: string | null; status: string } }

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  countries: (s?: AbortSignal) => get<{ countries: CountryRow[] }>("/api/countries", s).then((r) => r.countries),
  overview: (s?: AbortSignal) => get<Overview>("/api/world/overview", s),
  country: (iso2: string, s?: AbortSignal) => get<CountryDetail>(`/api/countries/${encodeURIComponent(iso2)}`, s),
};

// Retained as a separate view while the 3D explorer is developed.
export const LEGACY_UI = import.meta.env.DEV ? "http://127.0.0.1:8000" : "";

// System B social claims, exported from past.dev by `python -m socialclaims pull-pastdev`.
export interface ClaimStats {
  claims: number; posts: number; by_type: Record<string, number>; by_speaker: Record<string, number>;
  by_platform: Record<string, number>; by_day: { date: string; claims: number }[];
  first: string | null; last: string | null;
}

export interface ClaimsSummary {
  country: string; available: boolean; source?: string; pulled_at?: string; partial_posts?: number;
  politicians: (ClaimStats & { politician_id: string | null; name: string | null; image_url?: string | null })[];
  stats: ClaimStats;
}

export interface SocialClaim {
  claim_id: string; data_point_id: string; politician_id: string | null; politician_name: string | null;
  text: string | null; text_en: string | null; type: string | null; speaker: string | null;
  about_politician: boolean | null; checkable: boolean | null; source_field: string | null;
  evidence_span: string | null; platform: string | null; url: string | null; uploader_handle: string | null;
  uploader_name: string | null; published_at: string | null; format: string | null;
  engagement: { views?: number; likes?: number; comments?: number; shares?: number; captured_at?: string };
  evidence?: ClaimEvidence[]; evidence_counts?: StanceCounts; perception?: Perception | null;
}

// System C evidence: one document's relation to a claim. A stance is never a verdict on the claim.
export type Stance = "supports" | "contradicts" | "context";
export type StanceCounts = Record<Stance, number>;
export interface EvidenceDocument {
  document_datapoint: string; url: string | null; publisher: string | null; title: string | null;
  source_class: string | null; authority_tier: number | null; published_at: string | null; retrieved_at: string | null;
  date_is_retrieval?: boolean | null; summary: string | null;
}
export interface ClaimEvidence {
  link_id: string; stance: Stance; confidence: number | null; quote: string | null; rationale: string | null;
  assessed_at: string | null; mapper_version: string | null; authority_tier: number | null; source_class: string | null;
  document: EvidenceDocument | null;
}

// Storylines (`python -m socialclaims storylines`): statements grouped into one specific claim and its versions.
/** Per week: [all, said by the politician, said by others]. */
export interface WeekCell { statements: [number, number, number]; posts: [number, number, number] }
export interface StorylineRow {
  storyline_id: string; label: string | null; area: string | null; topic_id: string; judged: boolean; statements: number; posts: number;
  versions: number; weeks_active: number; counter_claims: number; needs_review: number; accounts: number;
  first: string | null; last: string | null; cells: Record<string, WeekCell>; evidence_counts: StanceCounts;
}
interface StorylineBase {
  country: string; politician_id: string; available: boolean; pulled_at: string | null; has_evidence: boolean;
  grouped_at: string | null;
}
/** A topic (`python -m socialclaims topics`): the subject several storylines share. `judged` is false while the
 * storylines' subject area stands in for a topic the judge has not assigned yet. */
export interface StorylineTopic {
  topic_id: string; label: string; judged: boolean; storylines: number; repeated: number; statements: number; posts: number;
  weeks_active: number; accounts: number; counter_claims: number; needs_review: number;
  cells: Record<string, WeekCell>; evidence_counts: StanceCounts;
}
export interface StorylineFilters { area?: string; evidence?: string; recurring?: boolean }
export interface StorylineGrid extends StorylineBase {
  weeks: string[]; areas: string[]; topics: StorylineTopic[]; storylines: StorylineRow[];
  totals: { statements: number; posts: number; topics: number; storylines: number; repeated: number; recurring: number; with_post_link: number };
  grouping: { units: number; placed: number; needs_review: number; without_topic: number; judges: string[] };
}
export interface StorylineStatement {
  claim_id: string; text: string | null; text_en: string | null; evidence_span: string | null; type: string | null;
  speaker: string | null; relation_to_politician: string | null; about_politician: boolean | null;
  proposition_id: string | null; canonical_text: string | null; published_at: string | null; week: string | null;
  unit: string;
  placement: { relation: string | null; confidence: number | null; rationale: string | null; judge: string | null;
    status: string | null; rule_blocks: string[] | null; decision_id: string | null } | null;
  evidence: ClaimEvidence[]; evidence_counts: StanceCounts;
}
export interface StorylinePost {
  data_point_id: string; published_at: string | null; platform: string | null; url: string | null;
  uploader_handle: string | null; uploader_name: string | null; platform_id: string | null;
  oriane_content_id: string | null; format: string | null; caption: string | null;
  engagement: SocialClaim["engagement"]; statements: StorylineStatement[];
}
export interface StorylineVersion {
  version_id: string; label: string | null; kind: "claim" | "counter_claim"; text: string | null; text_en: string | null;
  first_seen: string | null; statements: number; posts: number; cells: Record<string, WeekCell>; post_list: StorylinePost[];
}
export interface StorylineDetail {
  storyline_id: string; label: string | null; area: string | null;
  topic: { topic_id: string; label: string; judged: boolean }; judged: boolean; week: string | null;
  statements: number; posts: number; speakers: Record<string, number>; accounts: string[];
  cells: Record<string, WeekCell>; evidence_counts: StanceCounts; documents: number;
  pending_review: { decision_id: string; unit: string; relation: string; confidence: number; rationale: string; storyline_label: string | null }[];
  versions: StorylineVersion[];
}

export interface PoliticianClaims {
  country: string; politician_id: string; available: boolean; pulled_at: string | null;
  total: number; offset: number; limit: number; stats: ClaimStats; claims: SocialClaim[];
}

export interface ClaimFilters { type?: string; speaker?: string; platform?: string; q?: string; limit?: number; offset?: number }

export const claimsApi = {
  summary: (iso2: string, s?: AbortSignal) => get<ClaimsSummary>(`/api/claims/${encodeURIComponent(iso2)}/summary`, s),
  politician: (iso2: string, id: string, f: ClaimFilters = {}, s?: AbortSignal) => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
    return get<PoliticianClaims>(`/api/claims/${encodeURIComponent(iso2)}/politicians/${encodeURIComponent(id)}?${qs}`, s);
  },
  storylines: (iso2: string, id: string, f: StorylineFilters = {}, s?: AbortSignal) => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v).map(([k, v]) => [k, String(v)]));
    return get<StorylineGrid>(`/api/claims/${encodeURIComponent(iso2)}/storylines/${encodeURIComponent(id)}?${qs}`, s);
  },
  storyline: (iso2: string, id: string, storyline: string, week: string | null, s?: AbortSignal) => {
    const qs = new URLSearchParams(week ? { storyline, week } : { storyline });
    return get<StorylineBase & { storyline: StorylineDetail }>(
      `/api/claims/${encodeURIComponent(iso2)}/storylines/${encodeURIComponent(id)}?${qs}`, s);
  },
};

// Read-only view of the claims pipeline (Systems B and C's Postgres): capture -> claims -> evidence -> past.dev.
export interface StageInfo { key: string; label: string; meaning: string }
export interface PipelinePolitician {
  politician_id: string; name: string | null; posts_captured: number; posts_extracted: number;
  claims: number; records: number; stages: Record<string, number>;
}
export interface PipelineSummary {
  country: string; available: boolean; reason?: string; generated_at: string; stage_info: StageInfo[];
  funnel: { key: string; label: string; count: number }[]; stages: Record<string, number>;
  last: Record<"capture" | "extraction" | "search" | "judgement" | "sent" | "confirmed", string | null>;
  spend: { exa_usd: number; exa_requests: number; oriane_credits: number; pastdev_bytes: number };
  reconciliation: {
    neon_sent_claims: number; explorer_available: boolean; explorer_source?: string | null; explorer_pulled_at?: string | null;
    explorer_claims?: number; in_both?: number; sent_not_in_explorer?: number; in_explorer_not_in_log?: number;
    explorer_evidence_records?: number | null;
  };
  hourly: { hour: string; extracted: number; judged: number; sent: number; confirmed: number }[];
  skip_reasons: { reason: string; records: number }[];
  evidence: { stance: string; quote_verified: boolean; links: number; claims: number }[];
  fetch: { status: string; urls: number }[];
  fetch_errors: { status: string; error: string; urls: number }[];
  sent: { kind: string; status: string; records: number; bytes: number; last_sent: string | null; last_confirmed: string | null; errors: number }[];
  capture_runs: { status: string; runs: number; last: string | null }[];
  extraction: { status: string; extractions: number; last: string | null }[];
  politicians: PipelinePolitician[];
}
export interface PipelineRecord {
  politician_id: string; proposition_id: string; canonical_text: string; stage: string; skip_reason: string | null;
  claims: number; posts: number; types: string[]; first_stated_at: string | null; checkworthiness: number | null;
  priority: number | null; reach: number | null; judged: number; usable: number; supports: number; contradicts: number;
  context: number; datapoint_id: string | null; sent_at: string | null; completed_at: string | null;
}
export interface PipelineRecords { available: boolean; reason?: string; total: number; offset: number; limit: number; records: PipelineRecord[] }
export interface PipelineTrace {
  available: boolean; reason?: string; stage_info: StageInfo[];
  proposition: { proposition_id: string; canonical_text: string; first_stated_at: string | null };
  records: { politician_id: string; stage: string; skip_reason: string | null; datapoint_id: string | null; sent_status: string | null; sent_at: string | null; completed_at: string | null; send_error: string | null; in_explorer: boolean | null }[];
  claims: { claim_id: string; politician_id: string; oriane_id: string; quote: string | null; text: string | null; text_en: string | null; type: string | null; relation_to_politician: string | null; modality: string | null; grounding: string | null; grounding_issues: unknown; checkworthiness: number | null; stated_at: string | null; extractor_version: string | null; extracted_at: string | null; platform: string | null; url: string | null; profile_handle: string | null }[];
  queue: { status: string; skip_reason: string | null; max_checkworthiness: number | null; reach: number | null; priority: number | null; claim_count: number | null; queued_at: string | null; mapped_at: string | null; updated_at: string | null } | null;
  searches: { request_id: string; purpose: string | null; query: string | null; domains: string[] | null; result_count: number | null; cost_usd: number | null; latency_ms: number | null; requested_at: string | null; urls: { rank: number; url: string; title: string | null; fetch_status: string | null; attempts: number | null; last_error: string | null }[] }[];
  evidence: { link_id: string; query_row: string | null; stance: string; confidence: number | null; quote_verified: boolean; summary: string | null; quote: string | null; rationale: string | null; model: string | null; assessed_at: string | null; url: string; publisher_domain: string | null; title: string | null; source_class: string | null; authority_tier: number | null; published_at: string | null; usable: boolean; not_sent_because: string | null }[];
  sent: { datapoint_id: string; kind: string; politician_id: string | null; audience: string | null; status: string; content_bytes: number | null; sent_at: string | null; completed_at: string | null; error: string | null }[];
}
export interface PipelineFilters { politician_id?: string; stage?: string; q?: string; limit?: number; offset?: number }

const qs = (f: object) => new URLSearchParams(Object.entries(f).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
export const pipelineApi = {
  summary: (iso2: string, s?: AbortSignal) => get<PipelineSummary>(`/api/pipeline/${encodeURIComponent(iso2)}/summary`, s),
  records: (iso2: string, f: PipelineFilters = {}, s?: AbortSignal) => get<PipelineRecords>(`/api/pipeline/${encodeURIComponent(iso2)}/records?${qs(f)}`, s),
  trace: (iso2: string, propositionId: string, politicianId: string, s?: AbortSignal) =>
    get<PipelineTrace>(`/api/pipeline/${encodeURIComponent(iso2)}/trace/${encodeURIComponent(propositionId)}?${qs({ politician_id: politicianId })}`, s),
};

// Social metrics per politician: engagement from captured posts, and the presentation and feedback indices.
export type Tone3 = "positive" | "neutral" | "negative";
export type VideoReading = "official_communication" | "favourable_third_party" | "critical_content";
export interface EngagementSummary {
  posts: number; videos: number; views: number; likes: number; comments: number; shares: number; posts_with_views: number;
  own_posts: number; posts_with_claims: number; engagement_rate: number | null; platforms: Record<string, number>;
  first: string | null; last: string | null;
}
export interface PresentationSummary {
  positive: number; neutral: number; negative: number; total: number; index: number; label: Tone3;
  official: number; favourable_third_party: number; critical: number;
}
export interface FeedbackCounts {
  for: number; against: number; neutral: number; bot_for: number; bot_against: number; bot_neutral: number;
  analyzed: number; humans: number; bots: number; index: number | null; label: Tone3 | null; videos?: number;
}
export interface Perception {
  presentation: Tone3 | null; presentation_reason: string | null; feedback: FeedbackCounts | null; reading: VideoReading | null;
}
export interface MetricsPolitician {
  key: string; politician_id: string | null; name: string | null; image_url: string | null; in_graph: boolean; role?: string | null;
  engagement: EngagementSummary | null; presentation: PresentationSummary | null; feedback: FeedbackCounts | null;
}
export interface MetricsSources {
  engagement?: { source: string; exported_at: string; posts: number; politicians: number; first: string | null; last: string | null } | null;
  perception?: { source: string; imported_at: string; generated_at: string | null; range: { after: string; before: string } | null;
    platform: string; pairs: number; videos: number; videos_with_feedback: number; unmatched: string[] } | null;
  labels?: { source: string; exported_at: string; pairs: number; politicians: number; models: string[]; labelled_at: string | null } | null;
}
export interface MetricsMethod { presentation: string; feedback: string; threshold: number; min_human_comments: number; limits: string }
export interface MetricsSummary {
  country: string; available: boolean; politicians: MetricsPolitician[]; sources: MetricsSources; method: MetricsMethod;
  totals?: { engagement: EngagementSummary | null; presentation: PresentationSummary | null; feedback: FeedbackCounts | null };
}
export interface MetricsVideo extends Perception {
  platform: string | null; platform_id: string | null; format: string | null; url: string | null; uploader_handle: string | null;
  caption: string | null; published_at: string | null; views: number | null; likes: number | null; comments: number | null;
  shares: number | null; claims: number; own_account: boolean; captured: boolean; labelled: boolean;
}
export interface PoliticianMetrics extends Partial<MetricsPolitician> {
  country: string; key: string; available: boolean; sources: MetricsSources; method: MetricsMethod;
  total: number; offset?: number; limit?: number; videos: MetricsVideo[];
}
export interface MetricsFilters { sort?: string; labelled?: boolean; presentation?: string; platform?: string; limit?: number; offset?: number }
export const metricsApi = {
  summary: (iso2: string, s?: AbortSignal) => get<MetricsSummary>(`/api/metrics/${encodeURIComponent(iso2)}/summary`, s),
  politician: (iso2: string, key: string, f: MetricsFilters = {}, s?: AbortSignal) =>
    get<PoliticianMetrics>(`/api/metrics/${encodeURIComponent(iso2)}/politicians/${encodeURIComponent(key)}?${qs(f)}`, s),
};
