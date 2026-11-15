import { useState } from "react";
import { initials } from "../lib/claims";
import { cn } from "../lib/presentation";

/** Portrait from the graph (`image_url`), falling back to initials when missing or broken. */
export default function Avatar({ name, src, className, rounded = "rounded-full" }: {
  name: string | null | undefined; src?: unknown; className?: string; rounded?: string;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const url = typeof src === "string" && src && failed !== src ? src : null;
  return url
    ? <img src={url} alt={`Portrait of ${name ?? "politician"}`} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(url)}
      className={cn("shrink-0 bg-ink-700 object-cover object-top", rounded, className)} />
    : <span aria-hidden className={cn("flex shrink-0 items-center justify-center bg-gradient-to-br from-indigo-500 via-violet-500 to-fuchsia-500 font-semibold text-white", rounded, className)}>{initials(name)}</span>;
}
