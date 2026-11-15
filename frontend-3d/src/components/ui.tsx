// Small shadcn-style primitives (Tailwind + clsx/tailwind-merge). Kept local to avoid a component-CLI dependency.
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import type { RegimeGroup } from "../lib/api";
import { cn, REGIME_COLORS } from "../lib/presentation";

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-line bg-ink-900/80 backdrop-blur-sm", className)} {...p} />;
}

export function CardHeader({ title, aside, className }: { title: ReactNode; aside?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-center justify-between gap-2 px-4 pt-3 pb-2", className)}>
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">{title}</h3>
      {aside}
    </div>
  );
}

export function Badge({ className, tone = "default", ...p }: HTMLAttributes<HTMLSpanElement> & {
  tone?: "default" | "ok" | "warn" | "bad" | "accent";
}) {
  const tones = {
    default: "bg-ink-700 text-muted",
    ok: "bg-ok/15 text-ok",
    warn: "bg-warn/15 text-warn",
    bad: "bg-bad/15 text-bad",
    accent: "bg-accent/15 text-accent",
  };
  return <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", tones[tone], className)} {...p} />;
}

export function Button({ className, variant = "default", ...p }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "ghost" | "primary";
}) {
  const v = {
    default: "border border-line bg-ink-800 hover:bg-ink-700 text-fg",
    ghost: "hover:bg-ink-800 text-muted hover:text-fg",
    primary: "bg-accent hover:bg-accent/90 text-ink-950 font-semibold",
  };
  return (
    <button
      className={cn("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-accent", v[variant], className)}
      {...p}
    />
  );
}

export function RegimeDot({ group, className }: { group: RegimeGroup; className?: string }) {
  return <span className={cn("inline-block size-2.5 shrink-0 rounded-full", className)} style={{ background: REGIME_COLORS[group] }} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-ink-700/60", className)} />;
}

export function ExtLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  let safe = false;
  try { safe = ["http:", "https:"].includes(new URL(href).protocol); } catch { /* Invalid stored source. */ }
  if (!safe) return <span className={className}>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className={cn("text-accent hover:underline break-words", className)}>
      {children}
    </a>
  );
}

