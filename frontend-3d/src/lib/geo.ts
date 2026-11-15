// Country geometry from Natural Earth (public domain), joined to the knowledge graph by ISO 3166-1 alpha-2.

export interface CountryFeature {
  type: "Feature";
  properties: { NAME: string; ISO_A2: string; ISO_A2_EH: string; [k: string]: unknown };
  geometry: { type: "Polygon" | "MultiPolygon"; coordinates: number[][][] | number[][][][] };
  __iso2?: string | null;
  __centroid?: { lat: number; lng: number };
}

/** Natural Earth leaves ISO_A2 = "-99" for e.g. France and Norway; ISO_A2_EH carries the code. */
export function isoOf(f: CountryFeature): string | null {
  const code = f.properties.ISO_A2_EH && f.properties.ISO_A2_EH !== "-99" ? f.properties.ISO_A2_EH : f.properties.ISO_A2;
  return code && code !== "-99" ? code.toUpperCase() : null;
}

function ringArea(ring: number[][]): number {
  let a = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) a += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1]);
  return Math.abs(a / 2);
}

/** Camera target: centroid of the largest outer ring (good enough for flying to a country). */
export function centroidOf(f: CountryFeature): { lat: number; lng: number } {
  const polys = (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates) as number[][][][];
  let best = polys[0][0];
  for (const p of polys) if (ringArea(p[0]) > ringArea(best)) best = p[0];
  let x = 0, y = 0;
  for (const [lng, lat] of best) { x += lng; y += lat; }
  return { lng: x / best.length, lat: y / best.length };
}

export async function loadCountries(): Promise<CountryFeature[]> {
  const res = await fetch(`${import.meta.env.BASE_URL}data/countries-110m.geojson`);
  if (!res.ok) throw new Error(`country boundaries failed to load (${res.status})`);
  const geo = (await res.json()) as { features: CountryFeature[] };
  return geo.features.map((f) => ({ ...f, __iso2: isoOf(f), __centroid: centroidOf(f) }));
}

/** Emoji flag from an ISO code (regional indicator symbols); no external image needed. */
export function flagOf(iso2: string | null | undefined): string {
  if (!iso2 || iso2.length !== 2) return "🏳️";
  return String.fromCodePoint(...[...iso2.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

export function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    // Current Three.js requires WebGL 2; WebGL 1 must use the accessible list.
    const gl = c.getContext("webgl2");
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}
