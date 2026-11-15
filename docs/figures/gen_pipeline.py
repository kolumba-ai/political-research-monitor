"""Generate docs/figures/pipeline.svg: the animated end-to-end workflow (Systems A-D and the explorer).

Pure Python, no dependencies: `python docs/figures/gen_pipeline.py`. Motion is SMIL, so it plays in GitHub's
README image renderer and in a browser. Particles show the direction data moves; they are illustrative, not timing.
"""
from pathlib import Path
import math
import random

OUT = Path(__file__).with_name("pipeline.svg")
CW, GAP = 370, 56
W = 2280
X0 = (W - (5 * CW + 4 * GAP)) // 2
COLS = [X0 + i * (CW + GAP) for i in range(5)]
SPAN3 = 3 * CW + 2 * GAP
SPAN4 = 4 * CW + 3 * GAP
FULL = 5 * CW + 4 * GAP
CYAN, PINK, BLUE, PEACH, TEAL, YELLOW, MINT, LAV, RED = "#0891B2", "#DB2777", "#2563EB", "#EA580C", "#0D9488", "#B45309", "#059669", "#7C3AED", "#DC2626"
INK, SUB, MUTED = "#0F172A", "#475569", "#64748B"
FONT = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"
TOOLS = {  # label, fill, text colour
    "Exa": ("Exa", "#1F40ED", "#FFFFFF"),
    "past.dev": ("past.dev", "#7C3AED", "#FFFFFF"),
    "Modal": ("Modal", "#62DE61", "#0B2E0B"),
    "OpenAI": ("OpenAI", "#111827", "#FFFFFF"),
    "Claude": ("Claude", "#D97757", "#FFFFFF"),
    "Neon": ("Neon", "#00E599", "#05301F"),
    "Oriane": ("Oriane", "#E2E8F0", "#334155"),
}
random.seed(7)
defs, edges, cards_svg, fx = [], [], [], []
cards = {}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap(s, n):
    lines, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + [cur]


def text_w(s, size, bold=False):
    return len(s) * size * (0.58 if bold else 0.53)


def chip(x, y, text, color, size=17):
    w = text_w(text, size, True) + 24
    return (f'<rect x="{x}" y="{y}" width="{w:.0f}" height="{size + 13}" rx="{(size + 13) / 2}" fill="{color}" fill-opacity="0.1" '
            f'stroke="{color}" stroke-opacity="0.55"/><text x="{x + 12}" y="{y + size + 2}" font-size="{size}" fill="{color}" font-weight="700">{esc(text)}</text>')


def badge(x, y, tool, size=16, glow=True):
    label, bg, fg = TOOLS[tool]
    w = text_w(label, size, True) + 26
    halo = (f'<rect x="{x - 3}" y="{y - 3}" width="{w + 6:.0f}" height="{size + 18}" rx="{(size + 18) / 2}" fill="{bg}" opacity="0.35">'
            f'<animate attributeName="opacity" values="0.1;0.45;0.1" dur="2.4s" begin="{random.uniform(0, 2):.1f}s" repeatCount="indefinite"/></rect>') if glow else ""
    return w, (halo + f'<rect x="{x}" y="{y}" width="{w:.0f}" height="{size + 12}" rx="{(size + 12) / 2}" fill="{bg}"/>'
               f'<text x="{x + w / 2:.0f}" y="{y + size + 1}" text-anchor="middle" font-size="{size}" font-weight="800" fill="{fg}">{esc(label)}</text>')


def badges_right(x_right, y, tools):
    out, x = [], x_right
    for t in reversed(tools):
        x -= badge(0, 0, t)[0]
        out.append(badge(x, y, t)[1])
        x -= 8
    return "".join(out)


