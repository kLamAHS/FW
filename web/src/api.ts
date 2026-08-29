/**
 * The one place that talks to the backend.
 *
 * Types here mirror `fw/api/schemas.py`. They are hand-written rather than generated
 * because the surface is small and a generator would be another moving part in a project
 * whose whole promise is that it keeps working offline years from now.
 */

export interface Entity {
  id: string
  type_key: string
  name: string
  summary: string
  exists_from: number | null
  exists_to: number | null
  confidence: string
  tags: string[]
}

export interface Fact {
  id: string
  subject_id: string
  subject_name: string
  predicate_key: string
  predicate_label: string
  object_id: string | null
  object_name: string | null
  value: string | null
  valid_from: number | null
  valid_to: number | null
  confidence: string
  secrecy: string
  strength: string | null
  note: string
  is_secret: boolean
  /** All three written on every fact since the first migration and rendered nowhere,
   *  which is worse than absent: a writer who cited a note could not see that they had. */
  valid_from_text: string
  valid_to_text: string
  source: string
}

export interface WorldDate {
  day: number
  text: string
  /** The absolute year facts are stored against. */
  year: number
  month: number
  month_name: string
  day_of_month: number
  weekday: string
  season: string | null
  era: string | null
  era_name: string | null
  /** What the world's own reckoning calls this year — counts the other way in a BC-style era. */
  era_year: number | null
}

export interface EraInfo {
  name: string
  abbreviation: string
  start_year: number | null
  end_year: number | null
  counts_backward: boolean
  reckons_from: number | null
}

/** An era as stored, with its row id — what the editor lists. */
export interface EraRow extends EraInfo {
  id: string
  calendar_id: string
}

export interface CalendarInfo {
  name: string
  months: { name: string; days: number }[]
  weekdays: string[]
  days_in_year: number
  eras: EraInfo[]
  seasons: { name: string; start: number }[]
}

export interface WorldSummary {
  name: string
  description: string
  present_day: number
  calendar: CalendarInfo
  counts: Record<string, number>
  span: { first: number; last: number }
  branch: { name: string; is_canon: boolean }
}

export interface Vocabulary {
  entity_types: {
    key: string; label: string; plural: string; category: string
    icon: string; core_fields: string
  }[]
  predicates: {
    key: string; label: string; kind: string; inverse_key: string | null
    symmetric: number; transitive: number; category: string
    scale_key: string | null; description: string
  }[]
  scales: { key: string; label: string; steps: string }[]
  succession_laws: { key: string; label: string; description: string }[]
  transport_profiles: { key: string; label: string; description: string }[]
}

export interface WorldState {
  day: number
  date: WorldDate
  entities: Entity[]
  facts: Fact[]
  titles: Record<string, string | null>
}

export interface MapFeature {
  id: string
  entity_id: string
  name: string
  type_key: string
  kind: 'point' | 'line' | 'polygon'
  coordinates: number[] | number[][] | number[][][]
  layer: string
  style: Record<string, unknown>
  approximate: boolean
  control: Record<string, { id: string; name: string }[]>
}

/**
 * How to draw the map, worked out on the server (C11).
 *
 * Label placement is a layout problem — measure the text, fit it to the shape, drop
 * what does not fit — and doing it here would mean the same map labels itself
 * differently in two clients, and differently again in an export. It arrives solved.
 */
export interface MapLabel {
  key: string
  text: string
  kind: string
  tier: number
  role: string
  size: number
  x: number
  y: number
  anchor: 'start' | 'middle' | 'end'
  /** Set only when the name really bends; then the text runs along this path. */
  path?: number[][]
}

export interface MapIcon {
  key: string
  entity_id: string
  name: string
  shape: 'star' | 'ring' | 'disc' | 'dot' | 'keep' | 'tower' | 'anchor'
  rank: string
  x: number
  y: number
  radius: number
  role: string
  holder_role: string
  holder_name: string
  contested: boolean
}

