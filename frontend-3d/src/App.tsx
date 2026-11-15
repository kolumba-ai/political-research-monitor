import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";
import { ArrowLeft, Globe2, Network, Feather, LoaderCircle } from "lucide-react";
import { api, LEGACY_UI, type CountryRow, type Overview } from "./lib/api";
import { flagOf, hasWebGL, loadCountries, type CountryFeature } from "./lib/geo";
import CountryPanel from "./components/CountryPanel";
import FlatMap from "./components/FlatMap";
import SearchBox, { type EntitySearchHit } from "./components/SearchBox";
import { Badge, Button, Card, RegimeDot } from "./components/ui";
import { cn } from "./lib/presentation";

const GlobeView = lazy(() => import("./components/GlobeView"));
const GovernmentGraph = lazy(() => import("./components/GovernmentGraph"));
const prefersReducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function readView() {
  const params = new URLSearchParams(location.search);
  const code = params.get("country")?.toUpperCase();
  const country = code && /^[A-Z]{2}$/.test(code) ? code : null;
  return { country, exploring: !!country && params.get("explore") === "1", node: params.get("node") ?? undefined };
}

function Loading({ text }: { text: string }) {
  return <div className="flex h-full min-h-40 items-center justify-center gap-2 p-6 text-sm text-muted" role="status">
    <LoaderCircle className="size-4 animate-spin" />{text}
  </div>;
}