ICONS = {
    "globe": lambda c: f'<circle r="17" fill="none" stroke="{c}" stroke-width="2.4"/><ellipse rx="7" ry="17" fill="none" stroke="{c}" stroke-width="2">'
                       f'<animate attributeName="rx" values="16;2;16" dur="6s" repeatCount="indefinite"/></ellipse>'
                       f'<path d="M -17 0 H 17 M -14 -9 H 14 M -14 9 H 14" stroke="{c}" stroke-width="1.6"/>',
    "search": lambda c: f'<circle cx="-4" cy="-4" r="11" fill="none" stroke="{c}" stroke-width="2.6"/><path d="M 4 4 L 15 15" stroke="{c}" stroke-width="3.2" stroke-linecap="round"/>',
    "shield": lambda c: f'<path d="M 0 -18 L 15 -12 V 0 C 15 10 8 16 0 19 C -8 16 -15 10 -15 0 V -12 Z" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -7 0 L -2 6 L 8 -6" fill="none" stroke="{c}" stroke-width="2.6" stroke-linecap="round"/>',
    "check": lambda c: f'<rect x="-15" y="-18" width="26" height="34" rx="4" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -9 -8 H 5 M -9 -1 H 5" stroke="{c}" stroke-width="2"/><circle cx="10" cy="10" r="8" fill="#fff" stroke="{c}" stroke-width="2.2"/><path d="M 6 10 L 9 13 L 14 7" fill="none" stroke="{c}" stroke-width="2.2"/>',
    "graph": lambda c: f'<path d="M 0 -12 L -13 10 M 0 -12 L 13 10 M -13 10 H 13" stroke="{c}" stroke-width="2"/><circle cy="-12" r="6" fill="{c}"/><circle cx="-13" cy="10" r="5" fill="{c}" fill-opacity="0.7"/><circle cx="13" cy="10" r="5" fill="{c}" fill-opacity="0.7"/>',
    "phone": lambda c: f'<rect x="-11" y="-18" width="22" height="36" rx="5" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -4 -4 L 5 1 L -4 6 Z" fill="{c}"/>',
    "wave": lambda c: "".join(f'<rect x="{-16 + i * 7}" y="-4" width="4" height="8" rx="2" fill="{c}"><animate attributeName="height" values="6;{14 + i % 3 * 8};6" dur="{0.9 + i * 0.13:.2f}s" repeatCount="indefinite"/>'
                              f'<animate attributeName="y" values="-3;{-(7 + i % 3 * 4)};-3" dur="{0.9 + i * 0.13:.2f}s" repeatCount="indefinite"/></rect>' for i in range(5)),
    "doc": lambda c: f'<path d="M -13 -18 H 6 L 14 -10 V 18 H -13 Z" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -7 -6 H 8 M -7 1 H 8 M -7 8 H 3" stroke="{c}" stroke-width="2"/>',
    "layers": lambda c: f'<path d="M 0 -16 L 17 -7 L 0 2 L -17 -7 Z" fill="{c}" fill-opacity="0.3" stroke="{c}" stroke-width="2"/><path d="M -17 1 L 0 10 L 17 1 M -17 9 L 0 18 L 17 9" fill="none" stroke="{c}" stroke-width="2"/>',
    "db": lambda c: f'<ellipse cy="-12" rx="15" ry="6" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -15 -12 V 12 C -15 20 15 20 15 12 V -12 M -15 0 C -15 8 15 8 15 0" fill="none" stroke="{c}" stroke-width="2.4"/>',
    "plan": lambda c: f'<path d="M -15 -14 H 15 M -15 -2 H 9 M -15 10 H 3" stroke="{c}" stroke-width="2.6" stroke-linecap="round"/><circle cx="12" cy="10" r="5" fill="{c}"/>',
    "scale": lambda c: f'<path d="M 0 -17 V 17 M -11 17 H 11 M -16 -10 H 16" stroke="{c}" stroke-width="2.4"/><path d="M -16 -10 L -21 3 H -11 Z M 16 -10 L 11 3 H 21 Z" fill="{c}" fill-opacity="0.35" stroke="{c}" stroke-width="1.8"/>',
    "cloud": lambda c: f'<path d="M -12 12 C -22 12 -22 -2 -12 -2 C -12 -14 6 -16 8 -6 C 18 -8 22 12 10 12 Z" fill="none" stroke="{c}" stroke-width="2.4"/>',
    "people": lambda c: f'<circle cx="-7" cy="-8" r="6" fill="{c}"/><path d="M -17 14 C -17 1 3 1 3 14" fill="{c}" fill-opacity="0.6"/><circle cx="9" cy="-5" r="5" fill="{c}" fill-opacity="0.8"/><path d="M 1 15 C 1 4 18 4 18 15" fill="{c}" fill-opacity="0.45"/>',
    "import": lambda c: f'<path d="M 0 -17 V 7 M -9 -2 L 0 7 L 9 -2" fill="none" stroke="{c}" stroke-width="2.6" stroke-linecap="round"/><path d="M -15 6 V 16 H 15 V 6" fill="none" stroke="{c}" stroke-width="2.4"/>',
    "json": lambda c: f'<text x="0" y="7" text-anchor="middle" font-size="20" font-weight="800" fill="{c}">{{ }}</text>',
    "stop": lambda c: f'<circle r="16" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M 0 -9 V 0 L 7 5" stroke="{c}" stroke-width="2.4" stroke-linecap="round"/>',
    "script": lambda c: f'<path d="M -16 -14 H 10 V 6 H -2 L -10 13 V 6 H -16 Z" fill="none" stroke="{c}" stroke-width="2.4" stroke-linejoin="round"/><text x="-3" y="1" text-anchor="middle" font-size="13" font-weight="800" fill="{c}">¿?</text>',
    "gauge": lambda c: f'<path d="M -17 8 A 17 17 0 0 1 17 8" fill="none" stroke="{c}" stroke-width="2.6"/><path d="M 0 8 L 9 -6" stroke="{c}" stroke-width="2.6" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" values="-70 0 8;40 0 8;-70 0 8" dur="5s" repeatCount="indefinite"/></path>',
    "swarm": lambda c: "".join(f'<circle cx="{dx}" cy="{dy}" r="4.5" fill="{c}"><animate attributeName="opacity" values="0.3;1;0.3" dur="1.6s" begin="{i * 0.2:.1f}s" repeatCount="indefinite"/></circle>'
                               for i, (dx, dy) in enumerate([(-12, -10), (0, -14), (12, -10), (-14, 3), (0, 0), (14, 3), (-7, 14), (7, 14)])),
}


