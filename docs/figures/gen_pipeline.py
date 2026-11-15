"""Generate docs/figures/pipeline.svg: the animated end-to-end workflow (Systems A-D and the explorer).

Pure Python, no dependencies: `python docs/figures/gen_pipeline.py`. Motion is SMIL, so it plays in GitHub's
README image renderer. Particles show the direction data moves; they are illustrative, not timing.
"""
from pathlib import Path
import math
import random

OUT = Path(__file__).with_name("pipeline.svg")
W, H = 2280, 2030
CW, CH, GAP, X0 = 380, 220, 64, 60
COLS = [X0 + i * (CW + GAP) for i in range(5)]
LAV, CYAN, PINK, BLUE, PEACH, TEAL, YELLOW, MINT = "#B67DFF", "#27DFFF", "#FF5FA2", "#4A8DFF", "#FFAB5C", "#2FC4C4", "#FFE65C", "#52FFA8"
FONT = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"
random.seed(7)
defs, layers = [], {"edges": [], "cards": [], "fx": []}
cards = {}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap(s, n=31):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + [cur]


def chip(x, y, text, color, size=18):
    w = len(text) * size * 0.56 + 26
    return (f'<rect x="{x}" y="{y}" width="{w:.0f}" height="{size + 14}" rx="{(size + 14) / 2}" fill="{color}" fill-opacity="0.14" '
            f'stroke="{color}" stroke-opacity="0.6"/><text x="{x + 13}" y="{y + size + 2}" font-size="{size}" fill="{color}" '
            f'font-weight="600">{esc(text)}</text>')


ICONS = {
    "globe": lambda c: f'<circle r="17" fill="none" stroke="{c}" stroke-width="2.4"/><ellipse rx="7" ry="17" fill="none" stroke="{c}" stroke-width="2">'
                       f'<animate attributeName="rx" values="16;2;16" dur="6s" repeatCount="indefinite"/></ellipse>'
                       f'<path d="M -17 0 H 17 M -14 -9 H 14 M -14 9 H 14" stroke="{c}" stroke-width="1.6"/>',
    "search": lambda c: f'<circle cx="-4" cy="-4" r="11" fill="none" stroke="{c}" stroke-width="2.6"/><path d="M 4 4 L 15 15" stroke="{c}" stroke-width="3.2" stroke-linecap="round"/>',
    "shield": lambda c: f'<path d="M 0 -18 L 15 -12 V 0 C 15 10 8 16 0 19 C -8 16 -15 10 -15 0 V -12 Z" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -7 0 L -2 6 L 8 -6" fill="none" stroke="{c}" stroke-width="2.6" stroke-linecap="round"/>',
    "check": lambda c: f'<rect x="-15" y="-18" width="26" height="34" rx="4" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -9 -8 H 5 M -9 -1 H 5" stroke="{c}" stroke-width="2"/><circle cx="10" cy="10" r="8" fill="#0f1526" stroke="{c}" stroke-width="2.2"/><path d="M 6 10 L 9 13 L 14 7" fill="none" stroke="{c}" stroke-width="2.2"/>',
    "graph": lambda c: f'<path d="M 0 -12 L -13 10 M 0 -12 L 13 10 M -13 10 H 13" stroke="{c}" stroke-width="2"/><circle cy="-12" r="6" fill="{c}"/><circle cx="-13" cy="10" r="5" fill="{c}" fill-opacity="0.7"/><circle cx="13" cy="10" r="5" fill="{c}" fill-opacity="0.7"/>',
    "phone": lambda c: f'<rect x="-11" y="-18" width="22" height="36" rx="5" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -4 -4 L 5 1 L -4 6 Z" fill="{c}"/>',
    "wave": lambda c: "".join(f'<rect x="{-16 + i * 7}" y="-4" width="4" height="8" rx="2" fill="{c}"><animate attributeName="height" values="6;{14 + i % 3 * 8};6" dur="{0.9 + i * 0.13:.2f}s" repeatCount="indefinite"/>'
                              f'<animate attributeName="y" values="-3;{-(7 + i % 3 * 4)};-3" dur="{0.9 + i * 0.13:.2f}s" repeatCount="indefinite"/></rect>' for i in range(5)),
    "doc": lambda c: f'<path d="M -13 -18 H 6 L 14 -10 V 18 H -13 Z" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -7 -6 H 8 M -7 1 H 8 M -7 8 H 3" stroke="{c}" stroke-width="2"/>',
    "layers": lambda c: f'<path d="M 0 -16 L 17 -7 L 0 2 L -17 -7 Z" fill="{c}" fill-opacity="0.35" stroke="{c}" stroke-width="2"/><path d="M -17 1 L 0 10 L 17 1 M -17 9 L 0 18 L 17 9" fill="none" stroke="{c}" stroke-width="2"/>',
    "db": lambda c: f'<ellipse cy="-12" rx="15" ry="6" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M -15 -12 V 12 C -15 20 15 20 15 12 V -12 M -15 0 C -15 8 15 8 15 0" fill="none" stroke="{c}" stroke-width="2.4"/>',
    "plan": lambda c: f'<path d="M -15 -14 H 15 M -15 -2 H 9 M -15 10 H 3" stroke="{c}" stroke-width="2.6" stroke-linecap="round"/><circle cx="12" cy="10" r="5" fill="{c}"/>',
    "scale": lambda c: f'<path d="M 0 -17 V 17 M -11 17 H 11 M -16 -10 H 16" stroke="{c}" stroke-width="2.4"/><path d="M -16 -10 L -21 3 H -11 Z M 16 -10 L 11 3 H 21 Z" fill="{c}" fill-opacity="0.4" stroke="{c}" stroke-width="1.8"/>',
    "cloud": lambda c: f'<path d="M -12 12 C -22 12 -22 -2 -12 -2 C -12 -14 6 -16 8 -6 C 18 -8 22 12 10 12 Z" fill="none" stroke="{c}" stroke-width="2.4"/>',
    "people": lambda c: f'<circle cx="-7" cy="-8" r="6" fill="{c}"/><path d="M -17 14 C -17 1 3 1 3 14" fill="{c}" fill-opacity="0.6"/><circle cx="9" cy="-5" r="5" fill="{c}" fill-opacity="0.8"/><path d="M 1 15 C 1 4 18 4 18 15" fill="{c}" fill-opacity="0.45"/>',
    "import": lambda c: f'<path d="M 0 -17 V 7 M -9 -2 L 0 7 L 9 -2" fill="none" stroke="{c}" stroke-width="2.6" stroke-linecap="round"/><path d="M -15 6 V 16 H 15 V 6" fill="none" stroke="{c}" stroke-width="2.4"/>',
    "json": lambda c: f'<text x="0" y="7" text-anchor="middle" font-size="20" font-weight="700" fill="{c}">{{ }}</text>',
    "stop": lambda c: f'<circle r="16" fill="none" stroke="{c}" stroke-width="2.4"/><path d="M 0 -9 V 0 L 7 5" stroke="{c}" stroke-width="2.4" stroke-linecap="round"/>',
    "crowd": lambda c: f'<circle cx="-8" cy="-8" r="5" fill="{c}"/><circle cx="8" cy="-8" r="5" fill="{c}" fill-opacity="0.7"/><path d="M -16 12 C -16 0 0 0 0 12 M 0 12 C 0 0 16 0 16 12" fill="{c}" fill-opacity="0.5"/>',
}


