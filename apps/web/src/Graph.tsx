import { forceCenter, forceLink, forceManyBody, forceSimulation, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import { useEffect, useMemo, useRef, useState } from "react";

import type { FrameEdge, FrameResponse } from "./types";

const WIDTH = 980;
const HEIGHT = 560;
const P_VALUE_THRESHOLD = 0.05;

interface GraphProps {
  frame: FrameResponse | null;
  highlightEdgeKey?: string;
  diff?: {
    added: string[] | Set<string>;
    removed: string[] | Set<string>;
    changed: string[] | Set<string>;
  };
  ghostRemovedEdges?: FrameEdge[];
}

interface GraphNode extends SimulationNodeDatum {
  id: string;
  degree: number;
  radius: number;
}

interface GraphLink extends SimulationLinkDatum<GraphNode> {
  src: string;
  dst: string;
  weight: number;
  p_value: number;
  lag: number;
}

interface GhostEdge {
  id: string;
  key: string;
  edge: FrameEdge;
  fading: boolean;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function resolveNode(value: string | number | GraphNode, nodeMap: Map<string, GraphNode>): GraphNode | undefined {
  if (typeof value === "string") {
    return nodeMap.get(value);
  }
  if (typeof value === "number") {
    return undefined;
  }
  return value;
}

function toKeySet(value: string[] | Set<string> | undefined): Set<string> {
  if (!value) {
    return new Set();
  }
  return value instanceof Set ? value : new Set(value);
}

export function Graph({ frame, highlightEdgeKey, diff, ghostRemovedEdges }: GraphProps): JSX.Element {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [pulseKeys, setPulseKeys] = useState<Set<string>>(new Set());
  const [ghostEdges, setGhostEdges] = useState<GhostEdge[]>([]);
  const positionCacheRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  const ghostSeqRef = useRef(0);

  const data = useMemo(() => {
    if (!frame) {
      return { nodes: [], links: [] } as { nodes: GraphNode[]; links: GraphLink[] };
    }

    const degreeMap = new Map<string, number>();
    for (const edge of frame.edges) {
      degreeMap.set(edge.src, (degreeMap.get(edge.src) ?? 0) + 1);
      degreeMap.set(edge.dst, (degreeMap.get(edge.dst) ?? 0) + 1);
    }

    const graphNodes: GraphNode[] = frame.nodes.map((node, index) => ({
      id: node.symbol,
      degree: degreeMap.get(node.symbol) ?? 0,
      radius: 6 + Math.min(10, (degreeMap.get(node.symbol) ?? 0) * 2),
      x:
        positionCacheRef.current.get(node.symbol)?.x ??
        WIDTH / 2 + (Math.random() - 0.5) * 240 + Math.cos(index) * 20,
      y:
        positionCacheRef.current.get(node.symbol)?.y ??
        HEIGHT / 2 + (Math.random() - 0.5) * 200 + Math.sin(index) * 20,
    }));

    const graphLinks: GraphLink[] = frame.edges.map((edge) => ({
      source: edge.src,
      target: edge.dst,
      src: edge.src,
      dst: edge.dst,
      weight: edge.weight,
      p_value: edge.p_value,
      lag: edge.lag,
    }));

    return { nodes: graphNodes, links: graphLinks };
  }, [frame]);

  useEffect(() => {
    setNodes(data.nodes);
    setLinks(data.links);

    if (!data.nodes.length) {
      return;
    }

    const simulation = forceSimulation<GraphNode>(data.nodes)
      .force("charge", forceManyBody().strength(-260))
      .force("center", forceCenter(WIDTH / 2, HEIGHT / 2))
      .force(
        "link",
        forceLink<GraphNode, GraphLink>(data.links)
          .id((node) => node.id)
          .distance(140)
          .strength(0.38),
      )
      .on("tick", () => {
        for (const node of data.nodes) {
          positionCacheRef.current.set(node.id, { x: node.x ?? WIDTH / 2, y: node.y ?? HEIGHT / 2 });
        }
        setNodes([...data.nodes]);
        setLinks([...data.links]);
      });

    return () => {
      simulation.stop();
    };
  }, [data]);

  const nodeMap = useMemo(() => {
    const map = new Map<string, GraphNode>();
    for (const node of nodes) {
      map.set(node.id, node);
    }
    return map;
  }, [nodes]);
  const diffAddedSet = useMemo(() => toKeySet(diff?.added), [diff?.added]);
  const diffRemovedSet = useMemo(() => toKeySet(diff?.removed), [diff?.removed]);
  const diffChangedSet = useMemo(() => toKeySet(diff?.changed), [diff?.changed]);

  useEffect(() => {
    const nextPulse = toKeySet(diff?.changed);
    setPulseKeys(nextPulse);
    if (!nextPulse.size) {
      return;
    }

    const timer = window.setTimeout(() => {
      setPulseKeys(new Set());
    }, 300);
    return () => {
      window.clearTimeout(timer);
    };
  }, [frame?.snapshot_id, diff?.changed]);

  useEffect(() => {
    if (!ghostRemovedEdges?.length) {
      return;
    }

    const createdAt = ghostSeqRef.current++;
    const freshGhosts: GhostEdge[] = ghostRemovedEdges.map((edge, index) => ({
      id: `${createdAt}-${index}-${edge.src}-${edge.dst}-${edge.lag}`,
      key: `${edge.src}|${edge.dst}|${edge.lag}`,
      edge,
      fading: false,
    }));
    setGhostEdges((prev) => [...prev, ...freshGhosts]);

    const ids = new Set(freshGhosts.map((item) => item.id));
    const raf = window.requestAnimationFrame(() => {
      setGhostEdges((prev) => prev.map((item) => (ids.has(item.id) ? { ...item, fading: true } : item)));
    });
    const timer = window.setTimeout(() => {
      setGhostEdges((prev) => prev.filter((item) => !ids.has(item.id)));
    }, 380);

    return () => {
      window.cancelAnimationFrame(raf);
      window.clearTimeout(timer);
    };
  }, [frame?.snapshot_id, ghostRemovedEdges]);

  if (!frame) {
    return <div className="graph-empty">Load で再生データを取得してください</div>;
  }

  return (
    <svg className="graph-canvas" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Frames graph">
      <defs>
        <marker id="arrow-strong" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(26, 126, 210, 0.9)" />
        </marker>
        <marker id="arrow-weak" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(92, 102, 120, 0.45)" />
        </marker>
      </defs>
      <g>
        {links.map((link, index) => {
          const source = resolveNode(link.source, nodeMap);
          const target = resolveNode(link.target, nodeMap);

          if (!source || !target) {
            return null;
          }

          const strokeWidth = clamp(link.weight * 4.2, 1.1, 5.8);
          const weak = link.p_value > P_VALUE_THRESHOLD;
          const edgeKey = `${link.src}|${link.dst}|${link.lag}`;
          const highlighted = highlightEdgeKey === edgeKey;
          const added = diffAddedSet.has(edgeKey);
          const removed = diffRemovedSet.has(edgeKey);
          const changed = diffChangedSet.has(edgeKey);
          const pulsing = pulseKeys.has(edgeKey);
          const sourceX = source.x ?? WIDTH / 2;
          const sourceY = source.y ?? HEIGHT / 2;
          const targetX = target.x ?? WIDTH / 2;
          const targetY = target.y ?? HEIGHT / 2;
          const labelX = (sourceX + targetX) / 2;
          const labelY = (sourceY + targetY) / 2;
          const categoryBoost = added || changed ? 1 : 0;
          const pulseBoost = pulsing ? 2 : 0;
          const effectiveStrokeWidth = highlighted ? strokeWidth + 3 : strokeWidth + categoryBoost + pulseBoost;
          const effectiveOpacity = highlighted ? 1 : added ? 0.98 : changed ? 0.95 : weak ? 0.75 : 0.9;

          return (
            <g
              key={`${link.src}-${link.dst}-${index}`}
              className={`edge ${added ? "added" : ""} ${removed ? "removed" : ""} ${changed ? "changed" : ""} ${pulsing ? "pulse" : ""} ${highlighted ? "highlighted" : ""}`}
            >
              <line
                x1={sourceX}
                y1={sourceY}
                x2={targetX}
                y2={targetY}
                stroke={weak ? "rgba(92, 102, 120, 0.45)" : "rgba(26, 126, 210, 0.9)"}
                strokeWidth={effectiveStrokeWidth}
                opacity={effectiveOpacity}
                strokeDasharray={weak ? "6 5" : "0"}
                markerEnd={weak ? "url(#arrow-weak)" : "url(#arrow-strong)"}
              />
              <title>{`${link.src} -> ${link.dst}\nweight=${link.weight.toFixed(3)} p=${link.p_value.toFixed(3)} lag=${link.lag}`}</title>
              <text className="graph-lag" x={labelX} y={labelY - 3}>
                lag {link.lag}
              </text>
            </g>
          );
        })}
      </g>
      <g>
        {ghostEdges.map((ghost) => {
          const source = nodeMap.get(ghost.edge.src);
          const target = nodeMap.get(ghost.edge.dst);
          const sourcePosition = source
            ? { x: source.x ?? WIDTH / 2, y: source.y ?? HEIGHT / 2 }
            : positionCacheRef.current.get(ghost.edge.src);
          const targetPosition = target
            ? { x: target.x ?? WIDTH / 2, y: target.y ?? HEIGHT / 2 }
            : positionCacheRef.current.get(ghost.edge.dst);
          if (!sourcePosition || !targetPosition) {
            return null;
          }

          const highlighted = highlightEdgeKey === ghost.key;
          const ghostWidth = clamp(ghost.edge.weight * 4.2, 1.1, 5.8) + (highlighted ? 2 : 0);
          return (
            <g key={ghost.id} className={`edge removed-ghost ${ghost.fading ? "fade-out" : ""}`}>
              <line
                x1={sourcePosition.x}
                y1={sourcePosition.y}
                x2={targetPosition.x}
                y2={targetPosition.y}
                stroke="rgba(110, 120, 136, 0.55)"
                strokeWidth={ghostWidth}
                opacity={highlighted ? 0.9 : 0.45}
                strokeDasharray="4 5"
                markerEnd="url(#arrow-weak)"
              />
            </g>
          );
        })}
      </g>
      <g>
        {nodes.map((node) => (
          <g key={node.id} transform={`translate(${node.x ?? WIDTH / 2},${node.y ?? HEIGHT / 2})`}>
            <circle r={node.radius} className="graph-node" />
            <title>{`${node.id} (degree=${node.degree})`}</title>
            <text className="graph-label" textAnchor="middle" dy="5">
              {node.id}
            </text>
          </g>
        ))}
      </g>
    </svg>
  );
}