def card(cid, x, y, title, sub, color, pill=None, icon="doc", tools=(), w=CW, h=200, wrap_at=31, extra=None, strong=False):
    cards[cid] = (x, y, w, h, color)
    s = [f'<g id="card-{cid}">',
         f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="#FFFFFF" stroke="{color}" stroke-width="{3.2 if strong else 2}" filter="url(#shadow)"/>',
         f'<rect x="{x}" y="{y + 18}" width="6" height="{h - 36}" rx="3" fill="{color}"/>',
         f'<g transform="translate({x + 42},{y + 46})">{ICONS[icon](color)}</g>',
         f'<text x="{x + 76}" y="{y + 55}" font-size="27" font-weight="800" fill="{INK}">{esc(title)}</text>']
    for i, line in enumerate(wrap(sub, wrap_at) if sub else []):
        s.append(f'<text x="{x + 26}" y="{y + 98 + i * 25}" font-size="19" fill="{SUB}">{esc(line)}</text>')
    if pill:
        s.append(chip(x + 26, y + h - 50, pill, color))
    if tools:
        s.append(badges_right(x + w - 16, y - 14, tools))
    if extra:
        s.append(extra(x, y, w, h, color))
    s.append("</g>")
    cards_svg.append("".join(s))


def particles(d, length, color, speed=150, spacing=110, reverse=False):
    dur = max(length / speed, 0.6)
    n = max(2, int(length / spacing))
    kp = 'keyPoints="1;0" keyTimes="0;1" calcMode="linear"' if reverse else ""
    return "".join(f'<g><animateMotion dur="{dur:.2f}s" begin="{-dur * k / n:.2f}s" repeatCount="indefinite" path="{d}" {kp}/>'
                   f'<circle r="{(3.4 if k % 3 == 0 else 2.6) * 2.4:.1f}" fill="{color}" opacity="0.18"/><circle r="{3.4 if k % 3 == 0 else 2.6}" fill="{color}"/></g>'
                   for k in range(n))


def link(points, c1, c2, label=None, label_at=None, reply=False, rotate=False, curve=False):
    (x1, y1), (x2, y2) = points[0], points[-1]
    gid = f"g{len(defs)}"
    defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{x1}" y1="{y1}" x2="{x2 + (0.01 if x1 == x2 else 0)}" y2="{y2 + (0.01 if y1 == y2 else 0)}">'
                f'<stop stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>')
    if curve and len(points) == 2:
        my = (y1 + y2) / 2
        d = f"M {x1} {y1} C {x1} {my} {x2} {my} {x2} {y2}"
    else:
        d = "M " + " L ".join(f"{px} {py}" for px, py in points)
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
    e = [f'<path d="{d}" fill="none" stroke="url(#{gid})" stroke-width="8" opacity="0.16" stroke-linejoin="round"/>',
         f'<path d="{d}" fill="none" stroke="url(#{gid})" stroke-width="2.6" stroke-linejoin="round"/>',
         particles(d, length, c1, spacing=50 if length < 100 else 110)]
    if reply:
        e.append(particles(d, length, c2, speed=110, spacing=70, reverse=True))
    if label:
        mx, my = label_at or ((x1 + x2) / 2, (y1 + y2) / 2)
        lw = text_w(label, 16, True) + 26
        tr = f' transform="rotate(-90 {mx} {my})"' if rotate else ""
        e.append(f'<g{tr}><rect x="{mx - lw / 2:.0f}" y="{my - 16}" width="{lw:.0f}" height="32" rx="16" fill="#FFFFFF" stroke="{c1}"/>'
                 f'<text x="{mx}" y="{my + 6}" text-anchor="middle" font-size="16" font-weight="700" fill="{c1}">{esc(label)}</text></g>')
    edges.append("".join(e))


