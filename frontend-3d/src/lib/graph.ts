// Read-only graph client. Selection methods, hierarchy and evidence come from the backend.
export interface GraphNode {
  id: string;
  label: string;
  name?: string | null;
  child_count?: number;
  evidence_status?: string | null;
  is_elected?: boolean | null;
  last_verified?: string | null;
  [key: string]: unknown;
}

export interface GraphEdge {
  type?: string;
  current?: boolean;
  start?: string | null;
  end?: string | null;
  evidence_status?: string | null;
  constituency_code?: string | null;
  seat_type?: string | null;
  snippet?: string | null;
  [key: string]: unknown;
}

export interface GraphChild { node: GraphNode; edge: GraphEdge }
export interface GraphChildren { node: GraphNode; children: GraphChild[] }
export interface GraphSource {
  id: string;
  name?: string;
  url: string;
  domain?: string;
  retrieved_at?: string;
  edge?: GraphEdge;
}
export interface GraphDetail {
  node: GraphNode;
  path: GraphNode[];
  sources: GraphSource[];
  offices?: { office: GraphNode; edge: GraphEdge }[];
  parties?: { party: GraphNode; edge: GraphEdge }[];
  holders?: { person: GraphNode; edge: GraphEdge }[];
  terms?: GraphNode[];
}

export async function graphGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Could not load graph (${response.status}). Please retry.`);
  return response.json() as Promise<T>;
}

export function detailUrl(id: string): string {
  return `/api/nodes/${encodeURIComponent(id)}`;
}

export function visibleChildren(result: GraphChildren): GraphChild[] {
  // Country assessments and contact rules are in the country panel; source documents stay in the inspector.
  return result.children.filter(({ node }) => node.label !== "Source" &&
    !(result.node.label === "Country" && ["DemocracyAssessment", "ContactRule"].includes(node.label)));
}

export const NODE_COLORS: Record<string, string> = {
  Country: "#557bce", GovernmentLevel: "#7971c7", GovernmentBody: "#7460ae",
  PoliticalOffice: "#9b618e", Person: "#438f85", PoliticalParty: "#627790",
};

export function kindName(label: string): string {
  return label.replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not recorded";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map(valueText).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).replaceAll("_", " ");
}
