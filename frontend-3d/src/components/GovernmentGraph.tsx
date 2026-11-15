import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import ELK from "elkjs/lib/elk.bundled.js";
import { ChevronLeft, ChevronRight, Focus, LoaderCircle, Minus, Network, Plus, RotateCcw, UserRound } from "lucide-react";
import { Badge, Button, ExtLink } from "./ui";
import { PoliticianSocialClaims } from "./SocialClaims";
import { fmtDate } from "../lib/presentation";
import { detailUrl, graphGet, kindName, NODE_COLORS, valueText, visibleChildren,
  type GraphChild, type GraphChildren, type GraphDetail } from "../lib/graph";

interface Props {
  iso2: string;
  countryName: string;
  lowMotion: boolean;
  initialNode?: string;
  onNodeChange?: (id: string) => void;
}
interface Level { parent: string | null; children: GraphChild[]; page: number }
interface TreeState { levels: Level[]; openPath: string[] }
const PAGE_SIZE = 6;
const elk = new ELK();
const summaryFields = ["article", "constitutional_basis", "constitutional_article", "branch", "tier", "jurisdiction",
  "selection_method", "is_elected", "seats", "seat_count", "seat_types", "term_years", "term_start", "term_end",
  "description", "notes", "status"];

export default function GovernmentGraph({ iso2, countryName, initialNode, onNodeChange }: Props) {
  const root = `country:${iso2}`;
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const cache = useRef(new Map<string, GraphChildren>());
  const treeRef = useRef<TreeState>({ levels: [], openPath: [] });
  const [tree, setTree] = useState<TreeState>(treeRef.current);
  const [selected, setSelected] = useState(root);
  const selectedRef = useRef(root);
  const [detail, setDetail] = useState<GraphDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [includeSingle, setIncludeSingle] = useState(false);
  const [includeFormer, setIncludeFormer] = useState(false);
  const [detailVersion, setDetailVersion] = useState(0);
  const request = useRef(0);
  const layoutRequest = useRef(0);
  const handledNavigation = useRef<string | null>(null);
  const onChangeRef = useRef(onNodeChange);
  onChangeRef.current = onNodeChange;

  const commit = useCallback((next: TreeState) => {
    treeRef.current = next;
    setTree(next);
  }, []);

  const select = useCallback((id: string) => {
    selectedRef.current = id;
    setSelected(id);
    onChangeRef.current?.(id);
  }, []);

  const fetchChildren = useCallback(async (id: string): Promise<GraphChildren> => {
    const key = `${id}:${includeSingle}:${includeFormer}`;
    const saved = cache.current.get(key);
    if (saved) return saved;
    const response = await graphGet<GraphChildren>(`${detailUrl(id)}/children?include_single_source=${includeSingle}&include_former=${includeFormer}`);
    const result = { ...response, children: visibleChildren(response) };
    cache.current.set(key, result);
    return result;
  }, [includeSingle, includeFormer]);

  const open = useCallback(async (id: string, depth: number) => {
    const ticket = ++request.current;
    select(id);
    setError(null);
    const current = treeRef.current;
    if (current.openPath[depth] === id) {
      commit({ levels: current.levels.slice(0, depth + 1), openPath: current.openPath.slice(0, depth) });
      setBusy(false);
      return;
    }
    setBusy(true);
    try {
      const result = await fetchChildren(id);
      if (request.current !== ticket) return;
      const levels = current.levels.slice(0, depth + 1).map((level, index) => index === depth ? {
        ...level, page: Math.floor(Math.max(0, level.children.findIndex((c) => c.node.id === id)) / PAGE_SIZE),
      } : level);
      if (result.children.length) levels.push({ parent: id, children: result.children, page: 0 });
      commit({ levels, openPath: [...current.openPath.slice(0, depth), id] });
    } catch (e) {
      if (request.current === ticket) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (request.current === ticket) setBusy(false);
    }
  }, [commit, fetchChildren, select]);
  const openRef = useRef(open);
  openRef.current = open;

  const reveal = useCallback(async (id: string) => {
    const ticket = ++request.current;
    setBusy(true);
    setError(null);
    try {
      const target = await graphGet<GraphDetail>(detailUrl(id));
      const start = target.path.findIndex((n) => n.id === root);
      if (start < 0) throw new Error("This record belongs to another country. Select its country first.");
      const path = target.path.slice(start);
      const first = await fetchChildren(root);
      const next: TreeState = { levels: [{ parent: null, children: [{ node: first.node, edge: {} }], page: 0 }], openPath: [] };
      for (let depth = 0; depth < path.length; depth++) {
        const result = await fetchChildren(path[depth].id);
        if (request.current !== ticket) return;
        const level = next.levels[depth];
        const index = level?.children.findIndex((c) => c.node.id === path[depth].id) ?? -1;
        if (index < 0) throw new Error("This record is hidden by the current evidence or former-holder filters.");
        level.page = Math.floor(index / PAGE_SIZE);
        next.openPath.push(path[depth].id);
        if (result.children.length) next.levels.push({ parent: path[depth].id, children: result.children, page: 0 });
      }
      if (request.current !== ticket) return;
      commit(next);
      select(id);
    } catch (e) {
      if (request.current === ticket) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (request.current === ticket) setBusy(false);
    }
  }, [root, fetchChildren, commit, select]);
  const revealRef = useRef(reveal);
  revealRef.current = reveal;

  useEffect(() => {
    const ticket = ++request.current;
    cache.current.clear();
    setBusy(true);
    setError(null);
    selectedRef.current = root;
    setSelected(root);
    commit({ levels: [], openPath: [] });
    fetchChildren(root).then((result) => {
      if (request.current !== ticket) return;
      commit({ levels: [{ parent: null, children: [{ node: result.node, edge: {} }], page: 0 },
        ...(result.children.length ? [{ parent: root, children: result.children, page: 0 }] : [])], openPath: [root] });
      setBusy(false);
    }).catch((e: unknown) => {
      if (request.current !== ticket) return;
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    });
    // Invalidate the current generation so late responses cannot overwrite another country.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => { ++request.current; };
    // Filter changes reload the visible hierarchy using the same root.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root, fetchChildren, commit]);

  const rootReady = tree.levels[0]?.children[0]?.node.id === root;
  useEffect(() => {
    if (!initialNode) { handledNavigation.current = null; return; }
    const navigation = `${root}:${initialNode}`;
    if (!rootReady || handledNavigation.current === navigation) return;
    // A shared-link/search target is a navigation request, not a persistent selection constraint.
    // Consume it once so later filter reloads or Reset cannot reopen the original target.
    handledNavigation.current = navigation;
    if (initialNode !== selectedRef.current) void revealRef.current(initialNode);
  }, [initialNode, root, rootReady]);

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setDetailError(null);
    graphGet<GraphDetail>(detailUrl(selected), controller.signal).then(setDetail).catch((e: unknown) => {
      if (!controller.signal.aborted) setDetailError(e instanceof Error ? e.message : String(e));
    });
    return () => controller.abort();
  }, [selected, detailVersion]);

  const fit = useCallback((all = false) => {
    const graph = cy.current;
    if (!graph || graph.destroyed() || !graph.nodes().length) return;
    const deepest = treeRef.current.levels.length - 1;
    const active = treeRef.current.openPath[deepest - 1];
    const focus = all ? graph.nodes() : graph.nodes().filter((n) => n.data("depth") === deepest || n.id() === active);
    graph.resize();
    graph.fit(focus.length ? focus : graph.nodes(), 28);
    if (graph.zoom() > 1) { graph.zoom(1); graph.center(focus.length ? focus : graph.nodes()); }
  }, []);

  useEffect(() => {
    if (!box.current) return;
    const graph = cytoscape({ container: box.current, minZoom: 0.08, maxZoom: 2.2, autoungrabify: true,
      wheelSensitivity: 0.18, layout: { name: "preset" }, style: [
        { selector: "node", style: { shape: "round-rectangle", width: 178, height: 64, "background-color": "#ffffff",
          "border-color": "data(color)", "border-width": 1.5, color: "#111827", label: "data(display)",
          "font-size": 11, "font-family": "system-ui, sans-serif", "text-wrap": "wrap", "text-max-width": "158px",
          "text-valign": "center", "text-halign": "center", "text-overflow-wrap": "whitespace" } },
        { selector: "node.open", style: { "background-color": "#eef2ff", "border-width": 2.5 } },
        { selector: "node:selected", style: { "border-color": "#3557e6", "border-width": 3 } },
        { selector: "edge", style: { width: 1.2, "line-color": "#b6bfd1", "target-arrow-color": "#b6bfd1",
          "target-arrow-shape": "triangle", "arrow-scale": 0.65, "curve-style": "taxi", "taxi-direction": "rightward", "taxi-turn": "50%" } },
        { selector: "edge.former", style: { "line-style": "dashed" } },
      ] });
    cy.current = graph;
    graph.on("tap", "node", (event) => { void openRef.current(event.target.id(), event.target.data("depth")); });
    const observer = new ResizeObserver(() => fit());
    observer.observe(box.current);
    // These are generation and instance refs, rather than DOM refs captured by the effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    return () => { observer.disconnect(); ++layoutRequest.current; graph.destroy(); cy.current = null; };
  }, [fit]);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    const ticket = ++layoutRequest.current;
    const elements: ElementDefinition[] = [];
    const seen = new Set<string>();
    tree.levels.forEach((level, depth) => {
      level.children.slice(level.page * PAGE_SIZE, (level.page + 1) * PAGE_SIZE).forEach(({ node, edge }) => {
        if (seen.has(node.id)) return;
        seen.add(node.id);
        const title = node.name || node.id;
        const expanded = tree.levels[depth + 1];
        const childCount = expanded?.parent === node.id ? expanded.children.length : node.child_count;
        const count = childCount ? ` · ${childCount}` : "";
        const seat = edge.constituency_code ? `${edge.constituency_code} · ` : "";
        elements.push({ group: "nodes", classes: tree.openPath[depth] === node.id ? "open" : "", data: {
          id: node.id, depth, color: NODE_COLORS[node.label] || "#687794",
          display: `${seat}${title.length > 90 ? title.slice(0, 87) + "…" : title}\n${kindName(node.label)}${count}` } });
        if (level.parent) elements.push({ group: "edges", classes: edge.current === false ? "former" : "", data: {
          id: `${level.parent}->${node.id}`, source: level.parent, target: node.id } });
      });
    });
    graph.batch(() => { graph.elements().remove(); graph.add(elements); });
    if (box.current) {
      box.current.dataset.nodeCount = String(graph.nodes().length);
      box.current.dataset.layoutReady = "false";
    }
    if (!elements.length) return;
    void elk.layout({ id: "graph", layoutOptions: { "elk.algorithm": "layered", "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "20", "elk.layered.spacing.nodeNodeBetweenLayers": "70",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES" },
      children: graph.nodes().map((n) => ({ id: n.id(), width: 178, height: 64 })),
      edges: graph.edges().map((e) => ({ id: e.id(), sources: [e.source().id()], targets: [e.target().id()] })),
    }).then((layout) => {
      if (ticket !== layoutRequest.current || graph.destroyed()) return;
      graph.batch(() => { layout.children?.forEach((n) => {
        graph.getElementById(n.id).position({ x: (n.x || 0) + 89, y: (n.y || 0) + 32 });
      }); });
      graph.getElementById(selectedRef.current).select();
      fit();
      graph.forceRender();
      if (box.current) box.current.dataset.layoutReady = "true";
    }).catch(() => {
      if (ticket !== layoutRequest.current || graph.destroyed()) return;
      graph.layout({ name: "breadthfirst", directed: true, padding: 28, spacingFactor: 1.3, animate: false }).run();
      fit();
      if (box.current) box.current.dataset.layoutReady = "true";
    });
  }, [tree, fit]);

  useEffect(() => {
    const graph = cy.current;
    if (!graph) return;
    graph.$(":selected").unselect();
    graph.getElementById(selected).select();
  }, [selected]);

  const selectedLevel = tree.levels.findIndex((l) => l.children.some((c) => c.node.id === selected));
  const childLevel = tree.levels[selectedLevel + 1]?.parent === selected ? tree.levels[selectedLevel + 1] : undefined;
  const sources = useMemo(() => [...new Map((detail?.sources || []).map((s) => [s.url, s])).values()], [detail]);
  const crumbStart = detail?.path.findIndex((n) => n.id === root) ?? 0;
  const breadcrumbs = detail?.path.slice(Math.max(crumbStart, 0)) || [];
  const jurisdiction = detail?.path.findLast((n) => n.label === "Country" || n.label === "GovernmentLevel");
  const siblings = tree.levels[selectedLevel]?.children || [];
  const currentLevel = tree.levels.at(-1);
  const pageCount = Math.ceil((currentLevel?.children.length || 0) / PAGE_SIZE);

  const changePage = (page: number) => {
    const next = { ...treeRef.current, levels: treeRef.current.levels.map((l, i, levels) => i === levels.length - 1 ? { ...l, page } : l) };
    commit(next);
  };

  return <div className="flex min-h-0 flex-1 flex-col" data-testid="government-graph">
    <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
      <Network className="size-4 text-accent" /><h2 className="mr-auto text-sm font-medium">{countryName} · Government</h2>
      <Button variant="ghost" title="Zoom out" aria-label="Zoom out" onClick={() => cy.current?.zoom((cy.current?.zoom() || 1) / 1.2)}><Minus className="size-4" /></Button>
      <Button variant="ghost" title="Zoom in" aria-label="Zoom in" onClick={() => cy.current?.zoom((cy.current?.zoom() || 1) * 1.2)}><Plus className="size-4" /></Button>
      <Button variant="ghost" title="Fit all visible nodes" aria-label="Fit graph" onClick={() => fit(true)}><Focus className="size-4" /></Button>
      <Button variant="ghost" title="Return to country" aria-label="Reset graph" onClick={() => void reveal(root)}><RotateCcw className="size-4" /></Button>
    </div>
    <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-line px-3 py-2 text-[11px] text-muted">
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={includeSingle} onChange={(e) => setIncludeSingle(e.target.checked)} />Include single-source claims</label>
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={includeFormer} onChange={(e) => setIncludeFormer(e.target.checked)} />Include former holders</label>
    </div>
    {siblings.length > 1 && <label className="flex items-center gap-2 border-b border-line px-3 py-2 text-[11px] text-muted">
      <span className="shrink-0">Browse this level</span>
      <select aria-label="Browse records at the selected level" value={selected} className="min-w-0 flex-1 rounded-md border border-line bg-ink-900 px-2 py-1 text-xs text-fg focus-visible:outline-2 focus-visible:outline-accent" onChange={(event) => void open(event.target.value, selectedLevel)}>
        {siblings.map(({ node, edge }) => <option key={node.id} value={node.id}>{edge.constituency_code ? `${edge.constituency_code} · ` : ""}{node.name || node.id}</option>)}
      </select>
    </label>}
    <div className="relative min-h-[300px] flex-1 bg-[#f7f9fc]">
      {/* Cytoscape injects an unlayered position:relative rule, which outranks Tailwind's CSS layers.
          Inline sizing keeps its canvas anchored to this flex panel instead of collapsing to height zero. */}
      <div ref={box} style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} role="img" aria-label={`Interactive government graph of ${countryName}. Use the record list below to navigate by keyboard.`} />
      {busy && <div role="status" className="pointer-events-none absolute top-3 left-3 flex items-center gap-2 rounded-lg border border-line bg-ink-900/95 px-3 py-2 text-xs text-muted"><LoaderCircle className="size-3.5 animate-spin" />Loading records…</div>}
      {!busy && !tree.levels.length && !error && <p className="absolute inset-0 flex items-center justify-center p-4 text-sm text-muted">No government structure is stored yet.</p>}
      <div className="pointer-events-none absolute bottom-2 left-3 text-[10px] text-muted">Select a node to expand · select again to collapse · drag to pan</div>
    </div>
    {error && <div role="alert" className="flex items-center gap-2 border-y border-bad/30 bg-bad/10 p-3 text-xs text-bad">{error}<Button className="ml-auto" onClick={() => void reveal(selectedRef.current)}>Retry</Button></div>}
    {pageCount > 1 && currentLevel && <div className="flex items-center justify-between gap-2 border-t border-line px-3 py-1.5 text-xs text-muted">
      <Button aria-label="Previous records" disabled={currentLevel.page === 0} onClick={() => changePage(currentLevel.page - 1)}><ChevronLeft className="size-3.5" /></Button>
      <span>Records {currentLevel.page * PAGE_SIZE + 1}–{Math.min((currentLevel.page + 1) * PAGE_SIZE, currentLevel.children.length)} of {currentLevel.children.length}</span>
      <Button aria-label="Next records" disabled={currentLevel.page + 1 >= pageCount} onClick={() => changePage(currentLevel.page + 1)}><ChevronRight className="size-3.5" /></Button>
    </div>}
    <div className="max-h-[43vh] min-h-[100px] overflow-y-auto border-t border-line bg-ink-900/85 p-4" aria-label="Selected record details" aria-live="polite">
      {detailError ? <div className="text-xs text-bad">{detailError}<Button className="ml-2" onClick={() => setDetailVersion((v) => v + 1)}>Retry details</Button></div> : !detail ?
        <p className="text-xs text-muted">Loading record details…</p> : <>
        <nav aria-label="Government hierarchy" className="mb-2 flex flex-wrap items-center gap-x-1 text-[10px] text-muted">
          {breadcrumbs.map((n, i) => <span key={n.id}>{i > 0 && <span className="mx-1">›</span>}<button className="hover:text-fg focus-visible:outline-2 focus-visible:outline-accent" onClick={() => void reveal(n.id)}>{n.name || n.id}</button></span>)}
        </nav>
        <div className="flex items-start gap-2">
          {detail.node.label === "Person" && <div className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-line bg-ink-800"><UserRound className="size-5 text-muted" /></div>}
          <div><h3 className="text-base font-semibold">{detail.node.name || detail.node.id}</h3><div className="mt-1 flex flex-wrap gap-1.5"><Badge>{kindName(detail.node.label)}</Badge>
            {typeof detail.node.evidence_status === "string" && <Badge>{valueText(detail.node.evidence_status)}</Badge>}
          </div></div>
        </div>
        {detail.node.last_verified && <p className="mt-2 text-[11px] text-muted">Last verified {fmtDate(detail.node.last_verified)}</p>}
        {detail.offices?.map(({ office, edge }) => <div key={`${office.id}:${String(edge.start)}`} className="mt-3 rounded-lg border border-line p-2.5 text-xs">
          <button className="text-accent hover:underline" onClick={() => void reveal(office.id)}>{office.name}</button>
          <div className="mt-1 flex flex-wrap gap-1.5"><Badge tone={edge.current ? "accent" : "default"}>{edge.current ? "Current office" : "Former office"}</Badge>{edge.evidence_status && <Badge>{valueText(edge.evidence_status)}</Badge>}</div>
          <p className="mt-1 text-muted">Term: {fmtDate(edge.start)} → {edge.end ? fmtDate(edge.end) : edge.current ? "Present" : "Not recorded"}</p>
        </div>)}
        {!!detail.parties?.length && <p className="mt-3 text-xs"><span className="text-muted">Party: </span>{detail.parties.map((p) => p.party.name).join(", ")}</p>}
        {detail.node.label === "Person" && jurisdiction && <p className="mt-2 text-xs"><span className="text-muted">Jurisdiction: </span>{jurisdiction.name}</p>}
        {detail.node.label === "Person" && <PoliticianSocialClaims iso2={iso2} politicianId={detail.node.id} />}
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
          {summaryFields.filter((key) => detail.node[key] !== undefined && detail.node[key] !== null && detail.node[key] !== "").map((key) => <div key={key} className="contents"><dt className="text-muted">{valueText(key)}</dt><dd className="min-w-0 break-words">{valueText(detail.node[key])}</dd></div>)}
        </dl>
        {detail.node.label === "PoliticalOffice" && !detail.holders?.length && <p className="mt-3 text-xs text-muted">No officeholder has been recorded for this office.</p>}
        {detail.terms && detail.terms.length > 1 && <details className="mt-3">
          <summary className="cursor-pointer text-xs text-muted">Recorded terms ({detail.terms.length})</summary>
          {detail.terms.map((term) => <p key={term.id} className="mt-2 text-xs">{valueText(term.name)}<br /><span className="text-muted">{fmtDate(typeof term.start === "string" ? term.start : null)} → {typeof term.end === "string" ? fmtDate(term.end) : term.current ? "Present" : "Not recorded"}</span></p>)}
        </details>}
        {childLevel && childLevel.children.length > 0 && <div className="mt-4">
          <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">Explore records · {childLevel.children.length}</h4>
          <div className="grid gap-1">{childLevel.children.slice(childLevel.page * PAGE_SIZE, (childLevel.page + 1) * PAGE_SIZE).map(({ node, edge }) => <button key={node.id} className="flex items-center gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left text-xs hover:border-line hover:bg-ink-800 focus-visible:outline-2 focus-visible:outline-accent" onClick={() => void open(node.id, selectedLevel + 1)}>
            <span className="size-1.5 shrink-0 rounded-full" style={{ background: NODE_COLORS[node.label] || "#687794" }} /><span className="min-w-0 flex-1">{edge.constituency_code ? `${edge.constituency_code} · ` : ""}{node.name || node.id}</span><ChevronRight className="size-3.5 shrink-0 text-muted" /></button>)}</div>
        </div>}
        <details className="mt-4" open={detail.node.label === "Person"}>
          <summary className="cursor-pointer text-[11px] font-semibold text-muted">Evidence & sources ({sources.length})</summary>
          {sources.length ? sources.map((source) => <div key={source.url} className="mt-2 rounded-lg border border-line p-2.5 text-xs">
            <ExtLink href={source.url}>{source.name || source.domain || source.url}</ExtLink>
            <p className="mt-1 text-[10px] text-muted">{source.domain || "Source"} · Retrieved {fmtDate(source.retrieved_at)}</p>
            {source.edge?.snippet && <blockquote className="mt-2 border-l-2 border-accent/40 pl-2 text-[11px] leading-relaxed text-muted">{source.edge.snippet}</blockquote>}
          </div>) : <p className="mt-2 text-xs text-muted">No source links are stored for this record.</p>}
        </details>
      </>}
    </div>
  </div>;
}
