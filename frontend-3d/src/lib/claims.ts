// Presentation helpers for System B social claims (types, platforms, numbers).
export const CLAIM_TYPE_COLORS: Record<string, string> = {
  statistic: "#4f46e5", event: "#0d9488", policy: "#7c3aed", promise: "#d97706", accusation: "#dc2626", other: "#94a3b8",
};
export const SPEAKER_COLORS: Record<string, string> = { politician: "#4f46e5", uploader: "#0ea5e9", quoted: "#a855f7" };
export const PLATFORM_META: Record<string, { name: string; color: string; short: string }> = {
  tiktok: { name: "TikTok", color: "#111827", short: "TT" },
  instagram: { name: "Instagram", color: "#c13584", short: "IG" },
  youtube: { name: "YouTube", color: "#dc2626", short: "YT" },
};
export const SPEAKER_NAMES: Record<string, string> = {
  politician: "Politician speaking", uploader: "Account owner", quoted: "Quoted third party",
};

export const colorOf = (map: Record<string, string>, key: string | null | undefined) => (key && map[key]) || "#94a3b8";
export const humanize = (v: string | null | undefined) => (v ? v.replaceAll("_", " ") : "—");
export const titleCase = (v: string | null | undefined) => humanize(v).replace(/^\w/, (c) => c.toUpperCase());
export const compact = (n: number | undefined | null) =>
  n === undefined || n === null ? "—" : Intl.NumberFormat(undefined, { notation: "compact" }).format(n);
export const pct = (part: number, total: number) => (total ? Math.round((100 * part) / total) : 0);
export const initials = (name: string | null | undefined) =>
  (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