def card(cid, col, y, title, sub, color, pill, icon, extra=None, w=CW, h=CH, x=None):
    x = COLS[col] if x is None else x
    cards[cid] = (x, y, w, h, color)
    sub_lines = wrap(sub) if extra is None else [sub]
    s = [f'<g id="card-{cid}">',
         f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="none" stroke="{color}" stroke-width="6" opacity="0.45" filter="url(#glow)"/>',
         f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="url(#glass)" stroke="{color}" stroke-width="2.4"/>',
         f'<g transform="translate({x + 44},{y + 46})">{ICONS[icon](color)}</g>',
         f'<text x="{x + 82}" y="{y + 56}" font-size="29" font-weight="700" fill="#F3F6FC">{esc(title)}</text>']
    for i, line in enumerate(sub_lines):
        s.append(f'<text x="{x + 26}" y="{y + 102 + i * 26}" font-size="20" fill="#C9D3E6">{esc(line)}</text>')
    if pill:
        s.append(chip(x + 26, y + h - 54, pill, color))
    if extra:
        s.append(extra(x, y, color))
    s.append("</g>")
    layers["cards"].append("".join(s))


def particles(path_d, length, color, speed=150, spacing=110, reverse=False):
    dur = max(length / speed, 0.6)
    n = max(2, int(length / spacing))
    kp = 'keyPoints="1;0" keyTimes="0;1" calcMode="linear"' if reverse else ""
    out = []
    for k in range(n):
        begin = -dur * k / n
        r = 2.6 if k % 3 else 3.4
        out.append(f'<g><animateMotion dur="{dur:.2f}s" begin="{begin:.2f}s" repeatCount="indefinite" path="{path_d}" {kp}/>'
                   f'<circle r="{r * 2.6:.1f}" fill="{color}" opacity="0.18"/><circle r="{r}" fill="{color}"/></g>')
    return "".join(out)