export interface MapLegendEntry {
  key: string
  label: string
  role: string
  swatch: string
  note: string
  entity_id: string
}

export interface DrawPlan {
  bounds: { x: number; y: number; width: number; height: number }
  mode: string
  labels: MapLabel[]
  icons: MapIcon[]
  legend: MapLegendEntry[]
  holders: Record<string, string>
  unlabelled: string[]
}

export interface MapData {
  day: number
  layers: string[]
  features: MapFeature[]
  draw: DrawPlan
}

export interface GraphData {
  nodes: { id: string; name: string; type_key: string }[]
  edges: {
    source: string; target: string; predicate: string; label: string
    category: string; strength: string | null; secret: boolean; symmetric: boolean
  }[]
}

export interface PedigreePerson {
  id: string; name: string; x: number; y: number; generation: number
  born: number | null; died: number | null; gender: string | null
  legitimacy: string; house_id: string | null
  collapsed: boolean; hidden_descendants: number
}

export interface Pedigree {
  root_id: string | null
  width: number
  height: number
  people: PedigreePerson[]
  unions: { a_id: string; b_id: string; x: number; y: number }[]
  links: { parent_id: string; child_id: string; kind: string; uncertain: boolean }[]
}

export interface TitleInfo {
  id: string
  name: string
  rank: number
  succession_law: string
  territory_id: string | null
  holder: { id: string; name: string } | null
  holdings: {
    holder_id: string; holder_name: string
    from_day: number | null; to_day: number | null
    how: string; disputed: boolean
  }[]
}

export interface Succession {
  title_id: string
  title_name: string
  law_key: string
  law_label: string
  day: number
  hypothetical: boolean
  assumptions: string[]
  line: { position: number; id: string; name: string; note: string }[]
  excluded: { id: string; name: string; reason: string }[]
  explanation: string
}

export interface Violation {
  rule_key: string
  severity: 'error' | 'warning' | 'notice'
  message: string
  entity_ids: string[]
  day: number | null
  detail: string
  fingerprint: string
}

export interface ContinuityReport {
  summary: string
  violations: Violation[]
  suppressed: number
  rules_run: number
}

export interface SceneSummary {
  id: string
  title: string
  day: number | null
  date_text: string
  location_id: string | null
  location_name: string | null
  pov_id: string | null
  objective: string
  conflict: string
  position: number
}

export interface SceneContext {
  scene_id: string
  title: string
  date_text: string
  location: Entity | null
  participants: Entity[]
  relationships: {
    text: string; subject: string; subject_id: string; object: string
    object_id: string; predicate: string; strength: string | null
    secret: boolean; note: string; score: number; reasons: string[]
  }[]
  secrets: {
    text: string; secret_id: string; secret_name: string; observer: string
    observer_id: string; stance: string; about: string | null; note: string
  }[]
  goals: { person: string; person_id: string; kind: string; text: string }[]
  recent_events: { id: string; name: string; days_ago: number; summary: string }[]
  tensions: string[]
  world_state_notes: string[]
}

export interface WorldEvent {
  id: string
  name: string
  type_key: string
  summary: string
  start_day: number | null
  end_day: number | null
  location_id: string | null
  date_text: string
  participants: { id: string; name: string; role: string }[]
}

export interface SecretInfo {
  id: string
  name: string
  truth: string
  severity: string
  about: { id: string; name: string } | null
  by_stance: Record<string, {
    id: string; name: string
    about: { id: string; name: string } | null
    acquired_on: number | null; note: string
  }[]>
}

export interface EntityBundle {
  entity: Entity
  /** Containment chain upward — the region, then the realm. */
  within: { id: string; name: string; type_key: string }[]
  facts: Fact[]
  events: { id: string; name: string; start_day: number | null; type_key: string; summary: string }[]
  titles: { id: string; name: string; rank: number; succession_law: string }[]
  knowledge: {
    secret_id: string; secret_name: string; stance: string
    about_observer_id: string | null; acquired_on: number | null; note: string
  }[]
  geometry: { kind: string; coordinates: unknown; layer: string } | null
  scenes: { id: string; title: string; day: number | null }[]
}