def side(cid, which, frac=0.5):
    x, y, w, h, _ = cards[cid]
    return {"right": (x + w, y + h * frac), "left": (x, y + h * frac), "top": (x + w * frac, y), "bottom": (x + w * frac, y + h)}[which]


def col(cid):
    return cards[cid][4]


def h_edge(a, b, **kw):
    pa, pb = (side(a, "right"), side(b, "left")) if cards[a][0] < cards[b][0] else (side(a, "left"), side(b, "right"))
    link([pa, pb], col(a), col(b), **kw)


def v_edge(a, b, fa=0.5, fb=None, label=None, **kw):
    pa, pb = side(a, "bottom", fa), side(b, "top", fa if fb is None else fb)
    link([pa, pb], col(a), col(b), label=label, curve=pa[0] != pb[0], **kw)


def section(y, text, color, right=False):
    x, a = (X0 + FULL, "end") if right else (X0, "start")
    fx.append(f'<text x="{x}" y="{y - 26}" text-anchor="{a}" font-size="21" font-weight="800" letter-spacing="3" fill="{color}">{esc(text)}</text>')


# ---------- header and tool legend ----------
cards_svg.append(
    f'<rect x="{X0}" y="28" width="{FULL}" height="104" rx="18" fill="#FFFFFF" stroke="{INK}" stroke-width="2" filter="url(#shadow)"/>'
    f'<text x="{X0 + 30}" y="78" font-size="36" font-weight="800" fill="{INK}">Political Research Monitor · from a world knowledge graph to a simulated public</text>'
    f'<text x="{X0 + 30}" y="114" font-size="20" fill="{SUB}">Particles show which way data moves (motion is illustrative, not timing). Badges mark where each sponsor tool runs. Figures are for Mexico, November 2026.</text>')
legend = [("Exa", "constitutions, officeholder checks, monitors, evidence search"),
          ("past.dev", "shared claim + evidence memory between agents"),
          ("Modal", "capture, sharded extraction, polling swarm, explorer"),
          ("OpenAI", "every System D agent"),
          ("Claude", "claim extraction, query plans, stance")]
lx, ly = X0, 160
cards_svg.append(f'<rect x="{X0}" y="{ly - 8}" width="{FULL}" height="92" rx="18" fill="#FFFFFF" stroke="#CBD5E1" stroke-width="1.5"/>'
                 f'<text x="{X0 + 24}" y="{ly + 22}" font-size="16" font-weight="800" letter-spacing="2" fill="{MUTED}">SPONSOR TOOLS</text>')
slot = (FULL - 40) / len(legend)
for i, (tool, desc) in enumerate(legend):
    x = X0 + 24 + i * slot
    w, svg = badge(x, ly + 36, tool, size=18)
    cards_svg.append(svg + f'<text x="{x + w + 12:.0f}" y="{ly + 49}" font-size="16" fill="{SUB}">{esc(wrap(desc, 30)[0])}</text>'
                     + (f'<text x="{x + w + 12:.0f}" y="{ly + 68}" font-size="16" fill="{SUB}">{esc(" ".join(wrap(desc, 30)[1:]))}</text>' if len(wrap(desc, 30)) > 1 else ""))

# ---------- System A ----------
RA = 340
section(RA, "SYSTEM A · WORLD KNOWLEDGE GRAPH", CYAN)
card("registry", COLS[0], RA, "World registry", "ISO 3166 + UN M49: Earth → continents → countries", CYAN, "249 countries · no Exa", "globe")
card("constitution", COLS[1], RA, "Constitution", "Exa Agent reads the official text into a structure", PEACH, "Exa agent runs", "search", ["Exa"])
card("review", COLS[2], RA, "Structure review", "an article cited for every tier, body and office", YELLOW, "human approval", "shield")
card("holders", COLS[3], RA, "Officeholders", "kept only if the fetched page names person and office", PEACH, "Exa contents + search", "check", ["Exa"])
card("graph", COLS[4], RA, "Knowledge graph", "country → government → office → person", LAV, "Exa monitors revalidate", "graph", ["Exa"])
for a, b in [("registry", "constitution"), ("constitution", "review"), ("review", "holders"), ("holders", "graph")]:
    h_edge(a, b)

