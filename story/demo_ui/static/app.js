/**
 * The panel logic: one page, thirteen endpoints, and no value on screen that a payload did not
 * carry.
 *
 * Responsibility: own the network, own the state of one local session, and own every element of
 * `index.html` except the three `graph.js` writes. Boundaries, and they are the ones the
 * renderer's own header asks for: **this module draws nothing on the canvas and computes no
 * layout**, and `graph.js` fetches nothing and knows no endpoint. The seam between them is a
 * payload in and a node id out.
 *
 * ## Element ownership, decided once
 *
 * `graph.js` writes `#graph-legend`, `#graph-breadcrumb` and `#graph-status` when it is handed
 * them in `options`. It is handed all three here and this file never touches them again — two
 * writers on one element is a race whose loser is whichever ran second, and the renderer's
 * versions are the ones that can report a frame cost and a layout digest. Everything else on the
 * page is written from this file.
 *
 * ## Safe rendering, structurally rather than by care
 *
 * Nothing here assembles markup. Every node is `document.createElement` and every string is
 * `textContent`, including the Markdown renderer, which walks lines and emits elements rather
 * than producing HTML to be parsed. That is why the page's `require-trusted-types-for 'script'`
 * has nothing to refuse: there is no code path from a server string, or from a model-authored
 * sentence, to the HTML parser. `tests/story/test_demo_ui_static.py` scans this file for the
 * sinks; `tests/story/test_demo_ui_app.py` scans it for the rest.
 *
 * ## The four honesty rules this file is the last line of
 *
 * 1. **A highlight is resolved, never invented.** Every id sent to `view.highlight` is first
 *    looked up with `view.node(id)`; ids that do not resolve in the drawn view are counted and
 *    reported next to the trace step rather than silently discarded or drawn at a guess.
 * 2. **A fabricated zero is never rendered.** `cost.measured === false` prints the server's own
 *    `reason` string where a number would go. So does `package.retrieval_timing`.
 * 3. **A ranking score is not a probability.** `score.is_probability` is `false` in the payload
 *    and the disclaimer is rendered from `score_disclaimer`; a component in `suppressed` renders
 *    its recorded reason instead of a silent zero.
 * 4. **A rendered string the server sent is never reformatted.** `formatValue` formats numbers
 *    and returns strings untouched, so `"15.9 percentage points"` reaches the page exactly as
 *    §13 checked it, and only a raw float like `15.899999999999999` is shortened for display —
 *    with the exact value on the element's `title` so it stays inspectable.
 *
 * ## Two integration facts, measured against the running server on 2026-08-05
 *
 * * **Generation trace events carry no `graph_highlights`.** Measured over a real accepted run:
 *   all fifteen events have an empty `graph_highlights.node_ids`, and the two that point at the
 *   graph do it through `related_fact_ids`, which are observation ids. An observation id *is* a
 *   node id in the story and evidence projections (checked: both bound fact ids resolve in the
 *   evidence subgraph), so clicking a generation step resolves those instead. That is a lookup,
 *   not an invention, and it is the only reason rule 1 above can be kept for the generation
 *   half at all.
 * * **`max_suggestions` does not need raising for the demo candidate.** The plan's warning was
 *   that the default of 20 might hide it; measured, the candidate sits at position 6 of 262, so
 *   the default reaches it. 50 is asked for anyway, because the ranked list is the panel where
 *   position and rank being different facts is visible, and six rows is not enough to see it.
 */

import { createGraphView, createSplitter } from './graph.js';

// ---------------------------------------------------------------------------------------
// The endpoint table. Every path this file fetches is here and nowhere else, so
// `tests/story/test_demo_ui_app.py` can compare the set against what `api.register_endpoints`
// actually puts on a router — a typo in a path is otherwise a 404 nobody sees until a demo.
// ---------------------------------------------------------------------------------------

export const ENDPOINTS = Object.freeze({
  graphOverview: '/demo/graph/overview',
  graphSubgraph: '/demo/graph/subgraph',
  storySuggestions: '/demo/story-suggestions',
  discoveryEvents: '/demo/story-suggestions/{run_id}/events',
  discoveryResult: '/demo/story-suggestions/{run_id}',
  candidate: '/demo/candidates/{candidate_id}',
  evidencePackage: '/demo/evidence-package',
  promptPresets: '/demo/prompt-presets',
  generate: '/demo/generate',
  runEvents: '/demo/runs/{run_id}/events',
  runResult: '/demo/runs/{run_id}',
  runSources: '/demo/runs/{run_id}/sources',
});

const ENDPOINT_TEMPLATES = Object.freeze(Object.values(ENDPOINTS));

/** Substitute `{name}` placeholders, percent-encoding every value. */
function path(template, params = {}) {
  return template.replace(/\{([a-z_]+)\}/g, (whole, name) => (
    Object.prototype.hasOwnProperty.call(params, name)
      ? encodeURIComponent(String(params[name]))
      : whole));
}

function query(base, params) {
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(name, String(value));
  }
  const text = search.toString();
  return text === '' ? base : `${base}?${text}`;
}

/**
 * A URL the *server* handed back — `events_url`, `result_url`, `sources_url` — checked against
 * the same table before it is opened.
 *
 * Not paranoia about this server: it is the property that makes the endpoint test meaningful.
 * If a response could name any path and this file would open it, then "every path this file
 * fetches is a registered one" would be a statement about the table and not about the code.
 */
function serverPath(candidate, template, params) {
  const expected = path(template, params);
  if (typeof candidate === 'string' && candidate === expected) return candidate;
  return expected;
}

// ---------------------------------------------------------------------------------------
// The elements. One lookup each, by literal id, so the test can parse this block and check
// every one against `index.html` — a renamed id is then a failing test rather than a panel
// that silently stops updating.
// ---------------------------------------------------------------------------------------

const dom = {
  appError: document.getElementById('app-error'),
  pipelineState: document.getElementById('pipeline-state'),
  snapshotRunId: document.getElementById('snapshot-run-id'),
  snapshotCompletedAt: document.getElementById('snapshot-completed-at'),
  headerCompany: document.getElementById('header-company'),
  countDrawnNodes: document.getElementById('count-drawn-nodes'),
  countDrawnEdges: document.getElementById('count-drawn-edges'),
  countTotalNodes: document.getElementById('count-total-nodes'),
  countTotalEdges: document.getElementById('count-total-edges'),
  projectionDisclosure: document.getElementById('projection-disclosure'),
  companyDisclosure: document.getElementById('company-disclosure'),

  settingAggregate: document.getElementById('setting-aggregate'),
  settingLabels: document.getElementById('setting-labels'),
  settingMotion: document.getElementById('setting-motion'),
  settingSynthesised: document.getElementById('setting-synthesised'),

  graphRegion: document.getElementById('graph-region'),
  splitDivider: document.getElementById('split-divider'),
  viewFull: document.getElementById('view-full'),
  viewStory: document.getElementById('view-story'),
  viewEvidence: document.getElementById('view-evidence'),
  graphBreadcrumb: document.getElementById('graph-breadcrumb'),
  graphZoomIn: document.getElementById('graph-zoom-in'),
  graphZoomOut: document.getElementById('graph-zoom-out'),
  graphFit: document.getElementById('graph-fit'),
  graphReset: document.getElementById('graph-reset'),
  graphCanvas: document.getElementById('graph-canvas'),
  graphEmpty: document.getElementById('graph-empty'),
  graphLegend: document.getElementById('graph-legend'),
  graphStatus: document.getElementById('graph-status'),

  nodeDetailCard: document.getElementById('node-detail-card'),
  nodeDetailTitle: document.getElementById('node-detail-title'),
  nodeDetailClose: document.getElementById('node-detail-close'),
  nodeDetailDerived: document.getElementById('node-detail-derived'),
  nodeDetailProperties: document.getElementById('node-detail-properties'),

  panelTabs: document.getElementById('panel-tabs'),
  outputTabs: document.getElementById('output-tabs'),
  tabStory: document.getElementById('tab-story'),
  tabFacts: document.getElementById('tab-facts'),
  tabPrompts: document.getElementById('tab-prompts'),
  tabTrace: document.getElementById('tab-trace'),
  tabPost: document.getElementById('tab-post'),
  tabVerification: document.getElementById('tab-verification'),
  tabSources: document.getElementById('tab-sources'),

  runDiscovery: document.getElementById('run-discovery'),
  discoveryState: document.getElementById('discovery-state'),
  discoveryExplainer: document.getElementById('discovery-explainer'),
  candidateList: document.getElementById('candidate-list'),
  candidateDetail: document.getElementById('candidate-detail'),
  candidateTitle: document.getElementById('candidate-title'),
  candidateProperties: document.getElementById('candidate-properties'),
  scoreBreakdown: document.getElementById('score-breakdown'),
  buildPackage: document.getElementById('build-package'),

  packageSummary: document.getElementById('package-summary'),
  factsList: document.getElementById('facts-list'),
  packageBounds: document.getElementById('package-bounds'),

  promptPreset: document.getElementById('prompt-preset'),
  promptReset: document.getElementById('prompt-reset'),
  promptDiffToggle: document.getElementById('prompt-diff-toggle'),
  promptDiff: document.getElementById('prompt-diff'),
  plannerInstructions: document.getElementById('planner-instructions'),
  writerInstructions: document.getElementById('writer-instructions'),
  styleGuidance: document.getElementById('style-guidance'),
  promptLengths: document.getElementById('prompt-lengths'),
  promptWarnings: document.getElementById('prompt-warnings'),
  fixedRules: document.getElementById('fixed-rules'),
  replayNotice: document.getElementById('replay-notice'),
  generatePost: document.getElementById('generate-post'),

  traceEvents: document.getElementById('trace-events'),

  outputDivider: document.getElementById('output-divider'),
  outputRegion: document.getElementById('output-region'),
  outputExpand: document.getElementById('output-expand'),
  verdictBadge: document.getElementById('verdict-badge'),
  postOutput: document.getElementById('post-output'),
  postMeta: document.getElementById('post-meta'),
  verificationSummary: document.getElementById('verification-summary'),
  verificationFindings: document.getElementById('verification-findings'),
  coverageFigures: document.getElementById('coverage-figures'),
  sourcesList: document.getElementById('sources-list'),
  standingStatements: document.getElementById('standing-statements'),
};

// ---------------------------------------------------------------------------------------
// The sentences this file owns. Everything else a reader sees that makes a claim is quoted
// from a payload field — `disclosure`, `score_disclaimer`, `cost.reason`, `coverage.note`,
// `suppressed[component]`, `requires_live_reason`, `honest_labels` — so the interface and the
// artifacts cannot drift into two accounts of the same run.
// ---------------------------------------------------------------------------------------

