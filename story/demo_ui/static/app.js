/**
 * The panel logic: one page, thirteen endpoints, and no value on screen that a payload did
 * not carry.
 *
 * The count is the `ENDPOINTS` table's, and it was **wrong before S12 rather than made wrong
 * by it**: this line already read "thirteen" when the table held twelve. It holds thirteen
 * now, so the sentence is true for the first time; the number is corrected here rather than
 * left to be read as the count of something else.
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
 * ## Three more, added by the adversarial review on 2026-08-05
 *
 * 5. **A render is keyed on the run it came from.** `state.discoveryRunId` and
 *    `state.generationRunId` are the keys; a result that arrives for a run that is no longer the
 *    current one is discarded and said so, never painted over the run on screen. The review
 *    measured the opposite: two clicks inside `finishGeneration`'s await window put run A's post
 *    beside run B's trace.
 * 6. **A step is shown once.** `Run.stream()` replays from sequence 0 on every subscribe and
 *    ignores `Last-Event-ID` — deliberately, so a late subscriber sees the whole trace — and
 *    `EventSource` reconnects by itself. The client is therefore the only place a reconnect can
 *    be told from a new step: `state.traceSequences` is the ledger and the replayed steps are
 *    counted and reported rather than appended twice.
 * 7. **Two disagreeing signals are rendered as a disagreement.** `outcomeConsistency` reads
 *    `accepted`, `rendered_as`, the presence of `post`/`rejection` and the verifier's own verdict
 *    together; if they do not agree the panel says exactly that and prints all six, instead of
 *    picking one and captioning an accepted draft "Refused".
 * 8. **Silence is reported as silence.** A run that stops emitting is not a run that failed and
 *    is not a run that finished. `QUIET_AFTER_MS` re-enables the interface and says the run has
 *    gone quiet, with no claim about its outcome either way.
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
  providers: '/demo/providers',
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
  countPainted: document.getElementById('count-painted'),
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
  modelFacts: document.getElementById('model-facts'),
  modelFactsNote: document.getElementById('model-facts-note'),
  factsList: document.getElementById('facts-list'),
  packageBounds: document.getElementById('package-bounds'),

  promptPreset: document.getElementById('prompt-preset'),
  providerSelect: document.getElementById('provider-select'),
  modelSelect: document.getElementById('model-select'),
  providerNotice: document.getElementById('provider-notice'),
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
  providerSelection:
    'The provider and the model are chosen from what this server already holds. Only their ids '
    + 'travel with the request \u2014 no endpoint, no credential and no timeout is sent from '
    + 'this page, and the API refuses one rather than ignoring it.',
  providerUnavailable:
    'This build knows this provider, and this machine cannot use it yet. The reason below is '
    + 'the server\u2019s own and names the setting that is missing, never its value.',
  providerRequiresLive:
    'That provider has no recorded answer store, and one provider\u2019s recorded rows are a '
    + 'miss under another by design \u2014 the request digest names the adapter. Run it live, '
    + 'or choose the provider the recorded store belongs to.',
  temperaturePinned:
    'This model accepts the pinned temperature, so the run sends it and the manifest records '
    + 'the value that reached the wire.',
  temperatureRefused:
    'This model refuses a temperature, measured against the API rather than inferred from its '
    + 'name. None is sent, and the manifest records that none was sent rather than a number '
    + 'nothing carried.',
  rejectedRun:
    'This run was refused. What follows is the refused draft and the findings that refused it; '
    + 'no post was written and none is shown.',
  providerFaultRun:
    'This run got no answer. The request went out and the provider did not reply with one \u2014 '
    + 'so nothing was planned, nothing was drafted, nothing was verified and nothing was '
    + 'refused. What is below is the call that was attempted and what the provider boundary '
    + 'raised; there is no draft and no finding to show, because none was ever produced.',
  streamLost:
    'The event stream closed before the run reported a terminal state. The run’s own '
    + 'status was fetched instead of assuming it finished.',
  supersededRun:
    'A result arrived for a run that is no longer the one on screen, and was discarded rather '
    + 'than painted over the current run.',
  streamReplayed:
    'The stream reconnected and the server replayed the trace from the first step, which is '
    + 'what lets a late subscriber see the whole run. Steps already shown were not added again.',
  planIsModelText:
    'Model-authored, not verified. Every line of this plan is text the model returned inside a '
    + 'schema. The deterministic checks run over the draft and the numbers in it, never over the '
    + 'plan — so a causal phrase here has been examined by nothing.',
  inconsistentOutcome:
    'The payload’s own signals disagree about what this run was. The panel will not choose one '
    + 'of them: every signal is printed below exactly as it arrived, and no verdict is shown.',
  runQuiet:
    'The run has sent no trace event for a while and has not reported a terminal state. It may '
    + 'still be running on the server. This is not a claim that it completed, and not a claim '
    + 'that it failed. The interface is enabled again.',
  factUnavailable:
    'Not available. The corpus and the ontology were both asked and neither carries this, so '
    + 'the package says so rather than leaving a gap. Nothing on this page and nothing in the '
    + 'prompt fills it in.',
  readOnlyFact:
    'Read-only and authoritative. The ontology declares it, the prompt panel cannot reach it, '
    + 'and no field on this page edits it.',
  availableUnknown:
    'How many this section had was never counted, so it is shown as unknown rather than as '
    + 'equal to the number carried. The warning beside it names the bound that applied.',
  droppedNotListable:
    'The rows that did not fit are not in the package, so they cannot be listed here. What is '
    + 'below is every row the model was given for this section.',
  noRequiredFactDropped:
    'No required fact was dropped — measured from the facts that survived, not asserted. A '
    + 'package that cannot hold one refuses instead.',
  derivedBinding:
    'This numeral was computed by the derivation tool from two filed readings, before the '
    + 'writer ran. The model chose to state it and chose the words around it; it did not do '
    + 'the arithmetic, and there is no field in its schema where it could have.',
  derivedUnused:
    'No sentence in the final draft binds this fact. Code computed it and the post did not '
    + 'state it, which is an ordinary outcome and not a defect — the ledger this is read from '
    + 'records what the draft used, not what was available to it.',
  rulesEvaluated:
    'These are the comparability rules that were evaluated to permit this pair, not the whole '
    + 'set. A comparison refused at R3 never reaches R4, so a row claiming all ten ran would be '
    + 'false about exactly the ones that did not.',
});

// ---------------------------------------------------------------------------------------
// DOM helpers. Six functions, none of which can produce markup.
// ---------------------------------------------------------------------------------------

/* BEGIN DOM MAKE — `tests/story/test_demo_ui_table_grid.py` extracts this block and the grid
   renderer below it by their markers and runs both under `node` against a payload the server
   really built, so the highlighted cell in the report is the one this file draws and not a
   description of it. Keep it self-contained: no closure over module state. */
function make(tag, className, textValue) {
  const created = document.createElement(tag);
  if (className) created.className = className;
  if (textValue !== undefined && textValue !== null) created.textContent = String(textValue);
  return created;
}
/* END DOM MAKE */

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

/* BEGIN NUMBER FORMAT — `tests/story/test_demo_ui_number_format.py` extracts this block by these
   two markers and runs it under `node` against Python's own `%.10g`. Keep it self-contained:
   nothing between the markers may touch the DOM, the state or another function outside them. */

/**
 * §7's float residue, and nothing else — but formatted the way the *server* formats it.
 *
 * `api.py:_display_number` is `f"{value:.10g}"`. The previous version of this function was
 * `Number.parseFloat(value.toPrecision(10))`, which agrees with it on the corpus and disagrees
 * off it in two measurable ways the review found: `1234567890123` printed as `1234567890123`
 * here and `1.23456789e+12` there, and `1234567890.5` printed as `1234567891` here and
 * `1234567890` there, because C's `%g` rounds a tie to even and JavaScript's rounding of a tie
 * goes away from zero. The corpus maximum is 6.79e8 so neither could lie today; two renderings
 * of one fact value is still a divergence, and the fact panel and the sources panel are two
 * panels that show the same fact.
 *
 * So this is `%.10g` itself: round half to even at ten significant digits, then C's own choice
 * between fixed and exponential form, then C's stripping of trailing zeros.
 *
 * A value that is already a string — `value_display`, `rendered`, `recomputed_display`,
 * `printed_form`, a unit — never reaches this function. §13 checked the rendered string, and
 * re-rounding it here would be this layer editing evidence.
 */
export function formatNumber(value) {
  return formatSignificant(value, 10);
}

function formatSignificant(value, precision) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value);
  if (value === 0) return Object.is(value, -0) ? '-0' : '0';
  // Twenty-one significant digits: four more than a double can distinguish, so the digits below
  // the one we round at are the value's own and not an artefact of this conversion.
  const [mantissa, exponentText] = Math.abs(value).toExponential(20).split('e');
  const rounded = roundHalfEven(mantissa.replace('.', ''), Number(exponentText), precision);
  // C's `%g`: exponential when the exponent is below -4 or at least the precision, fixed between.
  const text = rounded.exponent < -4 || rounded.exponent >= precision
    ? exponentialForm(rounded.digits, rounded.exponent)
    : fixedForm(rounded.digits, rounded.exponent);
  return value < 0 ? `-${text}` : text;
}

/** Round a digit string to `precision` significant digits, a tie going to the even digit. */
function roundHalfEven(digits, exponent, precision) {
  const kept = digits.slice(0, precision).padEnd(precision, '0');
  const rest = digits.slice(precision);
  const first = rest === '' ? 0 : rest.charCodeAt(0) - 48;
  const tail = rest.slice(1).replace(/0+$/, '');
  const odd = (kept.charCodeAt(precision - 1) - 48) % 2 === 1;
  const up = first > 5 || (first === 5 && (tail !== '' || odd));
  if (!up) return { digits: kept, exponent };
  const bumped = String(Number(kept) + 1);
  // `9999999999` + 1 is eleven digits: the carry moves the decimal point rather than widening it.
  if (bumped.length > precision) {
    return { digits: bumped.slice(0, precision), exponent: exponent + 1 };
  }
  return { digits: bumped.padStart(precision, '0'), exponent };
}