def anchor(cid, side):
    x, y, w, h, _ = cards[cid]
    return {"right": (x + w, y + h / 2), "left": (x, y + h / 2), "top": (x + w / 2, y), "bottom": (x + w / 2, y + h)}[side]


def edge(src, s_side, dst, d_side, label=None, reply=False):
    (x1, y1), (x2, y2) = anchor(src, s_side), anchor(dst, d_side)
    c1, c2 = cards[src][4], cards[dst][4]
    gid = f"g-{src}-{dst}"
    defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">'
                f'<stop stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>')
    if y1 == y2:
        d = f"M {x1} {y1} H {x2}"
    else:
        my = (y1 + y2) / 2
        d = f"M {x1} {y1} C {x1} {my} {x2} {my} {x2} {y2}"
    length = math.hypot(x2 - x1, y2 - y1)
    e = [f'<path d="{d}" fill="none" stroke="url(#{gid})" stroke-width="7" opacity="0.28" filter="url(#soft)"/>',
         f'<path d="{d}" fill="none" stroke="url(#{gid})" stroke-width="2.6" opacity="0.95"/>',
         particles(d, length, c1, spacing=60 if length < 120 else 110)]
    if reply:
        e.append(particles(d, length, c2, speed=110, spacing=90, reverse=True))
    if label:
        mx, my = (x1 + x2) / 2, y1 + (y2 - y1) * 0.38
        lw = len(label) * 17 * 0.56 + 28
        e.append(f'<rect x="{mx - lw / 2:.0f}" y="{my - 17}" width="{lw:.0f}" height="34" rx="17" fill="#0d1322" stroke="{c1}" stroke-opacity="0.8"/>'
                 f'<text x="{mx}" y="{my + 6}" text-anchor="middle" font-size="17" font-weight="600" fill="{c1}">{esc(label)}</text>')
    layers["edges"].append("".join(e))


def section(y, text, color, right=False):
    x, a = (W - X0 - 4, "end") if right else (X0 + 4, "start")
    layers["fx"].append(f'<text x="{x}" y="{y - 22}" text-anchor="{a}" font-size="21" font-weight="700" letter-spacing="3" fill="{color}">{esc(text)}</text>')


def crowd(x, y, color):
    s = []
    for i in range(12):
        px, py = x + 30 + i * 27, y + 128
        d = round(random.uniform(0, 3), 2)
        s.append(f'<g transform="translate({px},{py})" opacity="0.35"><circle cy="-8" r="5" fill="{color}"/><path d="M -8 9 C -8 -2 8 -2 8 9 Z" fill="{color}"/>'
                 f'<animate attributeName="opacity" values="0.3;1;0.3" dur="3s" begin="{d}s" repeatCount="indefinite"/></g>')
    return "".join(s)


def ci_bar(x, y, color):
    cx, cy = x + 190, y + 128
    return (f'<path d="M {x + 30} {cy} H {x + 350}" stroke="#3a4560" stroke-width="2"/>'
            f'<rect x="{cx - 150}" y="{cy - 8}" width="300" height="16" rx="8" fill="{color}" fill-opacity="0.35" stroke="{color}">'
            f'<animate attributeName="width" values="300;300;70;70" keyTimes="0;0.15;0.75;1" dur="6s" repeatCount="indefinite"/>'
            f'<animate attributeName="x" values="{cx - 150};{cx - 150};{cx - 35};{cx - 35}" keyTimes="0;0.15;0.75;1" dur="6s" repeatCount="indefinite"/></rect>'
            f'<circle cx="{cx}" cy="{cy}" r="5" fill="#F3F6FC"/>'
            f'<text x="{x + 30}" y="{cy + 34}" font-size="15" fill="#9aa7c0">95% CI narrows as interviews arrive</text>')


def memory(x, y, color):
    s = []
    for i in range(36):
        px, py = x + random.uniform(30, 350), y + random.uniform(116, 150)
        col = random.choice([color, CYAN, PEACH, BLUE])
        s.append(f'<circle cx="{px:.0f}" cy="{py:.0f}" r="{random.uniform(1.5, 3.2):.1f}" fill="{col}">'
                 f'<animate attributeName="opacity" values="0.15;1;0.15" dur="{random.uniform(1.8, 4):.1f}s" begin="{random.uniform(0, 3):.1f}s" repeatCount="indefinite"/></circle>')
    return "".join(s)