export const LABELS = Object.freeze({
  notRecorded: 'not recorded',
  notMeasured: 'not measured',
  positionAndRank:
    'Position is this row’s place in the full ranked ordering. Rank is §6.10’s, '
    + 'and every member of a cluster carries its representative’s rank — so several '
    + 'rows can read rank 1. They are different facts and both are shown.',
  highlightResolution:
    'Highlighted ids are resolved against the drawn projection before they are drawn. An id '
    + 'the current view does not contain is counted here, never invented into it.',
  advisoryPreview:
    'Previewed in the browser with the phrase list the server published. The server’s own '
    + 'scan is the authoritative one and runs again when the post is generated — and '
    + 'neither is a safety boundary.',
  localDiff:
    'Compared against the selected preset’s text in the browser. The authoritative diff is '
    + 'the server’s, over the composed system messages, and replaces this once a run is '
    + 'requested.',
  serverDiff:
    'The server’s diff of the composed system message against its default. This is the '
    + 'text that would reach the model.',
  requiresLive:
    'Editing a prompt changes the request identity, so the run misses the replay store and '
    + 'needs a live provider.',
  rejectedRun:
    'This run was refused. What follows is the refused draft and the findings that refused it; '
    + 'no post was written and none is shown.',
  streamLost:
    'The event stream closed before the run reported a terminal state. The run’s own '
    + 'status was fetched instead of assuming it finished.',
});

// ---------------------------------------------------------------------------------------
// DOM helpers. Six functions, none of which can produce markup.
// ---------------------------------------------------------------------------------------

function make(tag, className, textValue) {
  const created = document.createElement(tag);
  if (className) created.className = className;
  if (textValue !== undefined && textValue !== null) created.textContent = String(textValue);
  return created;
}

function clear(host) {
  if (host) host.replaceChildren();
  return host;
}

function setText(host, value) {
  if (host) host.textContent = value === undefined || value === null ? '—' : String(value);
}

/** One `dt`/`dd` pair. A null or empty value renders as absent rather than as a blank. */
function field(list, name, value, options = {}) {
  const term = make('dt', null, name);
  const detail = make('dd');
  if (value === null || value === undefined || value === '') {
    detail.className = 'is-absent';
    detail.textContent = options.absentText ?? LABELS.notRecorded;
  } else {
    detail.textContent = formatValue(value);
    if (typeof value === 'number' && detail.textContent !== String(value)) {
      // The display form is shortened; the exact float stays reachable rather than lost.
      detail.title = String(value);
    }
    if (options.mono) detail.classList.add('mono');
  }
  list.append(term, detail);
  return detail;
}

function note(host, textValue) {
  const paragraph = make('p', 'note', textValue);
  host.append(paragraph);
  return paragraph;
}

function details(host, summaryText, { open = false } = {}) {
  const box = document.createElement('details');
  box.open = open;
  const summary = document.createElement('summary');
  summary.textContent = summaryText;
  box.append(summary);
  host.append(box);
  return box;
}

// ---------------------------------------------------------------------------------------
// Formatting. The one rule: strings are never touched.
// ---------------------------------------------------------------------------------------

/**
 * §7's float residue, and nothing else.
 *
 * Ten significant figures, which is `api.py:_display_number`'s own `%.10g`, so a number this
 * file shortens and a number the server shortened read the same. A value that is already a
 * string — `rendered`, `recomputed_display`, `printed_form`, a unit — is returned as it arrived:
 * §13 checked the rendered string, and re-rounding it here would be this layer editing evidence.
 */
export function formatNumber(value) {
  if (!Number.isFinite(value)) return String(value);
  if (Number.isInteger(value) && Math.abs(value) < 1e15) return String(value);
  return String(Number.parseFloat(value.toPrecision(10)));
}

function formatValue(value) {
  if (typeof value === 'number') return formatNumber(value);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (Array.isArray(value)) return value.map(formatValue).join(', ');
  if (value !== null && typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function formatCount(value) {
  return Number.isFinite(value) ? value.toLocaleString('en-GB') : '—';
}

function plural(count, singular, many) {
  return `${formatCount(count)} ${count === 1 ? singular : many ?? `${singular}s`}`;
}

// ---------------------------------------------------------------------------------------
// Session state.
// ---------------------------------------------------------------------------------------

const state = {
  view: null,
  overview: null,
  projectionName: 'overview',
  subgraphs: new Map(),
  viewMode: 'full',
  discoveryRunId: null,
  suggestions: [],
  candidateCounts: null,
  selectedCandidateId: null,
  candidateDetail: null,
  packagePayload: null,
  presets: null,
  presetById: new Map(),
  selectedPresetId: null,
  composed: null,
  requiresLiveOffered: false,
  generationRunId: null,
  outcome: null,
  sources: null,
  stream: null,
  traceEvents: [],
  streamLost: false,
  sentenceRows: new Map(),
  passageRows: new Map(),
  factRows: new Map(),
  busy: false,
};

function setPipelineState(value) {
  setText(dom.pipelineState, value);
}

// ---------------------------------------------------------------------------------------
// Errors. A failure is visible, carries the server's code, and never leaves a panel claiming
// something completed.
// ---------------------------------------------------------------------------------------

class ApiFailure extends Error {
  constructor(body, status) {
    const envelope = body && typeof body === 'object' ? body.error : null;
    super(envelope && envelope.message ? String(envelope.message) : `HTTP ${status}`);
    this.name = 'ApiFailure';
    this.code = envelope && envelope.code ? String(envelope.code) : `http_${status}`;
    this.status = envelope && envelope.status ? Number(envelope.status) : status;
    this.detail = envelope && envelope.detail ? String(envelope.detail) : '';
    this.body = body;
  }
}

function showError(where, failure) {
  const parts = [`${where} failed`];
  if (failure instanceof ApiFailure) {
    parts.push(`${failure.code} (${failure.status})`);
    parts.push(failure.message);
    if (failure.detail) parts.push(`detail: ${failure.detail}`);
  } else {
    // A network or parsing failure has no code from the server's table, and its own text is
    // the browser's rather than a stage's, so it is safe to show and useless to hide.
    parts.push(String(failure && failure.message ? failure.message : failure));
  }
  dom.appError.textContent = parts.join(' — ');
  dom.appError.hidden = false;
  setPipelineState(`error: ${failure instanceof ApiFailure ? failure.code : 'request failed'}`);
}

function clearError() {
  dom.appError.textContent = '';
  dom.appError.hidden = true;
}

/**
 * What the banner says once a run has been resolved against its own status endpoint.
 *
 * A lost stream and a failed run are different facts. If the stream died but the run turns out
 * to have completed, the outcome is real and is rendered — and the banner still says the trace
 * is incomplete, because a panel that quietly showed thirteen of fifteen steps would be claiming
 * to have watched the run. Only a run that did not complete clears to a plain error.
 */
function settleBanner() {
  if (!state.streamLost) {
    clearError();
    return;
  }
  dom.appError.textContent = `${LABELS.streamLost} The trace above may be missing steps; `
    + 'the result below was read from the run itself.';
  dom.appError.hidden = false;
}

// ---------------------------------------------------------------------------------------
// Fetching.
// ---------------------------------------------------------------------------------------

async function readJson(response) {
  try {
    return await response.json();
  } catch (failure) {
    return null;
  }
}

/**
 * The status decides, not the presence of an `error` key — and that distinction was a bug here
 * until the harness found it.
 *
 * `api.py:_run_envelope` puts an `error` object in a **200** body to describe a run that failed,
 * beside `run` and `ready`; a failed *request* is always a non-2xx status, because `ApiError.
 * response` builds its `JsonResponse` with the code's own status. Throwing on the key meant a
 * failed run raised out of the reader before the panel that renders it could look at it, and the
 * page said "reading the outcome failed" for a run whose refusal was sitting in the payload.
 */
async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  const body = await readJson(response);
  if (!response.ok) throw new ApiFailure(body, response.status);
  return body;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await readJson(response);
  if (!response.ok) throw new ApiFailure(body, response.status);
  return body;
}

// ---------------------------------------------------------------------------------------
// The trace stream.
//
// One `EventSource` at a time. `end` closes it and hands the summary to the caller; a stream
// that dies without an `end` frame is reported as lost rather than treated as a completion,
// which is the difference between "the run finished" and "we stopped hearing about it".
// ---------------------------------------------------------------------------------------

function openStream(url, { onEvent, onEnd, onLost }) {
  if (state.stream !== null) {
    state.stream.close();
    state.stream = null;
  }
  const stream = new EventSource(url);
  state.stream = stream;
  let ended = false;

  stream.addEventListener('trace', (message) => {
    let event = null;
    try {
      event = JSON.parse(message.data);
    } catch (failure) {
      return;
    }
    onEvent(event);
  });

  stream.addEventListener('end', (message) => {
    ended = true;
    let summary = null;
    try {
      summary = JSON.parse(message.data);
    } catch (failure) {
      summary = null;
    }
    stream.close();
    if (state.stream === stream) state.stream = null;
    onEnd(summary);
  });

  stream.addEventListener('error', () => {
    if (ended) return;
    // `EventSource` retries by itself while the connection is merely interrupted; CLOSED is the
    // state in which it has given up, and only then is the trace genuinely lost.
    if (stream.readyState !== EventSource.CLOSED) return;
    if (state.stream === stream) state.stream = null;
    onLost();
  });

  return stream;
}

// ---------------------------------------------------------------------------------------
// Tabs, splitters, the output region.
// ---------------------------------------------------------------------------------------

function wireTabList(list) {
  if (!list) return;
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  for (const tab of tabs) {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', (event) => {
      const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
      if (step === 0) return;
      event.preventDefault();
      const at = tabs.indexOf(tab);
      const next = tabs[(at + step + tabs.length) % tabs.length];
      next.focus();
      selectTab(next);
    });
  }
}

function selectTab(tab) {
  if (!tab) return;
  const list = tab.closest('[role="tablist"]');
  if (!list) return;
  for (const other of list.querySelectorAll('[role="tab"]')) {
    const chosen = other === tab;
    other.setAttribute('aria-selected', chosen ? 'true' : 'false');
    const panel = document.getElementById(other.dataset.panel);
    if (panel) panel.hidden = !chosen;
  }
}

function wireOutputRegion() {
  dom.outputExpand.addEventListener('click', () => {
    const expanded = dom.outputExpand.getAttribute('aria-pressed') === 'true';
    dom.outputExpand.setAttribute('aria-pressed', expanded ? 'false' : 'true');
    dom.outputRegion.classList.toggle('is-expanded', !expanded);
    // The splitter and the CSS `resize` handle both write an inline height, and an inline size
    // outranks the rule `.is-expanded` carries. Clearing it is what makes the class do anything.
    if (!expanded) dom.outputRegion.style.height = '';
    dom.outputExpand.textContent = expanded ? 'Expand' : 'Collapse';
    if (state.view !== null) state.view.resize();
  });
}

