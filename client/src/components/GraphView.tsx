"use client";

import { type MutableRefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Crosshair } from "lucide-react";
import * as THREE from "three";

// ForceGraph3D touches `window` on import, so it cannot be part of a server render.
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), { ssr: false });

/** Colour by the language of the file, so a mixed repository is readable at a glance. */
const LANGUAGE_COLOURS: Record<string, string> = {
  python: "#3776ab",
  javascript: "#f7df1e",
  typescript: "#3178c6",
  tsx: "#6ba2e8",
  go: "#00add8",
  java: "#e76f00",
  rust: "#dea584",
  c: "#555f6d",
  cpp: "#f34b7d",
  c_sharp: "#178600",
  ruby: "#cc342d",
  php: "#777bb4",
  kotlin: "#a97bff",
  swift: "#f05138",
  scala: "#dc322f",
  lua: "#000080",
  elixir: "#6e4a7e",
  perl: "#0298c3",
  r: "#198ce7",
  bash: "#89e051",
};
const FALLBACK_COLOUR = "#00ff41";

/** Fan-in needed before a node is given a text label, so a big graph stays legible. */
const LABEL_FAN_IN_THRESHOLD = 3;
/** Below this many nodes, everything is labelled - there is room. */
const LABEL_EVERYTHING_UNDER = 40;

/** Repulsion between files that import each other. */
const CHARGE_STRENGTH = -260;
/**
 * A file with no resolved imports gets far less repulsion, so it settles beside
 * the cluster instead of being flung to one side and dragging the view with it.
 */
const ISOLATE_CHARGE_STRENGTH = -35;
const LINK_DISTANCE = 70;

/** How far out from the middle of the canvas the furthest file is allowed to sit. */
const FIT_FILL = 0.82;
/** Where the camera looks from before anybody has orbited: a little above level. */
const DEFAULT_DIRECTION = new THREE.Vector3(0, 0.34, 1).normalize();
/** Below this many files nothing is trimmed - every one of them stays in frame. */
const FIT_TAIL_FREE_UNDER = 48;
/** Past this share of files, one of them stops deciding how far the camera sits. */
const FIT_TAIL = 0.98;
/** Framing stops once the picture is this close to the size that was asked for. */
const FIT_SCALE_TOLERANCE = 0.02;
/** Give up after this many corrections; the last one still stands. */
const FIT_ATTEMPTS = 14;

/**
 * The library types its `ref` as an object ref, but forwards it to
 * `useImperativeHandle`, which also takes a callback - and a callback is the only
 * way to find out when the instance arrives, because it is imported dynamically.
 */
type GraphRef = MutableRefObject<any>;

/**
 * The dependency graph, as the pipeline computed it.
 *
 * Node size is fan-in - how many other files import this one - because that is
 * the number that predicts blast radius, and colour is the file's language.
 * Files with findings are outlined rather than recoloured: a repository that
 * legitimately contains a security scanner should not read as a wall of red.
 *
 * Nothing is added to the graph to make it look fuller. If the analysis resolved
 * two edges, there are two edges on screen.
 */
