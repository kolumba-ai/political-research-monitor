import { useEffect, useMemo, useRef, useState } from "react";
import Globe, { type GlobeMethods } from "react-globe.gl";
import { RotateCcw, Pause, Play } from "lucide-react";
import type { CountryRow, RegimeGroup } from "../lib/api";
import type { CountryFeature } from "../lib/geo";
import { Button } from "./ui";
import { REGIME_COLORS } from "../lib/presentation";

interface Props {
  features: CountryFeature[];
  countries: Map<string, CountryRow>;
  selected: string | null;
  onSelect: (iso2: string) => void;
  lowMotion: boolean;
}

const IDLE_RESUME_MS = 20000;

function rgba(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export default function GlobeView({ features, countries, selected, onSelect, lowMotion }: Props) {
  const globeRef = useRef<GlobeMethods | undefined>(undefined);
  const boxRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 600, h: 600 });
  const [hover, setHover] = useState<CountryFeature | null>(null);
  const [rotating, setRotating] = useState(!lowMotion);
  const [rotationEnabled, setRotationEnabled] = useState(true);
  const idleTimer = useRef<number | undefined>(undefined);

  // Fit the WebGL canvas to its panel.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    // Measure the element itself (not a cached entry) so a resize missed while the layout settles
    // after lazy loading can't leave the canvas shorter than its panel.
    const measure = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (w > 0 && h > 0) setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    const raf = requestAnimationFrame(measure);
    const late = window.setTimeout(measure, 400);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); cancelAnimationFrame(raf); window.clearTimeout(late); window.removeEventListener("resize", measure); };
  }, []);

  // Auto-rotation when idle (off in low-motion mode).
  useEffect(() => {
    const controls = globeRef.current?.controls();
    if (!controls) return;
    controls.autoRotate = rotationEnabled && rotating && !lowMotion;
    controls.autoRotateSpeed = 0.35;
  }, [rotating, rotationEnabled, lowMotion, size]);

  useEffect(() => () => window.clearTimeout(idleTimer.current), []);

  const pauseForInteraction = () => {
    setRotating(false);
    window.clearTimeout(idleTimer.current);
    if (!lowMotion) idleTimer.current = window.setTimeout(() => setRotating(true), IDLE_RESUME_MS);
  };

  // Fly to the selected country.
  useEffect(() => {
    if (!selected) return;
    const f = features.find((x) => x.__iso2 === selected);
    if (f?.__centroid) {
      pauseForInteraction();
      globeRef.current?.pointOfView({ ...f.__centroid, altitude: 1.55 }, lowMotion ? 0 : 1400);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, features]);

  const groupOf = (f: CountryFeature): RegimeGroup =>
    (f.__iso2 && countries.get(f.__iso2)?.regime_group) || "Unclassified";

  const label = useMemo(() => (obj: object) => {
    const f = obj as CountryFeature;
    const row = f.__iso2 ? countries.get(f.__iso2) : undefined;
    if (!row) {
      return `<div style="font:12px Inter,sans-serif;background:#ffffffee;border:1px solid #dfe4ee;border-radius:8px;padding:6px 8px;color:#111827;box-shadow:0 4px 14px rgba(15,23,42,.12)">
        <b>${escapeHtml(f.properties.NAME)}</b><br/><span style="color:#5b6475">Not in the ISO 3166 registry</span></div>`;
    }
    const color = REGIME_COLORS[row.regime_group];
    return `<div style="font:12px Inter,sans-serif;background:#ffffffee;border:1px solid #dfe4ee;border-radius:8px;padding:6px 8px;color:#111827;min-width:150px;box-shadow:0 4px 14px rgba(15,23,42,.12)">
      <b>${escapeHtml(row.name)}</b><br/>
      <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:5px"></span>${escapeHtml(row.regime_group)}${row.regime_class && row.regime_class !== row.regime_group ? ` · ${escapeHtml(row.regime_class)}` : ""}<br/>
      <span style="color:#5b6475">${row.has_structure ? "Government structure available" : "Government structure not researched"}</span></div>`;
  }, [countries]);

  return (
    <div ref={boxRef} className="absolute inset-0 overflow-hidden" onPointerDown={pauseForInteraction}>
      <Globe
        ref={globeRef}
        width={size.w}
        height={size.h}
        backgroundColor="rgba(0,0,0,0)"
        globeImageUrl={`${import.meta.env.BASE_URL}img/earth-blue-marble.jpg`}
        bumpImageUrl={`${import.meta.env.BASE_URL}img/earth-topology.png`}
        showAtmosphere
        atmosphereColor="#9db4ff"
        atmosphereAltitude={0.16}
        polygonsData={features}
        polygonAltitude={(o) => ((o as CountryFeature).__iso2 === selected ? 0.035 : o === hover ? 0.018 : 0.006)}
        polygonCapColor={(o) => {
          const f = o as CountryFeature;
          if (f.__iso2 === selected) return "rgba(53,87,230,0.85)";
          return rgba(REGIME_COLORS[groupOf(f)], o === hover ? 0.75 : 0.42);
        }}
        polygonSideColor={() => "rgba(255,255,255,0.35)"}
        polygonStrokeColor={(o) => ((o as CountryFeature).__iso2 === selected ? "#1e3a8a" : "rgba(255,255,255,0.55)")}
        polygonLabel={label}
        onPolygonHover={(o) => setHover((o as CountryFeature) || null)}
        onPolygonClick={(o) => {
          const f = o as CountryFeature;
          if (f.__iso2 && countries.has(f.__iso2)) onSelect(f.__iso2);
        }}
        polygonsTransitionDuration={lowMotion ? 0 : 250}
      />
      <div className="absolute bottom-3 left-3 flex gap-1.5">
        <Button variant="default" title="Reset view" aria-label="Reset view"
          onClick={() => globeRef.current?.pointOfView({ lat: 15, lng: -30, altitude: 2.4 }, lowMotion ? 0 : 1000)}>
          <RotateCcw className="size-4" />
        </Button>
        <Button variant="default" title={rotationEnabled ? "Pause rotation" : "Resume rotation"} aria-label="Toggle rotation"
          aria-pressed={rotationEnabled && !lowMotion} disabled={lowMotion} onClick={() => {
            window.clearTimeout(idleTimer.current);
            setRotationEnabled((enabled) => !enabled);
            setRotating(true);
          }}>
          {rotationEnabled && rotating && !lowMotion ? <Pause className="size-4" /> : <Play className="size-4" />}
        </Button>
      </div>
      <div className="pointer-events-none absolute bottom-3 right-3 rounded-lg border border-line bg-ink-900/85 px-3 py-2 text-[11px] text-muted">
        {(Object.keys(REGIME_COLORS) as RegimeGroup[]).map((g) => (
          <div key={g} className="flex items-center gap-2"><span className="inline-block size-2.5 rounded-full" style={{ background: REGIME_COLORS[g] }} />{g}</div>
        ))}
      </div>
    </div>
  );
}