// ---------------------------------------------------------------------------------------
// The graph.
// ---------------------------------------------------------------------------------------

function buildGraph() {
  state.view = createGraphView(dom.graphCanvas, {
    legendElement: dom.graphLegend,
    breadcrumbElement: dom.graphBreadcrumb,
    statusElement: dom.graphStatus,
    emptyElement: dom.graphEmpty,
    controls: {
      zoomIn: dom.graphZoomIn,
      zoomOut: dom.graphZoomOut,
      fit: dom.graphFit,
      reset: dom.graphReset,
    },
    settings: {
      aggregate: dom.settingAggregate,
      labels: dom.settingLabels,
      motion: dom.settingMotion,
      synthesised: dom.settingSynthesised,
    },
    onSelect: showNodeDetail,
    onViewChange: (crumbs) => {
      const current = crumbs[crumbs.length - 1];
      if (current && current.index === 0) markViewMode('full');
    },
  });

  createSplitter(dom.splitDivider, { onResize: () => state.view.resize() });
  createSplitter(dom.outputDivider, {
    minimum: 96,
    onResize: () => {
      // A drag re-establishes an explicit height, so the expanded state is no longer what the
      // user asked for.
      dom.outputRegion.classList.remove('is-expanded');
      dom.outputExpand.setAttribute('aria-pressed', 'false');
      dom.outputExpand.textContent = 'Expand';
      state.view.resize();
    },
  });

  globalThis.addEventListener('resize', () => state.view.resize());
  dom.nodeDetailClose.addEventListener('click', () => {
    dom.nodeDetailCard.hidden = true;
    state.view.select(null);
  });

  dom.viewFull.addEventListener('click', () => showFullGraph());
  dom.viewStory.addEventListener('click', () => showSubgraph('story'));
  dom.viewEvidence.addEventListener('click', () => showSubgraph('evidence'));
}

function markViewMode(mode) {
  state.viewMode = mode;
  const buttons = { full: dom.viewFull, story: dom.viewStory, evidence: dom.viewEvidence };
  for (const [name, button] of Object.entries(buttons)) {
    button.setAttribute('aria-pressed', name === mode ? 'true' : 'false');
  }
}

/** Ids that exist in the drawn view, and a count of the ones that do not. Rule 1. */
function resolveNodeIds(ids) {
  const resolved = [];
  const missing = [];
  for (const id of ids ?? []) {
    const identifier = String(id);
    if (state.view !== null && state.view.node(identifier) !== null) resolved.push(identifier);
    else missing.push(identifier);
  }
  return { resolved, missing };
}

function highlightIds(nodeIds, edgeIds = [], { focus = false } = {}) {
  const nodes = resolveNodeIds(nodeIds);
  const edges = [];
  for (const id of edgeIds ?? []) {
    if (state.view !== null && state.view.edge(String(id)) !== null) edges.push(String(id));
  }
  if (nodes.resolved.length === 0 && edges.length === 0) {
    state.view.clearHighlight();
    return nodes;
  }
  state.view.highlight({
    nodes: nodes.resolved,
    edges,
    includeIncidentEdges: true,
    pulse: dom.settingMotion.checked,
    focus,
  });
  return nodes;
}

function showNodeDetail(nodeId, record) {
  if (nodeId === null || record === null) {
    dom.nodeDetailCard.hidden = true;
    return;
  }
  dom.nodeDetailCard.hidden = false;
  setText(dom.nodeDetailTitle, `${record.label ?? nodeId} · ${record.type ?? ''}`.trim());

  const derived = Boolean(record.synthesised) || record.derived_from !== undefined;
  dom.nodeDetailDerived.hidden = !derived;
  if (derived) {
    dom.nodeDetailDerived.textContent = record.note
      ? String(record.note)
      : `Derived from ${record.derived_from ?? 'a stored property'}, not read as its own record.`;
  }

  const list = clear(dom.nodeDetailProperties);
  field(list, 'id', nodeId, { mono: true });
  for (const key of Object.keys(record).sort()) {
    if (key === 'id' || key === 'note') continue;
    field(list, key, record[key], { mono: true });
  }
  const incident = state.view.edgesOf(nodeId);
  const byType = new Map();
  for (const edge of incident) {
    byType.set(edge.type, (byType.get(edge.type) ?? 0) + 1);
  }
  field(list, 'connections', [...byType.entries()].map(([type, count]) => `${type} × ${count}`)
    .join(', ') || null);
}

// ---------------------------------------------------------------------------------------
// Behaviour 1: the overview.
// ---------------------------------------------------------------------------------------

async function loadOverview() {
  setPipelineState('loading projection');
  try {
    const payload = await getJson(query(ENDPOINTS.graphOverview,
      { projection: state.projectionName }));
    state.overview = payload;
    state.view.setPayload(payload, { name: payload.projection, label: payload.projection });
    markViewMode('full');
    renderIdentity(payload);
    renderHonestLabels(payload.honest_labels);
    setPipelineState('graph loaded');
    clearError();
  } catch (failure) {
    showError('loading the graph projection', failure);
  }
}

function renderIdentity(payload) {
  const snapshot = payload.snapshot ?? {};
  setText(dom.snapshotRunId, snapshot.graph_run_id);
  setText(dom.snapshotCompletedAt, snapshot.completed_at);

  // The company node is derived from `observation.subject_entity_id`; it is found by the
  // property rather than by a type name, so a projection that renames the type still resolves.
  const company = (payload.nodes ?? []).find((node) => node.entity_id);
  setText(dom.headerCompany, company ? company.entity_id : null);

  const counts = payload.counts ?? {};
  setText(dom.countDrawnNodes, formatCount(counts.nodes));
  setText(dom.countDrawnEdges, formatCount(counts.edges));
  setText(dom.countTotalNodes, formatCount(snapshot.total_node_count));
  setText(dom.countTotalEdges, formatCount(snapshot.edge_count));

  const bounds = payload.bounds ?? {};
  dom.projectionDisclosure.textContent = [
    payload.disclosure,
    `Row limit ${formatCount(bounds.row_limit)}, ${formatCount(bounds.rows_returned)} returned, `
    + `timeout ${formatValue(bounds.timeout_seconds)} s, truncated: `
    + `${bounds.truncated ? 'yes' : 'no'}. Content digest ${payload.content_digest ?? ''}.`,
  ].filter(Boolean).join(' ');
  dom.companyDisclosure.textContent = String(payload.company_disclosure ?? '');
}

/**
 * §7's labels, rendered verbatim from the payload rather than from a copy in this file.
 *
 * The shell already states them in its own words; the server's are the ones the artifacts carry,
 * so any the page does not already say are appended rather than substituted. Nothing is removed:
 * three of the footer's statements — derived marking, no nulls, no credentials — are the shell's
 * own and no endpoint sends them.
 */
function renderHonestLabels(labels) {
  const list = dom.standingStatements.querySelector('ul');
  if (!list || !Array.isArray(labels)) return;
  const shown = [...list.querySelectorAll('li')].map((item) => item.textContent.trim());
  for (const label of labels) {
    const sentence = String(label);
    if (shown.some((existing) => existing === sentence)) continue;
    list.append(make('li', null, sentence));
    shown.push(sentence);
  }
}

function showFullGraph() {
  if (state.overview === null) return;
  const crumbs = state.view.breadcrumb();
  if (crumbs.length > 1) state.view.popTo(0);
  markViewMode('full');
  // A new payload clears the renderer's selection without raising `onSelect`, so the card would
  // otherwise keep describing a node that is no longer drawn.
  dom.nodeDetailCard.hidden = true;
  renderIdentity(state.overview);
}

async function showSubgraph(kind) {
  if (state.selectedCandidateId === null) return;
  const key = `${state.selectedCandidateId}|${kind}`;
  try {
    if (!state.subgraphs.has(key)) {
      const payload = await getJson(query(ENDPOINTS.graphSubgraph, {
        candidate_id: state.selectedCandidateId, view: kind,
      }));
      state.subgraphs.set(key, payload);
    }
    const payload = state.subgraphs.get(key);
    const detail = { name: payload.view, label: `${payload.projection} · ${payload.view}` };
    // At most two entries deep: the overview and one focused neighbourhood. Switching between
    // story and evidence replaces the top rather than stacking a third crumb the reader would
    // have to walk back through.
    if (state.view.breadcrumb().length > 1) {
      state.view.setPayload(payload, { ...detail, resetView: false });
    } else {
      state.view.pushView(payload, detail);
    }
    markViewMode(kind);
    dom.nodeDetailCard.hidden = true;
    renderIdentity(payload);
    highlightIds(payload.metric_ids ?? []);
    clearError();
  } catch (failure) {
    showError(`loading the ${kind} neighbourhood`, failure);
  }
}

// ---------------------------------------------------------------------------------------
// Behaviour 2: discovery, on demand, with the trace streamed.
// ---------------------------------------------------------------------------------------

function renderTraceEvent(event) {
  const item = make('li');
  item.dataset.sequence = String(event.sequence);
  const head = make('div');
  head.append(make('strong', null, event.message ?? `${event.stage} · ${event.status}`));
  item.append(head);
  if (event.status === 'failed') item.classList.add('is-blocking');
  else if (event.status === 'warning') item.classList.add('is-warning');

  const pointers = [];
  const nodeIds = event.graph_highlights ? event.graph_highlights.node_ids ?? [] : [];
  const edgeIds = event.graph_highlights ? event.graph_highlights.edge_ids ?? [] : [];
  const factIds = event.related_fact_ids ?? [];
  const sentenceIds = event.related_sentence_ids ?? [];
  if (nodeIds.length) pointers.push(plural(nodeIds.length, 'graph node'));
  if (edgeIds.length) pointers.push(plural(edgeIds.length, 'graph edge'));
  if (factIds.length) pointers.push(plural(factIds.length, 'fact'));
  if (sentenceIds.length) pointers.push(plural(sentenceIds.length, 'sentence'));
  if (pointers.length) item.append(make('div', 'mono', pointers.join(' · ')));

  const resolution = make('div', 'note');
  item.append(resolution);

  item.setAttribute('aria-selected', 'false');
  item.addEventListener('click', () => {
    for (const other of dom.traceEvents.querySelectorAll('li')) {
      other.setAttribute('aria-selected', 'false');
    }
    item.setAttribute('aria-selected', 'true');
    applyEventHighlight(event, resolution);
  });

  dom.traceEvents.append(item);
  return item;
}

/**
 * Light up what one trace step pointed at.
 *
 * The node ids come first because they are what §3 calls a highlight. Fact ids are used as well
 * because a generation event carries no node ids and an observation id *is* a node id in the two
 * candidate projections — verified against the live evidence subgraph. Both are resolved through
 * `view.node` before they are drawn, and what did not resolve is said out loud.
 */