export interface PlaceNode {
  entity: Entity
  depth: number
  settlement_type: string | null
  /** How many things sit at or below this node. */
  inside: number
  children: PlaceNode[]
  groups: Entity[]
  people: Entity[]
  other: Entity[]
}

export interface PlaceContents {
  tree: PlaceNode
  /** The containment chain upward: region, then realm. */
  within: Entity[]
  groups: { entity: Entity; how: string }[]
}

export interface GroupSummary {
  entity: Entity
  members: number
  branches: number
  seats: { id: string; name: string; how: string }[]
}

export interface GroupDetail {
  entity: Entity
  members: { entity: Entity; relation: string; note: string }[]
  branches: { entity: Entity; depth: number }[]
  seats: { entity: Entity; how: string }[]
  above: Entity[]
  group_types: string[]
}

export interface MapGenerationReport {
  summary: string
  regions_drawn: string[]
  regions_kept: string[]
  rivers: string[]
  roads: number
  notes: string[]
  placements: {
    entity_id: string | null; name: string; x: number; y: number
    rank: string; proposed: boolean; why: string
  }[]
}

export interface Finding {
  text: string
  weight: number
  evidence: string[]
  entity_ids: string[]
  kind: string
}

export interface RouteResult {
  origin_id: string
  destination_id: string
  profile: string
  days: number
  distance: number
  path: string[]
  path_names: string[]
  legs: { from_id: string; to_id: string; medium: string; length: number; days: number }[]
  explanation: string
}

export interface KinEntry {
  id: string
  name: string
  distance: number
  relationship: string | null
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  }
  const suffix = query.toString() ? `?${query}` : ''
  const response = await fetch(`/api${path}${suffix}`)
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch {
      // A non-JSON error body is still an error; keep the status text.
    }
    throw new ApiError(detail, response.status)
  }
  return response.json() as Promise<T>
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch { /* keep status text */ }
    throw new ApiError(detail, response.status)
  }
  return response.status === 204 ? (undefined as T) : (response.json() as Promise<T>)
}

export interface EntityDraft {
  type_key: string
  name: string
  summary?: string
  exists_from?: number | null
  exists_to?: number | null
  confidence?: string
  tags?: string[]
}

export interface FactDraft {
  subject_id: string
  predicate_key: string
  object_id?: string | null
  value?: string | null
  valid_from?: number | null
  valid_to?: number | null
  confidence?: string
  secrecy?: string
  strength?: string | null
  note?: string
}

/* ---- questions (§49) ---------------------------------------------------- */

export interface QueryCondition {
  predicate: string
  direction?: 'out' | 'in'
  test?: string
  value?: string
  object_id?: string
  object_type?: string
  strength?: string[]
  at?: number | null
  negate?: boolean
}

export interface QueryWithin {
  start_id: string
  predicates: string[]
  hops: number
  direction?: 'out' | 'in' | 'either'
  at?: number | null
  include_start?: boolean
}

export interface QueryShape {
  types?: string[]
  name_contains?: string
  tags?: string[]
  confidence?: string[]
  exists_on?: number | null
  began_after?: number | null
  began_before?: number | null
  conditions?: QueryCondition[]
  within?: QueryWithin | null
  order?: string
  descending?: boolean
  limit?: number
  explain?: boolean
}

export interface QueryRow {
  id: string
  name: string
  type_key: string
  summary: string
  confidence: string
  exists_from: number | null
  exists_to: number | null
  because: string[]
  distance: number | null
}

export interface QueryAnswer {
  query: QueryShape
  rows: QueryRow[]
  total: number
  truncated: boolean
  sql: string
  ms: number
  notes: string[]
}

