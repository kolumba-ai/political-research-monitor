/* Political Knowledge Graph UI — React (UMD + htm, no build step) + Cytoscape.js.
   Lazy, progressive expansion: nothing is loaded until its parent is clicked. */
const { useState, useEffect, useRef, useCallback } = React;
const html = htm.bind(React.createElement);

const COLORS = {
  Earth: "#2f5bd3", Continent: "#5b7fe0", Country: "#0e7c86", DemocracyAssessment: "#a86400",
  ContactRule: "#b45309", GovernmentLevel: "#6d28d9", GovernmentBody: "#8b5cf6", PoliticalOffice: "#be185d",
  Person: "#1f7a4d", PoliticalParty: "#64748b", Term: "#94a3b8", Source: "#9ca3af",
};
const api = (p) => fetch(p).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
const enc = encodeURIComponent;

function nodeLabel(n) {
  let t = n.name || n.id;
  if (n._seat) t = `${n._seat} · ${t}`;
  if (n.label === "Country" && n.regime_class) t += ` · ${n.regime_class}`;
  if (n.label === "PoliticalOffice" && n.is_elected === true) t += " • elected";
  if (n.child_count) t += `  [${n.child_count}]`;
  return t.length > 60 ? t.slice(0, 58) + "…" : t;
}

// Grid layout constants: each level wraps into rows instead of one very wide line.
const COL_W = 165, ROW_H = 72, LEVEL_GAP = 70;