function applyEventHighlight(event, resolution) {
  const highlights = event.graph_highlights ?? {};
  const wanted = [...(highlights.node_ids ?? []), ...(event.related_fact_ids ?? [])];
  const outcome = highlightIds(wanted, highlights.edge_ids ?? [],
    { focus: wanted.length > 0 && wanted.length <= 40 });
  if (resolution) {
    const total = wanted.length;
    resolution.textContent = total === 0
      ? 'This step named no graph id.'
      : `${outcome.resolved.length} of ${total} ids are in the drawn view`
        + `${outcome.missing.length ? `; ${outcome.missing.length} are not` : ''}. `
        + LABELS.highlightResolution;
  }
  const sentences = (event.related_sentence_ids ?? []).map(Number).filter(Number.isInteger);
  if (sentences.length === 1) revealSentence(sentences[0], { navigate: false });
}

async function runDiscovery() {
  if (state.busy) return;
  state.busy = true;
  dom.runDiscovery.disabled = true;
  dom.discoveryExplainer.hidden = true;
  clear(dom.candidateList);
  clear(dom.traceEvents);
  dom.candidateDetail.hidden = true;
  state.traceEvents = [];
  state.streamLost = false;
  setText(dom.discoveryState, 'starting');
  setPipelineState('finding story suggestions');
  selectTab(dom.tabTrace);

  try {
    const started = await postJson(ENDPOINTS.storySuggestions, { max_suggestions: 50 });
    state.discoveryRunId = started.run_id;
    const params = { run_id: started.run_id };
    setText(dom.discoveryState, `${started.run_id} · running`);

    openStream(serverPath(started.events_url, ENDPOINTS.discoveryEvents, params), {
      onEvent: (event) => {
        state.traceEvents.push(event);
        renderTraceEvent(event);
        setText(dom.discoveryState, `${started.run_id} · ${event.stage} · ${event.status}`);
        const ids = event.graph_highlights ? event.graph_highlights.node_ids ?? [] : [];
        if (ids.length) highlightIds(ids, event.graph_highlights.edge_ids ?? []);
      },
      onEnd: () => finishDiscovery(params),
      onLost: () => {
        state.streamLost = true;
        setText(dom.discoveryState, 'stream lost');
        finishDiscovery(params);
      },
    });
  } catch (failure) {
    state.busy = false;
    dom.runDiscovery.disabled = false;
    showError('starting discovery', failure);
  }
}

async function finishDiscovery(params) {
  try {
    const payload = await getJson(serverPath(null, ENDPOINTS.discoveryResult, params));
    state.busy = false;
    dom.runDiscovery.disabled = false;
    if (payload.error) {
      const failure = new ApiFailure(payload, payload.error.status ?? 500);
      setText(dom.discoveryState, `failed · ${payload.error.code}`);
      showError('the discovery run', failure);
      renderDiscoveryFailure(payload);
      return;
    }
    if (!payload.ready || !payload.result) {
      setText(dom.discoveryState, `${payload.run.status} · no result`);
      showError('the discovery run', new Error(
        `the run reported ${payload.run.status} and produced no result`));
      return;
    }
    renderSuggestions(payload.result);
    setPipelineState('suggestions ready');
    settleBanner();
    selectTab(dom.tabStory);
  } catch (failure) {
    state.busy = false;
    dom.runDiscovery.disabled = false;
    showError('reading the discovery result', failure);
  }
}

function renderDiscoveryFailure(payload) {
  const host = clear(dom.candidateList);
  const item = make('li');
  item.append(make('strong', null, `No suggestions: ${payload.error.code}`));
  item.append(make('div', null, payload.error.message));
  const failure = payload.failure;
  if (failure && Array.isArray(failure.refusal_codes) && failure.refusal_codes.length) {
    item.append(make('div', 'mono', `refused: ${failure.refusal_codes.join(', ')}`));
  }
  host.append(item);
}

function renderSuggestions(result) {
  state.suggestions = result.suggestions ?? [];
  state.candidateCounts = result.counts ?? {};
  const host = clear(dom.candidateList);

  setText(dom.discoveryState,
    `${formatCount(state.candidateCounts.returned)} of `
    + `${formatCount(state.candidateCounts.candidates)} ranked candidates`);

  const heading = make('li');
  heading.append(make('div', null,
    `${plural(state.candidateCounts.candidates ?? 0, 'candidate')} ranked, `
    + `${plural(state.candidateCounts.clusters ?? 0, 'cluster')}, `
    + `${plural(state.candidateCounts.representatives ?? 0, 'representative')}. `
    + `Ranking policy ${result.ranking_policy_version}.`));
  heading.append(make('div', 'note', result.score_disclaimer ?? ''));
  heading.append(make('div', 'note', LABELS.positionAndRank));
  host.append(heading);

  for (const suggestion of state.suggestions) {
    host.append(suggestionRow(suggestion));
  }
}

function suggestionRow(suggestion) {
  const item = make('li');
  item.setAttribute('aria-selected', 'false');
  item.dataset.candidateId = suggestion.candidate_id;
  item.tabIndex = 0;

  const head = make('div');
  head.append(make('strong', null,
    `#${suggestion.position} · ${suggestion.story_type.replace(/_/g, ' ')}`));
  head.append(make('span', 'mono',
    `  score ${formatNumber(suggestion.score.total)} · rank ${suggestion.rank}`));
  item.append(head);

  item.append(make('div', null,
    `${(suggestion.metric_ids ?? []).join(' vs ')} · `
    + `${(suggestion.anchor_period_keys ?? []).join(', ')}`));
  item.append(make('div', 'note',
    `${suggestion.detector_id} · `
    + (suggestion.is_representative
      ? 'cluster representative'
      : `cluster member (rank carried from ${suggestion.dedup_group})`)));
  if ((suggestion.warnings ?? []).length) {
    item.append(make('div', 'mono', `warnings: ${suggestion.warnings.join(', ')}`));
  }

  const choose = () => selectCandidate(suggestion.candidate_id);
  item.addEventListener('click', choose);
  item.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    choose();
  });
  return item;
}

// ---------------------------------------------------------------------------------------
// Behaviours 3 and 4: one candidate, its score, and its neighbourhood.
// ---------------------------------------------------------------------------------------

async function selectCandidate(candidateId) {
  state.selectedCandidateId = candidateId;
  for (const row of dom.candidateList.querySelectorAll('li[data-candidate-id]')) {
    row.setAttribute('aria-selected', row.dataset.candidateId === candidateId ? 'true' : 'false');
  }
  dom.viewStory.disabled = false;
  dom.viewEvidence.disabled = false;
  dom.buildPackage.disabled = false;

  try {
    const payload = await getJson(path(ENDPOINTS.candidate, { candidate_id: candidateId }));
    state.candidateDetail = payload;
    renderCandidate(payload);
    dom.candidateDetail.hidden = false;
    await showSubgraph('story');
    const suggestion = payload.suggestion ?? {};
    highlightIds([
      ...(suggestion.metric_ids ?? []),
      ...(suggestion.anchor_observation_ids ?? []),
    ], [], { focus: true });
    clearError();
  } catch (failure) {
    showError('reading the candidate', failure);
  }
}

function renderCandidate(payload) {
  // `#candidate-detail` holds the shell's own children — the title, the two lists, the caveat
  // and the button — and everything this function adds beside them is tagged and removed first,
  // so selecting a second candidate replaces the panel rather than growing it.
  for (const stale of dom.candidateDetail.querySelectorAll('[data-dynamic="true"]')) {
    stale.remove();
  }

  const suggestion = payload.suggestion ?? {};
  setText(dom.candidateTitle,
    `${suggestion.story_type.replace(/_/g, ' ')} · `
    + `${(suggestion.metric_ids ?? []).join(' vs ')}`);

  const list = clear(dom.candidateProperties);
  field(list, 'candidate id', payload.candidate_id, { mono: true });
  field(list, 'position', `${suggestion.position} of `
    + `${formatCount(state.candidateCounts ? state.candidateCounts.candidates : undefined)}`);
  field(list, 'rank (§6.10)', suggestion.rank);
  field(list, 'in the returned list', payload.in_suggestions);
  field(list, 'cluster representative', suggestion.is_representative);
  field(list, 'dedup group', suggestion.dedup_group, { mono: true });
  field(list, 'cluster members', (suggestion.cluster_member_ids ?? []).length);
  field(list, 'detector', `${suggestion.detector_id} ${suggestion.detector_version}`);
  field(list, 'subject', suggestion.subject_entity_id);
  field(list, 'audience', suggestion.audience);
  field(list, 'periods', (suggestion.anchor_period_keys ?? []).join(', '));
  field(list, 'anchor observations', (suggestion.anchor_observation_ids ?? []).length);

  const signals = suggestion.signals ?? {};
  const signalList = document.createElement('dl');
  signalList.className = 'mono';
  for (const key of Object.keys(signals).sort()) {
    field(signalList, key, signals[key]);
  }
  const signalBox = details(dom.candidateDetail,
    `Detector signals (${Object.keys(signals).length})`);
  signalBox.dataset.dynamic = 'true';
  signalBox.append(make('p', 'note',
    'Every one of these is a number this pipeline measured. A shortened figure keeps its exact '
    + 'value on the row’s tooltip.'), signalList);
  dom.candidateProperties.after(signalBox);

  renderScore(suggestion.score ?? {}, payload.score_disclaimer);
  renderCandidateWarnings(payload.warnings ?? []);
}

function renderScore(score, disclaimer) {
  const host = clear(dom.scoreBreakdown);
  const contributions = score.contributions ?? {};
  const components = score.components ?? {};
  const largest = Math.max(1e-9,
    ...Object.values(contributions).map((value) => Math.abs(Number(value) || 0)));

  const total = make('div', 'score-row');
  total.append(make('span', null, 'total'));
  total.append(make('span', 'mono', `policy ${score.policy_version ?? ''}`));
  total.append(make('span', 'mono', formatNumber(score.total ?? 0)));
  host.append(total);

  for (const name of Object.keys(contributions).sort()) {
    const contribution = Number(contributions[name]) || 0;
    const row = make('div', 'score-row');
    row.append(make('span', null, name));
    const track = make('div');
    const bar = make('div', 'score-bar');
    bar.style.width = `${Math.round((Math.abs(contribution) / largest) * 100)}%`;
    track.append(bar);
    row.append(track);
    const value = make('span', 'mono', formatNumber(contribution));
    value.title = `component ${String(components[name])}, contribution ${String(contribution)}`;
    row.append(value);
    host.append(row);
  }

  for (const [name, reason] of Object.entries(score.suppressed ?? {})) {
    const row = make('div', 'score-row score-suppressed');
    row.append(make('span', null, name));
    row.append(make('span', 'is-absent', String(reason)));
    row.append(make('span', 'mono', LABELS.notMeasured));
    host.append(row);
  }

  if (disclaimer) host.append(make('p', 'note', disclaimer));
  host.append(make('p', 'note',
    `is_probability: ${score.is_probability === false ? 'false' : String(score.is_probability)}`));
}

