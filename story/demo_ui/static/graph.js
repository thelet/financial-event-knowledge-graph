/**
 * The graph canvas: a deterministic force-directed renderer over a projection payload.
 *
 * Responsibility: turn one `/demo/graph/*` payload into a picture that can be zoomed, panned,
 * hovered, selected, highlighted and focused, and tell the caller which node was chosen.
 * Boundaries, and they matter: **this module never fetches anything and knows no endpoint.**
 * `app.js` owns the network and hands payloads in. It also owns every panel; the only DOM this
 * module writes is the canvas, and — when it is given the elements — the legend, the breadcrumb
 * and the one-line status. Those three are written with `createElement` and `textContent`, never
 * with markup assembled from payload text.
 *
 * ## Determinism
 *
 * Nothing in this file asks for a random number, and there is nothing for one to do. Every
 * starting position is a function of the node's own id through a 32-bit FNV-1a hash, the count
 * of iterations is fixed rather than time- or convergence-bounded, and the accumulation order is
 * the payload's own, which the projection builds under a total `ORDER BY`. Two loads of one
 * payload therefore produce the same coordinates to the bit, which is what `stats().layoutDigest`
 * reports and what makes a screenshot comparable to yesterday's. A layout seeded from a random
 * number generator would be a picture nobody could check.
 *
 * ## Honesty
 *
 * A synthesised node or edge is drawn with a dashed stroke and no fill, and `nodeAt`/`node(id)`
 * hand back the payload's own record — including `derived_from` — so the detail card `app.js`
 * builds can say what a value was derived from rather than presenting it as read. Nothing here
 * invents a property: a node carries exactly the keys the projection put on it.
 *
 * ## Cost, measured 2026-08-04 against the live projections
 *
 * | Payload | Nodes / edges | Layout | Frame traversal at fit |
 * | --- | --- | --- | --- |
 * | `overview` | 403 / 976 | 141 ms | 0.061 ms |
 * | `evidence_backbone` | 215 / 841 | 64 ms | 0.065 ms |
 * | `coverage` | 101 / 554 | 21 ms | 0.035 ms |
 *
 * Layout is a one-off at `setPayload`. The frame figure is the traversal — culling,
 * aggregation, label placement, draw-call issuing — under a stub 2D context, because no browser
 * was available to run this in: **rasterisation is not in those numbers.** What is measured
 * instead is what a frame asks a browser to do. At fit zoom the overview issues 400 arcs, 967
 * strokes and 48 labels; zoomed out to 0.25 the aggregation takes that to 112 arcs plus 50
 * cluster markers and 203 strokes. `stats()` reports all of it at runtime and the status line
 * shows it, because "it felt fast" is not a measurement.
 */

// ---------------------------------------------------------------------------------------
// Tunables. Exported so a caller can state a different budget and so the numbers a reviewer
// wants to check are in one place rather than scattered through the code.
// ---------------------------------------------------------------------------------------

export const DEFAULTS = Object.freeze({
  /**
   * Fixed, not convergence-bounded: a stopping rule that depends on the data is a layout whose
   * shape depends on floating-point luck.
   *
   * **Correction, measured rather than assumed.** An earlier note here said 400 was "where the
   * overview stops moving", which is not what the cooling schedule does. The temperature is a
   * function of `step / layoutIterations`, so a 400-iteration run is not a prefix of an
   * 800-iteration one, and comparing the two gives node positions a mean 16% of the layout's
   * diameter apart — the count is part of the layout's identity, not a budget that stops
   * mattering once it is large enough. What *is* true is that each run is settled at the end of
   * its own schedule: the final iteration's temperature floor is `ideal * 0.02`, about one world
   * unit against the overview's 1,288-unit diameter, so the last step moves nothing visibly.
   */
  layoutIterations: 400,
  /** Ideal-distance scale for the Fruchterman-Reingold spring/repulsion pair. */
  layoutSpread: 1000,
  /** Repulsion beyond this multiple of the ideal distance is not computed. The uniform grid
   *  below is built with cells this wide, which is what keeps layout near-linear in node count. */
  repulsionCutoff: 2.6,
  /** Below this zoom, low-priority nodes bin into screen-space clusters carrying a count. */
  aggregateBelowScale: 1.35,
  aggregateCellPx: 26,
  aggregateMinimumMembers: 3,
  /** Nodes always labelled, in priority order, at zoom 1. Grows with zoom. */
  labelBudget: 48,
  /** Above this zoom every individually drawn node is a label candidate. */
  labelAllAboveScale: 2.2,
  minimumScale: 0.08,
  maximumScale: 14,
  /** One gentle pulse every this many milliseconds while a highlight is active. */
  pulsePeriodMs: 2200,
  cameraEaseMs: 420,
  /** How far a non-highlighted element fades when a highlight is active. */
  dim: 0.82,
});

const TAU = Math.PI * 2;
const PALETTE_SIZE = 8;
const MAX_LABEL_CHARS = 42;

// ---------------------------------------------------------------------------------------
// Hashing. The only source of "randomness" in the file, and it is not random at all.
// ---------------------------------------------------------------------------------------

/**
 * FNV-1a, 32-bit, over UTF-16 code units. Stable across engines because every step is an
 * `imul` and a shift; `Math.imul` is what keeps the multiply from going through a double.
 *
 * @param {string} text
 * @returns {number} an unsigned 32-bit integer
 */