export interface QueryVocabulary {
  directions: string[]
  tests: string[]
  orders: string[]
  confidence: string[]
  tags: string[]
}

export interface SavedQuery {
  key: string
  name: string
  note: string
  query: QueryShape
}

/* ---- titles and secrets ------------------------------------------------- */

export interface TitleDraft {
  name: string
  rank?: number
  territory_id?: string | null
  succession_law?: string
  dynasty_root_id?: string | null
  created_on?: number | null
  entity_id?: string | null
}

export interface GrantDraft {
  holder_id: string
  from_day?: number | null
  to_day?: number | null
  how?: string
  disputed?: boolean
  note?: string
}

export interface SecretDraft {
  name: string
  truth?: string
  about_id?: string | null
  fact_id?: string | null
  severity?: string
}

export interface KnowledgeDraft {
  observer_id: string
  secret_id: string
  stance: string
  about_observer_id?: string | null
  acquired_on?: number | null
  acquired_from?: string | null
  scene_id?: string | null
  note?: string
}

export interface Chapter {
  id: string
  work_id: string
  work_title: string
  title: string
  position: number
  summary: string
}

export interface SceneDraft {
  title: string
  /** Which chapter it belongs to (§43). The column has been there all along and
   *  nothing could set one, so every scene was loose in the world rather than in
   *  the book. */
  chapter_id?: string | null
  day?: number | null
  end_day?: number | null
  location_id?: string | null
  pov_id?: string | null
  objective?: string
  conflict?: string
  outcome?: string
  participants?: string[]
}

export interface EventDraft {
  name: string
  type_key?: string
  summary?: string
  start_day?: number | null
  end_day?: number | null
  location_id?: string | null
  participants?: { id: string; role: string }[]
}

export interface DeletedEntry {
  revision_id: number
  entity_id: string
  name: string
  type_key: string
  at: string
}

export interface RevisionEntry {
  id: number
  table_name: string
  row_id: string
  action: 'insert' | 'update' | 'delete'
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  at: string
}

export interface LibraryWorld {
  file: string
  name: string
  modified: number
  size: number
  entities: number
  problem: string
}

export interface BranchInfo {
  id: string
  name: string
  is_canon: boolean
  parent_id: string | null
  branched_at: number | null
  open: boolean
}

export interface LibraryInfo {
  library: string | null
  worlds: LibraryWorld[]
  open: string | null
}


/** A map that does not exist yet (§66): every feature, with the case for it. */
export interface PlannedFeature {
  id: string
  kind: string
  name: string
  subject: { mode: string; type_key: string; entity_id: string | null } | null
  anchor_id: string | null
  shapes: {
    role: string
    kind: 'point' | 'line' | 'polygon'
    coordinates: any
    layer: string
    style: Record<string, string>
    approximate: boolean
  }[]
  why: string[]
  detail: Record<string, any>
  depends_on: string[]
  default_accept: boolean
  renameable: boolean
  status: string
}

export interface MapPlan {
  plan_id: string
  world_name: string
  branch: string
  summary: string
  features: PlannedFeature[]
  retiring: { feature_id: string; name: string; writer_touched: boolean; why: string }[]
  findings: { code: string; severity: string; message: string; quotes: string[] }[]
  stats: { features_by_kind: Record<string, number>; vertices: number; plan_ms: number }
}

export interface MapDecision {
  feature_id: string
  accept: boolean
  name?: string | null
  pinned?: boolean
}

export interface ApplyReport {
  plan_id: string
  action_id: string | null
  summary: string
  counts: Record<string, number>
  outcomes: { feature_id: string; name: string; op: string; why: string }[]
}