function stripTrailingZeros(fraction) {
  return fraction.replace(/0+$/, '');
}

function fixedForm(digits, exponent) {
  if (exponent >= 0) {
    const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, '0');
    const fraction = stripTrailingZeros(digits.slice(exponent + 1));
    return fraction === '' ? whole : `${whole}.${fraction}`;
  }
  const fraction = stripTrailingZeros('0'.repeat(-exponent - 1) + digits);
  return fraction === '' ? '0' : `0.${fraction}`;
}

function exponentialForm(digits, exponent) {
  const fraction = stripTrailingZeros(digits.slice(1));
  const magnitude = String(Math.abs(exponent)).padStart(2, '0');
  const sign = exponent < 0 ? '-' : '+';
  return `${digits.slice(0, 1)}${fraction === '' ? '' : `.${fraction}`}e${sign}${magnitude}`;
}

/* END NUMBER FORMAT */

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
  // The catalogue as the server published it, and the pair this session has chosen out of it.
  // Both ids and nothing else: there is no base URL and no key in this object because there is
  // none in the payload it is built from.
  providers: [],
  providerById: new Map(),
  selectedProviderId: null,
  selectedModelId: null,
  composed: null,
  requiresLiveOffered: false,
  generationRunId: null,
  outcome: null,
  sources: null,
  stream: null,
  traceEvents: [],
  // Rule 6: the sequence numbers already on screen, so a replayed frame is told from a new one.
  traceSequences: new Set(),
  replayedSteps: 0,
  replayRow: null,
  streamLost: false,
  // Rule 8: the watchdog handle, and whether it has already fired for the run in flight.
  quietHandle: 0,
  quiet: false,
  paintedStatus: '',
  sentenceRows: new Map(),
  // passage id -> **every** block drawn for it. One passage can be in two §10 sections at once
  // — measured live: a shareholder-letter passage that is `primary_support` in
  // `primary_passages` and `counter_evidence` in `counter_evidence[]` — so a map holding one
  // block per id would have silently kept whichever was drawn last.
  passageRows: new Map(),
  factRows: new Map(),
  busy: false,
};

/**
 * How long a run may say nothing before the interface says so, and why it is this long.
 *
 * Measured on 2026-08-05: `docker stop fkg-neo4j` mid-discovery left the run `running` with one
 * event for four minutes — the driver retries with backoff — and both buttons dead with no
 * explanation. A live generation, on the other hand, legitimately goes quiet between `planning`
 * and `drafting` while the model produces tokens, and the longest gap seen on a live Qwen run
 * was tens of seconds. Ninety seconds is above the second and far below the first.
 *
 * What happens at the end of it is deliberately small: the interface says the run has gone
 * quiet and re-enables itself. It does not close the stream, does not cancel the run, and does
 * not claim an outcome — a run we have stopped hearing from is neither finished nor failed.
 */
const QUIET_AFTER_MS = 90000;

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

/** A notice that is not an error: it does not set the pipeline state to `error:`. */
function showNotice(text) {
  dom.appError.textContent = text;
  dom.appError.hidden = false;
}

// ---------------------------------------------------------------------------------------
// Run identity. Rule 5: every render is keyed on the run it came from.
//
// The two ids are the keys, and they are written the moment the server hands one back. A result
// that resolves after a newer run has started belongs to a run nobody is looking at any more —
// the review measured that exact sequence putting run A's post beside run B's trace — so it is
// discarded and said so. This is deliberately not "disable the button for longer": the button is
// re-enabled by the quiet watchdog below while a run may still be in flight, so a superseded
// result can still arrive and must still be refused.
// ---------------------------------------------------------------------------------------

function currentRunId(phase) {
  return phase === 'discovery' ? state.discoveryRunId : state.generationRunId;
}

function isCurrentRun(phase, runId) {
  return runId !== null && runId !== undefined && currentRunId(phase) === runId;
}

/** Say that a late result was thrown away, without overwriting what the live run is saying. */
function noteSuperseded(phase, runId) {
  if (!dom.appError.hidden) return;
  showNotice(`${LABELS.supersededRun} Discarded: the ${phase} result for ${runId}; `
    + `the run on screen is ${currentRunId(phase) ?? 'none'}.`);
}

// ---------------------------------------------------------------------------------------
// The quiet watchdog. Rule 8.
// ---------------------------------------------------------------------------------------

function clearQuietTimer() {
  if (state.quietHandle !== 0) globalThis.clearTimeout(state.quietHandle);
  state.quietHandle = 0;
}

function armQuietTimer(phase, runId) {
  clearQuietTimer();
  if (state.quiet) {
    // A frame arrived after the run had gone quiet, so the notice is no longer true.
    state.quiet = false;
    clearError();
  }
  state.quietHandle = globalThis.setTimeout(() => reportQuiet(phase, runId), QUIET_AFTER_MS);
}

function reportQuiet(phase, runId) {
  state.quietHandle = 0;
  if (!isCurrentRun(phase, runId)) return;
  state.quiet = true;
  state.busy = false;
  dom.runDiscovery.disabled = false;
  dom.generatePost.disabled = false;
  setSelectionEnabled(true);
  const seconds = Math.round(QUIET_AFTER_MS / 1000);
  showNotice(`${LABELS.runQuiet} No event has arrived from ${runId} for ${seconds} s.`);
  setPipelineState(`${phase} run ${runId} · quiet for ${seconds} s · no terminal state reported`);
  if (phase === 'discovery') setText(dom.discoveryState, `${runId} · quiet, no terminal state`);
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
    // A stream this file has replaced is a stream whose frames belong to a run nobody is
    // looking at. `close()` stops a real `EventSource`; the check is what makes that a property
    // of this file rather than a property of the browser.
    if (state.stream !== stream) return;
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
    // A superseded stream is closed and then dropped: its run's outcome is not this panel's
    // any more, and reading it would be the race rule 5 exists to stop.
    if (state.stream !== stream) return;
    state.stream = null;
    onEnd(summary);
  });

  stream.addEventListener('error', () => {
    if (ended || state.stream !== stream) return;
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
    onStats: writePaintedCount,
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

/**
 * What the last frame actually painted, in the header, beside what the payload holds.
 *
 * `onStats` is the renderer's own callback and fires on every frame it draws, so the value is
 * the frame's and not an estimate. It is written through a cached string because a frame that
 * painted the same thing as the last one should not touch the DOM sixty times a second — and
 * `#graph-status` stays the renderer's element: this writes `#count-painted`, which is this
 * file's.
 */
function writePaintedCount(report) {
  const line = `${plural(report.drawnNodes, 'node')}`
    + `${report.clusters ? ` + ${plural(report.clusters, 'cluster')}` : ''} · `
    + `${plural(report.drawnEdges, 'edge')} at zoom ${formatSignificant(report.scale, 3)}`
    + `${report.aggregating ? ' (dense regions aggregated)' : ''}`;
  if (line === state.paintedStatus) return;
  state.paintedStatus = line;
  setText(dom.countPainted, line);
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

  // These two are the size of the projection *payload*. What is painted is a different number —
  // it changes with the zoom — and it is written from the renderer's own frame report into
  // `#count-painted` by `writePaintedCount`.
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

/**
 * Start a run's trace over: no rows, no sequence ledger, no replay notice.
 *
 * Called from both run starters rather than from `clear(dom.traceEvents)` on its own, because a
 * cleared list with a full ledger would silently swallow the next run's first steps.
 */
function resetTrace() {
  clear(dom.traceEvents);
  state.traceEvents = [];
  state.traceSequences = new Set();
  state.replayedSteps = 0;
  state.replayRow = null;
}

/**
 * Rule 6, said once and kept at the bottom of the list.
 *
 * `Run.stream()` replays from sequence 0 on every subscribe and ignores `Last-Event-ID`
 * (confirmed live: `Last-Event-ID: 15` still answers `id: 0` first). That is the server's
 * deliberate design — it is what lets a panel opened halfway through see the whole trace — so
 * the reconnect is normal and only the *doubling* was the bug. A single row, updated in place
 * and moved to the end, says it happened without pretending it did not.
 */
function noteReplayedStep() {
  state.replayedSteps += 1;
  if (state.replayRow === null) {
    state.replayRow = make('li', 'note');
    state.replayRow.dataset.replayed = 'true';
  }
  state.replayRow.textContent = `${plural(state.replayedSteps, 'replayed step')} already shown. `
    + LABELS.streamReplayed;
  dom.traceEvents.append(state.replayRow);
}

function renderTraceEvent(event) {
  const sequence = Number(event.sequence);
  if (Number.isInteger(sequence)) {
    if (state.traceSequences.has(sequence)) {
      noteReplayedStep();
      return null;
    }
    state.traceSequences.add(sequence);
  }
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
  // `period_keys` is not a node id and is never sent to `view.highlight`: a period is a property
  // of an observation, not a drawn node, and the projections carry no node for one. It is
  // counted here because a step that named 24 periods did name something.
  const periodKeys = event.graph_highlights ? event.graph_highlights.period_keys ?? [] : [];
  if (nodeIds.length) pointers.push(plural(nodeIds.length, 'graph node'));
  if (edgeIds.length) pointers.push(plural(edgeIds.length, 'graph edge'));
  if (periodKeys.length) pointers.push(plural(periodKeys.length, 'period key'));
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
  // The replay notice, if there is one, stays the last row.
  if (state.replayRow !== null) dom.traceEvents.append(state.replayRow);
  return { item, resolution };
}

/**
 * Light up what one trace step pointed at.
 *
 * The node ids come first because they are what §3 calls a highlight. Fact ids are used as well
 * because a generation event carries no node ids and an observation id *is* a node id in the two
 * candidate projections — verified against the live evidence subgraph. Both are resolved through
 * `view.node` before they are drawn, and what did not resolve is said out loud.
 */
function applyEventHighlight(event, resolution, { draw = true, focus = true } = {}) {
  const highlights = event.graph_highlights ?? {};
  const wanted = [...(highlights.node_ids ?? []), ...(event.related_fact_ids ?? [])];
  const outcome = draw
    ? highlightIds(wanted, highlights.edge_ids ?? [],
      { focus: focus && wanted.length > 0 && wanted.length <= 40 })
    : resolveNodeIds(wanted);
  if (resolution) {
    const total = wanted.length;
    resolution.textContent = total === 0
      ? 'This step named no graph id.'
      : `${outcome.resolved.length} of ${total} ids are in the drawn view`
        + `${outcome.missing.length ? `; ${outcome.missing.length} are not` : ''}. `
        + LABELS.highlightResolution
        + (draw ? '' : ' Click this step to light up the ones that are.');
  }
  const sentences = (event.related_sentence_ids ?? []).map(Number).filter(Number.isInteger);
  if (draw && sentences.length === 1) revealSentence(sentences[0], { navigate: false });
  return outcome;
}

/**
 * What the live stream does with a step, and why it is the same call the click makes.
 *
 * The review measured the gap: during the stream, `highlightIds` was called with no resolution
 * element, and a stage whose 24 ids resolved to **0** drawn nodes cleared the highlight and said
 * nothing — indistinguishable from a stage that highlighted nothing at all. Clicking the row
 * already printed the honest correction, so the live pass now prints it too. What it still does
 * not do is move the camera: a stream that re-framed the graph on every frame would be unusable,
 * and a step whose ids the reader wants to see is one click away.
 */
function reportStreamedEvent(event, rendered) {
  if (rendered === null) return;
  const nodeIds = event.graph_highlights ? event.graph_highlights.node_ids ?? [] : [];
  applyEventHighlight(event, rendered.resolution, { draw: nodeIds.length > 0, focus: false });
}

async function runDiscovery() {
  if (state.busy) return;
  state.busy = true;
  dom.runDiscovery.disabled = true;
  dom.discoveryExplainer.hidden = true;
  clear(dom.candidateList);
  dom.candidateDetail.hidden = true;
  resetTrace();
  state.streamLost = false;
  state.quiet = false;
  state.discoveryRunId = null;
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
        armQuietTimer('discovery', started.run_id);
        const rendered = renderTraceEvent(event);
        if (rendered === null) return;
        state.traceEvents.push(event);
        setText(dom.discoveryState, `${started.run_id} · ${event.stage} · ${event.status}`);
        reportStreamedEvent(event, rendered);
      },
      onEnd: () => finishDiscovery(params),
      onLost: () => {
        state.streamLost = true;
        setText(dom.discoveryState, 'stream lost');
        finishDiscovery(params);
      },
    });
    armQuietTimer('discovery', started.run_id);
  } catch (failure) {
    state.busy = false;
    dom.runDiscovery.disabled = false;
    showError('starting discovery', failure);
  }
}