function renderCandidateWarnings(warnings) {
  if (!warnings.length) return;
  const box = details(dom.candidateDetail, `Candidate warnings (${warnings.length})`);
  box.dataset.dynamic = 'true';
  for (const warning of warnings) {
    const row = make('div');
    row.append(make('strong', null, warning.code));
    if (warning.description) row.append(make('div', null, warning.description));
    else row.append(make('div', 'is-absent', 'no catalogue entry documents this code'));
    if (warning.remedy) row.append(make('div', 'note', warning.remedy));
    box.append(row);
  }
}

// ---------------------------------------------------------------------------------------
// Behaviour 5: the evidence package.
// ---------------------------------------------------------------------------------------

async function buildEvidencePackage() {
  if (state.selectedCandidateId === null) return;
  dom.buildPackage.disabled = true;
  setPipelineState('building the evidence package');
  try {
    const payload = await postJson(ENDPOINTS.evidencePackage,
      { candidate_id: state.selectedCandidateId });
    state.packagePayload = payload;
    renderPackage(payload);
    selectTab(dom.tabFacts);
    setPipelineState('evidence package built');
    clearError();
  } catch (failure) {
    showError('building the evidence package', failure);
  } finally {
    dom.buildPackage.disabled = false;
  }
}

function renderPackage(payload) {
  const pkg = payload.package ?? {};
  const host = clear(dom.packageSummary);
  const list = document.createElement('dl');
  field(list, 'package id', pkg.package_id, { mono: true });
  field(list, 'content digest', pkg.package_content_digest, { mono: true });
  field(list, 'rebuilt', payload.rebuilt);
  if (payload.rebuilt) {
    field(list, 'previous digest', payload.previous_digest, { mono: true });
    field(list, 'digest stable', payload.digest_stable);
  }
  field(list, 'package version', pkg.package_version);
  field(list, 'graph run', payload.graph_run_id, { mono: true });
  host.append(list);

  const counts = pkg.counts ?? {};
  const countLine = Object.entries(counts)
    .filter(([, value]) => value > 0)
    .map(([name, value]) => `${name} ${value}`)
    .join(' · ');
  host.append(make('p', 'mono', countLine));

  renderFacts(payload.facts ?? []);
  renderPackageBounds(payload);
}

function renderFacts(facts) {
  const host = clear(dom.factsList);
  if (!facts.length) {
    host.append(make('li', 'is-absent', 'The package carries no facts.'));
    return;
  }
  for (const fact of facts) {
    const item = make('li');
    item.dataset.factId = fact.observation_id;
    const head = make('div');
    head.append(make('strong', null, fact.metric_label ?? fact.metric_id));
    head.append(make('span', 'mono',
      `  ${formatNumber(fact.value)} ${fact.unit ?? ''} · ${fact.period_key}`));
    item.append(head);
    const line = make('div', 'mono', fact.observation_id);
    item.append(line);
    if (fact.quoted_text) {
      item.append(make('div', null, `quoted: “${fact.quoted_text}”`));
    }
    const meta = make('div', 'note',
      `${fact.source_lane} · ${fact.validation_state} · ${fact.document_id ?? ''}`);
    item.append(meta);
    if ((fact.warning_codes ?? []).length) {
      item.append(make('div', 'mono', `warnings: ${fact.warning_codes.join(', ')}`));
    }
    item.addEventListener('click', () => {
      highlightIds([fact.observation_id, fact.passage_id, fact.document_id].filter(Boolean),
        [], { focus: true });
    });
    host.append(item);
  }
}

function renderPackageBounds(payload) {
  const host = clear(dom.packageBounds);
  const pkg = payload.package ?? {};
  const budget = pkg.budget ?? {};

  const budgetBox = details(host, 'Budget and caps', { open: true });
  const list = document.createElement('dl');
  field(list, 'prompt token estimate', budget.prompt_token_estimate);
  field(list, 'artifact token estimate', budget.artifact_token_estimate);
  field(list, 'caps hit', (budget.caps_hit ?? []).join(', '));
  budgetBox.append(list);
  const parameters = document.createElement('dl');
  parameters.className = 'mono';
  for (const key of Object.keys(budget.parameters ?? {}).sort()) {
    field(parameters, key, budget.parameters[key]);
  }
  const parameterBox = details(budgetBox, 'Budget parameters');
  parameterBox.append(parameters);

  const warnings = pkg.warnings ?? [];
  const warningBox = details(host, `Package warnings (${warnings.length})`,
    { open: warnings.length > 0 });
  for (const warning of warnings) {
    const row = make('div');
    row.append(make('strong', null, `${warning.code} · ${warning.severity}`));
    row.append(make('div', null, warning.detail));
    const explanation = warning.explanation ?? {};
    if (explanation.description) row.append(make('div', 'note', explanation.description));
    if ((warning.subject_ids ?? []).length) {
      row.append(make('div', 'mono', warning.subject_ids.join(', ')));
    }
    warningBox.append(row);
  }

  const trace = pkg.retrieval_trace ?? [];
  const traceBox = details(host, `Retrieval trace (${trace.length} calls)`);
  const timing = pkg.retrieval_timing ?? {};
  traceBox.append(make('p', 'note',
    timing.measured === false ? String(timing.reason) : 'timing measured on this build'));
  for (const entry of trace) {
    const row = make('div', 'mono',
      `${entry.tool} · ${entry.outcome} · rows ${entry.row_count}`
      + `${entry.truncated ? ' · truncated' : ''}`);
    row.title = JSON.stringify(entry.parameters ?? {});
    traceBox.append(row);
  }

  const freshness = payload.freshness ?? {};
  const checks = freshness.checks ?? [];
  const freshnessBox = details(host, `Freshness checks (${checks.length})`);
  for (const check of checks) {
    // `detail` is deliberately not rendered: it carries an absolute filesystem path for two of
    // the fourteen checks, which names the operator's home directory. Reported to the endpoint
    // workstream rather than trimmed here, because trimming a string is not fixing a leak.
    const row = make('div', check.passed ? 'mono' : 'mono is-blocking',
      `${check.name} · ${check.passed ? 'passed' : `failed (${check.code})`}`
      + ` · expected ${check.expected} · observed ${check.observed}`);
    freshnessBox.append(row);
  }
}

// ---------------------------------------------------------------------------------------
// Behaviour 6: prompts.
// ---------------------------------------------------------------------------------------

async function loadPresets() {
  try {
    const payload = await getJson(ENDPOINTS.promptPresets);
    state.presets = payload;
    state.presetById = new Map((payload.presets ?? []).map((preset) => [preset.preset_id, preset]));
    const select = clear(dom.promptPreset);
    for (const preset of payload.presets ?? []) {
      const option = document.createElement('option');
      option.value = preset.preset_id;
      option.textContent = preset.label;
      option.title = preset.description;
      select.append(option);
    }
    select.value = payload.default_preset_id;
    applyPreset(payload.default_preset_id);
    renderFixedRules(payload);
    clearError();
  } catch (failure) {
    showError('loading the prompt presets', failure);
  }
}

function applyPreset(presetId) {
  const preset = state.presetById.get(presetId);
  if (!preset) return;
  state.selectedPresetId = presetId;
  dom.plannerInstructions.value = preset.planner_instructions ?? '';
  dom.writerInstructions.value = preset.writer_instructions ?? '';
  dom.styleGuidance.value = preset.style_guidance ?? '';
  state.composed = null;
  state.requiresLiveOffered = false;
  dom.generatePost.textContent = 'Generate post';
  onPromptEdited();
}

function editableFields() {
  return {
    planner_instructions: dom.plannerInstructions.value,
    writer_instructions: dom.writerInstructions.value,
    style_guidance: dom.styleGuidance.value,
  };
}

function onPromptEdited() {
  renderPromptLengths();
  renderAdvisories(null);
  renderPromptDiff();
  renderReplayNotice();
}

function renderPromptLengths() {
  const limits = state.presets ? state.presets.limits ?? {} : {};
  const values = editableFields();
  const total = Object.values(values).reduce((sum, text) => sum + text.length, 0);
  const parts = Object.entries(values).map(([name, text]) => {
    const lines = text === '' ? 0 : text.split('\n').length;
    return `${name} ${text.length}/${limits.max_field_chars ?? '?'} chars, `
      + `${lines}/${limits.max_field_lines ?? '?'} lines`;
  });
  parts.push(`total ${total}/${limits.max_total_editable_chars ?? '?'} chars`);
  dom.promptLengths.textContent = parts.join(' · ');
  const over = total > (limits.max_total_editable_chars ?? Infinity)
    || Object.values(values).some((text) => text.length > (limits.max_field_chars ?? Infinity));
  dom.promptLengths.classList.toggle('is-blocking', over);
}

/**
 * The advisory scan, previewed in the browser and then replaced by the server's.
 *
 * The phrase list, the concern and the refusal code all come from `/demo/prompt-presets`, and
 * the match is the documented one — case-insensitive over whitespace-collapsed text. This is a
 * preview so a typist sees the warning while typing; the server runs its own on the request and
 * that result is what is rendered once there is one. Neither blocks anything, which the
 * disclaimer the server publishes says in its own words.
 */
function renderAdvisories(serverAdvisories) {
  const host = clear(dom.promptWarnings);
  const advisory = state.presets ? state.presets.advisory ?? {} : {};
  let rows = serverAdvisories;
  let preview = false;
  if (rows === null || rows === undefined) {
    preview = true;
    rows = [];
    const values = editableFields();
    for (const [fieldName, text] of Object.entries(values)) {
      const collapsed = text.toLowerCase().split(/\s+/).join(' ');
      for (const entry of advisory.phrases ?? []) {
        if (collapsed.includes(String(entry.phrase).toLowerCase())) {
          rows.push({
            field: fieldName,
            phrase: entry.phrase,
            concern: entry.concern,
            refusal_code: entry.refusal_code,
          });
        }
      }
    }
  }
  if (!rows.length) return;
  host.append(make('strong', null,
    `${plural(rows.length, 'advisory warning')}${preview ? ' (preview)' : ''}`));
  for (const row of rows) {
    const item = make('div');
    item.append(make('div', null, `“${row.phrase}” in ${row.field}`));
    item.append(make('div', 'note', row.concern));
    item.append(make('div', 'mono', `the verifier refuses this as ${row.refusal_code}`));
    host.append(item);
  }
  host.append(make('p', 'note', preview ? LABELS.advisoryPreview : advisory.disclaimer ?? ''));
}

