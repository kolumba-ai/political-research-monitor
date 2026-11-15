import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Landmark, LoaderCircle, Search, UserRound } from "lucide-react";
import type { CountryRow } from "../lib/api";
import { flagOf } from "../lib/geo";
import { RegimeDot } from "./ui";

const norm = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
const ENTITY_LABELS = new Set(["Person", "GovernmentBody", "PoliticalOffice", "GovernmentLevel"]);
const ENTITY_NAMES: Record<string, string> = {
  Person: "Person", GovernmentBody: "Institution", PoliticalOffice: "Office", GovernmentLevel: "Government level",
};

export interface EntitySearchHit {
  id: string;
  name?: string | null;
  label: string;
  path: string[];
}

type Suggestion = { kind: "country"; country: CountryRow } | { kind: "entity"; entity: EntitySearchHit };

function countryOf(hit: EntitySearchHit): string | undefined {
  return hit.path.find((part) => /^country:[A-Z]{2}$/.test(part))?.slice("country:".length);
}

export default function SearchBox({ countries, onPick, onPickEntity }: {
  countries: CountryRow[];
  onPick: (iso2: string) => void;
  onPickEntity?: (hit: EntitySearchHit) => void;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [response, setResponse] = useState<{ query: string; hits: EntitySearchHit[]; error: string | null }>({
    query: "", hits: [], error: null,
  });
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const term = q.trim();
  const query = term.length >= 2 ? term : "";
  const loading = !!query && response.query !== query;
  const error = query && response.query === query ? response.error : null;

  useEffect(() => {
    if (!query) return;
    const ctl = new AbortController();
    const timer = window.setTimeout(() => {
      fetch(`/api/search?q=${encodeURIComponent(query)}&limit=25`, {
        signal: ctl.signal, headers: { Accept: "application/json" },
      })
        .then(async (res) => {
          if (!res.ok) throw new Error("Search is temporarily unavailable.");
          const data = await res.json() as { results?: EntitySearchHit[] };
          if (!Array.isArray(data.results)) throw new Error("Search returned an invalid response.");
          return data.results.filter((hit) => typeof hit.id === "string" && ENTITY_LABELS.has(hit.label)
            && Array.isArray(hit.path) && hit.path.every((part) => typeof part === "string") && countryOf(hit));
        })
        .then((hits) => {
          if (!ctl.signal.aborted) setResponse({ query, hits, error: null });
        })
        .catch((e: unknown) => {
          if (!ctl.signal.aborted) setResponse({ query, hits: [], error: e instanceof Error ? e.message : "Search is temporarily unavailable." });
        });
    }, 250);
    return () => { window.clearTimeout(timer); ctl.abort(); };
  }, [query]);

  const hits = useMemo<Suggestion[]>(() => {
    const n = norm(term);
    if (!n) return [];
    const local: Suggestion[] = countries
      .filter((c) => norm(c.name).includes(n) || c.iso2.toLowerCase() === n || c.iso3?.toLowerCase() === n)
      .sort((a, b) => Number(!norm(a.name).startsWith(n)) - Number(!norm(b.name).startsWith(n)))
      .slice(0, 8)
      .map((country) => ({ kind: "country", country }));
    const unique = new Set<string>();
    const entities: Suggestion[] = response.query === query && query ? response.hits
      .filter((hit) => {
        if (unique.has(hit.id)) return false;
        unique.add(hit.id); return true;
      })
      .slice(0, 8)
      .map((entity) => ({ kind: "entity", entity })) : [];
    return [...local, ...entities];
  }, [term, countries, query, response]);
  const activeIndex = Math.min(active, hits.length - 1);
  const expanded = open && term.length > 0;

  const pick = (hit: Suggestion) => {
    if (hit.kind === "country") onPick(hit.country.iso2);
    else {
      const country = countryOf(hit.entity);
      if (!country) return;
      if (onPickEntity) onPickEntity(hit.entity); else onPick(country);
    }
    setQ(""); setOpen(false); input.current?.blur();
  };

  return (
    <div className="relative w-full max-w-md">
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
      <input
        ref={input}
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { setOpen(true); setActive((a) => hits.length ? Math.min(a + 1, hits.length - 1) : 0); e.preventDefault(); }
          if (e.key === "ArrowUp") { setOpen(true); setActive((a) => Math.max(a - 1, 0)); e.preventDefault(); }
          if (e.key === "Enter" && expanded && hits[activeIndex]) { e.preventDefault(); pick(hits[activeIndex]); }
          if (e.key === "Escape") { setOpen(false); e.preventDefault(); }
        }}
        placeholder="Search countries, people, institutions…"
        aria-label="Search countries, people and institutions"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={expanded ? listId : undefined}
        aria-activedescendant={expanded && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        className="w-full rounded-lg border border-line bg-ink-850 py-2 pr-3 pl-9 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none"
      />
      {expanded && (
        <div className="absolute z-30 mt-1 max-h-[65svh] w-full overflow-y-auto rounded-lg border border-line bg-ink-900 shadow-2xl">
          <ul id={listId} role="listbox" aria-label="Search results" aria-busy={loading}>
            {hits.map((hit, i) => {
              const country = hit.kind === "country" ? hit.country : undefined;
              const entity = hit.kind === "entity" ? hit.entity : undefined;
              const entityCountry = entity ? countryOf(entity) : undefined;
              const countryName = countries.find((c) => c.iso2 === entityCountry)?.name ?? entityCountry;
              return (
                <li key={country?.iso2 ?? entity!.id} id={`${listId}-${i}`} role="option" aria-selected={i === activeIndex}
                  onMouseDown={(e) => { e.preventDefault(); pick(hit); }} onMouseEnter={() => setActive(i)}
                  className={`flex cursor-pointer items-start gap-2 px-3 py-2.5 text-sm ${i === activeIndex ? "bg-ink-700" : "hover:bg-ink-800"}`}>
                  {country ? <span aria-hidden>{flagOf(country.iso2)}</span>
                    : entity?.label === "Person" ? <UserRound aria-hidden className="mt-0.5 size-4 shrink-0 text-violet" />
                      : <Landmark aria-hidden className="mt-0.5 size-4 shrink-0 text-accent" />}
                  <span className="min-w-0 flex-1">
                    <span className="block break-words">{country?.name ?? entity?.name ?? entity?.id}</span>
                    {entity && <span className="mt-0.5 block text-xs text-muted">{ENTITY_NAMES[entity.label]} · {countryName}</span>}
                  </span>
                  {country && <><RegimeDot className="mt-1" group={country.regime_group} />
                    <span className="max-w-28 text-right text-xs text-muted">{country.regime_group}</span></>}
                </li>
              );
            })}
          </ul>
          <div className="border-t border-line px-3 py-2 text-xs text-muted" role="status" aria-live="polite">
            {loading ? <span className="flex items-center gap-2"><LoaderCircle aria-hidden className="size-3 animate-spin" />Searching people and institutions…</span>
              : error ? <span>{error} Country search still works; change the search to retry.</span>
                : hits.length === 0 ? query ? "No matching countries, people or institutions." : "Type two letters to search people and institutions."
                  : query ? `${hits.length} results · use ↑ ↓ and Enter to select` : "Type two letters to also search people and institutions."}
          </div>
        </div>
      )}
    </div>
  );
}
