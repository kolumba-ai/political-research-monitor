import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Feather, Globe2, LoaderCircle, Microscope } from "lucide-react";
import { api, LEGACY_UI, type CountryRow, type Overview } from "./lib/api";
import { hasWebGL, loadCountries, type CountryFeature } from "./lib/geo";
import CountryCard from "./components/CountryCard";
import FlatMap from "./components/FlatMap";
import Investigate, { type Section } from "./components/Investigate";
import SearchBox, { type EntitySearchHit } from "./components/SearchBox";
import { Badge, Button, Card, RegimeDot } from "./components/ui";
import { cn } from "./lib/presentation";

const GlobeView = lazy(() => import("./components/GlobeView"));
const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
const SECTIONS: Section[] = ["structure", "overview", "politicians", "claims", "metrics", "pipeline", "politician"];

interface View { country: string | null; investigating: boolean; section: Section; node?: string; tab?: string }

function readView(): View {
  const params = new URLSearchParams(location.search);
  const code = params.get("country")?.toUpperCase();
  const country = code && /^[A-Z]{2}$/.test(code) ? code : null;
  const raw = params.get("section") as Section | null;
  const section = raw && SECTIONS.includes(raw) ? raw : "structure";
  const node = params.get("node") ?? undefined;
  const investigating = !!country && (params.get("explore") === "1" || params.get("view") === "investigate");
  return { country, investigating, section: section === "politician" && !node ? "politicians" : section, node, tab: params.get("tab") ?? undefined };
}

function Loading({ text }: { text: string }) {
  return <div className="flex h-full min-h-40 items-center justify-center gap-2 p-6 text-sm text-muted" role="status">
    <LoaderCircle className="size-4 animate-spin" />{text}
  </div>;
}

