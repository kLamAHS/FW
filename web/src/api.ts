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
}

export interface WorldDate {
  day: number
  text: string
  year: number
  month: number
  month_name: string
  day_of_month: number
  weekday: string
  season: string | null
  era: string | null
}

export interface CalendarInfo {
  name: string
  months: { name: string; days: number }[]
  weekdays: string[]
  days_in_year: number
  eras: { name: string; abbreviation: string; start_year: number; end_year: number | null }[]
  seasons: { name: string; start: number }[]
}

export interface WorldSummary {
  name: string
  description: string
  present_day: number
  calendar: CalendarInfo
  counts: Record<string, number>
  span: { first: number; last: number }
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

export interface MapData {
  day: number
  layers: string[]
  features: MapFeature[]
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

export const api = {
  world: () => get<WorldSummary>('/world'),
  vocabulary: () => get<Vocabulary>('/vocabulary'),
  date: (day: number) => get<WorldDate>(`/date/${day}`),
  snapshots: () => get<{ id: string; name: string; day: number; note: string; date: WorldDate }[]>(
    '/snapshots'),

  entities: (params?: { type_key?: string; at?: number; limit?: number }) =>
    get<Entity[]>('/entities', params),
  entity: (id: string, at?: number) => get<EntityBundle>(`/entities/${id}`, { at }),
  search: (q: string, type_key?: string) => get<Entity[]>('/search', { q, type_key }),

  facts: (params?: { predicate_key?: string; subject_id?: string; object_id?: string; at?: number }) =>
    get<Fact[]>('/facts', params),

  state: (day: number, includeSecret = true) =>
    get<WorldState>('/state', { day, include_secret: includeSecret }),
  map: (day?: number, layer?: string) => get<MapData>('/map', { day, layer }),
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