# ---------- System B ----------
RB = RA + 200 + 110
section(RB, "SYSTEM B · WHAT POLITICIANS SAY", PINK, right=False)
card("capture", COLS[4], RB, "Post capture", "Instagram and TikTok posts of tracked politicians", PINK, "3,742 posts", "phone", ["Oriane", "Modal"])
card("transcribe", COLS[3], RB, "Transcripts", "speech and captions into timed segments", PINK, "227,626 segments", "wave")
card("extract", COLS[2], RB, "Claim extraction", "sharded across Modal containers", BLUE, "18,727 claims · 61 people", "doc", ["Claude", "Modal"])
card("storylines", COLS[1], RB, "Storylines", "one claim, its restatements and counter-claims", BLUE, "sl-… IDs", "layers")
card("neon", COLS[0], RB, "Postgres", "posts, claims and propositions shared by B, C, D", TEAL, "one evidence trail", "db", ["Neon"])
v_edge("graph", "capture", label="who to watch")
for a, b in [("capture", "transcribe"), ("transcribe", "extract"), ("extract", "storylines"), ("storylines", "neon")]:
    h_edge(a, b)

# ---------- System C ----------
RC = RB + 200 + 110
section(RC, "SYSTEM C · EVIDENCE FROM THE WEB", PEACH, right=True)
card("plan", COLS[0], RC, "Query plan", "plans searches for each proposition", PEACH, "16,484 propositions", "plan", ["Claude"])
card("exa", COLS[1], RC, "Exa search", "finds sources the model doesn't already know", PEACH, "Exa /search", "search", ["Exa"])
card("fetch", COLS[2], RC, "Publisher fetch", "pages read from the source, robots and licence kept", CYAN, "10,880 documents", "doc")
card("stance", COLS[3], RC, "Stance judge", "supports or contradicts, quote checked on the page", YELLOW, "9,995 stance links", "scale", ["Claude", "Modal"])


def memory(x, y, w, h, color):
    s = []
    for _ in range(34):
        px, py = x + random.uniform(30, w - 30), y + random.uniform(128, 146)
        c = random.choice([color, CYAN, PEACH, BLUE])
        s.append(f'<circle cx="{px:.0f}" cy="{py:.0f}" r="{random.uniform(1.8, 3.4):.1f}" fill="{c}">'
                 f'<animate attributeName="opacity" values="0.15;1;0.15" dur="{random.uniform(1.8, 4):.1f}s" begin="{random.uniform(0, 3):.1f}s" repeatCount="indefinite"/></circle>')
    return "".join(s)


card("pastdev", COLS[4], RC, "past.dev memory", "C writes claims + evidence; B reads them back", LAV, "multi-agent memory", "cloud", ["past.dev"], extra=None, strong=True)
v_edge("neon", "plan", label="propositions")
for a, b in [("plan", "exa"), ("fetch", "stance"), ("stance", "pastdev")]:
    h_edge(a, b)
h_edge("exa", "fetch", reply=True)

# ---------- System D ----------
RD = RC + 200 + 120
section(RD, "SYSTEM D · THE SIMULATED-PEOPLE SWARM", MINT)
card("import", COLS[0], RD, "Storyline import", "real statements and their comments, reversible", TEAL, "16 storylines", "import", h=190)
card("designer", COLS[1], RD, "Question designer", "writes a Spanish survey script per storyline", PINK, "question set", "script", ["OpenAI"], h=190)
card("strata", COLS[2], RD, "Population", "region × age × gender, with census-style shares", MINT, "12 strata", "people", h=190)
card("sampling", COLS[3], RD, "Sampling agent", "fills each stratum's quota; writes one persona per respondent", MINT, "personas", "people", ["OpenAI"], h=190, wrap_at=33)

D2 = RD + 190 + 120
card("scorer", COLS[0], D2, "Social scorer", "scores each real comment from −1 to +1", BLUE, "428 comments", "gauge", ["OpenAI"], h=220)

STRATA = ["Centro · 40+ · F", "Norte · 18-39 · M", "Sur-Sureste · 40+ · M", "Centro · 18-39 · F",
          "Norte · 40+ · F", "Centro · 40+ · M", "Sur-Sureste · 18-39 · F", "Norte · 18-39 · F"]
