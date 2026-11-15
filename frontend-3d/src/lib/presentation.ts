import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { RegimeGroup } from "./api";

export const cn = (...v: ClassValue[]) => twMerge(clsx(v));

export const REGIME_COLORS: Record<RegimeGroup, string> = {
  Democratic: "#5b8cff",
  "Partly democratic": "#b18cff",
  Authoritarian: "#e07a5f",
  Unclassified: "#64708a",
};

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
