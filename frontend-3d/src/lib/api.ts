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