function App() {
  const cyRef = useRef(null), boxRef = useRef(null);
  // Accordion state: openPath[d] is the single open node at depth d; levels[d] are the nodes shown at depth d.
  const openPath = useRef([]), levels = useRef([]);
  const [opts, setOpts] = useState({ include_single_source: false, include_former: false });
  const optsRef = useRef(opts); optsRef.current = opts;
  const [detail, setDetail] = useState(null);
  const [meta, setMeta] = useState(null);
  const [q, setQ] = useState(""); const [hits, setHits] = useState([]);

  const fetchKids = async (id) => {
    const o = optsRef.current;
    const res = await api(`/api/nodes/${enc(id)}/children?include_single_source=${o.include_single_source}` +
      `&include_former=${o.include_former}`);
    // Evidence documents live in the inspector, not in the graph.
    const kids = res.children.filter((c) => c.node.label !== "Source")
      .map((c) => ({ ...c.node, _former: c.edge.current === false, _seat: c.edge.constituency_code }));
    return { node: res.node, kids };
  };

  const render = useCallback((focusDepth) => {
    const cy = cyRef.current;
    const w = Math.max(boxRef.current.clientWidth, 600);
    const maxCols = Math.max(5, Math.floor(w / COL_W));
    const els = [];
    let y = 0;
    levels.current.forEach((row, d) => {
      const cols = Math.min(row.length, maxCols);
      row.forEach((n, i) => {
        const r = Math.floor(i / cols), c = i % cols;
        const inRow = Math.min(cols, row.length - r * cols);
        els.push({ group: "nodes", position: { x: (c - (inRow - 1) / 2) * COL_W, y: y + r * ROW_H },
          classes: openPath.current[d] === n.id ? "open" : "",
          data: { ...n, display: nodeLabel(n), color: COLORS[n.label] || "#9ca3af", depth: d } });
        if (d > 0) els.push({ group: "edges", data: { id: `${openPath.current[d - 1]}->${n.id}`,
          source: openPath.current[d - 1], target: n.id, former: !!n._former } });
      });
      y += Math.ceil(row.length / Math.max(cols, 1)) * ROW_H + LEVEL_GAP;
    });
    cy.elements().remove();
    cy.add(els);
    cy.resize();
    // Fit the newest level together with its open parent so the current step is always readable.
    const d = focusDepth ?? levels.current.length - 1;
    const focus = cy.nodes().filter((n) => n.data("depth") === d || n.id() === openPath.current[d - 1]);
    cy.fit(focus.nonempty() ? focus : cy.nodes(), 50);
    if (cy.zoom() > 1.4) { cy.zoom(1.4); cy.center(focus.nonempty() ? focus : cy.nodes()); }
  }, []);

  const inspect = useCallback(async (id) => {
    try { setDetail(await api(`/api/nodes/${enc(id)}`)); } catch { setDetail(null); }
  }, []);

  // Open node `id` at depth d: closes any sibling (and its descendants) that was open at the same depth.
  const open = useCallback(async (id, d) => {
    const res = await fetchKids(id);
    openPath.current = [...openPath.current.slice(0, d), id];
    levels.current = [...levels.current.slice(0, d + 1)];
    if (res.kids.length) levels.current.push(res.kids);
  }, []);

  const closeAt = (d) => {
    openPath.current = openPath.current.slice(0, d);
    levels.current = levels.current.slice(0, d + 1);
  };

  const reveal = useCallback(async (path) => {
    const root = await fetchKids("earth");
    levels.current = [[root.node]]; openPath.current = [];
    for (let d = 0; d < path.length; d++) await open(path[d], d);  // also open the target itself
    render();
    const t = cyRef.current.getElementById(path[path.length - 1]);
    cyRef.current.$(":selected").unselect(); t.select();
    inspect(path[path.length - 1]);
  }, [open, render, inspect]);

  useEffect(() => {
    const cy = cytoscape({
      container: boxRef.current, wheelSensitivity: 0.25, minZoom: 0.05, maxZoom: 2.5, autoungrabify: true,
      style: [
        { selector: "node", style: { "background-color": "data(color)", label: "data(display)", "font-size": 10,
          color: getComputedStyle(document.body).color, "text-valign": "bottom", "text-margin-y": 4,
          "text-wrap": "wrap", "text-max-width": COL_W - 20, width: 16, height: 16 } },
        { selector: 'node[label="Earth"]', style: { width: 36, height: 36, "font-size": 13, "font-weight": 700 } },
        { selector: 'node[label="Person"]', style: { shape: "round-rectangle", width: 22 } },
        { selector: 'node[label="Person"][evidence_status="single_source"]', style: { opacity: 0.6 } },
        { selector: "node.open", style: { "border-width": 4, "border-color": "#2f5bd3", "font-weight": 700 } },
        { selector: "node:selected", style: { "border-width": 4, "border-color": "#f59e0b" } },
        { selector: "edge", style: { width: 1, "line-color": "#cfcec8", "curve-style": "straight", opacity: 0.7 } },
        { selector: "edge[?former]", style: { "line-style": "dashed" } },
      ],
    });
    cyRef.current = cy;
    cy.on("tap", "node", async (evt) => {
      const n = evt.target, id = n.id(), d = n.data("depth");
      inspect(id);
      if (openPath.current[d] === id) closeAt(d); else await open(id, d);
      render();
    });
    (async () => {
      const deep = decodeURIComponent((location.hash.match(/^#node=(.+)$/) || [])[1] || "");
      if (deep) {
        try { const d = await api(`/api/nodes/${enc(deep)}`); await reveal(d.path.map((p) => p.id)); }
        catch { await reveal(["earth", "earth"]); }
      } else {
        const root = await fetchKids("earth");
        levels.current = [[root.node]];
        await open("earth", 0); render(); inspect("earth");
      }
      try { setMeta(await api("/api/meta")); } catch {}
    })();
    const onResize = () => render(levels.current.length - 1);
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); cy.destroy(); };
  }, []);

  // Visibility toggles: refetch every open level, keeping the open path where its nodes still exist.
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) { firstRun.current = false; return; }
    (async () => {
      const path = [...openPath.current];
      const root = await fetchKids("earth");
      levels.current = [[root.node]]; openPath.current = [];
      for (let d = 0; d < path.length; d++) {
        if (!levels.current[d] || !levels.current[d].some((n) => n.id === path[d])) break;
        await open(path[d], d);
      }
      render();
    })();
  }, [opts.include_single_source, opts.include_former]);

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = setTimeout(async () => { try { setHits((await api(`/api/search?q=${enc(q)}`)).results); } catch {} }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const lr = meta && meta.last_run;
  return html`
  <div class="app">
    <header>
      <h1>Political Knowledge Graph</h1>
      <div class="search">
        <input placeholder="Search country, politician, office, party…" value=${q} onInput=${(e) => setQ(e.target.value)} />
        ${hits.length > 0 && html`<div class="results">${hits.map((h) => html`
          <div key=${h.id} onClick=${() => { setQ(""); setHits([]); reveal(h.path); }}>
            <span class="badge">${h.label}</span>${h.name}
          </div>`)}</div>`}
      </div>
      <div class="toggles">
        <label><input type="checkbox" checked=${opts.include_single_source}
                 onChange=${(e) => setOpts({ ...opts, include_single_source: e.target.checked })} /> Single-source claims</label>
        <label><input type="checkbox" checked=${opts.include_former}
                 onChange=${(e) => setOpts({ ...opts, include_former: e.target.checked })} /> Former holders</label>
      </div>
      <div class="meta">${lr ? html`Last run ${lr.country} · ${lr.mode} · ${lr.status}<br />${lr.finished_at || lr.started_at}` : "No pipeline run yet"}</div>
    </header>
    <main>
      <div ref=${boxRef} id="cy"></div>
      <aside>
        ${detail ? html`<${Inspector} d=${detail} onNav=${(path) => reveal(path)}
                         onCollapse=${() => { const i = openPath.current.indexOf(detail.node.id); if (i >= 0) { closeAt(i); render(); } }} />`
                 : html`<p>Click a node to inspect it and expand its children.</p>`}
        <div class="legend">${Object.entries(COLORS).filter(([k]) => !["Term", "Source"].includes(k))
          .map(([k, c]) => html`<span key=${k} style=${{ "--c": c }}>${k}</span>`)}</div>
      </aside>
    </main>
  </div>`;
}