export default function App() {
  const [initialView] = useState(readView);
  const [rows, setRows] = useState<CountryRow[]>([]);
  const [features, setFeatures] = useState<CountryFeature[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selected, setSelected] = useState<string | null>(initialView.country);
  const [exploring, setExploring] = useState(initialView.exploring);
  const [graphTarget, setGraphTarget] = useState(initialView.node);
  const [selectedNode, setSelectedNode] = useState(initialView.node);
  const [lowMotion, setLowMotion] = useState(prefersReducedMotion);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [geoError, setGeoError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [webgl] = useState(hasWebGL);

  useEffect(() => {
    const ctl = new AbortController();
    api.countries(ctl.signal)
      .then(setRows)
      .catch((e) => { if (!ctl.signal.aborted) setLoadError(String(e.message || e)); })
      .finally(() => { if (!ctl.signal.aborted) setLoading(false); });
    api.overview(ctl.signal).then(setOverview).catch(() => {});
    return () => ctl.abort();
  }, [retry]);

  useEffect(() => {
    let active = true;
    loadCountries().then((f) => { if (active) setFeatures(f); })
      .catch(() => { if (active) setGeoError(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const handleBack = () => {
      const view = readView();
      setSelected(view.country); setExploring(view.exploring);
      setGraphTarget(view.node); setSelectedNode(view.node);
    };
    window.addEventListener("popstate", handleBack);
    return () => window.removeEventListener("popstate", handleBack);
  }, []);

  // Preserve the country, open graph and inspected entity in shareable links.
  useEffect(() => {
    const u = new URL(location.href);
    if (selected) u.searchParams.set("country", selected); else u.searchParams.delete("country");
    if (selected && exploring) u.searchParams.set("explore", "1"); else u.searchParams.delete("explore");
    if (selected && exploring && selectedNode) u.searchParams.set("node", selectedNode); else u.searchParams.delete("node");
    history.replaceState(null, "", u);
  }, [selected, exploring, selectedNode]);

  const chooseCountry = (iso2: string) => {
    if (iso2 !== selected) {
      const u = new URL(location.href);
      u.searchParams.set("country", iso2);
      u.searchParams.delete("explore"); u.searchParams.delete("node");
      history.pushState(null, "", u);
      setExploring(false); setGraphTarget(undefined); setSelectedNode(undefined);
    }
    setSelected(iso2);
  };

  const chooseEntity = (hit: EntitySearchHit) => {
    const country = hit.path.find((id) => /^country:[A-Z]{2}$/.test(id))?.split(":")[1];
    if (!country) return;
    const u = new URL(location.href);
    u.searchParams.set("country", country); u.searchParams.set("explore", "1"); u.searchParams.set("node", hit.id);
    history.pushState(null, "", u);
    setSelected(country); setExploring(true); setGraphTarget(hit.id); setSelectedNode(hit.id);
  };

  const byIso = useMemo(() => new Map(rows.map((r) => [r.iso2, r])), [rows]);
  const row = selected ? byIso.get(selected) : undefined;
  const feature = features.find((f) => f.__iso2 === selected);
  const mapFallback = !webgl || geoError;

  return (
    <div className="flex h-full flex-col bg-white">
      <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-line px-4 py-2.5">
        <button className="flex items-center gap-2 text-fg" aria-label="Return to Earth" onClick={() => {
          const u = new URL(location.href); u.search = ""; history.pushState(null, "", u);
          setSelected(null); setExploring(false); setGraphTarget(undefined); setSelectedNode(undefined);
        }}>
          <Globe2 className="size-5 text-accent" />
          <h1 className="text-[15px] font-semibold tracking-tight">Political World Explorer</h1>
        </button>
        <SearchBox countries={rows} onPick={chooseCountry} onPickEntity={chooseEntity} />
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs text-muted">
          {overview && <>
            <span>{overview.countries} countries</span>
            {(["Democratic", "Partly democratic", "Authoritarian", "Unclassified"] as const).map((g) => (
              <Badge key={g} title={g}><RegimeDot group={g} />{overview.by_regime_group[g] ?? 0}</Badge>
            ))}
            <span>· {overview.researched.length} with government structure</span>
          </>}
          <Button variant={lowMotion ? "default" : "ghost"} onClick={() => setLowMotion((v) => !v)} aria-pressed={lowMotion}
            title="Reduce animation"><Feather className="size-4" /> Low motion</Button>
        </div>
      </header>

      {loadError && <div className="m-3 flex shrink-0 flex-wrap items-center gap-3 rounded-lg border border-bad/40 bg-bad/10 p-3 text-sm text-bad" role="alert">
        <span>Country data could not be loaded. {loadError}</span><Button onClick={() => {
          setLoading(true); setLoadError(null); setRetry((n) => n + 1);
        }}>Retry</Button>
      </div>}

      <main className={cn("grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:overflow-hidden lg:grid-cols-[40fr_25fr_35fr]",
        exploring && "lg:grid-cols-[20fr_27fr_53fr]")}>
        <section className={cn("relative min-h-[50svh] border-b border-line lg:min-h-0 lg:border-r lg:border-b-0", exploring && "hidden lg:block")} aria-label="Globe">
          {loading && rows.length === 0 ? <Loading text="Loading countries…" /> : !webgl && !geoError && features.length > 0 ? (
            <FlatMap features={features} countries={byIso} selected={selected} onSelect={chooseCountry}
              reason="3D view needs WebGL 2, which this browser isn't providing (enable graphics acceleration to see the globe)." />
          ) : mapFallback ? (
            <div className="h-full max-h-[55svh] overflow-y-auto p-4 lg:max-h-none">
              <p className="mb-3 text-sm text-muted">{geoError ? "Globe boundaries could not be loaded." : "3D view is unavailable on this device."} Search above or choose a country:</p>
              <ul className="grid grid-cols-2 gap-2 text-sm">
                {rows.map((r) => <li key={r.iso2}>
                  <button className="flex items-start gap-2 text-left hover:text-accent" onClick={() => chooseCountry(r.iso2)}>
                    <RegimeDot className="mt-1" group={r.regime_group} />{r.name}
                  </button>
                </li>)}
              </ul>
            </div>
          ) : features.length > 0 && rows.length > 0 ? (
            <Suspense fallback={<Loading text="Preparing the globe…" />}>
              <GlobeView features={features} countries={byIso} selected={selected} onSelect={chooseCountry} lowMotion={lowMotion} />
            </Suspense>
          ) : <Loading text="Loading country boundaries…" />}
          {!selected && rows.length > 0 && !mapFallback && <div className="pointer-events-none absolute top-5 right-4 left-4">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted">A world of political systems</p>
            <h2 className="mt-1 text-xl font-semibold">Start with a country.</h2>
            <p className="mt-1 max-w-xs text-xs text-muted">Select its border or search above to explore institutions and their sources.</p>
          </div>}
        </section>

        <section className={cn("min-h-[35svh] border-b border-line lg:min-h-0 lg:border-r lg:border-b-0", exploring && "hidden lg:block")} aria-label="Country information">
          <CountryPanel iso2={selected} row={row} feature={feature} exploring={exploring} onExplore={() => setExploring(true)}
            onOpenPerson={(id) => { setExploring(true); setGraphTarget(id); setSelectedNode(id); }} />
        </section>

        <section className={cn("flex min-h-[60svh] min-w-0 flex-col lg:min-h-0", exploring && "min-h-0")} aria-label="Government structure">
          {selected && exploring ? (
            <motion.div className="flex min-h-0 min-w-0 flex-1 flex-col" initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              transition={{ duration: lowMotion ? 0 : 0.3 }}>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2 text-xs text-muted">
                <span className="flex items-center gap-1.5"><Network className="size-4 text-accent" />
                  {row?.name ?? selected} · Government structure</span>
                <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setExploring(false)}><ArrowLeft className="size-3.5" /> Back to globe</Button>
              </div>
              <Suspense fallback={<Loading text="Preparing the government graph…" />}>
                <GovernmentGraph key={selected} iso2={selected} countryName={row?.name ?? selected} lowMotion={lowMotion}
                  initialNode={graphTarget} onNodeChange={(id) => {
                    setSelectedNode(id); setGraphTarget(undefined);
                  }} />
              </Suspense>
            </motion.div>
          ) : (
            <div className="flex h-full items-center justify-center p-6">
              <Card className="max-w-sm space-y-3 p-6 text-center text-sm text-muted">
                <Network className="mx-auto size-8 text-accent" />
                <h2 className="font-medium text-fg">{selected ? `${flagOf(selected)} ${row?.name ?? selected}` : "Follow the institutions"}</h2>
                <p>{selected
                  ? row?.has_structure
                    ? "Explore government levels, institutions, offices and the people who hold them. Every connection comes from the knowledge graph."
                    : "Government structure has not been researched yet. Available country assessments appear in the country panel."
                  : "Choose a country to follow its government structure, from national institutions to local authorities."}</p>
                {row?.has_structure && <Button variant="primary" onClick={() => setExploring(true)}><Network className="size-4" /> Explore government structure</Button>}
                <p className="text-xs">{selected ? "Inspect a node to read its evidence and verification date." :
                  `${rows.filter((r) => r.has_structure).map((r) => r.name).join(" and ") || "No countries"} have government data available.`}</p>
                <a className="block text-xs hover:underline" href={`${LEGACY_UI}/`}>Open classic graph</a>
              </Card>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