# Heading
layers["cards"].append(
    f'<rect x="{X0}" y="28" width="{W - 2 * X0}" height="112" rx="20" fill="url(#glass)" stroke="{MINT}" stroke-width="2.4"/>'
    f'<text x="{X0 + 30}" y="78" font-size="36" font-weight="800" fill="#F3F6FC">Political Research Monitor · from a world knowledge graph to a simulated public</text>'
    f'<text x="{X0 + 30}" y="118" font-size="21" fill="#C9D3E6">Particles show which way data moves (motion is illustrative, not timing). Figures are for Mexico, November 2026.</text>')

RA, RB, RC, RD, RE = 230, 580, 930, 1280, 1640
section(RA, "SYSTEM A · WORLD KNOWLEDGE GRAPH", CYAN)
card("registry", 0, RA, "World registry", "ISO 3166 + UN M49: Earth → continents → countries", CYAN, "249 countries", "globe")
card("constitution", 1, RA, "Constitution", "Exa Agent reads the official text into a structure", PEACH, "Exa /agent/runs", "search")
card("review", 2, RA, "Structure review", "an article cited for every tier, body and office", YELLOW, "human approval", "shield")
card("holders", 3, RA, "Officeholders", "kept only if the fetched page names person and office", PEACH, "Exa /contents", "check")
card("graph", 4, RA, "Knowledge graph", "country → government → office → person", LAV, "person:MX:… IDs", "graph")
for a, b in [("registry", "constitution"), ("constitution", "review"), ("review", "holders"), ("holders", "graph")]:
    edge(a, "right", b, "left")

section(RB, "SYSTEM B · WHAT POLITICIANS SAY", PINK)
card("capture", 4, RB, "Post capture", "Instagram and TikTok posts of tracked politicians", PINK, "3,742 posts", "phone")
card("transcribe", 3, RB, "Transcripts", "speech and captions into timed segments", PINK, "227,626 segments", "wave")
card("extract", 2, RB, "Claim extraction", "Claude, sharded across Modal containers", BLUE, "18,727 claims · 61 people", "doc")
card("storylines", 1, RB, "Storylines", "one claim, its restatements and counter-claims", BLUE, "sl-… IDs", "layers")
card("neon", 0, RB, "Neon Postgres", "posts, claims and propositions shared by B, C, D", TEAL, "one evidence trail", "db")
edge("graph", "bottom", "capture", "top", "who to watch")
for a, b in [("capture", "transcribe"), ("transcribe", "extract"), ("extract", "storylines"), ("storylines", "neon")]:
    edge(a, "left", b, "right")

section(RC, "SYSTEM C · EVIDENCE FROM THE WEB", PEACH, right=True)
card("plan", 0, RC, "Query plan", "Claude plans searches for each proposition", PEACH, "16,484 propositions", "plan")
card("exa", 1, RC, "Exa search", "finds sources the model doesn't already know", PEACH, "Exa /search", "search")
card("fetch", 2, RC, "Publisher fetch", "pages read from the source, robots and licence kept", CYAN, "10,880 documents", "doc")
card("stance", 3, RC, "Stance judge", "supports or contradicts, with a quote checked on the page", YELLOW, "9,995 stance links", "scale")
card("pastdev", 4, RC, "past.dev memory", "claims + evidence shared by agents", LAV, None, "cloud", extra=memory)
edge("neon", "bottom", "plan", "top", "propositions")
edge("plan", "right", "exa", "left")
edge("exa", "right", "fetch", "left", reply=True)
edge("fetch", "right", "stance", "left")
edge("stance", "right", "pastdev", "left")

section(RD, "SYSTEM D · SIMULATED PEOPLE", MINT)
card("import", 4, RD, "Storyline import", "real statements and their comments, reversible", TEAL, "16 storylines", "import")
card("strata", 3, RD, "Population", "region × age × gender strata and personas", MINT, "12 strata", "people")
card("fleet", 2, RD, "Respondents", "LLM personas answer a survey script", PINK, None, "crowd", extra=crowd)
card("stopping", 1, RD, "Stopping rule", "converged · decisive · capped", YELLOW, None, "stop", extra=ci_bar)
card("opinion", 0, RD, "opinion.json", "belief share + CI, polled vs social sentiment, gap", BLUE, "1,375 interviews", "json")
edge("pastdev", "bottom", "import", "top", "storylines + real comments")
for a, b in [("import", "strata"), ("strata", "fleet"), ("fleet", "stopping"), ("stopping", "opinion")]:
    edge(a, "left", b, "right")