const HIDDEN = new Set(["id", "label", "name", "democracy_gate", "contact_gate", "regime_by_source", "regime_group",
  "regime_class", "regime_basis", "regime_sources_agree", "eligible", "dimensions", "laws", "search",
  "aliases", "keywords", "checked_urls", "agent_citations", "review", "gate", "status_reasons", "relevant_policy"]);

function Regime({ n }) {
  const src = n.regime_by_source || {};
  return html`<div>
    <h3>Regime classification</h3>
    <div class="gate"><b>${n.regime_group || "Unclassified"}</b>${n.regime_class && n.regime_class !== n.regime_group ? html` — ${n.regime_class}` : ""}
      <div class="snip">Basis: ${n.regime_basis || "—"}${n.regime_sources_agree === false ? " · indices disagree (all shown below)" : ""}</div>
      ${Object.keys(src).length > 0 && html`<table><thead><tr><th>Index</th><th>Class</th><th>Group</th><th>Year</th><th>Evidence</th></tr></thead>
        <tbody>${Object.entries(src).map(([k, v]) => html`<tr key=${k}><td>${k}</td><td>${v.class}</td><td>${v.group}</td>
          <td>${v.year || "—"}</td><td>${v.evidence_url ? html`<a href=${v.evidence_url} target="_blank" rel="noreferrer">${v.verification === "verified" ? "publisher" : "compilation"}</a>` : "—"}</td></tr>`)}</tbody></table>`}
    </div></div>`;
}

function Status({ s }) { return s ? html`<span class=${"status " + s}>${s}</span>` : null; }

function Gate({ title, g }) {
  if (!g) return html`<div class="gate"><b>${title}</b>: <span class="status unknown">not researched</span></div>`;
  return html`<div class="gate"><b>${title}</b>: <${Status} s=${g.status} />
    ${g.per_source && Object.keys(g.per_source).length > 0 &&
      html`<ul>${Object.entries(g.per_source).map(([k, v]) => html`<li key=${k}><b>${k}</b>: ${v}</li>`)}</ul>`}
    ${g.reasons && g.reasons.length > 0 && html`<ul>${g.reasons.map((r, i) => html`<li key=${i}>${r}</li>`)}</ul>`}
  </div>`;
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : x)).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  const s = String(v);
  return /^https?:\/\//.test(s) ? html`<a href=${s} target="_blank" rel="noreferrer">${s}</a>` : s;
}