export const api = {
  worlds: () => get<LibraryInfo>('/worlds'),
  createWorld: (name: string, example: boolean) =>
    send<{ file: string; name: string }>('/worlds', 'POST', { name, example }),
  openWorld: (file: string) =>
    send<{ file: string; name: string }>('/worlds/open', 'POST', { file }),

  world: () => get<WorldSummary>('/world'),
  branches: () => get<BranchInfo[]>('/branches'),
  createBranch: (name: string, branched_at: number | null) =>
    send<{ name: string }>('/branches', 'POST', { name, branched_at }),
  openBranch: (name: string) =>
    send<{ name: string }>('/branches/open', 'POST', { name }),
  vocabulary: () => get<Vocabulary>('/vocabulary'),
  date: (day: number) => get<WorldDate>(`/date/${day}`),
  dayIndex: (year: number, month = 1, day = 1, era?: string | null) =>
    get<WorldDate>('/day', { year, month, day, era: era || undefined }),

  eras: () => get<EraRow[]>('/eras'),
  createEra: (era: EraInfo) => send<{ id: string }>('/eras', 'POST', era),
  updateEra: (id: string, patch: Partial<EraInfo>) =>
    send<void>(`/eras/${id}`, 'PATCH', patch),
  deleteEra: (id: string) => send<void>(`/eras/${id}`, 'DELETE'),
  recent: (limit = 8) => get<{ entity: Entity; at: string }[]>('/recent', { limit }),
  history: (id: string) => get<RevisionEntry[]>(`/entities/${id}/history`),

  deleted: (limit = 10) => get<DeletedEntry[]>('/deleted', { limit }),
  restoreRevision: (id: number) =>
    send<{ message: string }>(`/revisions/${id}/restore`, 'POST'),
  undoState: () => get<{
    can_undo: boolean; undo: string | null; can_redo: boolean; redo: string | null
  }>('/undo'),
  undo: () => send<{ message: string }>('/undo', 'POST'),
  redo: () => send<{ message: string }>('/redo', 'POST'),
  createScene: (draft: SceneDraft) =>
    send<{ id: string; title: string; day: number | null }>('/scenes', 'POST', draft),
  createEvent: (draft: EventDraft) =>
    send<{ id: string; name: string; start_day: number | null }>('/events', 'POST', draft),
  linkCause: (cause_id: string, effect_id: string, note = '') =>
    send<{ status: string }>('/causal-links', 'POST', { cause_id, effect_id, note }),

  createEntity: (draft: EntityDraft) => send<Entity>('/entities', 'POST', draft),
  updateEntity: (id: string, patch: Partial<EntityDraft>) =>
    send<Entity>(`/entities/${id}`, 'PATCH', patch),
  deleteEntity: (id: string) => send<void>(`/entities/${id}`, 'DELETE'),
  createFact: (draft: FactDraft) => send<Fact>('/facts', 'POST', draft),
  deleteFact: (id: string) => send<void>(`/facts/${id}`, 'DELETE'),
  endFact: (id: string, onDay: number) =>
    send<Fact>(`/facts/${id}/end?on_day=${onDay}`, 'POST'),
  snapshots: () => get<{ id: string; name: string; day: number; note: string; date: WorldDate }[]>(
    '/snapshots'),

  entities: (params?: {
    type_key?: string; at?: number; limit?: number; hide_generated?: boolean
  }) => get<Entity[]>('/entities', params),
  entity: (id: string, at?: number) => get<EntityBundle>(`/entities/${id}`, { at }),
  /* ---- questions (§49) ------------------------------------------------- */
  ask: (query: QueryShape) => send<QueryAnswer>('/query', 'POST', { query }),
  queryVocabulary: () => get<QueryVocabulary>('/query/vocabulary'),
  chapters: () => get<Chapter[]>('/chapters'),
  savedQueries: () => get<SavedQuery[]>('/queries'),
  saveQuery: (name: string, query: QueryShape, note = '') =>
    send<SavedQuery>('/queries', 'POST', { name, note, query }),
  forgetQuery: (key: string) => send<void>(`/queries/${key}`, 'DELETE'),

  /* ---- the write surfaces for §8 and §6 --------------------------------- */
  createTitle: (draft: TitleDraft) => send<{ id: string }>('/titles', 'POST', draft),
  grantTitle: (titleId: string, draft: GrantDraft) =>
    send<{ id: string }>(`/titles/${titleId}/grants`, 'POST', draft),
  createSecret: (draft: SecretDraft) =>
    send<{ id: string }>('/secrets', 'POST', draft),
  recordKnowledge: (draft: KnowledgeDraft) =>
    send<{ id: string }>('/knowledge', 'POST', draft),

  /** Everywhere a journey can start or end — islands as well as towns. */
  travelPlaces: () =>
    get<{ id: string; name: string; type_key: string }[]>('/travel/places'),
  search: (q: string, type_key?: string) => get<Entity[]>('/search', { q, type_key }),

  facts: (params?: { predicate_key?: string; subject_id?: string; object_id?: string; at?: number }) =>
    get<Fact[]>('/facts', params),

  state: (day: number, includeSecret = true) =>
    get<WorldState>('/state', { day, include_secret: includeSecret }),
  map: (day?: number, layer?: string, mode?: string) =>
    get<MapData>('/map', { day, layer, mode }),
  generateMap: (seed: string | null, proposeSettlements: boolean) =>
    send<MapGenerationReport>('/map/generate', 'POST',
      { seed, propose_settlements: proposeSettlements }),
  planMap: (options: { seed?: string | null; invent_settlements?: boolean }) =>
    send<MapPlan>('/map/plan', 'POST', {
      seed: options.seed ?? null,
      invent_settlements: options.invent_settlements ?? false,
    }),
  applyMap: (plan: MapPlan, decisions: MapDecision[]) =>
    send<ApplyReport>('/map/apply', 'POST', { plan, decisions }),
  graph: (params?: { day?: number; categories?: string; centre?: string; hops?: number }) =>
    get<GraphData>('/graph', params),
  pedigree: (params?: { root_id?: string; lens?: string; living_only_on?: number; house_id?: string }) =>
    get<Pedigree>('/pedigree', params),
  kin: (id: string, hops = 3) => get<KinEntry[]>(`/kin/${id}`, { hops }),

  titles: (at?: number) => get<TitleInfo[]>('/titles', { at }),
  succession: (titleId: string, params?: {
    day?: number; law_key?: string; illegitimate?: string; assume_dead?: string; exclude?: string
  }) => get<Succession>(`/succession/${titleId}`, params),

  events: (params?: { first?: number; last?: number }) => get<WorldEvent[]>('/events', params),
  consequences: (id: string) =>
    get<{ id: string; name: string; depth: number; start_day: number | null; summary: string }[]>(
      `/events/${id}/consequences`),

  placeContents: (id: string, at?: number) =>
    get<PlaceContents>(`/places/${id}/contents`, { at }),
  groups: (at?: number) => get<GroupSummary[]>('/groups', { at }),
  group: (id: string, at?: number) => get<GroupDetail>(`/groups/${id}`, { at }),

  secrets: (at?: number) => get<SecretInfo[]>('/secrets', { at }),
  scenes: () => get<SceneSummary[]>('/scenes'),
  sceneContext: (id: string) => get<SceneContext>(`/scenes/${id}/context`),

  continuity: (minimum = 'notice') => get<ContinuityReport>('/continuity', { minimum }),
  suppress: (rule_key: string, fingerprint: string, reason: string) =>
    send<void>('/continuity/suppress', 'POST', { rule_key, fingerprint, reason }),

  route: (origin_id: string, destination_id: string, profile: string, day?: number) =>
    get<RouteResult>('/route', { origin_id, destination_id, profile, day }),

  why: (id: string, day?: number) =>
    get<{ entity: Entity; day: number; findings: Finding[]; note: string }>(`/why/${id}`, { day }),
  impact: (id: string, day?: number) =>
    get<{ entity: Entity; day: number; consequences: Finding[]; note: string }>(
      `/impact/${id}`, { day }),
}