layers["fx"].append(f'<text x="{COLS[2] + 26}" y="{RD + CH + 34}" font-size="17" fill="#9aa7c0">up to 96 interviews in parallel · Modal fleet</text>')

section(RE, "PUBLIC EXPLORER · LIVE ON MODAL", CYAN, right=True)
EX_W, EX_H = W - 2 * X0, 250
cards["explorer"] = (X0, RE, EX_W, EX_H, CYAN)
steps = ["Globe", "Country", "Government", "Politician", "Claims", "Evidence", "Simulated opinion"]
cols = [CYAN, CYAN, YELLOW, LAV, BLUE, PEACH, PINK]
ex = [f'<rect x="{X0}" y="{RE}" width="{EX_W}" height="{EX_H}" rx="20" fill="none" stroke="{CYAN}" stroke-width="6" opacity="0.45" filter="url(#glow)"/>',
      f'<rect x="{X0}" y="{RE}" width="{EX_W}" height="{EX_H}" rx="20" fill="url(#glass)" stroke="{CYAN}" stroke-width="2.4"/>',
      f'<g transform="translate({X0 + 44},{RE + 46})">{ICONS["globe"](CYAN)}</g>',
      f'<text x="{X0 + 82}" y="{RE + 56}" font-size="29" font-weight="700" fill="#F3F6FC">Political World Explorer</text>',
      f'<text x="{X0 + 470}" y="{RE + 55}" font-size="20" fill="#C9D3E6">One page per politician joins all four systems. Static exports on a Modal volume; no secrets in the public app.</text>']
sx, sw, sgap = X0 + 40, 262, 40
n = len(steps)
for i, (t, c) in enumerate(zip(steps, cols)):
    x = sx + i * (sw + sgap)
    ex.append(f'<rect x="{x}" y="{RE + 110}" width="{sw}" height="84" rx="16" fill="{c}" fill-opacity="0.12" stroke="{c}" stroke-width="2">'
              f'<animate attributeName="fill-opacity" values="0.1;0.1;0.5;0.1;0.1" keyTimes="0;{i / n:.3f};{(i + 0.5) / n:.3f};{(i + 1) / n:.3f};1" dur="8s" repeatCount="indefinite"/></rect>'
              f'<text x="{x + sw / 2}" y="{RE + 160}" text-anchor="middle" font-size="24" font-weight="700" fill="{c}">{esc(t)}</text>')
    if i < n - 1:
        ax = x + sw
        ex.append(f'<path d="M {ax + 8} {RE + 152} H {ax + sgap - 8}" stroke="#5b6886" stroke-width="2.4"/><path d="M {ax + sgap - 14} {RE + 145} L {ax + sgap - 6} {RE + 152} L {ax + sgap - 14} {RE + 159}" fill="none" stroke="#5b6886" stroke-width="2.4"/>')
ex.append(f'<text x="{X0 + 40}" y="{RE + 228}" font-size="17" fill="#9aa7c0">Graph from A · claims and storylines from B · evidence from C · simulated opinion from D, always labelled "simulated respondents, not a real poll".</text>')
layers["cards"].append("".join(ex))
cards["explorer_in"] = (COLS[0], RE, CW, EX_H, CYAN)
edge("opinion", "bottom", "explorer_in", "top", "publish")

FOOT = RE + EX_H + 60
for i, line in enumerate(["Exa: System A (agent runs, contents, search, monitors) and System C (evidence search) · past.dev: C writes claims and evidence; B and the explorer read them",
                          "Modal: B/C fan-out, D respondent fleet, public explorer · OpenAI: D respondents and sentiment · Claude: B/C claim extraction and stance"]):
    layers["fx"].append(f'<text x="{X0 + 4}" y="{FOOT + i * 28}" font-size="18" fill="#7f8aa3">{esc(line)}</text>')

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
       "<defs>",
       '<radialGradient id="bg" cx="0.3" cy="0.1" r="1.2"><stop stop-color="#18213a"/><stop offset="1" stop-color="#090d18"/></radialGradient>',
       '<linearGradient id="glass" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#2a3146" stop-opacity="0.95"/><stop offset="1" stop-color="#161b2b" stop-opacity="0.95"/></linearGradient>',
       '<filter id="glow" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="7"/></filter>',
       '<filter id="soft" x="-5%" y="-50%" width="110%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>',
       *defs, "</defs>",
       f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
       *layers["edges"], *layers["cards"], *layers["fx"], "</svg>"]
OUT.write_text("\n".join(svg), encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
