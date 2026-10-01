"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
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
}: {
  data?: { nodes?: any[]; links?: any[] };
  onNodeClick?: (node: any) => void;
}) {
  const graphRef = useRef<any>(null);
  const [mounted, setMounted] = useState(false);

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
      const source = typeof link?.source === "object" ? link.source?.id : link?.source;
      const target = typeof link?.target === "object" ? link.target?.id : link?.target;
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

  // The layout only needs configuring once the graph exists and the canvas is up.
  useEffect(() => {
    if (!mounted || !graphRef.current || graph.nodes.length === 0) return;
    const control = graphRef.current;
    control.cameraPosition({ x: 0, y: 0, z: 260 }, { x: 0, y: 0, z: 0 }, 1200);
    control.d3Force?.("charge")?.strength(-260);
    control.d3Force?.("link")?.distance(70);
    control.d3Force?.("collision")?.radius((node: any) => 6 + Math.sqrt(node.val) * 3);
  }, [mounted, graph]);

  const legend = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of graph.nodes) {
      const language = String(node.language ?? "").toLowerCase() || "other";
      counts.set(language, (counts.get(language) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [graph]);

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

  return (
    <div className="relative h-full w-full">
      <ForceGraph3D
        ref={graphRef}
        graphData={graph}
        backgroundColor="#0a0a0a"
        // Labelling every node turns a large graph into noise, so only the most
        // depended-upon files are named and the rest are found by hovering.
        nodeLabel={(node: any) =>
          `${node.path ?? node.name}\n${node.language ?? "unknown"} · imported by ${
            node.fanIn
          }, imports ${node.fanOut}${node.findings ? `\n${node.findings} finding(s)` : ""}`
        }
        nodeVal={(node: any) => node.val}
        nodeColor={(node: any) => node.colour}
        nodeRelSize={4}
        // A 3D graph draws nodes as spheres, so labels have to be 3D objects too.
        // Only the most depended-upon files get one, or the view becomes a wall
        // of overlapping text.
        nodeThreeObject={(node: any) => buildNode(node, graph.nodes.length)}
        linkColor={(link: any) => {
          const touched = [link.source, link.target].filter(Boolean);
          const hasFindings = touched.some((node: any) => Number(node?.findings) > 0);
          return hasFindings ? "rgba(239, 68, 68, 0.45)" : "rgba(0, 255, 65, 0.16)";
        }}
        linkWidth={0.6}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={0.9}
        onNodeClick={(node: any) => {
          const distance = 60;
          const ratio = 1 + distance / (Math.hypot(node.x, node.y, node.z) || 1);
          graphRef.current?.cameraPosition(
            { x: node.x * ratio, y: node.y * ratio, z: node.z * ratio },
            node,
            900
          );
          onNodeClick?.(node);
        }}
      />

      <div className="pointer-events-none absolute bottom-3 left-3 rounded border border-white/10 bg-black/70 px-2.5 py-2 text-[9px] uppercase tracking-widest text-[#555]">
        <p className="text-[#00ff41]">Size = imported by</p>
        <ul className="mt-1 space-y-0.5">
          {legend.map(([language, count]) => (
            <li key={language} className="flex items-center gap-1.5 normal-case tracking-normal">
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

      <div className="pointer-events-none absolute bottom-3 right-3 text-right text-[9px] uppercase tracking-widest text-[#444]">
        <p>{graph.nodes.length} files · {graph.links.length} imports</p>
        <p>drag to orbit · scroll to zoom</p>
      </div>
    </div>
  );
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