ANSWERS = [(True, "+0.3"), (False, "−0.5"), (True, "+0.1"), (False, "−0.2"), (True, "+0.6"), (False, "0.0"), (True, "+0.2"), (False, "−0.4")]


def swarm(x, y, w, h, color):
    s = [f'<text x="{x + 26}" y="{y + 96}" font-size="19" fill="{SUB}">Each polling agent interviews one simulated respondent in Spanish. Seed batch of 30 per storyline,</text>',
         f'<text x="{x + 26}" y="{y + 121}" font-size="19" fill="{SUB}">then 20 at a time. Built as a Modal .map() fleet; storyline-run-1 ran 96 interviews in parallel.</text>']
    cw, chh = (w - 52) / 4, 104
    for i in range(8):
        cx, cy = x + 26 + (i % 4) * cw, y + 148 + (i // 4) * (chh + 12)
        believes, sent = ANSWERS[i]
        ax, rx, my = cx + 36, cx + cw - 50, cy + 42
        d = f"M {ax + 22} {my} L {rx - 22} {my}"
        t0 = round(i * 0.45, 2)
        s.append(f'<rect x="{cx}" y="{cy}" width="{cw - 14:.0f}" height="{chh}" rx="14" fill="#F8FAFC" stroke="#E2E8F0"/>')
        s.append(f'<path d="{d}" stroke="#CBD5E1" stroke-width="2" stroke-dasharray="4 5"/>')
        s.append(particles(d, rx - ax - 44, PINK, speed=70, spacing=40) + particles(d, rx - ax - 44, MINT, speed=60, spacing=55, reverse=True))
        # polling agent (headset)
        s.append(f'<g transform="translate({ax},{my})"><circle r="20" fill="{PINK}" fill-opacity="0.12" stroke="{PINK}" stroke-width="2"/>'
                 f'<circle cy="-3" r="7" fill="{PINK}"/><path d="M -11 -2 A 11 11 0 0 1 11 -2" fill="none" stroke="{PINK}" stroke-width="2.4"/>'
                 f'<path d="M -9 12 C -9 4 9 4 9 12" fill="{PINK}"/></g>')
        # respondent
        s.append(f'<g transform="translate({rx},{my})"><circle r="20" fill="{MINT}" fill-opacity="0.12" stroke="{MINT}" stroke-width="2">'
                 f'<animate attributeName="stroke-width" values="2;4;2" dur="2s" begin="{t0}s" repeatCount="indefinite"/></circle>'
                 f'<circle cy="-5" r="6.5" fill="{MINT}"/><path d="M -10 12 C -10 2 10 2 10 12" fill="{MINT}"/></g>')
        s.append(f'<text x="{ax - 20}" y="{cy + 90}" font-size="14" fill="{PINK}" font-weight="700">agent {i + 1}</text>'
                 f'<text x="{rx + 20}" y="{cy + 90}" text-anchor="end" font-size="14" fill="{MUTED}">{esc(STRATA[i])}</text>')
        mark, mc = ("believes", MINT) if believes else ("doesn't", RED)
        bx = (ax + rx) / 2
        s.append(f'<g opacity="0"><animate attributeName="opacity" values="0;0;1;1;0" keyTimes="0;0.35;0.45;0.85;1" dur="4s" begin="{t0}s" repeatCount="indefinite"/>'
                 f'<rect x="{bx - 62}" y="{cy + 2}" width="124" height="24" rx="12" fill="{mc}" fill-opacity="0.12" stroke="{mc}"/>'
                 f'<text x="{bx}" y="{cy + 19}" text-anchor="middle" font-size="14" font-weight="700" fill="{mc}">{mark} · {sent}</text></g>')
    s.append(f'<text x="{x + 26}" y="{y + h - 20}" font-size="17" fill="{MUTED}">Each answer is stored in poll_responses: believes the claim (yes/no) and sentiment from −1 to +1.</text>')
    return "".join(s)


card("swarm", COLS[1], D2, "Polling swarm", None, PINK, None, "swarm", ["Modal", "OpenAI"], w=SPAN3, h=420, extra=swarm, strong=True)


def ci_bar(x, y, w, h, color):
    cx, cy = x + w / 2, y + 140
    lines = [("polling", MUTED, "fewer than 5 per stratum → next batch"), ("converged", MINT, "belief CI within ±7 points"),
             ("decisive", PINK, "sentiment CI excludes social"), ("capped", YELLOW, "400 interviews → inconclusive")]
    s = [f'<path d="M {x + 26} {cy} H {x + w - 26}" stroke="#CBD5E1" stroke-width="2"/>',
         f'<rect x="{cx - 150}" y="{cy - 9}" width="300" height="18" rx="9" fill="{color}" fill-opacity="0.25" stroke="{color}">'
         f'<animate attributeName="width" values="300;300;70;70" keyTimes="0;0.15;0.75;1" dur="6s" repeatCount="indefinite"/>'
         f'<animate attributeName="x" values="{cx - 150};{cx - 150};{cx - 35};{cx - 35}" keyTimes="0;0.15;0.75;1" dur="6s" repeatCount="indefinite"/></rect>'
         f'<circle cx="{cx}" cy="{cy}" r="5.5" fill="{INK}"/>',
         f'<text x="{x + 26}" y="{cy + 34}" font-size="15" fill="{MUTED}">95% CI narrows as interviews arrive</text>']
    for i, (name, c, desc) in enumerate(lines):
        ly = cy + 72 + i * 50
        s.append(f'<circle cx="{x + 36}" cy="{ly - 5}" r="7" fill="{c}"><animate attributeName="r" values="5;8;5" dur="2.4s" begin="{i * 0.6}s" repeatCount="indefinite"/></circle>'
                 f'<text x="{x + 52}" y="{ly}" font-size="18" font-weight="800" fill="{c}">{name}</text>'
                 f'<text x="{x + 52}" y="{ly + 21}" font-size="15" fill="{SUB}">{esc(desc)}</text>')
    return "".join(s)


card("controller", COLS[4], D2, "Controller", "after every batch runs decide()", YELLOW, None, "stop", w=CW, h=420, extra=ci_bar)

D3 = D2 + 420 + 120


def results(x, y, w, h, color):
    stats = [("1,375", "simulated interviews"), ("16", "real storylines"), ("66–103", "interviews per storyline"), ("15 + 1", "decisive + converged"), ("≈10 min", "wall clock, $8.27")]
    sw = (w - 52) / len(stats)
    s = []
    for i, (v, lab) in enumerate(stats):
        sx = x + 26 + i * sw
        s.append(f'<text x="{sx}" y="{y + 130}" font-size="40" font-weight="800" fill="{color}">{esc(v)}</text>'
                 f'<text x="{sx}" y="{y + 160}" font-size="17" fill="{SUB}">{esc(lab)}</text>')
    s.append(f'<text x="{x + 26}" y="{y + h - 18}" font-size="16" fill="{MUTED}">LLM-generated personas, not measured Mexican public opinion: the explorer labels every result "Simulated respondents, not a real poll".</text>')
    return "".join(s)


card("results", COLS[0], D3, "storyline-run-1", None, MINT, None, "people", w=SPAN4, h=210, extra=results)
card("opinion", COLS[4], D3, "opinion.json", "belief + 95% CI, polled vs social sentiment, gap", BLUE, "static export", "json", ["Modal"], h=210)

h_edge("import", "designer")
h_edge("strata", "sampling")
v_edge("import", "scorer", label="real comments")
v_edge("designer", "swarm", fb=(COLS[1] + CW / 2 - COLS[1]) / SPAN3, label="script")
v_edge("sampling", "swarm", fb=(COLS[3] + CW / 2 - COLS[1]) / SPAN3, label="respondents")
link([side("swarm", "right"), side("controller", "left")], PINK, YELLOW)
cx4 = COLS[4] + CW / 2
link([(cx4, D2), (cx4, RD + 95), (COLS[3] + CW, RD + 95)], YELLOW, MINT, label="next batch: 20, strata furthest below quota", label_at=(cx4, RD + 140))
sb = (COLS[0] + CW / 2, D2 + 220)
yb = D2 + 420 + 50
link([sb, (sb[0], yb), (COLS[4] + 70, yb), (COLS[4] + 70, D2 + 420)], BLUE, YELLOW, label="social sentiment", label_at=(COLS[2] + CW / 2, yb))
link([(COLS[4] + 300, D2 + 420), (COLS[4] + 300, D3)], YELLOW, BLUE, label="stop", label_at=(COLS[4] + 300, D2 + 420 + 60))
# Postgres → storyline import (left margin) and past.dev → explorer (right margin)
lm, rm = X0 / 2, W - X0 / 2
link([side("neon", "left"), (lm, RB + 100), (lm, RD + 95), side("import", "left")], TEAL, TEAL, label="storylines + comments", label_at=(lm, RC + 100), rotate=True)

# ---------- Explorer ----------
RE = D3 + 210 + 120
section(RE, "PUBLIC EXPLORER · LIVE ON MODAL", CYAN, right=False)
EX_H = 240
cards["explorer"] = (X0, RE, FULL, EX_H, CYAN)
steps = ["Globe", "Country", "Government", "Politician", "Claims", "Evidence", "Simulated opinion"]
cols = [CYAN, CYAN, YELLOW, LAV, BLUE, PEACH, PINK]
ex = [f'<rect x="{X0}" y="{RE}" width="{FULL}" height="{EX_H}" rx="18" fill="#FFFFFF" stroke="{CYAN}" stroke-width="3.2" filter="url(#shadow)"/>',
      f'<rect x="{X0}" y="{RE + 18}" width="6" height="{EX_H - 36}" rx="3" fill="{CYAN}"/>',
      f'<g transform="translate({X0 + 42},{RE + 46})">{ICONS["globe"](CYAN)}</g>',
      f'<text x="{X0 + 76}" y="{RE + 55}" font-size="27" font-weight="800" fill="{INK}">Political World Explorer</text>',
      f'<text x="{X0 + 450}" y="{RE + 54}" font-size="19" fill="{SUB}">One page per politician joins all four systems. Static exports on a Modal volume; no secrets in the public app.</text>',
      badges_right(X0 + FULL - 16, RE - 14, ["Modal"])]
sw, sgap = 256, (FULL - 80 - 7 * 256) / 6
for i, (t, c) in enumerate(zip(steps, cols)):
    x = X0 + 40 + i * (sw + sgap)
    ex.append(f'<rect x="{x:.0f}" y="{RE + 100}" width="{sw}" height="80" rx="16" fill="{c}" fill-opacity="0.08" stroke="{c}" stroke-width="2">'
              f'<animate attributeName="fill-opacity" values="0.06;0.06;0.35;0.06;0.06" keyTimes="0;{i / 7:.3f};{(i + 0.5) / 7:.3f};{(i + 1) / 7:.3f};1" dur="8s" repeatCount="indefinite"/></rect>'
              f'<text x="{x + sw / 2:.0f}" y="{RE + 148}" text-anchor="middle" font-size="23" font-weight="800" fill="{c}">{esc(t)}</text>')
    if i < 6:
        ax = x + sw
        ex.append(f'<path d="M {ax + 6:.0f} {RE + 140} H {ax + sgap - 6:.0f} M {ax + sgap - 13:.0f} {RE + 133} L {ax + sgap - 6:.0f} {RE + 140} L {ax + sgap - 13:.0f} {RE + 147}" fill="none" stroke="#94A3B8" stroke-width="2.4"/>')
ex.append(f'<text x="{X0 + 40}" y="{RE + 218}" font-size="17" fill="{MUTED}">Graph from A · claims and storylines from B · evidence from C (via past.dev) · simulated opinion from D, always labelled "simulated respondents, not a real poll".</text>')
cards_svg.append("".join(ex))
link([(COLS[4] + CW / 2, D3 + 210), (COLS[4] + CW / 2, RE)], BLUE, CYAN, label="publish", label_at=(COLS[4] + CW / 2, D3 + 210 + 60))
link([side("pastdev", "right"), (rm, RC + 100), (rm, RE + EX_H / 2), (X0 + FULL, RE + EX_H / 2)], LAV, LAV, label="evidence read back (pull-pastdev) → explorer", label_at=(rm, RD + 600), rotate=True)

FOOT = RE + EX_H + 56
fx.append(f'<text x="{X0}" y="{FOOT}" font-size="17" fill="{MUTED}">Solid paths are implemented handoffs. Systems A, B and C also run as Modal jobs (make modal-systema / -systemb / -systemc). Figures come from the Neon database and the System D run, November 2026.</text>')
H = FOOT + 40

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
       "<defs>",
       '<filter id="shadow" x="-5%" y="-5%" width="110%" height="120%"><feDropShadow dx="0" dy="4" stdDeviation="7" flood-color="#0F172A" flood-opacity="0.10"/></filter>',
       '<pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.3" fill="#E2E8F0"/></pattern>',
       *defs, "</defs>",
       f'<rect width="{W}" height="{H}" fill="#FFFFFF"/><rect width="{W}" height="{H}" fill="url(#dots)"/>',
       *edges, *cards_svg, *fx, "</svg>"]
OUT.write_text("\n".join(svg), encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {W}x{H})")
