// 2D world map used when WebGL 2 is unavailable: same Natural Earth boundaries and regime colours as the
// globe, drawn as SVG (equirectangular projection). Countries are clickable and keyboard-focusable.
import { useMemo, useState } from "react";
import type { CountryRow, RegimeGroup } from "../lib/api";
import type { CountryFeature } from "../lib/geo";
import { REGIME_COLORS } from "../lib/presentation";

const W = 1000, H = 500;
const project = ([lng, lat]: number[]) => `${((lng + 180) / 360) * W},${((90 - lat) / 180) * H}`;

function pathOf(f: CountryFeature): string {
  const polys = (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates) as number[][][][];
  return polys.map((poly) => poly.map((ring) => `M${ring.map(project).join("L")}Z`).join("")).join("");
}

export default function FlatMap({ features, countries, selected, onSelect, reason }: {
  features: CountryFeature[]; countries: Map<string, CountryRow>; selected: string | null;
  onSelect: (iso2: string) => void; reason: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const paths = useMemo(() => features.map((f) => ({ f, d: pathOf(f) })), [features]);
  const hovered = hover ? countries.get(hover) : undefined;

  return (
    <div className="flex h-full flex-col p-3">
      <p className="mb-2 text-xs text-muted">{reason} Showing a 2D map instead.</p>
      <div className="relative min-h-0 flex-1">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-full w-full" role="group" aria-label="World map of countries by regime group">
          <rect width={W} height={H} rx={8} className="fill-ink-850" />
          {paths.map(({ f, d }) => {
            const iso = f.__iso2;
            const row = iso ? countries.get(iso) : undefined;
            const group: RegimeGroup = row?.regime_group ?? "Unclassified";
            const isSel = iso === selected;
            return (
              <path key={`${f.properties.NAME}-${iso}`} d={d} fillRule="evenodd"
                fill={isSel ? "var(--color-accent)" : REGIME_COLORS[group]}
                fillOpacity={isSel ? 0.95 : iso === hover ? 0.9 : 0.6}
                stroke="#ffffff" strokeWidth={0.6}
                className={row ? "cursor-pointer outline-none focus-visible:stroke-accent" : ""}
                tabIndex={row ? 0 : -1}
                aria-label={row ? `${row.name}, ${row.regime_group}` : `${f.properties.NAME}, not in the ISO 3166 registry`}
                onMouseEnter={() => setHover(iso ?? null)} onMouseLeave={() => setHover(null)}
                onClick={() => row && onSelect(row.iso2)}
                onKeyDown={(e) => { if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onSelect(row.iso2); } }}>
                <title>{row ? `${row.name}: ${row.regime_group}` : f.properties.NAME}</title>
              </path>
            );
          })}
        </svg>
        {hovered && (
          <div className="pointer-events-none absolute top-2 left-2 rounded-md border border-line bg-ink-900 px-2.5 py-1.5 text-xs shadow">
            <b>{hovered.name}</b> · {hovered.regime_group}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
        {(Object.keys(REGIME_COLORS) as RegimeGroup[]).map((g) => (
          <span key={g} className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-full" style={{ background: REGIME_COLORS[g] }} />{g}</span>
        ))}
      </div>
    </div>
  );
}