export default function App() {
  const [initial] = useState(readView);
  const [rows, setRows] = useState<CountryRow[]>([]);
  const [features, setFeatures] = useState<CountryFeature[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [view, setView] = useState<View>(initial);
  const [graphTarget, setGraphTarget] = useState(initial.section === "structure" ? initial.node : undefined);
  const [lowMotion, setLowMotion] = useState(prefersReducedMotion);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [geoError, setGeoError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [webgl] = useState(hasWebGL);

  useEffect(() => {
    const ctl = new AbortController();
    api.countries(ctl.signal).then(setRows)
      .catch((e) => { if (!ctl.signal.aborted) setLoadError(String(e.message || e)); })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    api.overview(ctl.signal).then(setOverview).catch(() => {});
    return () => ctl.abort();
  }, [retry]);

  useEffect(() => {
    let active = true;
    loadCountries().then((f) => { if (active) setFeatures(f); }).catch(() => { if (active) setGeoError(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const handleBack = () => { const v = readView(); setView(v); setGraphTarget(v.section === "structure" ? v.node : undefined); };
    window.addEventListener("popstate", handleBack);
    return () => window.removeEventListener("popstate", handleBack);
  }, []);

  // Shareable links: country, open screen, section and the inspected entity.
  useEffect(() => {
    const u = new URL(location.href);
    u.searchParams.delete("explore");
    const set = (k: string, v: string | undefined | null | false) => { if (v) u.searchParams.set(k, v); else u.searchParams.delete(k); };
    set("country", view.country);
    set("view", view.country && view.investigating && "investigate");
    set("section", view.country && view.investigating && view.section);
    set("node", view.country && view.investigating && view.node);
    set("tab", view.country && view.investigating && view.section === "politician" && view.tab);
    history.replaceState(null, "", u);
  }, [view]);

  const navigate = (next: Partial<View>, push = true) => {
    setView((v) => {
      const merged = { ...v, ...next };
      if (push) {
        const u = new URL(location.href);
        u.search = "";
        if (merged.country) u.searchParams.set("country", merged.country);
        history.pushState(null, "", u);
      }
      return merged;
    });
  };

  const chooseCountry = (iso2: string) => {
    if (iso2 !== view.country) { setGraphTarget(undefined); navigate({ country: iso2, investigating: false, section: "structure", node: undefined }); }
  };
  const openPerson = (id: string, country = view.country, tab?: string) => navigate({ country, investigating: true, section: "politician", node: id, tab });
  const chooseEntity = (hit: EntitySearchHit) => {
    const country = hit.path.find((id) => /^country:[A-Z]{2}$/.test(id))?.split(":")[1];
    if (!country) return;
    if (hit.label === "Person") return openPerson(hit.id, country);
    setGraphTarget(hit.id);
    navigate({ country, investigating: true, section: "structure", node: hit.id });
  };

  const byIso = useMemo(() => new Map(rows.map((r) => [r.iso2, r])), [rows]);
  const selected = view.country;
  const row = selected ? byIso.get(selected) : undefined;
  const mapFallback = !webgl || geoError;
  const investigating = !!selected && view.investigating;

  return (
    <div className="flex h-full flex-col bg-white">
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-white/95 px-4 py-2.5 shadow-sm backdrop-blur">
        <button className="flex items-center gap-2 text-fg" aria-label="Return to Earth" onClick={() => { setGraphTarget(undefined); navigate({ country: null, investigating: false, node: undefined }); }}>
          <span className="flex size-8 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow"><Globe2 className="size-4.5" /></span>
          <h1 className="text-[16px] font-semibold tracking-tight">Political World Explorer</h1>
        </button>
        <nav className="flex items-center gap-1" aria-label="Main">
          <button onClick={() => navigate({ investigating: false }, false)} aria-current={!investigating ? "page" : undefined}
            className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm", !investigating ? "bg-accent/10 font-medium text-accent" : "text-muted hover:text-fg")}><Globe2 className="size-4" />Explore</button>
          <button onClick={() => selected && navigate({ investigating: true }, false)} disabled={!selected} title={selected ? undefined : "Select a country first"} aria-current={investigating ? "page" : undefined}
            className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm disabled:opacity-40", investigating ? "bg-accent/10 font-medium text-accent" : "text-muted hover:text-fg")}><Microscope className="size-4" />Investigate</button>
        </nav>
        <SearchBox countries={rows} onPick={(iso2) => { chooseCountry(iso2); }} onPickEntity={chooseEntity} />
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs text-muted">
          {overview && <>
            <span>{overview.countries} countries</span>
            {(["Democratic", "Partly democratic", "Authoritarian", "Unclassified"] as const).map((g) => (
              <Badge key={g} title={g}><RegimeDot group={g} />{overview.by_regime_group[g] ?? 0}</Badge>
            ))}
          </>}
          <Button variant={lowMotion ? "default" : "ghost"} onClick={() => setLowMotion((v) => !v)} aria-pressed={lowMotion} title="Reduce animation"><Feather className="size-4" /></Button>
        </div>
      </header>

      {loadError && <div className="m-3 flex shrink-0 flex-wrap items-center gap-3 rounded-lg border border-bad/40 bg-bad/10 p-3 text-sm text-bad" role="alert">
        <span>Country data could not be loaded. {loadError}</span><Button onClick={() => { setLoading(true); setLoadError(null); setRetry((n) => n + 1); }}>Retry</Button>
      </div>}

      {investigating && selected ? (
        <Investigate iso2={selected} row={row} section={view.section} lowMotion={lowMotion}
          onSection={(section) => navigate({ section, node: section === "structure" ? view.node : undefined }, false)}
          graphTarget={graphTarget} onGraphNode={(id) => { setView((v) => ({ ...v, node: id })); if (id !== graphTarget) setGraphTarget(id); }}
          personId={view.section === "politician" ? view.node : undefined} personTab={view.tab}
          onOpenPerson={(id, tab) => openPerson(id, view.country, tab)} />
      ) : (
        <main className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_480px] lg:overflow-hidden">
          <section className="relative min-h-[55svh] bg-gradient-to-b from-white to-ink-850 lg:min-h-0" aria-label="Globe">
            {loading && rows.length === 0 ? <Loading text="Loading countries…" /> : !webgl && !geoError && features.length > 0 ? (
              <FlatMap features={features} countries={byIso} selected={selected} onSelect={chooseCountry}
                reason="3D view needs WebGL 2, which this browser isn't providing (enable graphics acceleration to see the globe)." />
            ) : mapFallback ? (
              <div className="h-full max-h-[55svh] overflow-y-auto p-4 lg:max-h-none">
                <p className="mb-3 text-sm text-muted">{geoError ? "Globe boundaries could not be loaded." : "3D view is unavailable on this device."} Search above or choose a country:</p>
                <ul className="grid grid-cols-2 gap-2 text-sm">{rows.map((r) => <li key={r.iso2}>
                  <button className="flex items-start gap-2 text-left hover:text-accent" onClick={() => chooseCountry(r.iso2)}><RegimeDot className="mt-1" group={r.regime_group} />{r.name}</button>
                </li>)}</ul>
              </div>
            ) : features.length > 0 && rows.length > 0 ? (
              <Suspense fallback={<Loading text="Preparing the globe…" />}>
                <GlobeView features={features} countries={byIso} selected={selected} onSelect={chooseCountry} lowMotion={lowMotion} />
              </Suspense>
            ) : <Loading text="Loading country boundaries…" />}
            <div className="pointer-events-none absolute top-5 left-5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted">World explorer</p>
              <h2 className="mt-1 text-xl font-semibold">{selected ? row?.name ?? selected : "Start with a country."}</h2>
              <p className="mt-1 max-w-xs text-xs text-muted">Discover and select a country on the globe, then investigate its institutions, politicians and their claims.</p>
            </div>
          </section>
          <section className="flex items-start justify-center overflow-y-auto border-t border-line bg-ink-850 p-5 lg:items-center lg:border-t-0 lg:border-l" aria-label="Country information">
            {selected ? <CountryCard key={selected} iso2={selected} row={row}
              onExplore={() => navigate({ investigating: true, section: "structure" }, false)}
              onOverview={() => navigate({ investigating: true, section: "overview" }, false)} /> : (
              <Card className="w-full max-w-md space-y-3 p-6 text-sm text-muted shadow-xl shadow-slate-900/10">
                <Globe2 className="size-8 text-accent" />
                <h2 className="text-lg font-semibold text-fg">Follow the institutions</h2>
                <p>Choose a country to see its government, democracy assessments and verified sources, then follow it down to individual politicians and what they say on social media.</p>
                <div className="flex flex-wrap gap-2">{rows.filter((r) => r.has_structure).map((r) => <Button key={r.iso2} onClick={() => chooseCountry(r.iso2)}>{r.name}</Button>)}</div>
                <a className="block text-xs hover:underline" href={`${LEGACY_UI}/`}>Open classic graph</a>
              </Card>
            )}
          </section>
        </main>
      )}
    </div>
  );
}