/** A line diff, LCS-based. Both texts are bounded to twenty lines by the server's own limit. */
function diffLines(before, after) {
  const a = before === '' ? [] : before.split('\n');
  const b = after === '' ? [] : after.split('\n');
  const table = Array.from({ length: a.length + 1 }, () => new Int32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push(`  ${a[i]}`); i += 1; j += 1; } else if (
      table[i + 1][j] >= table[i][j + 1]) { out.push(`- ${a[i]}`); i += 1; } else {
      out.push(`+ ${b[j]}`); j += 1;
    }
  }
  while (i < a.length) { out.push(`- ${a[i]}`); i += 1; }
  while (j < b.length) { out.push(`+ ${b[j]}`); j += 1; }
  return out;
}

function renderPromptDiff() {
  const host = clear(dom.promptDiff);
  if (state.composed !== null) {
    host.append(make('p', 'note', LABELS.serverDiff));
    for (const stage of ['planner', 'writer']) {
      const block = state.composed[stage] ?? {};
      const box = details(host, `${stage} system message · `
        + `${block.edited ? 'edited' : 'unedited'} · ${block.system_sha256 ?? ''}`,
      { open: Boolean(block.edited) });
      if (!(block.diff ?? []).length) {
        box.append(make('div', 'is-absent', 'identical to the default'));
        continue;
      }
      for (const line of block.diff) box.append(diffLine(line));
    }
    return;
  }

  const defaultPreset = state.presetById.get(
    state.presets ? state.presets.default_preset_id : '') ?? {};
  host.append(make('p', 'note', LABELS.localDiff));
  const values = editableFields();
  let changed = false;
  for (const [name, text] of Object.entries(values)) {
    const before = String(defaultPreset[name] ?? '');
    if (before === text) continue;
    changed = true;
    const box = details(host, name, { open: true });
    for (const line of diffLines(before, text)) box.append(diffLine(line));
  }
  if (!changed) host.append(make('div', 'is-absent', 'identical to the default preset'));
}

function diffLine(line) {
  const text = String(line);
  const mark = text.charAt(0);
  const className = mark === '+' ? 'diff-added' : mark === '-' ? 'diff-changed' : 'mono';
  return make('div', className, text);
}

function renderReplayNotice() {
  const host = clear(dom.replayNotice);
  host.append(make('span', null, `${LABELS.requiresLive} `));
  if (state.composed !== null) {
    host.append(make('strong', null,
      state.composed.requires_live ? 'This configuration needs a live run. ' : 'Unedited. '));
    host.append(make('span', null, String(state.composed.requires_live_reason ?? '')));
    return;
  }
  const defaultPreset = state.presetById.get(
    state.presets ? state.presets.default_preset_id : '') ?? {};
  const edited = Object.entries(editableFields())
    .some(([name, text]) => text !== String(defaultPreset[name] ?? ''));
  host.append(make('strong', null, edited
    ? 'As typed, this differs from the recorded default, so the server is expected to ask for a '
      + 'live run. The server decides, not this page.'
    : 'As typed, this matches the recorded default, so the replay store can answer it.'));
}

function renderFixedRules(payload) {
  const host = clear(dom.fixedRules);
  host.append(make('p', 'note',
    `${(payload.fixed_sections ?? []).length} fixed sections. Request fields the server reads: `
    + `${(payload.limits ? payload.limits.request_fields ?? [] : []).join(', ')}.`));
  for (const section of payload.fixed_sections ?? []) {
    const box = details(host, `${section.title} · ${section.stage}`);
    box.append(make('div', 'mono', section.source));
    if (section.note) box.append(make('p', 'note', section.note));
    if (section.text) box.append(make('div', null, section.text));
    for (const rule of section.rules ?? []) {
      const row = make('div');
      row.append(make('strong', null, `${rule.number}. `));
      row.append(make('span', null, rule.text));
      if ((rule.refusal_codes ?? []).length) {
        row.append(make('div', 'mono', `refuses as: ${rule.refusal_codes.join(', ')}`));
      }
      box.append(row);
    }
  }
  for (const entry of payload.not_exposed ?? []) {
    const box = details(host, `Not exposed: ${entry.name}`);
    box.append(make('div', null, entry.reason));
    box.append(make('div', 'mono', `${entry.kind} · value ${entry.value}`));
  }
  for (const caveat of payload.caveats ?? []) {
    const box = details(host, caveat.title);
    box.append(make('div', null, caveat.text));
  }
}

// ---------------------------------------------------------------------------------------
// Behaviour 7: generation.
// ---------------------------------------------------------------------------------------

async function generatePost() {
  if (state.selectedCandidateId === null) {
    showError('generating a post', new Error('no candidate is selected'));
    return;
  }
  if (state.busy) return;
  state.busy = true;
  dom.generatePost.disabled = true;
  clear(dom.traceEvents);
  state.traceEvents = [];
  state.streamLost = false;
  resetOutput();
  setPipelineState('generating');
  selectTab(dom.tabTrace);

  const request = {
    candidate_id: state.selectedCandidateId,
    preset_id: state.selectedPresetId,
    ...editableFields(),
  };
  if (state.requiresLiveOffered) request.live = true;

  try {
    const started = await postJson(ENDPOINTS.generate, request);
    state.composed = started.prompt ?? null;
    state.generationRunId = started.run_id;
    renderPromptDiff();
    renderReplayNotice();
    renderAdvisories(state.composed ? state.composed.advisories ?? [] : null);
    renderStyleDelivery(started.style_delivery);

    const params = { run_id: started.run_id };
    openStream(serverPath(started.events_url, ENDPOINTS.runEvents, params), {
      onEvent: (event) => {
        state.traceEvents.push(event);
        renderTraceEvent(event);
        setPipelineState(`generating · ${event.stage} · ${event.status}`);
      },
      onEnd: () => finishGeneration(params),
      onLost: () => {
        state.streamLost = true;
        finishGeneration(params);
      },
    });
  } catch (failure) {
    state.busy = false;
    dom.generatePost.disabled = false;
    if (failure instanceof ApiFailure && failure.code === 'edited_prompt_requires_live') {
      renderRequiresLive(failure);
      return;
    }
    showError('starting the generation run', failure);
  }
}

/**
 * The 409, rendered as the thing to do next.
 *
 * `edited_prompt_requires_live` is not a fault: it is §6's stated consequence of editing a
 * prompt, and the body carries the whole composition beside the error, including which of the
 * three inputs moved. So the panel says that, shows the diff that caused it, and turns the
 * button into the action that answers it.
 */
function renderRequiresLive(failure) {
  const body = failure.body ?? {};
  state.composed = body.prompt ?? null;
  renderPromptDiff();
  renderReplayNotice();
  renderAdvisories(state.composed ? state.composed.advisories ?? [] : null);

  const host = dom.promptWarnings;
  host.append(make('strong', null, 'This prompt needs a live model server'));
  host.append(make('div', null, failure.message));
  if (state.composed) {
    host.append(make('div', 'note', String(state.composed.requires_live_reason ?? '')));
    host.append(make('div', 'mono',
      `effective prompt ${state.composed.effective_prompt_sha256 ?? ''}`));
  }
  host.append(make('div', null,
    'Press the button again to run it live against the configured model server, or reset to the '
    + 'preset to use the recorded answer.'));
  state.requiresLiveOffered = true;
  dom.generatePost.textContent = 'Generate post (live run)';
  setPipelineState('edited prompt needs a live run');
  selectTab(dom.tabPrompts);
}

function renderStyleDelivery(delivery) {
  if (!delivery || delivery.is_recorded_default) return;
  dom.promptWarnings.append(make('div', 'note',
    `Style delivered by ${delivery.mechanism}. ${delivery.note ?? ''}`));
}

async function finishGeneration(params) {
  state.busy = false;
  dom.generatePost.disabled = false;
  try {
    const payload = await getJson(serverPath(null, ENDPOINTS.runResult, params));
    if (payload.error) {
      setPipelineState(`generation failed: ${payload.error.code}`);
      showError('the generation run', new ApiFailure(payload, payload.error.status ?? 500));
      renderRunFailure(payload);
      return;
    }
    if (!payload.ready || !payload.outcome) {
      setPipelineState(`generation ${payload.run.status}`);
      showError('the generation run', new Error(
        `the run reported ${payload.run.status} and produced no outcome`));
      return;
    }
    state.outcome = payload.outcome;
    renderOutcome(payload.outcome);
    setPipelineState(`generation ${payload.outcome.disposition}`);

    const sources = await getJson(serverPath(null, ENDPOINTS.runSources, params));
    state.sources = sources.sources ?? null;
    renderSources(state.sources);
    settleBanner();
  } catch (failure) {
    showError('reading the generation outcome', failure);
  }
}

function renderRunFailure(payload) {
  dom.verdictBadge.textContent = 'failed';
  dom.verdictBadge.className = 'mono is-rejected';
  const host = clear(dom.postOutput);
  host.append(make('h3', null, 'The run did not produce a post'));
  host.append(make('p', null, payload.error.message));
  host.append(make('p', 'mono', `${payload.error.code} · ${payload.error.status}`));
  selectTab(dom.tabPost);
}

// ---------------------------------------------------------------------------------------
// Behaviour 8: the plan, the draft and the verification panel.
// ---------------------------------------------------------------------------------------

function resetOutput() {
  clear(dom.postOutput);
  clear(dom.verificationSummary);
  clear(dom.verificationFindings);
  clear(dom.coverageFigures);
  clear(dom.sourcesList);
  dom.postMeta.textContent = '';
  dom.verdictBadge.textContent = '';
  dom.verdictBadge.className = 'mono';
  state.sentenceRows.clear();
  state.passageRows.clear();
  state.factRows.clear();
}

/**
 * Markdown, rendered by construction rather than by escaping.
 *
 * Headings, unordered lists, paragraphs, inline code and inline emphasis, each emitted as an
 * element with `textContent`. There is no string of markup anywhere in the path, so there is
 * nothing for an escaping bug to leak through — which matters because this text is the one
 * thing on the page a model wrote.
 */
function renderMarkdown(host, markdown) {
  const lines = String(markdown ?? '').split('\n');
  let paragraph = [];
  let list = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    const element = make('p');
    renderInline(element, paragraph.join(' '));
    host.append(element);
    paragraph = [];
  };
  const flushList = () => {
    if (list !== null) host.append(list);
    list = null;
  };

  for (const line of lines) {
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    if (heading !== null) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1].length + 2, 6);
      const element = make(`h${level}`);
      renderInline(element, heading[2]);
      host.append(element);
    } else if (bullet !== null) {
      flushParagraph();
      if (list === null) list = document.createElement('ul');
      const item = document.createElement('li');
      renderInline(item, bullet[1]);
      list.append(item);
    } else if (line.trim() === '') {
      flushParagraph();
      flushList();
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  flushParagraph();
  flushList();
}

