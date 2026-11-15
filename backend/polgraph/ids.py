"""Canonical identifiers and name normalisation used for deduplication."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

# Particles that are not useful for identity matching (Spanish/Portuguese/French/Dutch/German).
NAME_PARTICLES = {"de", "del", "la", "las", "los", "y", "e", "da", "do", "dos", "das", "van", "von", "der", "le", "du"}
HONORIFICS = {"dr", "dra", "lic", "ing", "mtro", "mtra", "sr", "sra", "mr", "mrs", "ms", "miss", "hon", "c",
              "engr", "eng", "prof", "barrister", "advocate", "adv", "maj", "col", "gen", "lt", "capt", "brig",
              "retd", "retired", "r", "justice", "senator"}

# Leading titles removed from display names ("Mr. Abdul Majeed Badini", "Maj (retired) Bashir Ahmed Barech").
_TITLE_RX = re.compile(
    r"^(?:(?:mr|mrs|ms|miss|dr|dra|engr|eng|prof|barrister|advocate|adv|hon|lic|ing|mtro|mtra|"
    r"maj(?:or)?|col(?:onel)?|lt\.?\s*gen|lt|gen(?:eral)?|capt(?:ain)?|brig(?:adier)?)\b\.?\s*"
    r"(?:\((?:r|retd|retired)\.?\)\s*|(?:retd|retired)\.?\s+)?)+", re.IGNORECASE)


def clean_display_name(name: str) -> str:
    cleaned = _TITLE_RX.sub("", (name or "").strip()).strip(" ,.")
    return cleaned if len(cleaned.split()) >= 2 else (name or "").strip()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# Leading name elements often dropped in press usage (South Asian / Arabic naming): "Muhammad Shehbaz Sharif"
# is usually written "Shehbaz Sharif". Only stripped when they lead the name and >= 2 tokens remain.
LEADING_NAME_PREFIXES = {
    "muhammad", "mohammad", "muhammed", "mohammed", "mohd", "syed", "sayed", "sayyid", "mian", "sardar", "hafiz",
    "khawaja", "khwaja", "makhdoom", "pir", "mir", "sheikh", "shaikh", "haji", "maulana", "allama", "mufti",
    "nawabzada", "sahibzada", "begum", "rana", "raja", "chaudhry", "chaudhary", "malik", "abdul",
}


def name_variants(name: str) -> list[list[str]]:
    """Token lists to try when matching a name: as given, then with leading honorific/prefix tokens removed."""
    toks = name_tokens(name)
    out = [toks]
    stripped = list(toks)
    while len(stripped) > 2 and stripped[0] in LEADING_NAME_PREFIXES:
        stripped = stripped[1:]
        out.append(list(stripped))
    return out


def normalize_text(text: str) -> str:
    """Lower-case, accent-free, punctuation collapsed to single spaces."""
    t = strip_accents(text or "").lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def slugify(text: str) -> str:
    return normalize_text(text).replace(" ", "-")[:80] or "unnamed"


def name_tokens(name: str) -> list[str]:
    toks = [t for t in normalize_text(name).split() if t not in HONORIFICS]
    return [t for t in toks if t not in NAME_PARTICLES and len(t) > 1]


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def canonical_url(url: str) -> str:
    """Drop fragments, tracking params and trailing slashes so the same page dedupes."""
    parts = urlsplit(url.strip())
    query = "&".join(
        q for q in parts.query.split("&") if q and not q.lower().startswith(("utm_", "fbclid", "gclid"))
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, query, ""))


def registrable_domain(url: str) -> str:
    """Approximate eTLD+1 (handles common two-level public suffixes like gob.mx, co.uk)."""
    host = urlsplit(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    second_level = {"gob", "gov", "com", "org", "net", "edu", "co", "ac", "go", "gouv", "nic"}
    if labels[-2] in second_level and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# ---- canonical node ids -------------------------------------------------------

def earth_id() -> str:
    return "earth"


def continent_id(name: str) -> str:
    return f"continent:{slugify(name)}"


def country_id(iso2: str) -> str:
    return f"country:{iso2.upper()}"


def level_id(iso2: str, level: str, *parts: str) -> str:
    tail = ":".join(slugify(p) if not re.fullmatch(r"[A-Z]{2}-[A-Z0-9]{1,3}", p) else p for p in parts)
    return f"level:{iso2.upper()}:{level}" + (f":{tail}" if tail else "")


def body_id(level_node_id: str, name: str) -> str:
    return f"body:{level_node_id.split(':', 1)[1]}:{slugify(name)}"


def office_id(body_node_id: str, title: str) -> str:
    return f"office:{body_node_id.split(':', 1)[1]}:{slugify(title)}"


def person_id(iso2: str, name: str) -> str:
    return f"person:{iso2.upper()}:{slugify(' '.join(name_tokens(name)) or name)}"


def party_id(iso2: str, name_or_acronym: str) -> str:
    return f"party:{iso2.upper()}:{slugify(normalize_party_key(name_or_acronym))}"


def term_id(office_node_id: str, person_node_id: str, start: str | None) -> str:
    return f"term:{short_hash(office_node_id + '|' + person_node_id + '|' + (start or 'unknown'))}"


def source_id(url: str) -> str:
    return f"source:{short_hash(canonical_url(url), 16)}"


def assessment_id(iso2: str, index: str, year: int | None) -> str:
    return f"assessment:{iso2.upper()}:{slugify(index)}:{year or 'unknown'}"


def contact_rule_id(iso2: str) -> str:
    return f"contactrule:{iso2.upper()}"


def normalize_party_key(text: str) -> str:
    t = normalize_text(text)
    # "Movimiento Regeneración Nacional (Morena)" -> prefer the acronym in parentheses.
    m = re.search(r"\(([^)]+)\)", strip_accents(text or ""))
    if m and len(m.group(1)) <= 12:
        t = normalize_text(m.group(1))
    return t