async function finishDiscovery(params) {
  const runId = params.run_id;
  clearQuietTimer();
  if (!isCurrentRun('discovery', runId)) {
    noteSuperseded('discovery', runId);
    return;
  }
  // The run reported a terminal state, so whatever it was quiet about is over.
  state.quiet = false;
  try {
    const payload = await getJson(serverPath(null, ENDPOINTS.discoveryResult, params));
    if (!isCurrentRun('discovery', runId)) {
      noteSuperseded('discovery', runId);
      return;
    }
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

  // No derived group: a package is built before any model has run, so the run that would
  // request a derivation has not happened. Passing the previous run's group here would show one
  // candidate's arithmetic against another candidate's package.
  renderModelFacts(payload.model_facts, null);
  renderFacts(payload.facts ?? []);
  renderPackageBounds(payload);
}

/**
 * "Facts sent to the model", in the five groups the server composed.
 *
 * Everything a row says about itself — kind, statement, source, authority, editability, whether
 * it reached the model-visible slice — is read from `model_facts`, which `package_view.py`
 * builds with the same mapper every other surface uses. This function decides nothing; it
 * chooses where things go on the screen.
 *
 * The two rules it does enforce are about *absence*. A row with `available: false` is rendered
 * as unavailable rather than skipped or blanked, because the only company-description row this
 * corpus can produce is that one and a gap is what a reader (or a model) fills in. And a
 * section whose `available` count is unknown says so, rather than being drawn as complete.
 *
 * `derived` is the second argument because the derived group is a **run's** and the other four
 * are a **package's** — `derived_facts.json` exists only once the planner has requested a
 * derivation and code has performed it. The group arrives from the server already composed by
 * the same mapper, and this function substitutes it by name; it does not merge two payloads,
 * because deciding what a row says is what `package_view.py` is for.
 */
function renderModelFacts(modelFacts, derived) {
  const host = clear(dom.modelFacts);
  // The rows this panel registered last time are about to be detached from the document, so
  // their entries go with them. Only this panel's: the sources panel's entries belong to a
  // block that is still on screen, and dropping those would break the observed-fact click.
  for (const [factId, entries] of [...state.factRows]) {
    const kept = entries.filter((entry) => entry.tab !== dom.tabFacts);
    if (kept.length) state.factRows.set(factId, kept);
    else state.factRows.delete(factId);
  }
  if (!modelFacts && !derived) {
    setText(dom.modelFactsNote, '');
    host.append(make('p', 'is-absent', 'No package has been built for this story yet.'));
    return;
  }
  setText(dom.modelFactsNote, modelFacts ? modelFacts.slice_note : '');
  const groups = (modelFacts?.groups ?? []).map(
    (group) => (derived && group.group === derived.group ? derived : group));
  if (derived && !groups.includes(derived)) {
    // A run whose package was never built through this panel. The group is still shown, because
    // the derived facts are this run's and losing them would be the panel hiding the numbers the
    // post rests on.
    groups.push(derived);
  }
  for (const group of groups) {
    const box = details(host,
      `${group.label} (${formatCount(group.count)})`, { open: group.count > 0 });
    box.append(make('p', 'note', group.description));
    // No section, no ledger: `derived_fact_group` sends `null` rather than a stand-in, and a
    // ledger line about a section that does not exist would invent a bound nobody applied.
    if (group.section) box.append(sectionLedgerLine(group.ledger));
    if (group.ran === false) {
      box.append(make('div', 'is-absent', String(group.not_run_because ?? '')));
      continue;
    }
    for (const refusal of group.refusals ?? []) {
      // A refused request is a finding about the plan, not an absent row: the planner asked for
      // a derivation and code would not perform it.
      const row = make('div', 'is-blocking');
      row.append(make('strong', null, refusal.code));
      row.append(make('div', 'mono',
        `${refusal.operation} · ${refusal.from_fact_id} → ${refusal.to_fact_id}`
        + `${refusal.rule_id ? ` · ${refusal.rule_id}` : ''}`));
      row.append(make('div', null, refusal.detail));
      box.append(row);
    }
    if (!group.count) {
      box.append(make('div', 'is-absent', group.empty_because
        ?? 'The package carries none of these.'));
      continue;
    }
    for (const row of group.rows ?? []) {
      box.append(group === derived ? derivedFactRow(row, box) : modelFactRow(row));
    }
  }
}

/** One fact row: what it says, where it came from, who is authoritative, and can it be edited. */
function modelFactRow(row) {
  const item = make('div', row.available === false ? 'is-absent' : null);
  item.dataset.factId = row.fact_id;
  item.dataset.factKind = row.fact_kind;

  const head = make('div');
  head.append(make('span', 'citation', row.fact_kind));
  head.append(make('strong', null, `  ${row.statement}`));
  item.append(head);

  if (row.available === false) {
    // The unavailable row is the one place this panel adds a sentence of its own, and it says
    // that nothing may fill the gap — which is the whole reason the row exists.
    item.append(make('div', 'is-absent', LABELS.factUnavailable));
  }
  const list = document.createElement('dl');
  field(list, 'source', row.source);
  if (row.source_detail) field(list, 'source detail', row.source_detail);
  field(list, 'authority', row.authority);
  field(list, 'authoritative', row.authoritative);
  field(list, 'editable', row.editable);
  field(list, 'statement is', row.statement_source);
  field(list, 'sent to the model', row.in_model_slice);
  item.append(list);

  if (row.editable === false && row.authoritative === true) {
    item.append(make('div', 'note', LABELS.readOnlyFact));
  } else if (row.editable === false && row.not_editable_because) {
    item.append(make('div', 'note', row.not_editable_because));
  }
  if ((row.warning_codes ?? []).length) {
    item.append(make('div', 'mono', `warnings: ${row.warning_codes.join(', ')}`));
  } else {
    item.append(make('div', 'note', 'no warning is recorded against this fact'));
  }
  if ((row.corroborating_document_ids ?? []).length) {
    item.append(make('div', 'mono',
      `${plural(row.corroborating_document_ids.length, 'corroborating document')}: `
      + row.corroborating_document_ids.join(', ')));
  }
  // The evidence id is printed in the panel that shows what the model was given, because it is
  // what the model was given: the FACTS block prints this string under the fact and rule 8
  // says a citation is that string copied character for character. A reader comparing a
  // refusal to the prompt needs both ends of that.
  if (row.evidence_handle) {
    const handle = make('div', 'mono', `evidence id ${row.evidence_handle}`);
    if (row.cell) {
      handle.title = `row ${row.cell.row_index}, column ${row.cell.value_column_index}; `
        + `period header at row ${row.cell.period_header_row_index}, `
        + `column ${row.cell.period_header_column_index}`;
    }
    item.append(handle);
    if (row.row_label || row.column_label) {
      item.append(make('div', 'note',
        `read from row “${row.row_label ?? ''}” under “${row.column_label ?? ''}”`
        + `${row.quoted_text ? `, cell value “${row.quoted_text}”` : ''}`));
    }
  } else if (row.evidence_handle_absent_because) {
    item.append(make('div', 'is-absent', row.evidence_handle_absent_because));
  }
  item.append(make('div', 'mono', row.fact_id));
  return item;
}

/**
 * One section's ledger, and the only honest way to print an uncounted `available`.
 *
 * "4 of 9 primary passages sent to the model" is a reading of `available` and `carried`. Where
 * `available_known` is false the server deliberately sent no number — an earlier cap dropped
 * rows and reported no count — and printing `carried` in its place would turn "the section had
 * nine" into "the section had four".
 */
function sectionLedgerLine(ledger) {
  const box = make('div');
  if (!ledger) {
    box.append(make('div', 'is-absent', 'no ledger row for this section'));
    return box;
  }
  const carried = formatCount(ledger.carried);
  if (ledger.available_known) {
    box.append(make('div', 'mono',
      `${carried} of ${formatCount(ledger.available)} ${ledger.section} sent to the model`));
  } else {
    box.append(make('div', 'mono',
      `${carried} ${ledger.section} sent to the model; available: unknown`));
    box.append(make('div', 'note', LABELS.availableUnknown));
  }
  if (ledger.dropped) {
    box.append(make('div', 'mono', `dropped: ${formatCount(ledger.dropped)}`));
    box.append(make('div', 'note', LABELS.droppedNotListable));
  }
  if ((ledger.reasons ?? []).length) {
    box.append(make('div', 'mono', `reason: ${ledger.reasons.join(', ')}`));
  }
  if (ledger.protected) {
    box.append(make('div', 'note', 'protected: the token trimmer may not take a row from here'));
  }
  if (ledger.required_dropped === false) {
    box.append(make('div', 'note', LABELS.noRequiredFactDropped));
  } else if (ledger.required_dropped === true) {
    box.append(make('div', 'is-blocking', 'a required fact was dropped from this section'));
  }
  return box;
}

/**
 * One derived fact, and the four things that make it different from a filed reading.
 *
 * 1. **It has no evidence of its own, and must not look as though it has.** The filing states
 *    each of the two readings and says nothing about their difference, so no evidence handle is
 *    minted for this row and none can be. The server's sentence saying so is printed where an
 *    observed row prints its handle — an absence in the same place as the thing it is the
 *    absence of.
 * 2. **Its inputs are the route to the evidence**, so each input id is a click target that
 *    reveals the observed fact and, through it, the cell it was read from.
 * 3. **It is deterministic**, which is a claim about provenance and is the server's badge.
 * 4. **Whether the final draft bound it is a different question from whether it was computed.**
 *    `used_by_draft` comes from `VerifiedDraft.fact_ledger`, and `false` is a real answer: code
 *    computed a quantity the post did not state.
 *
 * The row is registered in `state.factRows` under its own id, which is what makes a draft
 * binding chip clickable at all — a derived fact has no `passage_id` by design, so the sources
 * panel never registers one and `revealFact` landed in its no-rows fallback.
 */
function derivedFactRow(row, box) {
  const item = make('div');
  item.dataset.factId = row.fact_id;
  item.dataset.factKind = row.fact_kind;

  const head = make('div');
  head.append(make('span', 'citation is-derived', row.fact_kind));
  head.append(make('strong', null, `  ${row.statement}`));
  item.append(head);
  item.append(make('div', 'mono', row.badge));

  const list = document.createElement('dl');
  if (row.fact_kind === 'evidence_scope') {
    // §7's row. The label is the server's and says what the claim is *about*, because the whole
    // point of the type is that it speaks about the evidence and never about the world.
    field(list, 'claim', row.claim, { mono: true });
    field(list, 'claim is about', row.claim_about);
  } else {
    field(list, 'operation', row.operation, { mono: true });
    field(list, 'result', row.value_display);
    field(list, 'unit', row.unit);
    field(list, 'periods', row.period_label);
    field(list, 'from period', row.from_period);
    field(list, 'to period', row.to_period);
    field(list, 'reads as', row.display_semantics);
    field(list, 'from value', row.from_value_display);
    field(list, 'to value', row.to_value_display);
  }
  field(list, 'source', row.source, { mono: true });
  field(list, 'authority', row.authority);
  field(list, 'authoritative', row.authoritative);
  field(list, 'editable', row.editable);
  field(list, 'statement is', row.statement_source);
  field(list, 'sent to the model', row.in_model_slice);
  item.append(list);

  if (row.editable === false && row.not_editable_because) {
    item.append(make('div', 'note', row.not_editable_because));
  }
  if ((row.comparability_rule_ids ?? []).length) {
    item.append(make('div', 'mono',
      `comparability rules evaluated: ${row.comparability_rule_ids.join(', ')}`));
    item.append(make('div', 'note', LABELS.rulesEvaluated));
  }
  if (row.reused_detector_signal) {
    item.append(make('div', 'mono',
      `asserted equal to the candidate's own ${row.reused_detector_signal} signal`));
  }
  if ((row.warning_codes ?? []).length) {
    item.append(make('div', 'mono', `warnings: ${row.warning_codes.join(', ')}`));
  }

  // Both lists are package fact ids and both are clicks into the sources panel: the inputs are
  // what this number was computed from, the examined ids are what the §7 rule looked at before
  // it said the package contains no explanation. Neither list is evidence *for* the row, which
  // is why the absence sentence below still stands beneath them.
  for (const [caption, ids] of [['computed from ', row.input_fact_ids ?? []],
    ['examined ', row.examined_fact_ids ?? []]]) {
    if (!ids.length) continue;
    const group = make('div');
    group.append(make('span', 'note', caption));
    for (const factId of ids) {
      const chip = make('span', 'citation', `${factId}  `);
      chip.title = factId;
      chip.addEventListener('click', () => revealFact(factId));
      group.append(chip);
    }
    item.append(group);
  }

  // Where an observed row prints its evidence id. The sentence is the server's and says why
  // there is none and where a citation for this number has to attach instead.
  item.append(make('div', 'is-absent', row.evidence_handle_absent_because));

  const sentences = row.used_in_sentence_indexes ?? [];
  if (row.used_by_draft) {
    const group = make('div');
    group.append(make('span', 'note', 'bound by '));
    for (const index of sentences) {
      const chip = make('span', 'citation', `sentence ${index}  `);
      chip.addEventListener('click', () => revealSentence(Number(index)));
      group.append(chip);
    }
    item.append(group);
  } else {
    item.append(make('div', 'is-absent', LABELS.derivedUnused));
  }
  item.append(make('div', 'mono', row.fact_id));

  if (!state.factRows.has(row.fact_id)) state.factRows.set(row.fact_id, []);
  state.factRows.get(row.fact_id).push({ row: item, box, tab: dom.tabFacts });
  return item;
}

/**
 * One fact value, one string, whoever rendered it.
 *
 * The sources panel prints `value_display`, which the server produced with `%.10g`; this panel
 * had no such field and formatted the raw float itself, so the same fact could in principle read
 * two ways on one page. Rule 4 decides the order: a string the server sent is used as it
 * arrived, and only when there is none does this file format the number — with `formatNumber`,
 * which is now `%.10g` itself. The exact float goes on the element's `title` either way.
 */
function factValue(fact) {
  for (const key of ['value_display', 'rendered']) {
    if (typeof fact[key] === 'string' && fact[key] !== '') return fact[key];
  }
  return formatNumber(fact.value);
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
    const value = make('span', 'mono',
      `  ${factValue(fact)} ${fact.unit ?? ''} · ${fact.period_key}`);
    value.title = `exact value ${fact.value}`;
    head.append(value);
    item.append(head);
    const line = make('div', 'mono', fact.observation_id);
    item.append(line);
    if (fact.quoted_text) {
      item.append(make('div', null, `cell value: “${fact.quoted_text}”`));
    }
    if (fact.row_label || fact.column_label) {
      item.append(make('div', null,
        `read from row “${fact.row_label ?? ''}” under “${fact.column_label ?? ''}”`));
    }
    // The evidence id, on the fact it was minted for. It is the only citation token the model
    // is given and the only one a §13.7 refusal names, so a reader matching a rejection to a
    // line has to be able to find it here.
    if (fact.evidence_handle) {
      const handle = make('div', 'mono', `evidence id ${fact.evidence_handle}`);
      if (fact.cell) {
        handle.title = `row ${fact.cell.row_index}, column ${fact.cell.value_column_index}; `
          + `period header at row ${fact.cell.period_header_row_index}, `
          + `column ${fact.cell.period_header_column_index}`;
      }
      item.append(handle);
    } else {
      item.append(make('div', 'is-absent',
        'no evidence id: this fact names no filed passage, so V1 refuses a citation to it'));
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

/**
 * One warning, with its §4 S5 category and the consequence the numbers alone do not state.
 *
 * `detail` carries the counts — how many rows were returned, the ordering they kept, the lowest
 * severity that survived — and is printed as it arrived. `consequence` is the server's standing
 * sentence for the truncation codes, and it is the part a reader needs once: that the result is
 * not exhaustive.
 */
function warningRow(warning) {
  const row = make('div');
  row.append(make('strong', null, `${warning.code} · ${warning.severity}`));
  if (warning.category_label) {
    row.append(make('span', 'citation', `  ${warning.category_label}`));
  }
  if (warning.detail) row.append(make('div', null, warning.detail));
  if (warning.consequence) row.append(make('div', 'note', warning.consequence));
  const explanation = warning.explanation ?? {};
  if (explanation.description) row.append(make('div', 'note', explanation.description));
  if ((warning.subject_ids ?? []).length) {
    row.append(make('div', 'mono', warning.subject_ids.join(', ')));
  }
  return row;
}

/** One carried row inside a section's inspect control, labelled with the role it plays. */
function carriedRow(item) {
  const row = make('div', 'mono');
  const identifier = item.passage_id ?? item.fact_id ?? item.observation_id ?? '';
  row.textContent = String(identifier);
  if (item.role_label) {
    row.append(make('span', 'citation', `  ${item.role_label}`));
  }
  return row;
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

  const view = pkg.warning_view ?? {};
  const limitationCodes = new Set(
    (view.capability_limitations ?? []).map((row) => row.code));
  const warnings = (pkg.warnings ?? []).filter((row) => !limitationCodes.has(row.code));
  const warningBox = details(host, `Package warnings (${warnings.length})`,
    { open: warnings.length > 0 });
  for (const warning of warnings) {
    warningBox.append(warningRow(warning));
  }

  // §4 S5 and §4 S6: a capability limitation is a thing this version cannot do at all, it fires
  // on every package ever built, and it is **not** a defect in this evidence. Rendered as its
  // own group with the server's own sentence, so it cannot read as a caveat about the post.
  const limitations = view.capability_limitations ?? [];
  const limitationBox = details(host, `Limitations of this version (${limitations.length})`,
    { open: limitations.length > 0 });
  if (view.limitation_note) limitationBox.append(make('p', 'note', view.limitation_note));
  for (const warning of limitations) limitationBox.append(warningRow(warning));

  // Every section's available / carried / dropped / reason, which is what "4 of 9 primary
  // passages sent to the model" is a reading of, with the control to expand each one.
  const ledger = view.section_ledger ?? budget.section_ledger ?? [];
  const ledgerBox = details(host, `What each section carried (${ledger.length})`);
  for (const row of ledger) {
    const section = make('div');
    section.append(make('strong', null, row.section));
    section.append(sectionLedgerLine(row));
    const rows = payload[row.section];
    if (Array.isArray(rows) && rows.length) {
      const inspect = details(section, `inspect the ${formatCount(rows.length)} carried`);
      for (const passage of rows) inspect.append(carriedRow(passage));
    }
    ledgerBox.append(section);
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

/**
 * The provider catalogue, fetched beside the presets and rendered as two selects.
 *
 * **Every provider the server knows is listed, and an unusable one is disabled rather than
 * dropped.** Dropping would be the smaller render and the dishonest one: a reader who cannot see
 * OpenAI cannot tell whether this build lacks it or this machine has not been given a key, and
 * those are different problems with different fixes. The reason is the server's own sentence and
 * it names a setting, never a value — there is nothing in the payload that could name a value.
 *
 * Nothing here can be typed. The two controls choose among ids the server published, and the
 * request carries those ids alone; `api.py` refuses a body carrying a URL, a key or a timeout
 * rather than ignoring it, so "this page cannot reconfigure the server" is enforced at both ends.
 */
async function loadProviders() {
  try {
    const payload = await getJson(ENDPOINTS.providers);
    state.providers = payload.providers ?? [];
    state.providerById = new Map(state.providers.map((entry) => [entry.provider_id, entry]));
    const select = clear(dom.providerSelect);
    for (const provider of state.providers) {
      const option = document.createElement('option');
      option.value = provider.provider_id;
      option.textContent = provider.available
        ? provider.label
        : `${provider.label} — ${provider.unavailable_reason}`;
      option.disabled = !provider.available;
      select.append(option);
    }
    const chosen = state.providerById.get(payload.default_provider_id)?.available === true
      ? payload.default_provider_id
      : (state.providers.find((entry) => entry.available) ?? {}).provider_id ?? '';
    select.value = chosen;
    applyProvider(chosen);
    clearError();
  } catch (failure) {
    showError('loading the provider catalogue', failure);
  }
}

/** Repopulate the model list from the chosen provider's own declared models. */
function applyProvider(providerId) {
  const provider = state.providerById.get(providerId);
  state.selectedProviderId = provider ? providerId : null;
  const select = clear(dom.modelSelect);
  const models = provider ? provider.models ?? [] : [];
  for (const model of models) {
    const option = document.createElement('option');
    option.value = model.model_id;
    option.textContent = model.label;
    select.append(option);
  }
  const preferred = provider ? provider.default_model_id : '';
  state.selectedModelId = models.some((model) => model.model_id === preferred)
    ? preferred
    : (models[0] ?? {}).model_id ?? null;
  select.value = state.selectedModelId ?? '';
  select.disabled = models.length === 0;
  renderProviderNotice();
}

/**
 * The provider and model controls follow the Generate button's busy latch exactly.
 *
 * Not decoration: the pair is frozen on the server when the 202 is issued, so a select that
 * still moved during a run would show one provider while the run recorded another — the
 * interface would be the only thing that had changed, and it would be lying about the run.
 */
function setSelectionEnabled(enabled) {
  dom.providerSelect.disabled = !enabled;
  dom.modelSelect.disabled = !enabled || dom.modelSelect.options.length === 0;
}

function applyModel(modelId) {
  state.selectedModelId = modelId === '' ? null : modelId;
  renderProviderNotice();
}

/**
 * What the two controls mean, what the selected model does with a temperature, and every
 * provider this machine cannot use, with the server's reason for each.
 *
 * The temperature line is a measured capability the catalogue carries per model, not a
 * preference: a model that refuses the parameter has none sent and its manifest says so, and the
 * panel is the only place a reader would otherwise have to guess which of the two they picked.
 */
function renderProviderNotice() {
  const host = clear(dom.providerNotice);
  host.append(make('p', 'note', LABELS.providerSelection));

  const unavailable = state.providers.filter((provider) => !provider.available);
  if (unavailable.length > 0) {
    host.append(make('p', 'note', LABELS.providerUnavailable));
    for (const provider of unavailable) {
      host.append(make('p', 'mono', `${provider.label}: ${provider.unavailable_reason}`));
    }
  }

  const provider = state.providerById.get(state.selectedProviderId);
  const models = provider ? provider.models ?? [] : [];
  const model = models.find((entry) => entry.model_id === state.selectedModelId);
  if (!model) return;
  host.append(make('p', 'note', model.supports_temperature
    ? LABELS.temperaturePinned
    : LABELS.temperatureRefused));
  if (model.reasoning_effort) {
    host.append(make('p', 'mono', `reasoning effort ${model.reasoning_effort}`));
  }
}

/**
 * The 409 a provider with no recorded store returns, rendered as the thing to do next.
 *
 * `provider_requires_live` mirrors `edited_prompt_requires_live` on purpose and is answered the
 * same way: the body carries the frozen selection beside the error, so the panel names the
 * provider that needs a live run instead of saying only that something does.
 */
function renderProviderRequiresLive(failure) {
  const selection = (failure.body ?? {}).provider_selection ?? {};
  const host = dom.providerNotice;
  host.append(make('p', null, failure.message));
  host.append(make('p', 'note', LABELS.providerRequiresLive));
  host.append(make('p', 'mono',
    `${selection.provider_id ?? ''} · ${selection.model_id ?? ''}`));
  state.requiresLiveOffered = true;
  dom.generatePost.textContent = 'Generate post (live run)';
  setPipelineState('the selected provider needs a live run');
  selectTab(dom.tabPrompts);
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
  setSelectionEnabled(false);
  resetTrace();
  state.streamLost = false;
  state.quiet = false;
  state.generationRunId = null;
  resetOutput();
  setPipelineState('generating');
  selectTab(dom.tabTrace);

  const request = {
    candidate_id: state.selectedCandidateId,
    preset_id: state.selectedPresetId,
    ...editableFields(),
  };
  // Two ids and never a third field. The server validates the pair against its own catalogue
  // and freezes it; omitting them would be a run against the configured default, which is what
  // every call site written before there was a second provider still gets.
  if (state.selectedProviderId !== null) request.provider_id = state.selectedProviderId;
  if (state.selectedModelId !== null) request.model_id = state.selectedModelId;
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
        armQuietTimer('generation', started.run_id);
        const rendered = renderTraceEvent(event);
        if (rendered === null) return;
        state.traceEvents.push(event);
        setPipelineState(`generating · ${event.stage} · ${event.status}`);
        reportStreamedEvent(event, rendered);
      },
      onEnd: () => finishGeneration(params),
      onLost: () => {
        state.streamLost = true;
        finishGeneration(params);
      },
    });
    armQuietTimer('generation', started.run_id);
  } catch (failure) {
    state.busy = false;
    dom.generatePost.disabled = false;
    setSelectionEnabled(true);
    if (failure instanceof ApiFailure && failure.code === 'edited_prompt_requires_live') {
      renderRequiresLive(failure);
      return;
    }
    if (failure instanceof ApiFailure && failure.code === 'provider_requires_live') {
      renderProviderRequiresLive(failure);
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

/**
 * Read the run's own answer and paint it — if it is still this panel's run.
 *
 * Rule 5 is enforced at every await boundary rather than once at the top, because there are two
 * of them and a run can be superseded during either. The button and `state.busy` are released in
 * the `finally`, and only for the current run: releasing them for a superseded one would
 * re-enable the interface on behalf of a run nobody is watching.
 */
async function finishGeneration(params) {
  const runId = params.run_id;
  clearQuietTimer();
  if (!isCurrentRun('generation', runId)) {
    noteSuperseded('generation', runId);
    return;
  }
  state.quiet = false;
  try {
    const payload = await getJson(serverPath(null, ENDPOINTS.runResult, params));
    if (!isCurrentRun('generation', runId)) {
      noteSuperseded('generation', runId);
      return;
    }
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
    const shape = renderOutcome(payload.outcome);
    setPipelineState(shape === 'inconsistent'
      ? `generation outcome inconsistent · payload says ${payload.outcome.disposition}`
      : `generation ${payload.outcome.disposition}`);

    const sources = await getJson(serverPath(null, ENDPOINTS.runSources, params));
    if (!isCurrentRun('generation', runId)) {
      noteSuperseded('generation', runId);
      return;
    }
    state.sources = sources.sources ?? null;
    renderSources(state.sources);
    // **After the sources, and the order is load-bearing.** `renderSources` clears
    // `state.factRows` and repopulates it from the passages; the derived rows register
    // themselves as they are drawn, so drawing them first would leave every derived fact with
    // no click target. The facts panel is the one place a run writes into a package panel, and
    // it is the last thing painted for that reason.
    renderModelFacts(state.packagePayload?.model_facts ?? null,
      payload.outcome.derived_facts ?? null);
    if (payload.outcome.derived_facts_error) {
      dom.modelFacts.append(make('div', 'is-blocking',
        String(payload.outcome.derived_facts_error.message)));
    }
    if (shape !== 'inconsistent') settleBanner();
  } catch (failure) {
    showError('reading the generation outcome', failure);
  } finally {
    if (isCurrentRun('generation', runId)) {
      state.busy = false;
      dom.generatePost.disabled = false;
      setSelectionEnabled(true);
    }
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

/**
 * Which of the three shapes this payload is, and every signal that disagrees about it.
 *
 * The review produced a payload the endpoint can really emit — `accepted: true`,
 * `rendered_as: "post"`, `post: null`, because `post.md` was unreadable — and the panel rendered
 * "**Refused · accepted**", asserted "verifier ran: no" while `verification.passed` was `true`
 * over twelve checks and no blocking finding, and captioned an accepted draft as the refused
 * one. Three claims, all false, from one missing file.
 *
 * The rule this function exists for: **when the signals disagree, the disagreement is the
 * finding**. Picking the branch that happens to have content is how a panel ends up asserting
 * something the payload never said. So each signal is tested against each other one, and what
 * comes back is either a coherent shape or a list of the contradictions, in the payload's own
 * words, for the panel to print instead of a verdict.
 */
function outcomeConsistency(outcome) {
  const verification = outcome.verification ?? null;
  const blocking = verification ? (verification.blocking_findings ?? []).length : null;
  const asPost = outcome.rendered_as === 'post';
  const asRejection = outcome.rendered_as === 'rejection';
  const signals = [
    `accepted: ${formatValue(outcome.accepted)}`,
    `disposition: ${outcome.disposition}`,
    `rendered_as: ${outcome.rendered_as}`,
    `post in the payload: ${outcome.post ? 'yes' : 'no'}`,
    `rejection block in the payload: ${outcome.rejection ? 'yes' : 'no'}`,
    verification === null
      ? 'verification: absent'
      : `verification: passed ${verification.passed ? 'yes' : 'no'}, `
        + `${(verification.checks ?? []).length} checks, ${blocking} blocking findings`,
  ];

  const conflicts = [];
  if (!asPost && !asRejection) {
    conflicts.push(`rendered_as is “${outcome.rendered_as}”, which is neither a post nor a `
      + 'rejection, so there is no shape to render.');
  }
  if (asPost && !outcome.post) {
    conflicts.push('The run is rendered as a post but the payload carries no post.');
  }
  if (asRejection && !outcome.rejection) {
    conflicts.push('The run is rendered as a rejection but the payload carries no rejection.');
  }
  if (asPost && outcome.accepted === false) {
    conflicts.push('The run is rendered as a post while `accepted` is false.');
  }
  if (asRejection && outcome.accepted === true) {
    conflicts.push('The run is rendered as a rejection while `accepted` is true.');
  }
  if (asPost && verification !== null && verification.passed === false) {
    conflicts.push('The run is rendered as a post while the deterministic verifier refused the '
      + 'draft. Verification is authoritative.');
  }
  if (asRejection && verification !== null && verification.passed === true && blocking === 0) {
    conflicts.push('The run is rendered as a rejection while the deterministic verifier passed '
      + 'the draft with no blocking finding.');
  }
  const shape = conflicts.length > 0 ? 'inconsistent' : (asPost ? 'post' : 'rejection');
  return { shape, conflicts, signals };
}

function renderOutcome(outcome) {
  resetOutput();
  const consistency = outcomeConsistency(outcome);
  const accepted = consistency.shape === 'post';
  const host = dom.postOutput;

  if (consistency.shape === 'inconsistent') {
    // No verdict class: `is-accepted` and `is-rejected` are both claims, and neither is safe.
    dom.verdictBadge.textContent = `inconsistent · payload says ${outcome.disposition}`;
    dom.verdictBadge.className = 'mono is-inconsistent';
    showNotice('This run’s outcome is inconsistent and no verdict is shown. '
      + LABELS.inconsistentOutcome);
    renderInconsistentOutcome(host, outcome, consistency);
    renderPlan(host, outcome.plan, outcome);
    renderDraft(host, outcome);
    renderPostMeta(outcome);
    renderVerification(outcome.verification);
    selectTab(dom.tabPost);
    return consistency.shape;
  }

  dom.verdictBadge.textContent = outcome.disposition;
  dom.verdictBadge.className = `mono ${accepted ? 'is-accepted' : 'is-rejected'}`;

  if (accepted && outcome.post) {
    renderMarkdown(host, outcome.post.markdown);
  } else {
    // `rendered_as` is the branch, not the disposition string: a run that was refused shows the
    // refused draft and the findings that refused it, and there is no path here that can put an
    // accepted-looking post on the screen for a rejected run.
    const rejection = outcome.rejection ?? {};
    // The one thing inside this branch that is *not* a refusal. `provider_fault` is present
    // only when the provider answered nothing, and it changes the two sentences at the top:
    // "Refused" and "the findings that refused it" are both claims about a model's answer, and
    // this run has none. The `codes` loop below runs either way and is empty for a fault,
    // because the server sends no code it cannot name a stage for.
    const fault = rejection.provider_fault ?? null;
    host.append(make('h3', null, fault
      ? `No answer · ${outcome.disposition}`
      : `Refused · ${outcome.disposition}`));
    host.append(make('p', null, fault ? LABELS.providerFaultRun : LABELS.rejectedRun));
    host.append(make('p', 'mono', fault
      ? `attempted at ${fault.stage ?? ''} · ${fault.error_class ?? ''} · answer produced: `
        + `${fault.answer_produced ? 'yes' : 'no'} · artifact ${rejection.filename ?? ''}`
      : `refused at ${rejection.stage ?? ''} · artifact ${rejection.filename ?? ''} · `
        + `verifier ran: ${rejection.verifier_ran ? 'yes' : 'no'}`));
    for (const code of rejection.codes ?? []) {
      const row = make('div', 'is-blocking');
      row.append(make('strong', null, code.code));
      row.append(make('div', null, code.description || 'no catalogue entry documents this code'));
      if (code.remedy) row.append(make('div', 'note', code.remedy));
      host.append(row);
    }
    // The server's own sentence for an empty code list, rendered rather than dropped. It was
    // sent and never shown until 2026-08-19, which is how a panel came to print "Refused" and
    // nothing else about a run that had reached no model.
    if (rejection.codes_absent_reason) {
      host.append(make('p', 'note', String(rejection.codes_absent_reason)));
    }
    if (outcome.draft) {
      const box = details(host, 'The refused draft, as written', { open: true });
      for (const sentence of outcome.draft.sentences ?? []) {
        box.append(make('p', null, sentence.text));
      }
    }
  }

  renderPlan(host, outcome.plan, outcome);
  renderDraft(host, outcome);
  renderPostMeta(outcome);
  renderVerification(outcome.verification);
  selectTab(dom.tabPost);
  return consistency.shape;
}

/**
 * The payload that cannot be rendered as either shape, rendered as exactly that.
 *
 * What is on screen is the six signals and the contradictions between them. Whatever content the
 * payload does carry is shown underneath, captioned as what it is — a draft is "the draft", not
 * "the refused draft" — because relabelling it is the mistake this branch exists to stop.
 */
function renderInconsistentOutcome(host, outcome, consistency) {
  host.append(make('h3', null, 'This run’s outcome is inconsistent'));
  host.append(make('p', null, LABELS.inconsistentOutcome));
  for (const conflict of consistency.conflicts) {
    host.append(make('div', 'is-blocking', conflict));
  }
  const box = details(host, 'Every signal, exactly as the payload sent it', { open: true });
  for (const signal of consistency.signals) box.append(make('div', 'mono', signal));

  if (outcome.post) {
    const posted = details(host, 'The post the payload carries', { open: true });
    renderMarkdown(posted, outcome.post.markdown);
  }
  const rejection = outcome.rejection;
  if (rejection) {
    const fault = rejection.provider_fault ?? null;
    const refusal = details(host, fault
      ? 'The provider fault the payload carries'
      : 'The rejection the payload carries', { open: true });
    // Captioned as what it is, for this function's own reason: relabelling a fault as a refusal
    // is the mistake this branch exists to stop, and it does not stop being one because the
    // payload is also inconsistent about something else.
    refusal.append(make('p', 'mono', fault
      ? `attempted at ${fault.stage ?? ''} · ${fault.error_class ?? ''} · answer produced: `
        + `${fault.answer_produced ? 'yes' : 'no'} · artifact ${rejection.filename ?? ''}`
      : `refused at ${rejection.stage ?? ''} · artifact ${rejection.filename ?? ''} · `
        + `verifier ran: ${rejection.verifier_ran ? 'yes' : 'no'}`));
    for (const code of rejection.codes ?? []) {
      const row = make('div', 'is-blocking');
      row.append(make('strong', null, code.code));
      row.append(make('div', null, code.description || 'no catalogue entry documents this code'));
      refusal.append(row);
    }
  }
  if (outcome.draft) {
    const drafted = details(host, 'The draft, as written', { open: true });
    for (const sentence of outcome.draft.sentences ?? []) {
      drafted.append(make('p', null, sentence.text));
    }
  }
}

/**
 * The plan, with the one caveat the review found missing.
 *
 * §3 clause 2 permits this panel: `thesis`, `why_it_matters` and each `claim` are fields the
 * model returned under schema constraint, not free text it was asked to narrate. What was
 * missing is that a reader cannot tell that from the page. On a live run the model wrote
 * *"…driven by non-recurring costs excluded from the adjusted metric"* and *"The divergence is
 * caused by…"* — causal wording, `statement_class: explanatory`, `required_fact_ids: []` — while
 * the run's own trace reported `checking_causal_language · skipped` and the payload carried no
 * verification at all. Not a rule violation; an honesty gap, and this is where it closes.
 *
 * The three things said here are all measured, never inferred: that the text is the model's,
 * whether the verifier ran, and what this run's trace reported about the causal-language check.
 * Nothing here scans the prose for causal words — that would be this file inventing a check and
 * then reporting its result as if §13 had run one.
 */
function renderPlan(host, plan, outcome = {}) {
  const box = details(host, plan === null ? 'Editorial plan — none' : 'Editorial plan');
  if (plan === null || plan === undefined) {
    box.append(make('div', 'is-absent', 'The planner produced no plan.'));
    return;
  }
  box.append(make('p', 'note', LABELS.planIsModelText));
  const verification = outcome.verification ?? null;
  if (verification === null) {
    box.append(make('div', 'is-absent',
      'The deterministic verifier did not run on this outcome, so nothing below — including any '
      + 'causal wording — has been examined by any check.'));
  }
  const causal = state.traceEvents
    .filter((event) => event.stage === 'checking_causal_language')
    .slice(-1)[0];
  if (causal && causal.status !== 'passed') {
    box.append(make('div', 'is-warning',
      `This run’s trace reports the causal-language check over the draft as “${causal.status}”.`));
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
      if ((point.required_fact_ids ?? []).length === 0) {
        // A count of zero is already on the line above; saying it in words is what stops a
        // reader taking an explanatory claim with no fact behind it for a supported one.
        row.append(make('div', 'is-absent', 'The plan binds no fact id to this claim.'));
      }
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
 *
 * **`sentence.calculation` is gone and is not rendered as absent** (S13). It was a field the
 * writer filled — an operation, an expression and a rendered result the model declared — and
 * the failure that started S13 was not its arithmetic but its optional `period_surface`, which
 * the model left empty while its own text read *"in the third quarter of 2022"*. The field left
 * the writer schema; a computed value is now bound like any other fact, so the chip is where it
 * shows and there is nothing left for a `calculated:` line to say. A branch on a field that no
 * schema can produce would be dead code that read as a feature.
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
  // This run's derived facts, by id, so a binding chip can say what it is bound to. The rows are
  // the server's — the operation and the two periods are `DerivedFact`'s own fields — and the
  // map is only the lookup.
  const derivedRows = new Map(
    (outcome.derived_facts?.rows ?? []).map((row) => [String(row.fact_id), row]));

  for (const sentence of draft.sentences ?? []) {
    const row = make('div');
    row.dataset.sentenceIndex = String(sentence.index);
    row.append(make('div', null, `${sentence.index}. ${sentence.text}`));
    row.append(make('div', 'mono', `kind: ${sentence.kind}`));

    const bindings = sentence.fact_bindings ?? [];
    if (bindings.length) {
      const group = make('div');
      let derivedHere = false;
      for (const binding of bindings) {
        // A derived binding looks exactly like a filed one on the wire — since S13 a computed
        // value is stated by an ordinary `FactBinding`, which is the repair — so the chip is
        // told apart by looking the id up in this run's derived group rather than by reading
        // the id's own prefix. Parsing an id in the browser would be a second opinion about
        // what a `fact:derived:` string means.
        const derived = derivedRows.get(String(binding.fact_id));
        if (derived) derivedHere = true;
        // Three shapes, because a binding can name three kinds of fact. `operation` is absent on
        // §7's kind, which is a claim about the evidence rather than a quantity — rendering it
        // as `undefined undefined` was the failure mode this branch exists to avoid.
        const chip = make('span', derived ? 'citation is-derived' : 'citation', derived
          ? (derived.operation
            ? `${binding.rendered} ← ${derived.operation} ${derived.period_label}  `
            : `${binding.rendered} ← ${derived.claim}  `)
          : `${binding.rendered} ← ${binding.metric_surface} ${binding.period_surface}  `);
        chip.title = derived ? `${LABELS.derivedBinding} ${derived.fact_id}` : binding.fact_id;
        chip.addEventListener('click', () => revealFact(binding.fact_id));
        group.append(chip);
      }
      row.append(group);
      if (derivedHere) row.append(make('div', 'note', LABELS.derivedBinding));
    } else {
      row.append(make('div', 'is-absent', 'no fact binding'));
    }

    const citations = sentence.citations ?? [];
    if (citations.length) {
      const group = make('div');
      for (const citation of citations) {
        // The evidence id leads because it is the whole of what the model wrote: since S4 a
        // citation is one field, `evidence_id`, and the passage and the offsets beside it were
        // derived from it by `writer.draft_from`. Showing the derived span first would put the
        // model's name for the evidence behind two numbers it did not choose.
        // A citation with no `passage_id` is Rule C's `:EvidenceSource` form, which V1 refuses
        // and the corpus can produce none of — but it used to render as `cites undefined`
        // rather than as the thing it is.
        const names = citation.evidence_handle ?? citation.passage_id
          ?? citation.evidence_source_id ?? 'evidence with no filed passage';
        const span = citation.passage_id
          ? ` [${citation.char_start}–${citation.char_end}]` : '';
        const chip = make('span', 'citation', `cites ${names}${span}  `);
        chip.title = citation.passage_id ?? citation.kind ?? '';
        chip.addEventListener('click', () => revealPassage(citation.passage_id));
        group.append(chip);
      }
      row.append(group);
    } else if (sentence.kind !== 'connective') {
      row.append(make('div', 'is-absent', 'no citation'));
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
    Object.entries(counts)
      .filter(([, value]) => typeof value === 'number')
      .map(([name, value]) => `${name} ${value}`).join(' · ')));
  // Where a row was fetched from and what it turned out to be, as two lines: a section with no
  // rows is not the same fact as a role nothing plays, and both zeros are kept.
  for (const [name, breakdown] of [['by section', counts.by_section],
    ['by role', counts.by_role]]) {
    if (!breakdown) continue;
    heading.append(make('div', 'mono',
      `${name}: ` + Object.entries(breakdown)
        .map(([key, value]) => `${key} ${value}`).join(' · ')));
  }
  for (const role of sources.roles ?? []) {
    if (!role.count) continue;
    heading.append(make('div', 'note', `${role.label}: ${role.description}`));
  }
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

/**
 * A flattened table passage drawn as the grid it came from, with the cited cell in place.
 *
 * **Why this is not a bare quote any more.** A citation used to render as `“(12.6)” [492–498]`:
 * a numeral and two offsets. Nothing in that says which line item the number sits on or which
 * period stands over it, and those two are what make a figure mean anything. The server sends
 * `grid` and `cell_marks` from `story/demo_ui/table_grid.py`, which resolves every cell through
 * `story.core.table_cells.resolve_cell` — the same function the verifier uses — so this file
 * places nothing itself and cannot become a third opinion about where the evidence is.
 *
 * Three things it draws that are easy to get wrong, and all three come off measurements of the
 * live corpus recorded in `table_grid`'s docstring:
 *
 * - **Every column, including the blank ones.** 72.0% of cells in the corpus's 503 table
 *   passages are empty once stripped. Collapsing them would renumber the grid, and the number
 *   in the handle — `r5c2` — counts them.
 * - **An index ruler down the left and across the top.** The widest table here has 46 columns
 *   and 111 of the 503 have twenty or more, so "column 2" is not something an eye finds by
 *   counting cells. The ruler is what makes the handle legible against the picture.
 * - **The row label and the period header are marked as such**, not just the value: `is-cited`
 *   is the value, `is-row-label` its line item, `is-period-header-cell` the column header the
 *   package's `period_header_column_index` names — which is a different column from the value's
 *   on four rows in five.
 */
/* BEGIN CELL GRID — extracted with the block above; see its comment. */
function cellGrid(grid) {
  const table = make('table', 'cell-grid');
  const head = make('thead');
  const ruler = make('tr');
  ruler.append(make('th', 'grid-corner', 'r\\c'));
  for (const index of grid.column_indices ?? []) {
    ruler.append(make('th', 'grid-index', String(index)));
  }
  head.append(ruler);
  table.append(head);

  const body = make('tbody');
  for (const row of grid.rows ?? []) {
    const line = make('tr');
    line.dataset.rowIndex = String(row.row_index);
    if (row.is_rule) line.classList.add('is-rule');
    if (row.is_period_header) line.classList.add('is-period-header');
    line.append(make('th', 'grid-index', String(row.row_index)));
    const cells = row.cells ?? [];
    for (const cell of cells) {
      const box = make('td', null, cell.text);
      box.dataset.columnIndex = String(cell.column_index);
      if (cell.empty) box.classList.add('is-empty');
      if (cell.is_row_label) box.classList.add('is-row-label');
      if ((cell.header_for ?? []).length) box.classList.add('is-period-header-cell');
      if ((cell.marked_by ?? []).length) {
        box.classList.add('is-cited');
        // **All** the marks, space separated, matched below with `~=`. One cell ordinarily
        // carries two — the packaged fact and the sentence citing it — and writing only the
        // first left every "jump to the cited cell" from a citation row finding nothing.
        box.dataset.markIndex = cell.marked_by.join(' ');
        box.title = `row ${row.row_index}, column ${cell.column_index} · `
          + `characters ${cell.char_start}–${cell.char_end}`;
      }
      line.append(box);
    }
    // A short row keeps the ruler honest rather than sliding the remaining cells left. No
    // passage in the corpus is ragged (0 of 503), so this draws nothing today; it is here so a
    // re-extracted corpus that did go ragged renders as ragged.
    for (let index = cells.length; index < (grid.column_count ?? 0); index += 1) {
      line.append(make('td', 'is-missing'));
    }
    body.append(line);
  }
  table.append(body);
  return table;
}
/* END CELL GRID */

/**
 * One piece of evidence, said in words: which cell, on which row, under which header.
 *
 * The evidence id leads because it is the model's entire citation vocabulary since S4 and it is
 * what a §13.7 refusal names — `evidence_handle_not_for_fact` prints `ev:…:r5c2` and nothing
 * else, so a reader with a rejection in front of them needs that string beside the cell it
 * means.
 *
 * Where the package and its own passage text disagree, both are printed and the row is marked
 * blocking. This states no verdict: §13.7's checks 3–5 decide whether a disagreement refuses a
 * draft, and they run whether or not anything renders.
 */
function cellMarkRow(mark, grid, onReveal) {
  const row = make('div');
  row.dataset.evidenceHandle = mark.evidence_handle ?? '';
  const head = make('div');
  if (mark.kind === 'citation' && mark.sentence_index !== null
      && mark.sentence_index !== undefined) {
    const chip = make('span', 'citation', `sentence ${mark.sentence_index}  `);
    chip.addEventListener('click', () => revealSentence(Number(mark.sentence_index)));
    head.append(chip);
  }
  head.append(make('span', null, `${mark.label}  `));
  row.append(head);
  row.append(make('div', 'mono', mark.evidence_handle ?? 'no evidence id on this citation'));

  if (!mark.resolved) {
    row.append(make('div', 'is-absent', mark.unplaced_reason));
    if (mark.declared_value_text) {
      row.append(make('div', null, `the package reads this fact as “${mark.declared_value_text}”`
        + `${mark.declared_row_label ? ` on row “${mark.declared_row_label}”` : ''}`));
    }
    return row;
  }

  const where = make('div', null,
    `row ${mark.row_index} “${mark.row_label}” × column ${mark.column_index} `
    + `under “${mark.column_header}” = “${mark.text}”`);
  where.addEventListener('click', () => onReveal(mark));
  where.classList.add('cell-mark-where');
  row.append(where);
  row.append(make('div', 'mono',
    `characters ${mark.char_start}–${mark.char_end} · period header at row `
    + `${mark.header_row_index}, column ${mark.header_column_index}`));

  for (const [flag, said] of [
    ['matches_value', `the package records the value as “${mark.declared_value_text}”`],
    ['matches_row_label', `the package records the row as “${mark.declared_row_label}”`],
    ['matches_column_label', `the package records the column as “${mark.declared_column_label}”`],
  ]) {
    if (mark[flag] === false) {
      row.classList.add('is-blocking');
      row.append(make('div', null, `${said}, and the passage does not agree`));
    }
  }
  if (mark.span_matches_cell === false) {
    row.classList.add('is-blocking');
    row.append(make('div', null,
      `this citation highlights characters ${mark.span_char_start}–${mark.span_char_end}, `
      + 'which is not the span of the cell its evidence id names'));
  }
  if (grid && grid.coordinates_apply === false) {
    row.append(make('div', 'note', grid.coordinates_note));
  }
  return row;
}

function passageBlock(passage, document_) {
  const wrapper = make('div');
  wrapper.dataset.passageId = passage.passage_id;
  wrapper.dataset.role = passage.role ?? '';
  if (passage.is_counter_evidence) wrapper.classList.add('is-blocking');
  const box = details(wrapper,
    `${passage.role_label ?? passage.role} · ${passage.passage_id} · `
    + `${passage.char_count} chars · ${(passage.facts ?? []).length} facts · `
    + `${(passage.citations ?? []).length} citations`);

  // The role and the section are two different facts and both are shown. The section says why
  // the row was fetched; the role says what it turned out to be — and a `diagnostic_passages`
  // row that is `primary_support` must read as support, not as a diagnostic.
  box.append(make('div', 'note', passage.role_description ?? ''));
  box.append(make('div', 'note',
    `from ${passage.section}: ${passage.section_description ?? ''}`));
  if ((passage.also_in ?? []).length) {
    box.append(make('div', 'note',
      `this passage is also carried in ${passage.also_in.join(', ')}, in another role`));
  }
  if (passage.match_basis) {
    box.append(make('div', 'mono', `matched on: ${passage.match_basis}`));
  }
  if ((passage.diagnostic_codes ?? []).length) {
    box.append(make('div', 'mono',
      `issue codes on this passage: ${passage.diagnostic_codes.join(', ')}`));
  }
  if (passage.unusable_reason) {
    box.append(make('div', 'is-absent', `unusable: ${passage.unusable_reason}`));
  }

  if ((passage.heading_path ?? []).length) {
    box.append(make('div', 'note', passage.heading_path.join(' › ')));
  }
  box.append(make('div', 'mono', passage.excerpted ? 'excerpted' : 'whole passage'));

  // The grid is drawn first so a mark row below it can scroll its own cell into view.
  // `marked_by` on a cell is an index into `cell_marks`, and the two arrived in one payload
  // built from one sequence — see `package_view.passage_rows`.
  const grid = passage.grid ? cellGrid(passage.grid) : null;
  let gridBox = null;
  if (grid) {
    // A table passage renders as its grid **and** keeps its raw text one disclosure away. The
    // grid is the reading; the text is what the package actually carries and what the character
    // offsets index, and dropping it would leave a reader unable to check the drawing.
    gridBox = details(box, `table · ${passage.grid.row_count} rows × `
      + `${passage.grid.column_count} columns · ${passage.grid.empty_cells} of `
      + `${passage.grid.cell_count} cells empty`, { open: true });
    gridBox.append(make('div', 'note', passage.grid.coordinates_note));
    const scroller = make('div', 'cell-grid-scroll');
    scroller.append(grid);
    gridBox.append(scroller);
  }
  const revealCell = (target) => {
    if (!grid) return;
    const cell = grid.querySelector(
      `td[data-mark-index~="${cellMarkIndex(passage, target)}"]`);
    if (!cell) return;
    if (gridBox) gridBox.open = true;
    mark(cell);
    cell.scrollIntoView({ block: 'nearest', inline: 'center' });
  };

  const marks = passage.cell_marks ?? [];
  if (marks.length) {
    const evidence = details(box, `evidence in this passage · ${marks.length}`, { open: true });
    evidence.append(make('div', 'note',
      'Each row names the evidence id a citation carries and the cell it resolves to. The id '
      + 'is what a refusal message names; the cell is what the verifier checked.'));
    for (const entry of marks) {
      const row = cellMarkRow(entry, passage.grid, revealCell);
      if (entry.kind === 'citation' && entry.sentence_text) {
        row.append(make('div', 'note', entry.sentence_text));
      }
      if (entry.kind === 'citation' && entry.span_resolved === false) {
        row.classList.add('is-blocking');
        row.append(make('div', null,
          'the citation\u2019s character range falls outside the text this package carries'));
      }
      evidence.append(row);
    }
  }

  for (const fact of passage.facts ?? []) {
    const row = make('div');
    row.dataset.factId = fact.fact_id;
    row.append(make('div', null,
      `${fact.metric_label} · ${fact.value_display} ${fact.unit} · ${fact.period_key}`));
    row.append(make('div', 'mono', fact.fact_id));
    if (fact.evidence_handle) {
      row.append(make('div', 'mono', `evidence id ${fact.evidence_handle}`));
    }
    if (fact.row_label || fact.column_label) {
      row.append(make('div', null,
        `read from row “${fact.row_label ?? ''}” under “${fact.column_label ?? ''}”`));
    }
    if (fact.quoted_text) row.append(make('div', null, `cell value: “${fact.quoted_text}”`));
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
    state.factRows.get(fact.fact_id).push(
      { row, box, passageId: passage.passage_id, tab: dom.tabSources });
    box.append(row);
  }

  const text = details(box, grid ? 'passage text, as the package carries it' : 'passage text');
  text.append(make('div', 'mono', passage.text));
  if (!state.passageRows.has(passage.passage_id)) {
    state.passageRows.set(passage.passage_id, []);
  }
  state.passageRows.get(passage.passage_id).push({ box, wrapper, role: passage.role, grid });
  return wrapper;
}

/** A mark's position in its passage's `cell_marks`, which is what `marked_by` indexes. */
function cellMarkIndex(passage, mark) {
  return (passage.cell_marks ?? []).indexOf(mark);
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

/**
 * Post → sources, by passage.
 *
 * Every block drawn for the id is opened, not only the first: a passage carried in two sections
 * has two, and opening one of them would hide the fact that the same text is playing a second
 * part. The mark goes on the first, because a mark is a pointer and there is one cursor.
 */
function revealPassage(passageId) {
  const found = state.passageRows.get(String(passageId)) ?? [];
  if (!found.length) return;
  selectTab(dom.tabSources);
  for (const entry of found) entry.box.open = true;
  mark(found[0].wrapper);
  highlightIds([passageId], [], { focus: true });
}

/**
 * Post → the panel that holds this fact.
 *
 * **The tab is on the entry rather than fixed here**, since S13. An observed fact is registered
 * by the sources panel and lives under a passage; a derived fact has no `passage_id` by design —
 * §3 forbids one, because a derived fact names no filed passage — so it is registered by the
 * facts panel and lives under its group. Sending every click to the sources tab put a derived
 * fact id in a panel that had no row for it, which is the no-rows fallback this function already
 * has for an id nothing drew.
 */
function revealFact(factId) {
  const rows = state.factRows.get(String(factId)) ?? [];
  if (!rows.length) {
    highlightIds([factId], [], { focus: true });
    return;
  }
  const first = rows[0];
  selectTab(first.tab ?? dom.tabSources);
  first.box.open = true;
  mark(first.row);
  // `passageId` is absent on a derived row and is not defaulted: highlighting a passage this
  // fact does not name would draw evidence for a number that has none of its own.
  highlightIds([factId, first.passageId].filter(Boolean), [], { focus: true });
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
  dom.providerSelect.addEventListener('change', () => applyProvider(dom.providerSelect.value));
  dom.modelSelect.addEventListener('change', () => applyModel(dom.modelSelect.value));
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
  loadProviders();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start, { once: true });
} else {
  start();
}
