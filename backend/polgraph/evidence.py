"""Evidence evaluation: source authority, text-level claim support, and corroboration status.

A claim is never accepted because an LLM/agent said so. It is accepted only when page text that
we fetched ourselves (Exa Contents/Search) actually mentions the claimed entity together with the
office, and the number/authority of independent supporting domains meets the policy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .ids import canonical_url, name_tokens, name_variants, normalize_text, registrable_domain


class EvidenceStatus(str, Enum):
    verified = "verified"                # >= N independent domains support the claim
    official_single = "official_single"  # one official/primary source supports it
    single_source = "single_source"      # one non-official source supports it
    conflicting = "conflicting"          # supported, but a competing claim is equally supported
    unsupported = "unsupported"          # no fetched source text supports it


PERSISTABLE = {EvidenceStatus.verified, EvidenceStatus.official_single, EvidenceStatus.single_source}
DISPLAY_AS_VERIFIED = {EvidenceStatus.verified, EvidenceStatus.official_single}

# Primary / institutional sources beyond government TLD patterns.
_INSTITUTIONAL = {
    "freedomhouse.org", "v-dem.net", "eiu.com", "ipu.org", "un.org", "oas.org", "idea.int",
    "constituteproject.org", "ine.mx", "scjn.gob.mx", "dof.gob.mx", "diputados.gob.mx", "senado.gob.mx",
    "inai.org.mx", "te.gob.mx", "congresocdmx.gob.mx", "cdmx.gob.mx", "europa.eu", "legislation.gov.uk",
}
_REFERENCE = {
    "wikipedia.org", "britannica.com", "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "elpais.com",
    "eluniversal.com.mx", "milenio.com", "jornada.com.mx", "excelsior.com.mx", "elfinanciero.com.mx",
    "eleconomista.com.mx", "proceso.com.mx", "animalpolitico.com", "infobae.com", "expansion.mx",
    "nytimes.com", "ft.com", "theguardian.com", "aljazeera.com", "cnn.com", "forbes.com.mx", "ourworldindata.org",
    "ballotpedia.org", "electionguide.org",
}


def authority_tier(url: str) -> int:
    """1 = official/primary, 2 = established reference/press, 3 = other."""
    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", url.lower()).split("/")[0].split(":")[0])
    dom = registrable_domain(url)
    if dom in _INSTITUTIONAL or host in _INSTITUTIONAL:
        return 1
    labels = host.split(".")
    if any(l in ("gob", "gov", "gouv", "gv", "govt", "parliament", "parlament", "parlamento") for l in labels) \
            or host.endswith(".gov") or host.endswith(".mil"):
        return 1
    if dom in _REFERENCE or any(host.endswith("." + r) or host == r for r in _REFERENCE):
        return 2
    return 3


def is_official(url: str) -> bool:
    return authority_tier(url) == 1


def mentions_name(name: str, text: str) -> bool:
    """True if `text` mentions the person: full token sequence, or first given name followed
    (within 2 words, never across a conjunction) by one of the surnames. Accent- and case-insensitive.
    Leading honorifics/prefixes ("Muhammad", "Syed", "Mian"…) may be absent in the text."""
    if not text or not name:
        return False
    t = " " + normalize_text(text) + " "
    for toks in name_variants(name):
        if len(toks) < 2:
            continue
        if " " + " ".join(toks) + " " in t:
            return True
        first, surnames = toks[0], toks[1:]
        for sur in surnames:
            if len(sur) < 3:
                continue
            # Up to two middle names between, but never across a conjunction ("Juan García y Pedro Pérez").
            if re.search(rf"\b{re.escape(first)}\b(?:\s+(?!(?:y|e|and|o|or|u)\b)\w+){{0,2}}?\s+\b{re.escape(sur)}\b", t):
                return True
    return False


def mentions_any(keywords: list[str], text: str) -> bool:
    """Prefix-tolerant keyword match ('president' matches 'presidenta', 'presidente')."""
    if not keywords:
        return True
    t = " " + normalize_text(text) + " "
    for kw in keywords:
        k = normalize_text(kw)
        if not k:
            continue
        if re.search(rf"\b{re.escape(k)}", t):
            return True
    return False


def office_keywords(*titles: str | None, extra: list[str] | None = None) -> list[str]:
    """Derive matching keywords from office titles: the distinctive head noun of each title."""
    stop = {"of", "the", "de", "del", "la", "el", "los", "las", "and", "y", "mexico", "republic", "republica",
            "state", "estado", "city", "ciudad", "federal", "nacional", "national", "municipal", "united",
            "unidos", "mexicanos", "mexican"}
    weak_heads = {"head", "chief", "member", "jefe", "jefa", "titular", "miembro", "integrante"}
    out: list[str] = []
    for t in titles:
        if not t:
            continue
        words = [w for w in normalize_text(t).split() if w not in stop and len(w) > 3]
        take = words[:2] if words and words[0] in weak_heads else words[:1]
        for w in take:
            # Gender/number-neutral stem: presidenta/presidente -> president, jefa/jefe -> jef
            out.append(w[:-1] if w.endswith(("a", "e", "o")) and len(w) >= 4 else w)
    for e in extra or []:
        if e and len(normalize_text(e)) > 3:
            out.append(normalize_text(e))
    return list(dict.fromkeys(out))


@dataclass
class SupportingSource:
    url: str
    title: str | None
    domain: str
    tier: int
    via: str  # "agent_citation" | "corroboration_search" | "index_page"
    snippet: str | None = None
    published_date: str | None = None


@dataclass
class Verification:
    status: EvidenceStatus
    supporting: list[SupportingSource] = field(default_factory=list)
    checked_urls: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        if self.status == EvidenceStatus.unsupported:
            return 0.0
        doms = {s.domain for s in self.supporting}
        official = any(s.tier == 1 for s in self.supporting)
        score = 0.35 + 0.2 * min(len(doms), 3) + (0.15 if official else 0.0)
        if self.status == EvidenceStatus.conflicting:
            score *= 0.5
        return round(min(score, 0.99), 2)


def _snippet(name: str, text: str, width: int = 160) -> str | None:
    toks = name_tokens(name)
    if not toks or not text:
        return None
    flat = re.sub(r"\s+", " ", text)
    norm = normalize_text(flat)
    # Positions in the normalised string roughly track the original; good enough for a context snippet.
    idx = norm.find(toks[-1]) if toks[-1] in norm else norm.find(toks[0])
    if idx < 0:
        return None
    start = max(0, idx - width)
    return "…" + flat[start: idx + width].strip() + "…"


def classify(supporting: list[SupportingSource], min_domains: int) -> EvidenceStatus:
    doms = {s.domain for s in supporting}
    if len(doms) >= min_domains:
        return EvidenceStatus.verified
    if any(s.tier == 1 for s in supporting):
        return EvidenceStatus.official_single
    if doms:
        return EvidenceStatus.single_source
    return EvidenceStatus.unsupported


def evaluate_pages(name: str, keywords: list[str], pages: dict[str, dict], via: str,
                   context_terms: list[str] | None = None) -> list[SupportingSource]:
    """Pages (canonical_url -> {url,title,text,...}) that mention the person AND the office.
    `context_terms` (e.g. the state or municipality name) must also appear when given, so that
    a page about a different governor/mayor with the same surname does not count."""
    out = []
    for url, page in pages.items():
        text = " ".join(filter(None, [page.get("title"), page.get("text"), " ".join(page.get("highlights") or [])]))
        if not mentions_name(name, text):
            continue
        if not mentions_any(keywords, text):
            continue
        if context_terms and not mentions_any(context_terms, text):
            continue
        out.append(SupportingSource(url=page.get("url") or url, title=page.get("title"),
                                    domain=registrable_domain(page.get("url") or url),
                                    tier=authority_tier(page.get("url") or url), via=via,
                                    snippet=_snippet(name, text), published_date=page.get("published_date")))
    return out


def merge_supporting(*groups: list[SupportingSource]) -> list[SupportingSource]:
    seen: dict[str, SupportingSource] = {}
    for g in groups:
        for s in g:
            seen.setdefault(canonical_url(s.url), s)
    return list(seen.values())
