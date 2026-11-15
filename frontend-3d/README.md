# Political World Explorer

React + TypeScript + Vite frontend for the existing political knowledge graph. The 3D globe,
country panel and native Cytoscape/ELK government explorer read real graph data through FastAPI.
The original UI remains at `/`; the production explorer is served at `/explorer/`.

## Run

From the repository root, after installing the Python backend dependencies and configuring `.env`:

```bash
export PATH="$PWD/.local/node-v24.22.2-linux-x64/bin:$PATH"
npm --prefix frontend-3d ci
make explorer-build
make neo4j-start              # skip for SQLite or if Neo4j is already running
make serve
```

Open **http://127.0.0.1:8000/explorer/**. FastAPI mounts the built `frontend-3d/dist` directory at
startup, so build before starting or restarting the API. Existing graph data is sufficient to browse;
these views make no Exa requests. For an empty database, load the saved snapshot with `make import-kb`.

For frontend development, keep `make serve` running and use a second terminal:

```bash
make explorer-dev
# http://127.0.0.1:5173/; /api is proxied to http://127.0.0.1:8000
```

The equivalent npm commands from `frontend-3d/`, with the local Node directory on `PATH`, are
`npm ci`, `npm run build`, and `npm run dev`. Run `make explorer-check` for lint and a typed production
build. Override `NODE_BIN` in Make if Node is installed elsewhere.

## Implementation

| Component | Responsibility |
| --- | --- |
| `src/App.tsx` | Responsive layout, country selection and shareable URL state |
| `src/components/GlobeView.tsx` | `react-globe.gl`/Three.js Earth, country selection and camera controls |
| `src/components/CountryPanel.tsx` | Government summary, democracy assessments, contact rules and evidence |
| `src/components/GovernmentGraph.tsx` | Lazy graph expansion, ELK layout, sibling accordion, paging and node inspection |
| `src/lib/api.ts` | Typed client for the existing backend |
| `../backend/polgraph/world_api.py` | Read-only world overview and country detail routes |

The graph follows the hierarchy already stored in the database. Large child lists use UI paging;
they do not create artificial government tiers. Expanding a sibling closes the previously expanded
branch at the same depth. Evidence stays in the inspector with source links and stored excerpts.
Political classification is informational and does not hide a country's structure.

| Endpoint | Data |
| --- | --- |
| `GET /api/world/overview` | Country counts by classification and countries with stored structures |
| `GET /api/countries` | Registry rows, classification and structure availability |
| `GET /api/countries/{ISO2}` | Assessments, contact rules, constitutional tiers, spec metadata and graph counts |
| `GET /api/nodes/{id}/children` | Existing lazy graph expansion, including current-holder evidence filters |
| `GET /api/nodes/{id}` | Full node details, hierarchy path and supporting sources |
| `GET /api/search?q=...` | Existing graph search |

World and country responses have a **120-second cache per application instance**. Graph node endpoints
read the store directly. Single non-official-source holdings remain an explicit display option, separate
from political classification. No research or graph mutation is triggered by browsing.

## Current scope

The globe, country panel and native graph are implemented. Country image discovery, verified image
licensing, and Discover/Refresh controls are pending; politician portraits use neutral placeholders.
No new country data is fetched by this frontend. Future discovery must use the official constitution
or official government documents where no codified constitution exists, prepare a cited Structure Spec,
and preserve human approval before creating structure or fetching officeholders, as required by
[AGENTS.md](../AGENTS.md). Existing Exa spending caps remain applicable to future research controls.

## Browser checks

Run the automated read-only browser check with the API running and the saved Mexico graph loaded:

```bash
export PATH="$PWD/.local/node-v24.22.2-linux-x64/bin:$PATH"
npm --prefix frontend-3d run test:browser
```

This uses the installed Chrome and Node's built-in WebSocket client. Set `CHROME_BIN` if Chrome is
elsewhere, `EXPLORER_ORIGIN` for a different API origin, and `EXPLORER_SCREENSHOTS` for the screenshot
directory. From `frontend-3d/`, screenshots default to `../var/explorer-checks`. It checks actual graph
canvas sizing and node clicks, country and entity search, the Mexico officeholder path, sources, shared
links, sibling collapse, paging, filters, missing data, mobile overflow and the WebGL fallback. It does
not fetch new officeholders or spend Exa credits.

With the API serving the existing graph:

1. Open `/explorer/`, search for Mexico, and check that the globe selects it and loads its country panel.
2. Click **Explore Government Structure** and follow Mexico → Federation → Presidency → President →
   current holder. Check that the inspector displays the stored source links and excerpts.
3. Open another institution at the same depth and check that the previous branch collapses.
4. Browse federal entities or a large roster with the page controls; check that entries remain under
   their stored constitutional parents.
5. Open a country without a stored structure, then check the narrow-screen layout and browser reload.

These are checks against the saved graph, not a claim that all country records are complete or current.

## Geographic assets

Country borders are bundled Natural Earth 1:110m admin-0 GeoJSON from
[`natural-earth-vector`](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson).
See [Natural Earth's terms of use](https://www.naturalearthdata.com/about/terms-of-use/).

`public/img/earth-blue-marble.jpg`, `earth-topology.png` and `night-sky.png` were copied from the
[`three-globe` example image directory](https://github.com/vasturiano/three-globe/tree/master/example/img).
This records the download source; a country-photo or portrait license-verification pipeline is not yet
implemented.