export default function GraphView({
  data,
  onNodeClick,
  analysedFiles,
}: {
  data?: { nodes?: any[]; links?: any[] };
  onNodeClick?: (node: any) => void;
  /** Files the analysis read, which is not the same as files in the graph. */
  analysedFiles?: number;
}) {
  const holderRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<any>(null);
  const [mounted, setMounted] = useState(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  // Re-armed whenever a new graph arrives, so the first settle of a new layout is
  // the one that reframes. Later engine stops are the reader dragging a file
  // around, and those must not yank the camera back.
  const frameOnSettle = useRef(true);
  // The box the camera was last framed for, so a resize can be told apart from
  // the many other reasons this component re-renders.
  const framedSize = useRef({ width: 0, height: 0 });
  // Whether the layout has stopped moving at least once. Before that there is
  // nothing worth framing, and any fit would be thrown away by the first settle.
  const settledOnce = useRef(false);

  useEffect(() => setMounted(true), []);

  const graph = useMemo(() => {
    const sourceNodes = Array.isArray(data?.nodes) ? data!.nodes : [];
    const sourceLinks = Array.isArray(data?.links) ? data!.links : [];

    const degree = new Map<string, { fanIn: number; fanOut: number }>();
    for (const node of sourceNodes) {
      const id = String(node?.id ?? "");
      if (id) degree.set(id, { fanIn: 0, fanOut: 0 });
    }
    for (const link of sourceLinks) {
      // Force-graph mutates `source`/`target` from ids into objects after layout,
      // so both shapes have to be handled.
      const source = typeof link?.source === "object" ? link?.source?.id : link?.source;
      const target = typeof link?.target === "object" ? link?.target?.id : link?.target;
      if (degree.has(String(source))) degree.get(String(source))!.fanOut += 1;
      if (degree.has(String(target))) degree.get(String(target))!.fanIn += 1;
    }

    const maxFanIn = Math.max(1, ...[...degree.values()].map((value) => value.fanIn));

    const nodes = sourceNodes.map((node: any) => {
      const id = String(node?.id ?? "");
      const counts = degree.get(id) ?? { fanIn: 0, fanOut: 0 };
      const findings = Number(node?.finding_count ?? 0);
      const language = String(node?.language ?? "").toLowerCase();
      return {
        ...node,
        id,
        fanIn: counts.fanIn,
        fanOut: counts.fanOut,
        findings,
        // A file nothing resolves to has no place in the layout to speak of, but
        // it was still read, so it stays a node rather than quietly disappearing.
        linked: counts.fanIn + counts.fanOut > 0,
        // Area grows with fan-in, so the most depended-upon files read as anchors.
        val: 1 + (counts.fanIn / maxFanIn) * 7,
        colour: LANGUAGE_COLOURS[language] ?? FALLBACK_COLOUR,
      };
    });

    const links = sourceLinks
      .filter((link: any) => link?.source !== undefined && link?.target !== undefined)
      .map((link: any) => ({ source: link.source, target: link.target }));

    return { nodes, links };
  }, [data]);

  const linkedFiles = useMemo(
    () => graph.nodes.filter((node: any) => node.linked).length,
    [graph]
  );

  // The library sizes its canvas from `window.innerWidth`/`window.innerHeight`
  // unless it is told otherwise, which would draw a viewport-sized graph inside a
  // 520px panel that clips it. Measuring the holder keeps the canvas the size of
  // the box it is actually shown in, and a zero-size container simply waits for a
  // real measurement instead of rendering at zero.
  useEffect(() => {
    const holder = holderRef.current;
    if (!holder) return;

    const measure = () => {
      const rect = holder.getBoundingClientRect();
      setSize((previous) =>
        Math.abs(previous.width - rect.width) < 1 && Math.abs(previous.height - rect.height) < 1
          ? previous
          : { width: rect.width, height: rect.height }
      );
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(holder);
    return () => observer.disconnect();
  }, [mounted]);

  const frame = useCallback(
    (durationMs: number, resetDirection: boolean) => {
      if (!graphRef.current) return false;
      return frameGraph(graphRef.current, graph.nodes, size, durationMs, resetDirection);
    },
    [graph, size]
  );

  // A callback ref, not an effect keyed on `mounted`: the graph component arrives
  // asynchronously, so an effect that runs when this component mounts finds no
  // instance, and never runs again to find the one that arrives later.
  const attach = useCallback(
    (control: any) => {
      graphRef.current = control;
      if (!control) return;
      control.d3Force?.("charge")?.strength((node: any) =>
        node?.linked ? CHARGE_STRENGTH : ISOLATE_CHARGE_STRENGTH
      );
      control.d3Force?.("link")?.distance(LINK_DISTANCE);
      frameOnSettle.current = true;
    },
    []
  );

  // A new layout has to be reframed once it settles.
  useEffect(() => {
    frameOnSettle.current = true;
  }, [graph]);

  const legend = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of graph.nodes) {
      const language = String(node.language ?? "").toLowerCase() || "other";
      counts.set(language, (counts.get(language) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [graph]);

  // Every callback handed to the graph is stable on purpose. The library rebuilds
  // the scene whenever one of them changes identity, which restarts the layout
  // and moves every file - so an inline arrow here means the camera stops
  // matching the graph the first time this component re-renders for any reason.
  const labelNode = useCallback(
    (node: any) =>
      `${node.path ?? node.name}\n${node.language ?? "unknown"} · imported by ${
        node.fanIn
      }, imports ${node.fanOut}${node.findings ? `\n${node.findings} finding(s)` : ""}`,
    []
  );
  const valueOfNode = useCallback((node: any) => node.val, []);
  const colourOfNode = useCallback((node: any) => node.colour, []);
  const buildForNode = useCallback(
    (node: any) => buildNode(node, graph.nodes.length),
    [graph]
  );
  const colourOfLink = useCallback((link: any) => {
    const touched = [link.source, link.target].filter(Boolean);
    const hasFindings = touched.some((node: any) => Number(node?.findings) > 0);
    return hasFindings ? "rgba(239, 68, 68, 0.45)" : "rgba(0, 255, 65, 0.16)";
  }, []);
  const frameAfterSettle = useCallback(() => {
    if (!frameOnSettle.current) return;
    settledOnce.current = true;
    // A panel that has not been measured yet cannot be framed, and the engine
    // does not stop again on its own, so stay armed until a fit actually lands.
    if (!frame(0, false)) return;
    frameOnSettle.current = false;
    framedSize.current = { width: size.width, height: size.height };
  }, [frame, size]);

  // A resize moves the edges of the picture, so the camera has to be solved again
  // or the graph stays framed for a panel that no longer exists - which is also how
  // a panel that was collapsed when the layout settled gets framed when it comes
  // back. Unlike a node drag - which restarts the engine and must not be fought -
  // this is allowed to reframe, because that is the whole point of it.
  useEffect(() => {
    const previous = framedSize.current;
    if (!settledOnce.current || !graphRef.current) return;
    if (previous.width === size.width && previous.height === size.height) return;
    if (!frame(250, false)) return;
    framedSize.current = { width: size.width, height: size.height };
  }, [frame, size]);
  const selectNode = useCallback(
    (node: any) => {
      const control = graphRef.current;
      const view = control?.cameraPosition?.();
      const aim = new THREE.Vector3(
        view?.lookAt?.x ?? 0,
        view?.lookAt?.y ?? 0,
        view?.lookAt?.z ?? 0
      );
      const away = new THREE.Vector3(view?.x ?? 0, view?.y ?? 0, view?.z ?? 0).sub(aim);
      const current = away.length();
      // Step in towards the clicked file without spinning the view round.
      const distance = Math.max(current > 0 ? current * 0.45 : 60, 30);
      const direction = current > 0 ? away.normalize() : DEFAULT_DIRECTION;
      control?.cameraPosition(
        {
          x: node.x + direction.x * distance,
          y: node.y + direction.y * distance,
          z: node.z + direction.z * distance,
        },
        { x: node.x, y: node.y, z: node.z },
        700
      );
      onNodeClick?.(node);
    },
    [onNodeClick]
  );

  if (!mounted) {
    return (
      <div className="grid h-full w-full place-items-center bg-[#0a0a0a] text-xs text-[#4d4d4d]">
        Preparing the graph...
      </div>
    );
  }

  if (graph.nodes.length === 0) {
    return (
      <div className="grid h-full w-full place-items-center bg-[#0a0a0a] px-6 text-center text-xs text-[#5a5a5a]">
        No files were analysed, so there is nothing to lay out.
      </div>
    );
  }

  const ready = size.width > 1 && size.height > 1;

  return (
    <div className="flex h-full w-full flex-col">
      <div ref={holderRef} className="relative min-h-0 w-full flex-1">
        {ready && (
          <ForceGraph3D
            ref={attach as unknown as GraphRef}
            width={size.width}
            height={size.height}
            graphData={graph}
            backgroundColor="#0a0a0a"
            // The library draws its own control hints across the bottom of the canvas
            // by default. The caption under the picture says the same thing.
            showNavInfo={false}
            // Labelling every node turns a large graph into noise, so only the most
            // depended-upon files are named and the rest are found by hovering.
            nodeLabel={labelNode}
            nodeVal={valueOfNode}
            nodeColor={colourOfNode}
            nodeRelSize={4}
            // A 3D graph draws nodes as spheres, so labels have to be 3D objects too.
            // Only the most depended-upon files get one, or the view becomes a wall
            // of overlapping text.
            nodeThreeObject={buildForNode}
            linkColor={colourOfLink}
            linkWidth={0.6}
            linkDirectionalArrowLength={3}
            linkDirectionalArrowRelPos={0.9}
            // Once the layout has stopped moving, frame it. Doing this on every stop
            // would fight the reader: dragging a file restarts the engine.
            onEngineStop={frameAfterSettle}
            onNodeClick={selectNode}
          />
        )}

        <button
          type="button"
          onClick={() => frame(450, true)}
          className="absolute right-3 top-3 flex items-center gap-1.5 rounded border border-white/10 bg-black/70 px-2 py-1 text-[9px] uppercase tracking-widest text-[#7a7a7a] transition-colors hover:border-[#00ff41]/40 hover:text-[#00ff41] focus:outline-none focus-visible:ring-1 focus-visible:ring-[#00ff41]"
          aria-label="Reset the graph view"
        >
          <Crosshair size={11} aria-hidden />
          Reset view
        </button>
      </div>

      {/* The legend and the caption used to float over the canvas, where they sat
          on top of files and hid them. They belong under the picture. */}
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1 border-t border-white/5 px-1 pt-1.5 text-[9px] uppercase tracking-widest">
        <div className="flex items-center gap-2 text-[#555]">
          <span className="shrink-0 text-[#00ff41]">Size = imported by</span>
          <ul className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            {legend.map(([language, count]) => (
              <li key={language} className="flex items-center gap-1 normal-case tracking-normal">
                <span
                  aria-hidden
                  className="inline-block h-2 w-2 rounded-sm"
                  style={{ background: LANGUAGE_COLOURS[language] ?? FALLBACK_COLOUR }}
                />
                <span className="text-[#7a7a7a]">{language}</span>
                <span className="text-[#4d4d4d]">{count}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Files with no resolved import have nothing to connect them, so the graph
            is smaller than the file count. Saying so is better than looking like a
            bug. */}
        <div className="text-right text-[#444]">
          <p>
            {linkedFiles} of {analysedFiles ?? linkedFiles} files have import links
          </p>
          <p>{graph.links.length} resolved imports · drag to orbit · scroll to zoom</p>
        </div>
      </div>
    </div>
  );
}

/**
 * Point the camera at the graph's hub and frame the rest of it inside the panel.
 *
 * The anchor is the hub - the file the most other files connect to - and the camera
 * is aimed straight at it. That is what puts it on the exact middle of the canvas:
 * the aim becomes the controls' target, and a point on the view axis always lands
 * at the centre of the projection. So the hub's position is not solved for at all,
 * it is chosen, and it cannot drift off centre afterwards.
 *
 * That leaves one thing to solve, the distance: how far back the camera has to
 * stand for the furthest file to stay inside the panel. It is measured from the
 * middle of the canvas rather than from the bounding box, so what is kept at zero
 * is the hub's own offset. A hub that sits off to one side of the cloud therefore
 * makes the picture lopsided around it, which is the trade being made on purpose -
 * a radial graph anchored on its hub, not a photograph of the cloud's extent.
 *
 * `resetDirection` snaps back to the default angle; otherwise the reader's orbit
 * is kept, so refitting after a resize does not undo it.
 */
function frameGraph(
  control: any,
  nodes: any[],
  size: { width: number; height: number },
  durationMs: number,
  resetDirection: boolean
): boolean {
  const placed = nodes.filter(
    (node: any) =>
      Number.isFinite(node?.x) && Number.isFinite(node?.y) && Number.isFinite(node?.z)
  );
  if (placed.length === 0 || size.width < 2 || size.height < 2) return false;
  if (typeof control.graph2ScreenCoords !== "function") return false;

  const camera = typeof control.camera === "function" ? control.camera() : null;
  if (!camera) return false;

  const hub = pickHub(placed);
  let aim: THREE.Vector3;
  if (hub) {
    aim = new THREE.Vector3(hub.x, hub.y, hub.z);
  } else {
    // Nothing is connected to anything, so there is no hub and the middle of the
    // files is as good an anchor as any.
    const median = (read: (node: any) => number) => {
      const values = placed.map(read).sort((a, b) => a - b);
      return values[Math.floor(values.length / 2)];
    };
    aim = new THREE.Vector3(
      median((node) => node.x),
      median((node) => node.y),
      median((node) => node.z)
    );
  }

  // Past a certain size a handful of files at the far end of a large graph must
  // not be able to shrink everything else into a dot, so they are left out of the
  // framing radius. They stay in the scene - they just do not get to pick the zoom.
  const reach = placed
    .map((node: any) => Math.hypot(node.x - aim.x, node.y - aim.y, node.z - aim.z))
    .sort((a, b) => a - b);
  const radius =
    reach.length <= FIT_TAIL_FREE_UNDER
      ? reach[reach.length - 1]
      : reach[Math.min(reach.length - 1, Math.ceil(reach.length * FIT_TAIL) - 1)];
  if (!(radius > 0)) return false;

  const view = control.cameraPosition?.() ?? null;
  const lookAt = view?.lookAt ?? { x: 0, y: 0, z: 0 };
  const direction = new THREE.Vector3(
    (view?.x ?? 0) - lookAt.x,
    (view?.y ?? 0) - lookAt.y,
    (view?.z ?? 0) - lookAt.z
  );
  let distance = direction.length();
  if (!(distance > 0)) {
    direction.copy(DEFAULT_DIRECTION);
    distance = radius * 4;
  }
  direction.normalize();
  if (resetDirection) direction.copy(DEFAULT_DIRECTION);

  const halfWidth = size.width / 2;
  const halfHeight = size.height / 2;

  const place = () => {
    const position = aim.clone().addScaledVector(direction, distance);
    control.cameraPosition({ x: position.x, y: position.y, z: position.z }, aim, 0);
    // The renderer only refreshes the camera's matrices when it draws, so it is
    // brought up to date here - otherwise the projection answered below would be
    // describing the previous camera.
    if (typeof camera.lookAt === "function") camera.lookAt(aim);
    camera.updateMatrixWorld(true);
  };

  for (let attempt = 0; attempt < FIT_ATTEMPTS; attempt += 1) {
    place();

    let spreadX = 0;
    let spreadY = 0;
    for (const node of placed) {
      const point = control.graph2ScreenCoords(node.x, node.y, node.z);
      if (!Number.isFinite(point?.x) || !Number.isFinite(point?.y)) continue;
      spreadX = Math.max(spreadX, Math.abs(point.x - halfWidth) / halfWidth);
      spreadY = Math.max(spreadY, Math.abs(point.y - halfHeight) / halfHeight);
    }
    const spread = Math.max(spreadX, spreadY);
    if (!Number.isFinite(spread) || spread <= 0) return false;
    if (Math.abs(spread - FIT_FILL) <= FIT_FILL * FIT_SCALE_TOLERANCE) break;

    // Too big for the panel means too close, so stand further back.
    distance = THREE.MathUtils.clamp(distance * (spread / FIT_FILL), radius, radius * 40);
  }

  const position = aim.clone().addScaledVector(direction, distance);
  control.cameraPosition({ x: position.x, y: position.y, z: position.z }, aim, durationMs);
  return true;
}

/**
 * The file the most other files connect to - the one a reader means when they call
 * a dependency graph's "centre node".
 *
 * Degree first, because connected with the rest of the graph is what a hub means;
 * then fan-in, because that is what the sphere is sized by, so the hub is also the
 * node that reads as biggest; then the id, so a tie cannot pick a different answer
 * on the next render. Null when no file is connected to anything, which is the
 * caller's signal that there is no hub to anchor on.
 */
function pickHub(
  nodes: any[]
): { x: number; y: number; z: number } | null {
  let best: { degree: number; fanIn: number; id: string; x: number; y: number; z: number } | null =
    null;
  for (const node of nodes) {
    const fanIn = Number(node?.fanIn ?? 0);
    const degree = fanIn + Number(node?.fanOut ?? 0);
    if (degree <= 0) continue;
    const id = String(node?.id ?? "");
    if (
      !best ||
      degree > best.degree ||
      (degree === best.degree && fanIn > best.fanIn) ||
      (degree === best.degree && fanIn === best.fanIn && id < best.id)
    ) {
      best = { degree, fanIn, id, x: node.x, y: node.y, z: node.z };
    }
  }
  return best ? { x: best.x, y: best.y, z: best.z } : null;
}

/**
 * One node: a sphere sized by fan-in, a red shell when it has findings, and a
 * sprite label for the files worth naming.
 */
function buildNode(node: any, nodeCount: number): THREE.Object3D {
  const group = new THREE.Group();
  const radius = 1.6 + Math.sqrt(Math.max(0.1, node.val)) * 1.5;

  const shell = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 16, 12),
    new THREE.MeshBasicMaterial({ color: node.colour, transparent: true, opacity: 0.9 })
  );
  group.add(shell);

  if (Number(node.findings) > 0) {
    // A separate wireframe shell rather than a recolour: the language colour
    // still identifies the file, and the finding count stays legible.
    const outline = new THREE.Mesh(
      new THREE.SphereGeometry(radius * 1.45, 12, 8),
      new THREE.MeshBasicMaterial({ color: "#ef4444", wireframe: true, transparent: true, opacity: 0.45 })
    );
    group.add(outline);
  }

  const worthLabelling = node.fanIn >= LABEL_FAN_IN_THRESHOLD || nodeCount <= LABEL_EVERYTHING_UNDER;
  const label = String(node.name ?? "");
  if (label && worthLabelling) {
    const sprite = makeLabelSprite(label.length > 20 ? `${label.slice(0, 19)}…` : label);
    sprite.position.y = radius + 2.4;
    sprite.scale.set(11, 2.6, 1);
    group.add(sprite);
  }

  return group;
}

function makeLabelSprite(text: string): THREE.Sprite {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.font = "44px monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "rgba(210, 210, 210, 0.85)";
    ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false })
  );
}