function Inspector({ d, onNav, onCollapse }) {
  const n = d.node;
  const props = Object.entries(n).filter(([k, v]) => !HIDDEN.has(k) && v !== null && v !== "" &&
                                                     !(Array.isArray(v) && v.length === 0));
  const seen = new Set();
  const sources = d.sources.filter((s) => (seen.has(s.url) ? false : seen.add(s.url)));
  return html`<div>
    <div class="crumbs">${d.path.map((p, i) => html`<span key=${p.id}>${i ? " › " : ""}
      <a onClick=${() => onNav(d.path.slice(0, i + 1).map((x) => x.id))}>${p.name}</a></span>`)}</div>
    <h2>${n.name}</h2>
    <span class="badge">${n.label}</span>
    ${n.evidence_status && html`<span class="badge">evidence: <${Status} s=${n.evidence_status} /></span>`}
    ${n.last_verified && html`<span class="badge">last verified ${n.last_verified}</span>`}
    ${n.label !== "Earth" && html` <button onClick=${onCollapse}>Collapse</button>`}

    ${n.label === "Country" && html`<${Regime} n=${n} />`}
    ${n.label === "Country" && n.democracy_gate && html`
      <h3>Policy checks (informational)</h3>
      <${Gate} title="Democracy policy" g=${n.democracy_gate} />
      <${Gate} title="Political contact legality" g=${n.contact_gate} />
      <p class="snip">Recorded for reference only; they do not hide or limit any data. Not legal advice — the system never contacts anyone.</p>`}
    ${n.label === "Country" && n.research_status === "not_researched" &&
      html`<div class="notice">Government structure not researched yet. Run <code>python -m polgraph build --country ${n.iso2}</code>.</div>`}

    ${n.label === "ContactRule" && html`
      <div class="notice">${n.disclaimer}</div>
      <p>Aggregate status: <${Status} s=${n.status} /> · review: <${Status} s=${n.review_status} /></p>
      ${n.status_reasons && n.status_reasons.length > 0 && html`<ul>${n.status_reasons.map((r, i) => html`<li key=${i}>${r}</li>`)}</ul>`}
      <h3>Researched rules (${(n.dimensions || []).length})</h3>
      <table><thead><tr><th>Purpose</th><th>Channel</th><th>Sender</th><th>Recipient</th><th>Status</th><th>Basis</th></tr></thead>
      <tbody>${(n.dimensions || []).map((r, i) => html`<tr key=${i}><td>${r.purpose}</td><td>${r.channel}</td>
        <td>${r.sender}</td><td>${r.recipient}</td><td><${Status} s=${r.status} />${r.evidence_fetched ? "" : " (no evidence)"}</td>
        <td>${r.legal_basis || "—"}${r.conditions ? html`<div class="snip">${r.conditions}</div>` : ""}</td></tr>`)}</tbody></table>
      <h3>Laws considered</h3>
      <ul>${(n.laws || []).map((l, i) => html`<li key=${i}><b>${l.category}</b>: ${l.url ? html`<a href=${l.url} target="_blank" rel="noreferrer">${l.name}</a>` : l.name}</li>`)}</ul>`}

    ${d.holders && html`<h3>Officeholders</h3>${d.holders.length === 0 ? html`<p>None verified.</p>` :
      d.holders.map((h) => html`<div key=${h.person.id} class="src"><a onClick=${() => onNav([...d.path.map((p) => p.id), h.person.id])}>${h.person.name}</a>
        — ${h.edge.current ? "current" : "former"} · <${Status} s=${h.edge.evidence_status} /> · since ${h.edge.start || "?"}</div>`)}`}
    ${d.offices && html`<h3>Offices</h3>${d.offices.map((o) => html`<div key=${o.office.id} class="src">${o.office.name}
        — ${o.edge.current ? "current" : "former"} · <${Status} s=${o.edge.evidence_status} /> · confidence ${o.edge.confidence ?? "—"}
        · term ${o.edge.start || "?"} → ${o.edge.end || "?"}</div>`)}
      ${d.parties && d.parties.length > 0 && html`<h3>Party</h3>${d.parties.map((p) => html`<div key=${p.party.id}>${p.party.name}${p.edge.coalition ? ` (coalition: ${p.edge.coalition})` : ""}</div>`)}`}`}

    <h3>Attributes</h3>
    <div class="kv">${props.map(([k, v]) => html`<${React.Fragment} key=${k}><div>${k}</div><div>${fmt(v)}</div><//>`)}</div>

    <h3>Evidence (${sources.length})</h3>
    ${sources.length === 0 ? html`<p class="snip">No linked sources for this node.</p>` : sources.map((s) => html`
      <div class="src" key=${s.id}>
        <a href=${s.url} target="_blank" rel="noreferrer">${s.name || s.url}</a>
        <div class="snip">${s.domain} · tier ${s.authority_tier} · via ${(s.edge && s.edge.via) || s.retrieved_via || "?"} · retrieved ${s.retrieved_at || "?"}</div>
        ${s.edge && s.edge.snippet && html`<div class="snip">“${s.edge.snippet}”</div>`}
      </div>`)}
  </div>`;
}

ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