function renderInline(host, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  for (const match of String(text).matchAll(pattern)) {
    if (match.index > last) host.append(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    if (token.startsWith('`')) host.append(make('code', null, token.slice(1, -1)));
    else host.append(make('strong', null, token.slice(2, -2)));
    last = match.index + token.length;
  }
  if (last < text.length) host.append(document.createTextNode(text.slice(last)));
}

function renderOutcome(outcome) {
  resetOutput();
  const accepted = outcome.rendered_as === 'post';
  dom.verdictBadge.textContent = outcome.disposition;
  dom.verdictBadge.className = `mono ${accepted ? 'is-accepted' : 'is-rejected'}`;

  const host = dom.postOutput;
  if (accepted && outcome.post) {
    renderMarkdown(host, outcome.post.markdown);
  } else {
    // `rendered_as` is the branch, not the disposition string: a run that was refused shows the
    // refused draft and the findings that refused it, and there is no path here that can put an
    // accepted-looking post on the screen for a rejected run.
    host.append(make('h3', null, `Refused · ${outcome.disposition}`));
    host.append(make('p', null, LABELS.rejectedRun));
    const rejection = outcome.rejection ?? {};
    host.append(make('p', 'mono',
      `refused at ${rejection.stage ?? ''} · artifact ${rejection.filename ?? ''} · `
      + `verifier ran: ${rejection.verifier_ran ? 'yes' : 'no'}`));
    for (const code of rejection.codes ?? []) {
      const row = make('div', 'is-blocking');
      row.append(make('strong', null, code.code));
      row.append(make('div', null, code.description || 'no catalogue entry documents this code'));
      if (code.remedy) row.append(make('div', 'note', code.remedy));
      host.append(row);
    }
    if (outcome.draft) {
      const box = details(host, 'The refused draft, as written', { open: true });
      for (const sentence of outcome.draft.sentences ?? []) {
        box.append(make('p', null, sentence.text));
      }
    }
  }

  renderPlan(host, outcome.plan);
  renderDraft(host, outcome);
  renderPostMeta(outcome);
  renderVerification(outcome.verification);
  selectTab(dom.tabPost);
}

function renderPlan(host, plan) {
  const box = details(host, plan === null ? 'Editorial plan — none' : 'Editorial plan');
  if (plan === null || plan === undefined) {
    box.append(make('div', 'is-absent', 'The planner produced no plan.'));
    return;
  }
  const list = document.createElement('dl');
  field(list, 'thesis', plan.thesis);
  field(list, 'why it matters', plan.why_it_matters);
  field(list, 'uncertainty', plan.uncertainty);
  field(list, 'causal language', plan.causal_language);
  field(list, 'structure', (plan.structure ?? []).join(' → '));
  field(list, 'model', plan.model_id, { mono: true });
  field(list, 'package', plan.package_id, { mono: true });
  box.append(list);

  for (const [title, rows] of [['Key points', plan.key_points ?? []],
    ['Counterpoints', plan.counterpoints ?? []]]) {
    const group = make('div');
    group.append(make('strong', null, `${title} (${rows.length})`));
    for (const point of rows) {
      const row = make('div');
      row.append(make('div', null, point.claim));
      row.append(make('div', 'mono', `${point.statement_class} · `
        + `facts ${(point.required_fact_ids ?? []).length} · `
        + `passages ${(point.required_citation_passage_ids ?? []).length}`));
      const ids = [...(point.required_fact_ids ?? []),
        ...(point.required_citation_passage_ids ?? [])];
      row.addEventListener('click', () => highlightIds(ids, [], { focus: true }));
      group.append(row);
    }
    box.append(group);
  }
  if ((plan.prohibited_claims ?? []).length) {
    const group = make('div');
    group.append(make('strong', null, 'Prohibited claims'));
    for (const claim of plan.prohibited_claims) group.append(make('div', 'note', claim));
    box.append(group);
  }
  if ((plan.required_warnings ?? []).length) {
    box.append(make('div', 'mono', `required warnings: ${plan.required_warnings.join(', ')}`));
  }
}

/**
 * The draft, one row per sentence: what kind of sentence it is, what facts it is bound to, what
 * it cites, and what the verifier said about it.
 *
 * The verification status is per sentence and is computed from the findings' own
 * `sentence_index`, so a sentence with no finding reads "no finding", not "verified" — the
 * verifier reports what it refused, and turning silence into a pass would be this layer
 * inventing a result.
 */
function renderDraft(host, outcome) {
  const draft = outcome.draft;
  const box = details(host, draft === null || draft === undefined
    ? 'Draft — none' : `Draft · ${(draft.sentences ?? []).length} sentences`,
  { open: true });
  if (!draft) {
    box.append(make('div', 'is-absent', 'The writer produced no draft.'));
    return;
  }
  box.append(make('div', 'mono',
    `title: ${draft.title} · style ${draft.style_profile_id} · model ${draft.model_id}`));

  // From the checks *and* from the top-level list: `VerificationResult` carries findings in both
  // places — `all_findings` is what the server sums — so reading one of them would lose the
  // other's rows and make a sentence look unexamined.
  const verification = outcome.verification ?? {};
  const findingsBySentence = new Map();
  const everyFinding = [
    ...(verification.findings ?? []),
    ...(verification.checks ?? []).flatMap((check) => check.findings ?? []),
  ];
  for (const finding of everyFinding) {
    if (finding.sentence_index === null || finding.sentence_index === undefined) continue;
    const key = Number(finding.sentence_index);
    if (!findingsBySentence.has(key)) findingsBySentence.set(key, []);
    findingsBySentence.get(key).push(finding);
  }

  for (const sentence of draft.sentences ?? []) {
    const row = make('div');
    row.dataset.sentenceIndex = String(sentence.index);
    row.append(make('div', null, `${sentence.index}. ${sentence.text}`));
    row.append(make('div', 'mono', `kind: ${sentence.kind}`));

    const bindings = sentence.fact_bindings ?? [];
    if (bindings.length) {
      const group = make('div');
      for (const binding of bindings) {
        const chip = make('span', 'citation',
          `${binding.rendered} ← ${binding.metric_surface} ${binding.period_surface}  `);
        chip.title = binding.fact_id;
        chip.addEventListener('click', () => revealFact(binding.fact_id));
        group.append(chip);
      }
      row.append(group);
    } else {
      row.append(make('div', 'is-absent', 'no fact binding'));
    }

    const citations = sentence.citations ?? [];
    if (citations.length) {
      const group = make('div');
      for (const citation of citations) {
        const chip = make('span', 'citation',
          `cites ${citation.passage_id} [${citation.char_start}–${citation.char_end}]  `);
        chip.addEventListener('click', () => revealPassage(citation.passage_id));
        group.append(chip);
      }
      row.append(group);
    } else if (sentence.kind !== 'connective') {
      row.append(make('div', 'is-absent', 'no citation'));
    }

    if (sentence.calculation) {
      const calculation = sentence.calculation;
      row.append(make('div', 'mono',
        `calculated: ${calculation.operation} · ${calculation.expression} = `
        + `${calculation.result_rendered}`));
    }

    const findings = findingsBySentence.get(Number(sentence.index)) ?? [];
    if (findings.length) {
      row.classList.add(findings.some((finding) => finding.blocking)
        ? 'is-blocking' : 'is-warning');
      for (const finding of findings) {
        row.append(make('div', 'mono',
          `${finding.blocking ? 'blocking' : 'warning'} · ${finding.code} · `
          + `${finding.check ?? ''}`));
      }
    } else {
      row.append(make('div', 'note', 'no verification finding names this sentence'));
    }

    row.addEventListener('click', () => {
      const ids = [
        ...bindings.map((binding) => binding.fact_id),
        ...citations.map((citation) => citation.passage_id),
      ];
      highlightIds(ids, [], { focus: ids.length > 0 });
    });

    state.sentenceRows.set(Number(sentence.index), row);
    box.append(row);
  }
}

function renderPostMeta(outcome) {
  const host = clear(dom.postMeta);
  const parts = [
    `run ${outcome.story_run_id}`,
    `${outcome.disposition} · rendered as ${outcome.rendered_as}`,
    `${outcome.generation_mode} · selection ${outcome.selection_mode}`,
    `directory ${outcome.directory}`,
  ];
  host.append(make('div', 'mono', parts.join(' · ')));

  const cost = outcome.cost ?? {};
  const costLine = make('div', 'mono');
  if (cost.measured === false) {
    // Rule 2: the reason, never a zero. The two package estimates are computed before any call
    // and are real, so they are shown as the measurements they are.
    costLine.textContent = `tokens: ${LABELS.notMeasured} · latency: ${LABELS.notMeasured}`
      + ` · generation calls ${cost.generation_calls} · package prompt estimate `
      + `${cost.package_prompt_token_estimate} · package artifact estimate `
      + `${cost.package_artifact_token_estimate}`;
    host.append(costLine);
    host.append(make('div', 'note', String(cost.reason ?? '')));
  } else {
    costLine.textContent = `prompt ${cost.prompt_tokens} · completion `
      + `${cost.completion_tokens} · total ${cost.total_tokens} · calls `
      + `${cost.generation_calls}`;
    host.append(costLine);
    if (cost.reason) host.append(make('div', 'note', String(cost.reason)));
  }

  const manifest = outcome.manifest ?? {};
  const manifestBox = details(host, 'Run manifest');
  const list = document.createElement('dl');
  for (const key of Object.keys(manifest).sort()) {
    if (key === 'provider_model_id_note') continue;
    field(list, key, manifest[key], { mono: true });
  }
  manifestBox.append(list);
  if (manifest.provider_model_id_note) {
    manifestBox.append(make('p', 'note', String(manifest.provider_model_id_note)));
  }

  const artifactBox = details(host, `Artifacts (${Object.keys(outcome.artifacts ?? {}).length})`);
  const artifacts = document.createElement('dl');
  artifacts.className = 'mono';
  for (const [name, digest] of Object.entries(outcome.artifacts ?? {})) {
    field(artifacts, name, digest);
  }
  artifactBox.append(artifacts);
  const trace = outcome.trace_events ?? {};
  artifactBox.append(make('div', 'mono',
    `${trace.filename} · ${trace.events} events · ${trace.sha256}`));
  artifactBox.append(make('p', 'note', String(trace.note ?? '')));
}

function renderVerification(verification) {
  const summary = clear(dom.verificationSummary);
  const findingsHost = clear(dom.verificationFindings);
  const coverage = clear(dom.coverageFigures);

  if (!verification) {
    summary.append(make('p', 'is-absent',
      'The deterministic verifier did not run on this outcome.'));
    return;
  }

  const list = document.createElement('dl');
  field(list, 'verdict', verification.passed ? 'passed' : 'refused');
  field(list, 'draft digest', verification.draft_content_sha256, { mono: true });
  field(list, 'checks run', (verification.checks ?? []).length);
  field(list, 'blocking findings', (verification.blocking_findings ?? []).length);
  field(list, 'warnings', (verification.warnings ?? []).length);
  summary.append(list);

  for (const check of verification.checks ?? []) {
    const item = make('li');
    const findings = check.findings ?? [];
    const blocking = findings.filter((finding) => finding.blocking);
    if (blocking.length) item.classList.add('is-blocking');
    else if (findings.length) item.classList.add('is-warning');
    item.append(make('strong', null, check.name));
    item.append(make('span', 'mono', `  examined ${check.examined}`));
    if (!findings.length) {
      item.append(make('div', 'note', 'no finding'));
    }
    for (const finding of findings) {
      const row = make('div');
      row.append(make('div', 'mono',
        `${finding.blocking ? 'blocking' : 'warning'} · ${finding.code}`));
      if (finding.detail) row.append(make('div', null, finding.detail));
      if (finding.sentence_index !== null && finding.sentence_index !== undefined) {
        const link = make('span', 'citation', `sentence ${finding.sentence_index}`);
        link.addEventListener('click', () => revealSentence(Number(finding.sentence_index)));
        row.append(link);
      }
      item.append(row);
    }
    findingsHost.append(item);
  }

  for (const finding of verification.blocking_findings ?? []) {
    const explanation = finding.explanation_row ?? {};
    if (!explanation.description) continue;
    const item = make('li', 'is-blocking');
    item.append(make('strong', null, finding.code));
    item.append(make('div', null, explanation.description));
    if (explanation.remedy) item.append(make('div', 'note', explanation.remedy));
    findingsHost.append(item);
  }

  const figures = verification.coverage ?? {};
  const coverageList = document.createElement('dl');
  field(coverageList, 'checks', figures.checks);
  field(coverageList, 'items examined', figures.examined);
  field(coverageList, 'findings', figures.findings);
  field(coverageList, 'blocking', figures.blocking);
  coverage.append(coverageList);
  coverage.append(make('p', 'note', String(figures.note ?? '')));

  const ledger = verification.calculation_ledger_display ?? [];
  if (ledger.length) {
    const box = details(coverage, `Calculation ledger (${ledger.length})`, { open: true });
    for (const entry of ledger) {
      const row = make('div');
      row.append(make('div', 'mono',
        `sentence ${entry.sentence_index} · ${entry.expression}`));
      // `rendered` and `recomputed_display` are the server's strings and are printed as sent;
      // `recomputed_value` is the exact float and rides on the tooltip.
      const shown = make('div', null,
        `rendered ${entry.rendered} · recomputed ${entry.recomputed_display}`);
      shown.title = `exact recomputed value ${entry.recomputed_value}`;
      row.append(shown);
      box.append(row);
    }
  }

  const fact = verification.fact_ledger ?? [];
  if (fact.length) {
    const box = details(coverage, `Fact ledger (${fact.length})`);
    for (const entry of fact) {
      box.append(make('div', 'mono',
        `${entry.fact_id} · ${entry.metric_id} · ${entry.passage_id ?? ''}`));
    }
  }
}

// ---------------------------------------------------------------------------------------
// Behaviour 9: sources, and navigation in both directions.
// ---------------------------------------------------------------------------------------

function renderSources(sources) {
  const host = clear(dom.sourcesList);
  state.passageRows.clear();
  state.factRows.clear();
  if (!sources) {
    host.append(make('li', 'is-absent', 'This run produced no source index.'));
    return;
  }

  const counts = sources.counts ?? {};
  const heading = make('li');
  heading.append(make('div', 'mono',
    Object.entries(counts).map(([name, value]) => `${name} ${value}`).join(' · ')));
  if ((sources.unresolved ?? []).length) {
    heading.classList.add('is-blocking');
    heading.append(make('div', null,
      `${plural(sources.unresolved.length, 'citation')} did not resolve to a packaged passage.`));
    for (const entry of sources.unresolved) {
      heading.append(make('div', 'mono', JSON.stringify(entry)));
    }
  } else {
    heading.append(make('div', 'note',
      'Every citation resolved to a passage in the package.'));
  }
  host.append(heading);

  for (const document_ of sources.documents ?? []) {
    const item = make('li');
    item.dataset.documentId = document_.document_id;
    const head = make('div');
    head.append(make('strong', null,
      `${document_.form ?? 'document'} · ${document_.filing_date ?? ''}`));
    head.append(make('span', 'mono', `  ${document_.accession ?? ''}`));
    item.append(head);
    item.append(make('div', 'mono', document_.document_id));
    if (!document_.in_package) {
      item.append(make('div', 'is-absent', 'not carried in the package'));
    }
    if (document_.source_url && String(document_.source_url).startsWith('https:')) {
      const link = make('a', null, 'open the filing');
      link.setAttribute('href', String(document_.source_url));
      link.setAttribute('rel', 'noreferrer noopener');
      link.setAttribute('target', '_blank');
      item.append(link);
    }
    head.addEventListener('click', () => highlightIds([document_.document_id], [],
      { focus: true }));

    for (const passage of document_.passages ?? []) {
      item.append(passageBlock(passage, document_));
    }
    host.append(item);
  }
}

function passageBlock(passage, document_) {
  const wrapper = make('div');
  wrapper.dataset.passageId = passage.passage_id;
  const box = details(wrapper,
    `${passage.passage_id} · ${passage.char_count} chars · `
    + `${(passage.facts ?? []).length} facts · ${(passage.citations ?? []).length} citations`);

  if ((passage.heading_path ?? []).length) {
    box.append(make('div', 'note', passage.heading_path.join(' › ')));
  }
  box.append(make('div', 'mono', passage.excerpted ? 'excerpted' : 'whole passage'));

  for (const citation of passage.citations ?? []) {
    const row = make('div');
    const chip = make('span', 'citation', `sentence ${citation.sentence_index}`);
    chip.addEventListener('click', () => revealSentence(Number(citation.sentence_index)));
    row.append(chip);
    row.append(make('span', null, `  “${citation.quoted_text}” `));
    row.append(make('span', 'mono',
      `[${citation.char_start}–${citation.char_end}]`
      + `${citation.quote_resolved ? '' : ' · span did not resolve'}`));
    if (!citation.quote_resolved) row.classList.add('is-blocking');
    row.append(make('div', 'note', citation.sentence_text));
    box.append(row);
  }

  for (const fact of passage.facts ?? []) {
    const row = make('div');
    row.dataset.factId = fact.fact_id;
    row.append(make('div', null,
      `${fact.metric_label} · ${fact.value_display} ${fact.unit} · ${fact.period_key}`));
    row.append(make('div', 'mono', fact.fact_id));
    if (fact.quoted_text) row.append(make('div', null, `quoted: “${fact.quoted_text}”`));
    const indexes = fact.sentence_indexes ?? [];
    if (indexes.length) {
      const group = make('div');
      group.append(make('span', 'note', 'used by '));
      for (const index of indexes) {
        const chip = make('span', 'citation', `sentence ${index}  `);
        chip.addEventListener('click', () => revealSentence(Number(index)));
        group.append(chip);
      }
      row.append(group);
    } else {
      row.append(make('div', 'is-absent', 'no sentence binds this fact'));
    }
    row.addEventListener('click', () => highlightIds(
      [fact.fact_id, passage.passage_id, document_.document_id], [], { focus: true }));
    if (!state.factRows.has(fact.fact_id)) state.factRows.set(fact.fact_id, []);
    state.factRows.get(fact.fact_id).push({ row, box, passageId: passage.passage_id });
    box.append(row);
  }

  const text = details(box, 'passage text');
  text.append(make('div', 'mono', passage.text));
  state.passageRows.set(passage.passage_id, { box, wrapper });
  return wrapper;
}

/**
 * Point at one row, visibly, without a class the stylesheet does not have.
 *
 * `style.css` is another workstream's file and it carries no "this one" rule, so the marker is
 * an inline outline in the accent colour the sheet already defines — which is also the one thing
 * that survives being applied to a `div` inside a `details` inside a list item.
 */
function mark(element) {
  if (!element) return;
  for (const other of document.querySelectorAll('[data-marked="true"]')) {
    other.removeAttribute('data-marked');
    other.style.outline = '';
    other.style.outlineOffset = '';
  }
  element.dataset.marked = 'true';
  element.style.outline = '2px solid var(--accent)';
  element.style.outlineOffset = '2px';
  element.scrollIntoView({ block: 'nearest' });
}

/** Sources → post. */
function revealSentence(index, { navigate = true } = {}) {
  const row = state.sentenceRows.get(Number(index));
  if (!row) return;
  if (navigate) selectTab(dom.tabPost);
  const box = row.closest('details');
  if (box) box.open = true;
  mark(row);
}

/** Post → sources, by passage. */
function revealPassage(passageId) {
  const found = state.passageRows.get(String(passageId));
  if (!found) return;
  selectTab(dom.tabSources);
  found.box.open = true;
  mark(found.wrapper);
  highlightIds([passageId], [], { focus: true });
}

/** Post → sources, by fact. */
function revealFact(factId) {
  const rows = state.factRows.get(String(factId)) ?? [];
  if (!rows.length) {
    highlightIds([factId], [], { focus: true });
    return;
  }
  selectTab(dom.tabSources);
  const first = rows[0];
  first.box.open = true;
  mark(first.row);
  highlightIds([factId, first.passageId], [], { focus: true });
}

// ---------------------------------------------------------------------------------------
// Boot.
// ---------------------------------------------------------------------------------------

function wire() {
  wireTabList(dom.panelTabs);
  wireTabList(dom.outputTabs);
  wireOutputRegion();
  buildGraph();

  dom.runDiscovery.addEventListener('click', runDiscovery);
  dom.buildPackage.addEventListener('click', buildEvidencePackage);
  dom.buildPackage.disabled = true;
  dom.generatePost.addEventListener('click', generatePost);

  dom.promptPreset.addEventListener('change', () => applyPreset(dom.promptPreset.value));
  dom.promptReset.addEventListener('click', () => applyPreset(dom.promptPreset.value));
  dom.promptDiffToggle.addEventListener('click', () => {
    const shown = dom.promptDiffToggle.getAttribute('aria-pressed') === 'true';
    dom.promptDiffToggle.setAttribute('aria-pressed', shown ? 'false' : 'true');
    dom.promptDiff.hidden = shown;
    if (!shown) renderPromptDiff();
  });
  for (const box of [dom.plannerInstructions, dom.writerInstructions, dom.styleGuidance]) {
    box.addEventListener('input', onPromptEdited);
  }
}

function start() {
  wire();
  loadOverview();
  loadPresets();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start, { once: true });
} else {
  start();
}