export function hash32(text) {
  let hash = 0x811c9dc5;
  const value = String(text);
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/** A digest over the laid-out coordinates. Two runs that agree here drew the same picture. */
function positionDigest(nodes) {
  let hash = 0x811c9dc5;
  for (const node of nodes) {
    hash = mixNumber(hash, Math.round(node.x * 64));
    hash = mixNumber(hash, Math.round(node.y * 64));
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function mixNumber(hash, value) {
  let next = hash;
  let remaining = value | 0;
  for (let byte = 0; byte < 4; byte += 1) {
    next ^= remaining & 0xff;
    next = Math.imul(next, 0x01000193) >>> 0;
    remaining >>>= 8;
  }
  return next >>> 0;
}

// ---------------------------------------------------------------------------------------
// The model. Built once per payload; nothing below reads the payload again except to hand a
// node's own record back to the caller.
// ---------------------------------------------------------------------------------------

/**
 * Node priority, from which label eligibility, radius and aggregation all follow.
 *
 * Weighted 0.85 toward the *rarity of the node's type within this payload* and 0.15 toward
 * degree. That ordering is deliberate and is what keeps documents labelled: a document has
 * degree 2 and would lose every degree-ranked contest to a passage, but there are 25 of them
 * against 308 observations, and the rare types are the ones a reader navigates by. **No type
 * name appears in this calculation** — the legend the payload carries is the only list of types
 * this module has, so a new projection with new types needs no edit here.
 */
function computePriority(typeShare, degreeShare) {
  return 0.85 * (1 - typeShare) + 0.15 * Math.sqrt(degreeShare);
}

function buildModel(payload, options) {
  const rawNodes = Array.isArray(payload?.nodes) ? payload.nodes : [];
  const rawEdges = Array.isArray(payload?.edges) ? payload.edges : [];

  const index = new Map();
  const nodes = rawNodes.map((record, position) => {
    const id = String(record.id);
    index.set(id, position);
    return {
      id,
      position,
      record,
      type: String(record.type ?? 'unknown'),
      label: shortLabel(record.label ?? id),
      synthesised: Boolean(record.synthesised),
      degree: 0,
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      radius: 3,
      priority: 0,
    };
  });

  const edges = [];
  for (const record of rawEdges) {
    const source = index.get(String(record.source));
    const target = index.get(String(record.target));
    // An edge whose endpoint is outside the projection is skipped rather than drawn to a
    // guessed position. The bounded query can return one; inventing the other end would be
    // the renderer asserting a node the payload did not include.
    if (source === undefined || target === undefined || source === target) continue;
    edges.push({
      id: String(record.id),
      source,
      target,
      record,
      type: String(record.type ?? 'unknown'),
      synthesised: Boolean(record.synthesised),
    });
    nodes[source].degree += 1;
    nodes[target].degree += 1;
  }

  const typeCounts = new Map();
  for (const node of nodes) typeCounts.set(node.type, (typeCounts.get(node.type) ?? 0) + 1);
  const maximumDegree = nodes.reduce((best, node) => Math.max(best, node.degree), 0) || 1;
  const total = nodes.length || 1;

  for (const node of nodes) {
    const typeShare = (typeCounts.get(node.type) ?? 1) / total;
    const degreeShare = node.degree / maximumDegree;
    node.priority = computePriority(typeShare, degreeShare);
    node.mass = 1 + Math.log2(1 + node.degree);
    // Rarer, better-connected nodes push harder. Without it the seventeen metrics in the
    // evidence backbone are dragged into one overlapping knot by the passages they all cite.
    node.charge = 0.5 + 1.5 * node.priority;
    node.radius = 2.6 + 5 * node.priority * node.priority + 4 * Math.sqrt(degreeShare);
  }

  const edgeIndex = new Map(edges.map((edge, position) => [edge.id, position]));
  const labelOrder = nodes
    .map((node) => node.position)
    .sort((left, right) => {
      const difference = nodes[right].priority - nodes[left].priority;
      if (difference !== 0) return difference;
      // Ties break on the id, so the label set is a function of the payload and not of the
      // sort's stability.
      return nodes[left].id < nodes[right].id ? -1 : 1;
    });

  const model = {
    payload,
    nodes,
    edges,
    index,
    edgeIndex,
    labelOrder,
    typeCounts,
    colours: assignColours(payload, nodes, edges),
    layoutMs: 0,
    layoutIterations: 0,
    layoutDigest: '',
  };
  runLayout(model, options);
  return model;
}

function shortLabel(value) {
  const text = String(value ?? '');
  return text.length > MAX_LABEL_CHARS ? `${text.slice(0, MAX_LABEL_CHARS - 1)}…` : text;
}

/**
 * A palette slot per type, hashed from the type name and then walked forward to the first free
 * slot. Hashing keeps a type the same colour in every view — a metric that changed hue between
 * the overview and the evidence graph would look like a different thing — and the walk keeps
 * two types in one payload from colliding on one slot.
 */
function assignColours(payload, nodes, edges) {
  const assign = (names) => {
    const taken = new Set();
    const chosen = new Map();
    for (const name of names) {
      let slot = hash32(name) % PALETTE_SIZE;
      for (let attempt = 0; attempt < PALETTE_SIZE && taken.has(slot); attempt += 1) {
        slot = (slot + 1) % PALETTE_SIZE;
      }
      taken.add(slot);
      chosen.set(name, slot);
    }
    return chosen;
  };
  const legendNodeTypes = (payload?.legend?.nodes ?? []).map((entry) => String(entry.type));
  const legendEdgeTypes = (payload?.legend?.edges ?? []).map((entry) => String(entry.type));
  const nodeTypes = uniqueInOrder(legendNodeTypes.concat(nodes.map((node) => node.type)));
  const edgeTypes = uniqueInOrder(legendEdgeTypes.concat(edges.map((edge) => edge.type)));
  return { nodes: assign(nodeTypes), edges: assign(edgeTypes) };
}

function uniqueInOrder(values) {
  const seen = new Set();
  const result = [];
  for (const value of values) {
    if (seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}

// ---------------------------------------------------------------------------------------
// The layout. Fruchterman-Reingold with a uniform grid for repulsion, degree as mass, and a
// fixed cooling schedule. Every quantity in it comes from the payload or from `DEFAULTS`.
// ---------------------------------------------------------------------------------------

function runLayout(model, options) {
  const started = now();
  const { nodes, edges } = model;
  const count = nodes.length;
  const iterations = options.layoutIterations;
  const spread = options.layoutSpread * Math.sqrt(Math.max(count, 1) / 400);

  // Seeded, not scattered: two 16-bit halves of the id's hash become an angle and an
  // area-uniform radius, so the starting cloud is a disc and is the same disc every time.
  for (const node of nodes) {
    const hash = hash32(node.id);
    const angle = ((hash & 0xffff) / 0x10000) * TAU;
    const radius = Math.sqrt(((hash >>> 16) & 0xffff) / 0x10000) * spread;
    node.x = Math.cos(angle) * radius;
    node.y = Math.sin(angle) * radius;
    node.vx = 0;
    node.vy = 0;
  }

  if (count > 1 && iterations > 0) {
    const ideal = spread / Math.sqrt(count);
    const cutoff = ideal * options.repulsionCutoff;
    const grid = new Map();
    for (let step = 0; step < iterations; step += 1) {
      const progress = step / iterations;
      const temperature = spread * 0.09 * Math.pow(1 - progress, 1.4) + ideal * 0.02;

      grid.clear();
      for (const node of nodes) {
        const key = cellKey(Math.floor(node.x / cutoff), Math.floor(node.y / cutoff));
        const bucket = grid.get(key);
        if (bucket === undefined) grid.set(key, [node]);
        else bucket.push(node);
        node.vx = 0;
        node.vy = 0;
      }

      // Repulsion, over the nine cells around each node. Neighbours are visited in the order
      // they entered their bucket, which is the payload's order, so the floating-point sum is
      // the same sum on every run.
      for (const node of nodes) {
        const column = Math.floor(node.x / cutoff);
        const row = Math.floor(node.y / cutoff);
        for (let dc = -1; dc <= 1; dc += 1) {
          for (let dr = -1; dr <= 1; dr += 1) {
            const bucket = grid.get(cellKey(column + dc, row + dr));
            if (bucket === undefined) continue;
            for (const other of bucket) {
              if (other === node) continue;
              let dx = node.x - other.x;
              let dy = node.y - other.y;
              let distance = Math.sqrt(dx * dx + dy * dy);
              if (distance > cutoff) continue;
              if (distance < 1e-6) {
                // Two nodes exactly on top of each other need a direction, and it has to be a
                // deterministic one. Their position difference gives none, so the difference of
                // their hashes does: fixed per pair, and opposite for the mirrored pair.
                const jitter = ((hash32(node.id) ^ hash32(other.id)) & 0xff) / 0x100;
                dx = Math.cos(jitter * TAU) * 1e-3;
                dy = Math.sin(jitter * TAU) * 1e-3;
                distance = 1e-3;
              }
              const force = ((ideal * ideal) / distance) * (node.charge + other.charge) * 0.6;
              node.vx += (dx / distance) * force;
              node.vy += (dy / distance) * force;
            }
          }
        }
      }

      // Attraction along edges, with a rest length that grows with the endpoints' degree so a
      // 308-spoke hub does not fold its neighbours into a disc of overlapping points.
      for (const edge of edges) {
        const from = nodes[edge.source];
        const to = nodes[edge.target];
        const dx = to.x - from.x;
        const dy = to.y - from.y;
        const distance = Math.sqrt(dx * dx + dy * dy) || 1e-6;
        const rest = ideal * (1 + 0.16 * Math.log2(1 + from.degree + to.degree));
        const force = (distance * distance) / (rest * 8);
        const ux = (dx / distance) * force;
        const uy = (dy / distance) * force;
        from.vx += ux;
        from.vy += uy;
        to.vx -= ux;
        to.vy -= uy;
      }

      // A weak pull to the origin keeps disconnected components — the coverage view has nine
      // isolated metrics — from drifting out of every viewport.
      for (const node of nodes) {
        node.vx -= node.x * 0.012;
        node.vy -= node.y * 0.012;
      }

      for (const node of nodes) {
        const speed = Math.sqrt(node.vx * node.vx + node.vy * node.vy) || 1e-9;
        const limit = Math.min(speed, temperature) / node.mass;
        node.x += (node.vx / speed) * limit;
        node.y += (node.vy / speed) * limit;
      }
    }
  }

  let sumX = 0;
  let sumY = 0;
  for (const node of nodes) {
    sumX += node.x;
    sumY += node.y;
  }
  const centreX = sumX / (count || 1);
  const centreY = sumY / (count || 1);
  for (const node of nodes) {
    node.x -= centreX;
    node.y -= centreY;
  }

  model.layoutIterations = iterations;
  model.layoutMs = now() - started;
  model.layoutDigest = positionDigest(nodes);
}

/**
 * A grid cell as one integer rather than as `"column:row"`.
 *
 * Measured, not assumed: with string keys the overview's 400 iterations cost 409 ms, because
 * each one built 403 keys and did 3,627 concatenating lookups. Packing the pair into a single
 * number took the same layout to the figure quoted in the module docstring. The pack is exact
 * for coordinates inside ±32,768 cells, which at this cutoff is far outside any layout the
 * cooling schedule can produce.
 */
function cellKey(column, row) {
  return (column + 0x8000) * 0x10000 + (row + 0x8000);
}

function now() {
  return typeof performance === 'object' && performance ? performance.now() : Date.now();
}

// ---------------------------------------------------------------------------------------
// The view.
// ---------------------------------------------------------------------------------------

/**
 * Build a graph view over a canvas.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {object} [options]
 * @param {(nodeId: string|null, record: object|null) => void} [options.onSelect]
 *        Raised with the node's **real payload id** and its untouched payload record, or with
 *        `(null, null)` when the selection is cleared.
 * @param {(nodeId: string|null, record: object|null) => void} [options.onHover]
 * @param {(members: string[]) => void} [options.onAggregateSelect]
 *        Raised when a cluster marker is chosen instead of a node. The view zooms to the
 *        members; the callback is there so a panel can say what was in it.
 * @param {(crumbs: {index: number, name: string, label: string, current: boolean}[]) => void}
 *        [options.onViewChange]
 * @param {(stats: object) => void} [options.onStats]
 * @param {HTMLElement} [options.legendElement] Written by this module from `payload.legend`.
 * @param {HTMLElement} [options.breadcrumbElement] Written by this module from the view stack.
 * @param {HTMLElement} [options.statusElement] One line: counts, layout cost, frame cost, digest.
 * @param {HTMLElement} [options.emptyElement] Hidden once a payload is set.
 * @param {{zoomIn?: HTMLElement, zoomOut?: HTMLElement, fit?: HTMLElement, reset?: HTMLElement}}
 *        [options.controls] Wired with `addEventListener`; there is no inline handler anywhere.
 * @param {{aggregate?: HTMLInputElement, labels?: HTMLInputElement, motion?: HTMLInputElement,
 *          synthesised?: HTMLInputElement}} [options.settings]
 * @returns {GraphView}
 */
export function createGraphView(canvas, options = {}) {
  const settings = { ...DEFAULTS, ...(options.tuning ?? {}) };
  const context = canvas.getContext('2d');
  const listeners = [];
  const view = {};

  let model = null;
  let palette = readPalette(canvas);
  let camera = { x: 0, y: 0, scale: 1 };
  let cameraAnimation = null;
  let width = 1;
  let height = 1;
  let ratio = 1;

  let selectedId = null;
  let hoveredId = null;
  let highlight = null;
  let frameHandle = 0;
  let lastFrameMs = 0;
  let frameSamples = [];
  let drawn = { nodes: 0, edges: 0, clusters: 0, aggregatedEdges: 0, labels: 0 };
  let clusters = [];
  let slots = new Int32Array(0);

  const preferences = {
    aggregate: true,
    labels: true,
    motion: true,
    markSynthesised: true,
  };

  /** The view stack. Index 0 is always the full graph, which is what "back to full" means. */
  const stack = [];

  // ------------------------------------------------------------------- geometry ----

  function toScreenX(worldX) { return (worldX - camera.x) * camera.scale + width / 2; }
  function toScreenY(worldY) { return (worldY - camera.y) * camera.scale + height / 2; }
  function toWorldX(screenX) { return (screenX - width / 2) / camera.scale + camera.x; }
  function toWorldY(screenY) { return (screenY - height / 2) / camera.scale + camera.y; }

  function measure() {
    const box = canvas.getBoundingClientRect();
    ratio = Math.min(globalThis.devicePixelRatio || 1, 2);
    width = Math.max(1, Math.round(box.width));
    height = Math.max(1, Math.round(box.height));
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }

  function boundsOf(nodeIndices) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const position of nodeIndices) {
      const node = model.nodes[position];
      if (node === undefined) continue;
      minX = Math.min(minX, node.x - node.radius);
      minY = Math.min(minY, node.y - node.radius);
      maxX = Math.max(maxX, node.x + node.radius);
      maxY = Math.max(maxY, node.y + node.radius);
    }
    if (minX === Infinity) return null;
    return { minX, minY, maxX, maxY };
  }

  function cameraForBounds(bounds, padding) {
    const spanX = Math.max(bounds.maxX - bounds.minX, 1);
    const spanY = Math.max(bounds.maxY - bounds.minY, 1);
    const scale = clamp(
      Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY),
      settings.minimumScale,
      settings.maximumScale,
    );
    return {
      x: (bounds.minX + bounds.maxX) / 2,
      y: (bounds.minY + bounds.maxY) / 2,
      scale,
    };
  }

  function moveCamera(target, animate) {
    if (!animate) {
      camera = { ...target };
      cameraAnimation = null;
      schedule();
      return;
    }
    cameraAnimation = { from: { ...camera }, to: { ...target }, started: now() };
    schedule();
  }

  function stepCameraAnimation() {
    if (cameraAnimation === null) return false;
    const progress = clamp((now() - cameraAnimation.started) / settings.cameraEaseMs, 0, 1);
    const eased = progress < 0.5
      ? 4 * progress * progress * progress
      : 1 - Math.pow(-2 * progress + 2, 3) / 2;
    const { from, to } = cameraAnimation;
    camera = {
      x: from.x + (to.x - from.x) * eased,
      y: from.y + (to.y - from.y) * eased,
      scale: from.scale + (to.scale - from.scale) * eased,
    };
    if (progress >= 1) cameraAnimation = null;
    return cameraAnimation !== null;
  }

  function clamp(value, low, high) { return Math.min(Math.max(value, low), high); }

  // ------------------------------------------------------------------- painting ----

  function schedule() {
    if (frameHandle !== 0) return;
    frameHandle = globalThis.requestAnimationFrame(() => {
      frameHandle = 0;
      draw();
    });
  }

  function animating() {
    return cameraAnimation !== null
      || (highlight !== null && highlight.pulse && preferences.motion && !reducedMotion());
  }

  function reducedMotion() {
    return typeof globalThis.matchMedia === 'function'
      && globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function draw() {
    if (context === null) return;
    const started = now();
    stepCameraAnimation();

    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    context.fillStyle = palette.surface;
    context.fillRect(0, 0, width, height);

    if (model !== null) {
      planFrame();
      paintEdges();
      paintNodes();
      paintLabels();
      paintTooltip();
    }

    lastFrameMs = now() - started;
    frameSamples.push(lastFrameMs);
    if (frameSamples.length > 60) frameSamples.shift();
    writeStatus();
    if (animating()) schedule();
  }

  /**
   * Decide, for this frame, what is drawn individually and what is binned into a cluster.
   *
   * Aggregation is screen-space: a cell is `aggregateCellPx` wide however far the camera is
   * out, so the picture thins where it is dense and nowhere else. Selected, hovered and
   * highlighted nodes are never binned — a highlight that vanished into a cluster would be the
   * interface hiding the thing it was asked to point at.
   */
  function planFrame() {
    const aggregating = preferences.aggregate && camera.scale < settings.aggregateBelowScale;
    const cell = settings.aggregateCellPx;
    const margin = 80;
    const buckets = new Map();
    clusters = [];
    if (slots.length !== model.nodes.length) slots = new Int32Array(model.nodes.length);
    slots.fill(-1);

    for (const node of model.nodes) {
      node.screenX = toScreenX(node.x);
      node.screenY = toScreenY(node.y);
      node.screenRadius = Math.max(1.2, node.radius * Math.sqrt(camera.scale));
      node.visible = node.screenX > -margin && node.screenX < width + margin
        && node.screenY > -margin && node.screenY < height + margin;
      const pinned = node.id === selectedId || node.id === hoveredId
        || (highlight !== null && highlight.nodes.has(node.id));
      if (!aggregating || pinned || node.priority >= 0.6 || !node.visible) continue;
      const key = `${Math.floor(node.screenX / cell)}:${Math.floor(node.screenY / cell)}`;
      const bucket = buckets.get(key);
      if (bucket === undefined) buckets.set(key, [node.position]);
      else bucket.push(node.position);
    }

    for (const members of buckets.values()) {
      if (members.length < settings.aggregateMinimumMembers) continue;
      let sumX = 0;
      let sumY = 0;
      for (const position of members) {
        sumX += model.nodes[position].screenX;
        sumY += model.nodes[position].screenY;
        slots[position] = clusters.length;
      }
      clusters.push({
        members,
        screenX: sumX / members.length,
        screenY: sumY / members.length,
        radius: Math.min(14, 4 + Math.sqrt(members.length) * 1.8),
        type: model.nodes[members[0]].type,
      });
    }
  }

  function clusterAt(position) {
    const slot = slots[position];
    return slot === -1 ? null : clusters[slot];
  }

  function paintEdges() {
    const aggregated = new Map();
    let painted = 0;
    context.lineCap = 'round';
    for (const edge of model.edges) {
      const from = model.nodes[edge.source];
      const to = model.nodes[edge.target];
      if (!from.visible && !to.visible) continue;
      const fromCluster = clusterAt(edge.source);
      const toCluster = clusterAt(edge.target);
      if (fromCluster !== null || toCluster !== null) {
        if (fromCluster !== null && fromCluster === toCluster) continue;
        const key = `${slots[edge.source]}:${slots[edge.target]}`;
        const existing = aggregated.get(key);
        if (existing === undefined) {
          aggregated.set(key, {
            x1: fromCluster ? fromCluster.screenX : from.screenX,
            y1: fromCluster ? fromCluster.screenY : from.screenY,
            x2: toCluster ? toCluster.screenX : to.screenX,
            y2: toCluster ? toCluster.screenY : to.screenY,
            count: 1,
          });
        } else existing.count += 1;
        continue;
      }
      const emphasis = emphasisOf(edge.id, edge.source, edge.target);
      if (emphasis === 0) continue;
      context.globalAlpha = 0.16 + 0.55 * emphasis;
      context.strokeStyle = palette.slot(model.colours.edges.get(edge.type) ?? 0);
      context.lineWidth = emphasis > 0.9 ? 1.8 : 1;
      context.setLineDash(edge.synthesised && preferences.markSynthesised ? [4, 3] : []);
      context.beginPath();
      context.moveTo(from.screenX, from.screenY);
      context.lineTo(to.screenX, to.screenY);
      context.stroke();
      painted += 1;
    }

    context.setLineDash([]);
    context.strokeStyle = palette.edge;
    for (const bundle of aggregated.values()) {
      context.globalAlpha = 0.1 + Math.min(0.25, bundle.count / 60);
      context.lineWidth = Math.min(3, 0.6 + Math.log2(1 + bundle.count) * 0.5);
      context.beginPath();
      context.moveTo(bundle.x1, bundle.y1);
      context.lineTo(bundle.x2, bundle.y2);
      context.stroke();
    }
    context.globalAlpha = 1;
    drawn.edges = painted;
    drawn.aggregatedEdges = aggregated.size;
  }

  /** 0 dims an element away entirely, 1 is full strength. */
  function emphasisOf(elementId, sourcePosition, targetPosition) {
    if (highlight === null) return 1;
    if (highlight.edges.has(elementId)) return 1;
    if (highlight.incident && sourcePosition !== undefined
      && highlight.nodes.has(model.nodes[sourcePosition].id)
      && highlight.nodes.has(model.nodes[targetPosition].id)) return 1;
    return 1 - highlight.dim;
  }

  function nodeEmphasis(node) {
    if (highlight === null) return 1;
    return highlight.nodes.has(node.id) ? 1 : 1 - highlight.dim;
  }

  function paintNodes() {
    let painted = 0;
    const pulse = pulseAmount();

    for (const cluster of clusters) {
      context.globalAlpha = highlight === null ? 0.55 : 0.55 * (1 - highlight.dim);
      context.fillStyle = palette.slot(model.colours.nodes.get(cluster.type) ?? 0);
      context.beginPath();
      context.arc(cluster.screenX, cluster.screenY, cluster.radius, 0, TAU);
      context.fill();
      context.globalAlpha = 1;
      if (cluster.radius >= 9) {
        context.fillStyle = palette.tooltipInk;
        context.font = '9px ui-monospace, monospace';
        context.textAlign = 'center';
        context.textBaseline = 'middle';
        context.fillText(String(cluster.members.length), cluster.screenX, cluster.screenY);
      }
    }

    for (const node of model.nodes) {
      if (!node.visible || clusterAt(node.position) !== null) continue;
      const emphasis = nodeEmphasis(node);
      const colour = palette.slot(model.colours.nodes.get(node.type) ?? 0);
      const radius = node.screenRadius;

      if (highlight !== null && highlight.nodes.has(node.id) && pulse > 0) {
        context.globalAlpha = 0.16 + 0.3 * pulse;
        context.strokeStyle = palette.highlight;
        context.lineWidth = 2;
        context.beginPath();
        context.arc(node.screenX, node.screenY, radius + 3 + 3 * pulse, 0, TAU);
        context.stroke();
      }

      context.globalAlpha = 0.25 + 0.75 * emphasis;
      context.beginPath();
      context.arc(node.screenX, node.screenY, radius, 0, TAU);
      if (node.synthesised && preferences.markSynthesised) {
        // Derived, and drawn as such: hollow with a dashed stroke, so the one node in the
        // overview that was computed from `observation.subject_entity_id` cannot be mistaken
        // for one that was read.
        context.setLineDash([3, 2.5]);
        context.strokeStyle = colour;
        context.lineWidth = 1.6;
        context.stroke();
        context.setLineDash([]);
      } else {
        context.fillStyle = colour;
        context.fill();
      }

      if (node.id === selectedId || node.id === hoveredId) {
        context.globalAlpha = 1;
        context.strokeStyle = node.id === selectedId ? palette.selection : palette.label;
        context.lineWidth = node.id === selectedId ? 2.4 : 1.4;
        context.beginPath();
        context.arc(node.screenX, node.screenY, radius + 2.5, 0, TAU);
        context.stroke();
      }
      painted += 1;
    }
    context.globalAlpha = 1;
    drawn.nodes = painted;
    drawn.clusters = clusters.length;
  }

  function pulseAmount() {
    if (highlight === null || !highlight.pulse) return 0;
    if (!preferences.motion || reducedMotion()) return 0.5;
    const phase = (now() % settings.pulsePeriodMs) / settings.pulsePeriodMs;
    return (1 - Math.cos(phase * TAU)) / 2;
  }

  /**
   * Labels, in priority order, until the budget or the space runs out. Collision is resolved by
   * refusing the later label rather than by moving it: a label that drifts off its node is worse
   * than no label, and refusing in a fixed order keeps the label set deterministic per camera.
   */
  function paintLabels() {
    drawn.labels = 0;
    if (!preferences.labels) return;
    const budget = Math.round(settings.labelBudget * clamp(camera.scale, 1, 6));
    const everything = camera.scale >= settings.labelAllAboveScale;
    const boxes = [];
    context.font = '11px system-ui, sans-serif';
    context.textAlign = 'left';
    context.textBaseline = 'middle';

    const forced = [];
    for (const node of model.nodes) {
      if (node.id === selectedId || node.id === hoveredId) forced.push(node.position);
    }

    let placed = 0;
    const done = new Set();
    for (const position of forced.concat(model.labelOrder)) {
      if (done.has(position)) continue;
      done.add(position);
      const node = model.nodes[position];
      if (!node.visible || clusterAt(position) !== null) continue;
      const insisted = node.id === selectedId || node.id === hoveredId
        || (highlight !== null && highlight.nodes.has(node.id));
      if (!insisted && !everything && placed >= budget) break;
      if (!insisted && everything && node.screenRadius < 2) continue;
      const text = node.label;
      const metrics = context.measureText(text);
      const boxX = node.screenX + node.screenRadius + 4;
      const boxY = node.screenY - 6;
      const box = { x: boxX, y: boxY, w: metrics.width + 4, h: 12 };
      if (!insisted && boxes.some((other) => overlaps(box, other))) continue;
      boxes.push(box);

      context.globalAlpha = highlight === null || highlight.nodes.has(node.id)
        ? 1 : 1 - highlight.dim;
      context.lineWidth = 3;
      context.strokeStyle = palette.labelHalo;
      context.strokeText(text, boxX, node.screenY);
      context.fillStyle = palette.label;
      context.fillText(text, boxX, node.screenY);
      placed += 1;
      drawn.labels += 1;
    }
    context.globalAlpha = 1;
  }

  function overlaps(left, right) {
    return left.x < right.x + right.w && left.x + left.w > right.x
      && left.y < right.y + right.h && left.y + left.h > right.y;
  }

  /** The hover card, on the canvas. Text only, and every line is a payload value or a count. */
  function paintTooltip() {
    if (hoveredId === null) return;
    const node = model.nodes[model.index.get(hoveredId) ?? -1];
    if (node === undefined || !node.visible) return;
    const lines = [node.label, node.type];
    if (node.synthesised) {
      lines.push(`derived from ${String(node.record.derived_from ?? 'a stored property')}`);
    }
    lines.push(`${node.degree} connection${node.degree === 1 ? '' : 's'} in this projection`);

    context.font = '11px system-ui, sans-serif';
    context.textAlign = 'left';
    context.textBaseline = 'top';
    const widths = lines.map((line) => context.measureText(line).width);
    const boxWidth = Math.max(...widths) + 14;
    const boxHeight = lines.length * 14 + 10;
    let boxX = node.screenX + node.screenRadius + 8;
    let boxY = node.screenY + node.screenRadius + 8;
    if (boxX + boxWidth > width) boxX = Math.max(4, node.screenX - boxWidth - 8);
    if (boxY + boxHeight > height) boxY = Math.max(4, node.screenY - boxHeight - 8);

    context.globalAlpha = 0.94;
    context.fillStyle = palette.tooltip;
    context.beginPath();
    // `roundRect` is recent enough that a browser without it is possible; a square card is a
    // better answer there than an exception that stops the frame.
    if (typeof context.roundRect === 'function') {
      context.roundRect(boxX, boxY, boxWidth, boxHeight, 4);
    } else {
      context.rect(boxX, boxY, boxWidth, boxHeight);
    }
    context.fill();
    context.globalAlpha = 1;
    context.fillStyle = palette.tooltipInk;
    lines.forEach((line, position) => {
      context.fillText(line, boxX + 7, boxY + 5 + position * 14);
    });
  }

  // -------------------------------------------------------------------- picking ----

  function nodeAtScreen(screenX, screenY) {
    if (model === null) return null;
    let best = null;
    let bestDistance = Infinity;
    for (const node of model.nodes) {
      if (!node.visible || clusterAt(node.position) !== null) continue;
      const distance = Math.hypot(node.screenX - screenX, node.screenY - screenY);
      const reach = Math.max(node.screenRadius + 3, 6);
      if (distance <= reach && distance < bestDistance) {
        best = node;
        bestDistance = distance;
      }
    }
    return best;
  }

  function clusterAtScreen(screenX, screenY) {
    for (const cluster of clusters) {
      if (Math.hypot(cluster.screenX - screenX, cluster.screenY - screenY) <= cluster.radius + 3) {
        return cluster;
      }
    }
    return null;
  }

  // ------------------------------------------------------------------- pointers ----

  let pointer = null;

  function pointerPosition(event) {
    const box = canvas.getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }

  function onPointerDown(event) {
    if (model === null) return;
    const at = pointerPosition(event);
    const node = nodeAtScreen(at.x, at.y);
    pointer = {
      id: event.pointerId,
      startX: at.x,
      startY: at.y,
      lastX: at.x,
      lastY: at.y,
      node,
      moved: false,
    };
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.toggle('is-panning', node === null);
  }

  function onPointerMove(event) {
    if (model === null) return;
    const at = pointerPosition(event);

    if (pointer === null) {
      const node = nodeAtScreen(at.x, at.y);
      const cluster = node === null ? clusterAtScreen(at.x, at.y) : null;
      canvas.classList.toggle('is-over-node', node !== null || cluster !== null);
      const nextHover = node === null ? null : node.id;
      if (nextHover !== hoveredId) {
        hoveredId = nextHover;
        if (typeof options.onHover === 'function') {
          options.onHover(hoveredId, node === null ? null : node.record);
        }
        schedule();
      }
      return;
    }

    const dx = at.x - pointer.lastX;
    const dy = at.y - pointer.lastY;
    pointer.lastX = at.x;
    pointer.lastY = at.y;
    if (Math.hypot(at.x - pointer.startX, at.y - pointer.startY) > 3) pointer.moved = true;

    if (pointer.node !== null) {
      // Dragging moves the node and nothing else. `resetLayout()` is how the deterministic
      // positions come back, and `stats().layoutDigest` still describes the layout as computed.
      pointer.node.x += dx / camera.scale;
      pointer.node.y += dy / camera.scale;
    } else {
      camera.x -= dx / camera.scale;
      camera.y -= dy / camera.scale;
      cameraAnimation = null;
    }
    schedule();
  }

  function onPointerUp(event) {
    if (pointer === null) return;
    const wasMoved = pointer.moved;
    const node = pointer.node;
    const at = pointerPosition(event);
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    pointer = null;
    canvas.classList.toggle('is-panning', false);
    if (wasMoved) {
      schedule();
      return;
    }
    if (node !== null) {
      view.select(node.id);
      return;
    }
    const cluster = clusterAtScreen(at.x, at.y);
    if (cluster !== null) {
      const members = cluster.members.map((position) => model.nodes[position].id);
      view.focusOn(members, { animate: true });
      if (typeof options.onAggregateSelect === 'function') options.onAggregateSelect(members);
      return;
    }
    view.select(null);
  }

  function onWheel(event) {
    if (model === null) return;
    event.preventDefault();
    const at = pointerPosition(event);
    const worldX = toWorldX(at.x);
    const worldY = toWorldY(at.y);
    const factor = Math.exp(-event.deltaY * 0.0016);
    const scale = clamp(camera.scale * factor, settings.minimumScale, settings.maximumScale);
    camera = {
      scale,
      x: worldX - (at.x - width / 2) / scale,
      y: worldY - (at.y - height / 2) / scale,
    };
    cameraAnimation = null;
    schedule();
  }

  function onKeyDown(event) {
    const step = 40 / camera.scale;
    const moves = {
      ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step],
    };
    if (moves[event.key] !== undefined) {
      camera.x += moves[event.key][0];
      camera.y += moves[event.key][1];
      event.preventDefault();
      schedule();
      return;
    }
    if (event.key === '+' || event.key === '=') view.zoomBy(1.25);
    else if (event.key === '-') view.zoomBy(1 / 1.25);
    else if (event.key === '0') view.fit({ animate: true });
    else if (event.key === 'Escape') view.select(null);
  }

  function listen(target, type, handler, extra) {
    target.addEventListener(type, handler, extra);
    listeners.push(() => target.removeEventListener(type, handler, extra));
  }

  listen(canvas, 'pointerdown', onPointerDown);
  listen(canvas, 'pointermove', onPointerMove);
  listen(canvas, 'pointerup', onPointerUp);
  listen(canvas, 'pointercancel', onPointerUp);
  listen(canvas, 'wheel', onWheel, { passive: false });
  listen(canvas, 'keydown', onKeyDown);
  listen(canvas, 'pointerleave', () => {
    if (hoveredId === null) return;
    hoveredId = null;
    if (typeof options.onHover === 'function') options.onHover(null, null);
    schedule();
  });

  const observer = typeof globalThis.ResizeObserver === 'function'
    ? new globalThis.ResizeObserver(() => view.resize())
    : null;
  if (observer !== null) observer.observe(canvas);

  const scheme = typeof globalThis.matchMedia === 'function'
    ? globalThis.matchMedia('(prefers-color-scheme: dark)') : null;
  const onScheme = () => {
    palette = readPalette(canvas);
    paintLegend();
    schedule();
  };
  if (scheme !== null && typeof scheme.addEventListener === 'function') {
    scheme.addEventListener('change', onScheme);
    listeners.push(() => scheme.removeEventListener('change', onScheme));
  }

  const controls = options.controls ?? {};
  if (controls.zoomIn) listen(controls.zoomIn, 'click', () => view.zoomBy(1.3));
  if (controls.zoomOut) listen(controls.zoomOut, 'click', () => view.zoomBy(1 / 1.3));
  if (controls.fit) listen(controls.fit, 'click', () => view.fit({ animate: true }));
  if (controls.reset) listen(controls.reset, 'click', () => view.resetLayout());

  const toggles = options.settings ?? {};
  for (const [name, element] of Object.entries(toggles)) {
    if (!element) continue;
    preferences[name === 'synthesised' ? 'markSynthesised' : name] = Boolean(element.checked);
    listen(element, 'change', () => {
      preferences[name === 'synthesised' ? 'markSynthesised' : name] = Boolean(element.checked);
      schedule();
    });
  }

  // ------------------------------------------------------------- written elements ----

  function element(tag, className, text) {
    const created = document.createElement(tag);
    if (className) created.className = className;
    if (text !== undefined) created.textContent = String(text);
    return created;
  }

  /**
   * The legend, from `payload.legend` and from nothing else. Every entry names a type the
   * payload declared, with the payload's own count and its own description, and a derived type
   * gets the dashed swatch its nodes get. No type list lives in this file.
   */
  function paintLegend() {
    const host = options.legendElement;
    if (!host) return;
    host.replaceChildren();
    if (model === null) return;
    const legend = model.payload?.legend ?? {};
    const groups = [
      ['Nodes', legend.nodes ?? [], model.colours.nodes, false],
      ['Edges', legend.edges ?? [], model.colours.edges, true],
    ];
    for (const [title, entries, colours, isEdge] of groups) {
      if (entries.length === 0) continue;
      const group = element('div', 'legend-group');
      group.append(element('span', 'legend-group-title', title));
      for (const entry of entries) {
        const type = String(entry.type);
        const item = element('span', 'legend-entry');
        item.title = String(entry.description ?? '');
        const swatch = element('span', 'legend-swatch');
        if (isEdge) swatch.classList.add('is-edge');
        const colour = palette.slot(colours.get(type) ?? 0);
        if (String(entry.source) === 'derived') {
          swatch.classList.add('is-derived');
          swatch.style.borderColor = colour;
        } else if (isEdge) {
          swatch.style.borderTopColor = colour;
        } else {
          swatch.style.background = colour;
          swatch.style.borderColor = colour;
        }
        item.append(swatch, element('span', null, type),
          element('span', 'legend-count', entry.count));
        if (String(entry.source) === 'derived') {
          item.append(element('span', 'legend-derived-mark', '· derived'));
        }
        group.append(item);
      }
      host.append(group);
    }
  }

  function paintBreadcrumb() {
    const crumbs = view.breadcrumb();
    if (typeof options.onViewChange === 'function') options.onViewChange(crumbs);
    const host = options.breadcrumbElement;
    if (!host) return;
    host.replaceChildren();
    if (crumbs.length <= 1) return;
    crumbs.forEach((crumb, position) => {
      if (position > 0) host.append(element('span', 'crumb-separator', '›'));
      if (crumb.current) {
        host.append(element('span', 'crumb-current', crumb.label));
        return;
      }
      const button = element('button', null, crumb.label);
      button.type = 'button';
      button.addEventListener('click', () => view.popTo(crumb.index));
      host.append(button);
    });
  }

  function writeStatus() {
    const host = options.statusElement;
    const report = view.stats();
    if (typeof options.onStats === 'function') options.onStats(report);
    if (!host) return;
    if (model === null) {
      host.textContent = 'no projection';
      return;
    }
    const parts = [
      `${report.nodes} nodes · ${report.edges} edges`,
      `drawn ${report.drawnNodes}${report.clusters ? ` + ${report.clusters} clusters` : ''}`,
      `layout ${report.layoutMs.toFixed(1)} ms / ${report.layoutIterations} iterations`,
      `frame ${report.lastFrameMs.toFixed(1)} ms`,
      `layout digest ${report.layoutDigest}`,
    ];
    host.textContent = parts.join(' · ');
  }

  // ------------------------------------------------------------------ public API ----

  /**
   * Replace the drawn graph. Runs the layout synchronously and fits the viewport.
   *
   * @param {object} payload a `/demo/graph/*` response, used as given
   * @param {{label?: string, name?: string, resetView?: boolean, animate?: boolean}} [detail]
   */
  view.setPayload = function setPayload(payload, detail = {}) {
    model = buildModel(payload, settings);
    selectedId = null;
    hoveredId = null;
    highlight = null;
    frameSamples = [];
    const entry = {
      name: String(detail.name ?? payload?.view ?? payload?.projection ?? 'view'),
      label: String(detail.label ?? labelFor(payload)),
      payload,
    };
    if (detail.resetView === false && stack.length > 0) stack[stack.length - 1] = entry;
    else stack.splice(0, stack.length, entry);
    measure();
    view.fit({ animate: false });
    paintLegend();
    paintBreadcrumb();
    if (options.emptyElement) options.emptyElement.hidden = true;
    schedule();
    return view;
  };

  /** Push a neighbourhood on top of the current view, keeping a breadcrumb back to it. */
  view.pushView = function pushView(payload, detail = {}) {
    const keep = stack.slice();
    view.setPayload(payload, detail);
    stack.splice(0, stack.length, ...keep, stack[stack.length - 1]);
    paintBreadcrumb();
    return view;
  };

  /** Return to an earlier view. `popTo(0)` is "back to the full graph". */
  view.popTo = function popTo(index) {
    if (index < 0 || index >= stack.length - 1) return view;
    const target = stack[index];
    const keep = stack.slice(0, index);
    view.setPayload(target.payload, { name: target.name, label: target.label });
    stack.splice(0, stack.length, ...keep, stack[stack.length - 1]);
    paintBreadcrumb();
    return view;
  };

  view.back = function back() { return view.popTo(stack.length - 2); };

  view.breadcrumb = function breadcrumb() {
    return stack.map((entry, index) => ({
      index,
      name: entry.name,
      label: entry.label,
      current: index === stack.length - 1,
    }));
  };

  view.payload = function payloadOf() { return model === null ? null : model.payload; };

  /** The payload's own record for a node, or `null`. Not a copy with extra keys: the record. */
  view.node = function nodeOf(nodeId) {
    if (model === null) return null;
    const position = model.index.get(String(nodeId));
    return position === undefined ? null : model.nodes[position].record;
  };

  view.edge = function edgeOf(edgeId) {
    if (model === null) return null;
    const position = model.edgeIndex.get(String(edgeId));
    return position === undefined ? null : model.edges[position].record;
  };

  /** Ids of the edges incident to a node — what a detail card lists as its connections. */
  view.edgesOf = function edgesOf(nodeId) {
    if (model === null) return [];
    const position = model.index.get(String(nodeId));
    if (position === undefined) return [];
    return model.edges
      .filter((edge) => edge.source === position || edge.target === position)
      .map((edge) => edge.record);
  };

  view.legend = function legendOf() {
    return model === null ? null : model.payload?.legend ?? null;
  };

  view.select = function select(nodeId, detail = {}) {
    const next = nodeId === null || nodeId === undefined ? null : String(nodeId);
    if (next !== null && (model === null || !model.index.has(next))) return view;
    selectedId = next;
    if (next !== null && detail.focus) view.focusOn([next], { animate: true });
    if (typeof options.onSelect === 'function') {
      options.onSelect(selectedId, selectedId === null ? null : view.node(selectedId));
    }
    schedule();
    return view;
  };

  view.selection = function selection() { return selectedId; };
  view.hovered = function hovered() { return hoveredId; };

  /**
   * Emphasise a set and fade the rest.
   *
   * @param {{nodes?: string[], edges?: string[], dim?: number, pulse?: boolean,
   *          includeIncidentEdges?: boolean, focus?: boolean}} request
   */
  view.highlight = function setHighlight(request = {}) {
    const nodes = new Set((request.nodes ?? []).map(String));
    const edges = new Set((request.edges ?? []).map(String));
    if (nodes.size === 0 && edges.size === 0) return view.clearHighlight();
    highlight = {
      nodes,
      edges,
      dim: clamp(request.dim ?? settings.dim, 0, 0.95),
      pulse: request.pulse !== false,
      incident: request.includeIncidentEdges !== false,
    };
    if (request.focus) view.focusOn([...nodes], { animate: true });
    schedule();
    return view;
  };

  view.clearHighlight = function clearHighlight() {
    highlight = null;
    schedule();
    return view;
  };

  view.highlighted = function highlighted() {
    return highlight === null
      ? null
      : { nodes: [...highlight.nodes], edges: [...highlight.edges] };
  };

  /** Fit the viewport to a set of node ids. Unknown ids are ignored, never drawn at a guess. */
  view.focusOn = function focusOn(nodeIds, detail = {}) {
    if (model === null) return view;
    const positions = [];
    for (const nodeId of nodeIds ?? []) {
      const position = model.index.get(String(nodeId));
      if (position !== undefined) positions.push(position);
    }
    const bounds = boundsOf(positions);
    if (bounds === null) return view;
    moveCamera(cameraForBounds(bounds, detail.padding ?? 72), detail.animate !== false);
    return view;
  };

  view.fit = function fit(detail = {}) {
    if (model === null) return view;
    const bounds = boundsOf(model.nodes.map((node) => node.position));
    if (bounds === null) return view;
    moveCamera(cameraForBounds(bounds, detail.padding ?? 36), detail.animate === true);
    return view;
  };

  view.zoomBy = function zoomBy(factor) {
    const scale = clamp(camera.scale * factor, settings.minimumScale, settings.maximumScale);
    moveCamera({ x: camera.x, y: camera.y, scale }, true);
    return view;
  };

  view.camera = function cameraOf() { return { ...camera }; };

  view.setCamera = function setCamera(next, animate = false) {
    moveCamera({
      x: Number(next.x ?? camera.x),
      y: Number(next.y ?? camera.y),
      scale: clamp(Number(next.scale ?? camera.scale),
        settings.minimumScale, settings.maximumScale),
    }, animate);
    return view;
  };

  /** Recompute the deterministic layout, undoing any dragging. Same payload, same coordinates. */
  view.resetLayout = function resetLayout() {
    if (model === null) return view;
    runLayout(model, settings);
    view.fit({ animate: true });
    return view;
  };

  view.setOptions = function setOptions(next = {}) {
    for (const key of ['aggregate', 'labels', 'motion', 'markSynthesised']) {
      if (key in next) preferences[key] = Boolean(next[key]);
    }
    schedule();
    return view;
  };

  view.resize = function resize() {
    measure();
    schedule();
    return view;
  };

  view.render = function render() {
    draw();
    return view;
  };

  view.stats = function stats() {
    const average = frameSamples.length === 0
      ? 0 : frameSamples.reduce((total, sample) => total + sample, 0) / frameSamples.length;
    return {
      nodes: model === null ? 0 : model.nodes.length,
      edges: model === null ? 0 : model.edges.length,
      layoutMs: model === null ? 0 : model.layoutMs,
      layoutIterations: model === null ? 0 : model.layoutIterations,
      layoutDigest: model === null ? '' : model.layoutDigest,
      contentDigest: model === null ? '' : String(model.payload?.content_digest ?? ''),
      lastFrameMs,
      averageFrameMs: average,
      drawnNodes: drawn.nodes,
      drawnEdges: drawn.edges,
      clusters: drawn.clusters,
      aggregatedEdges: drawn.aggregatedEdges,
      labels: drawn.labels,
      scale: camera.scale,
      aggregating: preferences.aggregate && camera.scale < settings.aggregateBelowScale,
    };
  };

  view.destroy = function destroy() {
    for (const off of listeners) off();
    listeners.length = 0;
    if (observer !== null) observer.disconnect();
    if (frameHandle !== 0) globalThis.cancelAnimationFrame(frameHandle);
    frameHandle = 0;
    model = null;
  };

  measure();
  return view;
}

function labelFor(payload) {
  const projection = String(payload?.projection ?? 'graph');
  const scope = payload?.view;
  return scope ? `${projection} · ${scope}` : projection;
}

/**
 * The palette, read from the stylesheet rather than declared here, so a legend swatch and the
 * disc it describes are the same colour and a theme change moves both.
 */
function readPalette(canvas) {
  const computed = typeof globalThis.getComputedStyle === 'function'
    ? globalThis.getComputedStyle(canvas) : null;
  const value = (name, fallback) => {
    const found = computed === null ? '' : computed.getPropertyValue(name).trim();
    return found === '' ? fallback : found;
  };
  const slots = [];
  for (let index = 0; index < PALETTE_SIZE; index += 1) {
    slots.push(value(`--graph-palette-${index}`, '#888888'));
  }
  return {
    slot: (index) => slots[((index % PALETTE_SIZE) + PALETTE_SIZE) % PALETTE_SIZE],
    surface: value('--graph-surface', '#ffffff'),
    edge: value('--graph-edge', '#999999'),
    label: value('--graph-label', '#111111'),
    labelHalo: value('--graph-label-halo', '#ffffffcc'),
    selection: value('--graph-selection', '#1f5fa8'),
    highlight: value('--graph-highlight', '#d08a00'),
    tooltip: value('--graph-tooltip', '#111111'),
    tooltipInk: value('--graph-tooltip-ink', '#ffffff'),
  };
}

// ---------------------------------------------------------------------------------------
// The splitter. Here rather than in a module of its own because the thing it resizes is the
// canvas, and the canvas's `ResizeObserver` is what has to notice — one owner for the resize
// path. `style.css` gives the same two regions a `resize` property, so the panel is already
// adjustable with no script at all; both write the same inline size to the same element.
// ---------------------------------------------------------------------------------------

/**
 * Make a separator element resize its target by dragging or with the arrow keys.
 *
 * @param {HTMLElement} handle the `[role=separator]` element
 * @param {{axis?: 'x'|'y', target?: HTMLElement, minimum?: number, maximum?: number,
 *          onResize?: (size: number) => void}} [options]
 *        `axis` and `target` default to the handle's `data-axis` and `data-target`.
 * @returns {{destroy: () => void}}
 */
export function createSplitter(handle, options = {}) {
  if (!handle) return { destroy() {} };
  const axis = options.axis ?? handle.dataset.axis ?? 'x';
  const target = options.target
    ?? (handle.dataset.target ? document.getElementById(handle.dataset.target) : null);
  if (!target) return { destroy() {} };

  const minimum = options.minimum ?? 260;
  const listeners = [];
  let dragging = false;

  const apply = (size) => {
    const parent = target.parentElement;
    const room = axis === 'x'
      ? (parent ? parent.clientWidth : size + minimum)
      : (parent ? parent.clientHeight : size + minimum);
    const maximum = options.maximum ?? Math.max(minimum, room - minimum);
    const bounded = Math.min(Math.max(size, minimum), maximum);
    // A width and a height, not a flex basis: `style.css` gives both regions `flex-basis: auto`
    // so that the CSS `resize` handle — which can only write a width or a height — is not
    // overruled. Writing the same property here is what keeps the two handles agreeing.
    if (axis === 'x') target.style.width = `${bounded}px`;
    else target.style.height = `${bounded}px`;
    if (typeof options.onResize === 'function') options.onResize(bounded);
  };

  const on = (element, type, handler, extra) => {
    element.addEventListener(type, handler, extra);
    listeners.push(() => element.removeEventListener(type, handler, extra));
  };

  on(handle, 'pointerdown', (event) => {
    dragging = true;
    handle.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  on(handle, 'pointermove', (event) => {
    if (!dragging) return;
    const box = target.getBoundingClientRect();
    apply(axis === 'x' ? event.clientX - box.left : event.clientY - box.top);
  });
  const stop = (event) => {
    if (!dragging) return;
    dragging = false;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  on(handle, 'pointerup', stop);
  on(handle, 'pointercancel', stop);
  on(handle, 'keydown', (event) => {
    const box = target.getBoundingClientRect();
    const current = axis === 'x' ? box.width : box.height;
    const decrease = axis === 'x' ? 'ArrowLeft' : 'ArrowUp';
    const increase = axis === 'x' ? 'ArrowRight' : 'ArrowDown';
    if (event.key === decrease) apply(current - 24);
    else if (event.key === increase) apply(current + 24);
    else return;
    event.preventDefault();
  });

  return {
    destroy() {
      for (const off of listeners) off();
      listeners.length = 0;
    },
  };
}
