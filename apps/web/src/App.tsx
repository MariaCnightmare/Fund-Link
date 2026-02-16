import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchFrame, fetchFramesRange } from "./api";
import { Graph } from "./Graph";
import type { FrameEdge, FrameResponse, FramesIndexItem } from "./types";

interface QueryState {
  start_date: string;
  end_date: string;
  window_size: number;
  method: string;
}

const INITIAL_QUERY: QueryState = {
  start_date: "2026-02-10",
  end_date: "2026-02-11",
  window_size: 30,
  method: "granger",
};

const DEFAULT_PLAYBACK_INTERVAL_MS = 500;
const MIN_PLAYBACK_INTERVAL_MS = 200;
const MAX_PLAYBACK_INTERVAL_MS = 1500;
const DIFF_PREVIEW_LIMIT = 5;

interface ChangedEdgeEntry {
  key: string;
  previous: FrameEdge;
  current: FrameEdge;
  deltaWeight: number;
}

interface EdgeDiffResult {
  added: FrameEdge[];
  removed: FrameEdge[];
  changed: ChangedEdgeEntry[];
}

interface GraphDiffKeys {
  added: string[];
  removed: string[];
  changed: string[];
}

function toEdgeKey(edge: FrameEdge): string {
  return `${edge.src}|${edge.dst}|${edge.lag}`;
}

function toFixed3(value: number): string {
  return value.toFixed(3);
}

function toSignedFixed3(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

function computeEdgeDiff(previousEdges: FrameEdge[], currentEdges: FrameEdge[]): EdgeDiffResult {
  const previousMap = new Map<string, FrameEdge>(previousEdges.map((edge) => [toEdgeKey(edge), edge]));
  const currentMap = new Map<string, FrameEdge>(currentEdges.map((edge) => [toEdgeKey(edge), edge]));

  const added = currentEdges
    .filter((edge) => !previousMap.has(toEdgeKey(edge)))
    .sort((a, b) => b.weight - a.weight);
  const removed = previousEdges
    .filter((edge) => !currentMap.has(toEdgeKey(edge)))
    .sort((a, b) => b.weight - a.weight);

  const changed: ChangedEdgeEntry[] = [];
  for (const [key, current] of currentMap) {
    const previous = previousMap.get(key);
    if (!previous) {
      continue;
    }
    const deltaWeight = current.weight - previous.weight;
    if (Math.abs(deltaWeight) < 1e-9) {
      continue;
    }
    changed.push({ key, previous, current, deltaWeight });
  }
  changed.sort((a, b) => Math.abs(b.deltaWeight) - Math.abs(a.deltaWeight));

  return { added, removed, changed };
}

export default function App(): JSX.Element {
  const [query, setQuery] = useState<QueryState>(INITIAL_QUERY);
  const [rangeItems, setRangeItems] = useState<FramesIndexItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [cache, setCache] = useState<Record<number, FrameResponse>>({});
  const [playbackIntervalMs, setPlaybackIntervalMs] = useState(DEFAULT_PLAYBACK_INTERVAL_MS);
  const [loading, setLoading] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [highlightEdgeKey, setHighlightEdgeKey] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const cacheRef = useRef<Record<number, FrameResponse>>({});
  const pendingRef = useRef<Set<number>>(new Set());

  const currentItem = rangeItems[currentIndex] ?? null;
  const currentFrame = currentItem ? cache[currentItem.snapshot_id] ?? null : null;
  const previousItem = currentIndex > 0 ? rangeItems[currentIndex - 1] ?? null : null;
  const previousFrame = previousItem ? cache[previousItem.snapshot_id] ?? null : null;

  const sliderMax = useMemo(() => Math.max(rangeItems.length - 1, 0), [rangeItems.length]);
  const edgeDiff = useMemo<EdgeDiffResult>(() => {
    if (!currentFrame || !previousFrame) {
      return { added: [], removed: [], changed: [] };
    }
    return computeEdgeDiff(previousFrame.edges, currentFrame.edges);
  }, [currentFrame, previousFrame]);
  const diffKeys = useMemo<GraphDiffKeys>(
    () => ({
      added: edgeDiff.added.map((edge) => toEdgeKey(edge)),
      removed: edgeDiff.removed.map((edge) => toEdgeKey(edge)),
      changed: edgeDiff.changed.map((entry) => entry.key),
    }),
    [edgeDiff],
  );

  const updateQuery = <K extends keyof QueryState>(key: K, value: QueryState[K]): void => {
    setQuery((prev) => ({ ...prev, [key]: value }));
  };

  const ensureFrame = useCallback(async (item: FramesIndexItem): Promise<void> => {
    if (cacheRef.current[item.snapshot_id] || pendingRef.current.has(item.snapshot_id)) {
      return;
    }

    pendingRef.current.add(item.snapshot_id);
    try {
      const frame = await fetchFrame(item.snapshot_id);
      cacheRef.current = { ...cacheRef.current, [item.snapshot_id]: frame };
      setCache(cacheRef.current);
    } finally {
      pendingRef.current.delete(item.snapshot_id);
    }
  }, []);

  const prefetchAround = useCallback(
    (items: FramesIndexItem[], index: number): void => {
      const targets = [items[index - 1], items[index], items[index + 1]].filter(
        (item): item is FramesIndexItem => Boolean(item),
      );
      for (const item of targets) {
        void ensureFrame(item).catch((error: unknown) => {
          setErrorMessage(error instanceof Error ? error.message : "Failed to prefetch frame detail.");
        });
      }
    },
    [ensureFrame],
  );

  const handleLoad = async (): Promise<void> => {
    setLoading(true);
    setErrorMessage(null);
    setIsPlaying(false);

    try {
      const range = await fetchFramesRange(query);
      setRangeItems(range.items);
      cacheRef.current = {};
      pendingRef.current.clear();
      setCache(cacheRef.current);
      setCurrentIndex(0);
      setHighlightEdgeKey(null);
      if (range.items[0]) {
        await ensureFrame(range.items[0]);
        prefetchAround(range.items, 0);
      }
    } catch (error) {
      setRangeItems([]);
      cacheRef.current = {};
      pendingRef.current.clear();
      setCache(cacheRef.current);
      setCurrentIndex(0);
      setHighlightEdgeKey(null);
      setErrorMessage(error instanceof Error ? error.message : "Failed to load range.");
    } finally {
      setLoading(false);
    }
  };

  const handleStepNext = useCallback((): void => {
    if (!rangeItems.length) {
      return;
    }

    setCurrentIndex((prev) => {
      if (prev >= rangeItems.length - 1) {
        return prev;
      }
      return prev + 1;
    });
  }, [rangeItems.length]);

  const handleStepPrev = useCallback((): void => {
    if (!rangeItems.length) {
      return;
    }
    setCurrentIndex((prev) => Math.max(0, prev - 1));
  }, [rangeItems.length]);

  const handleReset = useCallback((): void => {
    setIsPlaying(false);
    setCurrentIndex(0);
    setHighlightEdgeKey(null);
  }, []);

  useEffect(() => {
    if (!currentItem) {
      return;
    }

    prefetchAround(rangeItems, currentIndex);
  }, [currentIndex, currentItem, prefetchAround, rangeItems]);

  useEffect(() => {
    if (!currentItem) {
      return;
    }
    ensureFrame(currentItem).catch((error: unknown) => {
      setErrorMessage(error instanceof Error ? error.message : "Failed to load frame detail.");
      setIsPlaying(false);
    });
  }, [currentItem, ensureFrame]);

  useEffect(() => {
    if (!isPlaying || rangeItems.length < 2) {
      return;
    }

    const timer = window.setInterval(() => {
      setCurrentIndex((prev) => {
        if (prev >= rangeItems.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, playbackIntervalMs);

    return () => {
      window.clearInterval(timer);
    };
  }, [isPlaying, playbackIntervalMs, rangeItems.length]);

  useEffect(() => {
    const handler = (event: KeyboardEvent): void => {
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName.toLowerCase();
      if (tagName === "input" || tagName === "textarea" || tagName === "select" || target?.isContentEditable) {
        return;
      }

      if (event.code === "Space") {
        event.preventDefault();
        if (!rangeItems.length) {
          return;
        }
        setIsPlaying((prev) => !prev);
        return;
      }

      if (event.key === "ArrowRight") {
        event.preventDefault();
        handleStepNext();
        return;
      }

      if (event.key === "ArrowLeft") {
        event.preventDefault();
        handleStepPrev();
        return;
      }

      if (event.key === "r" || event.key === "R") {
        event.preventDefault();
        handleReset();
      }
    };

    window.addEventListener("keydown", handler);
    return () => {
      window.removeEventListener("keydown", handler);
    };
  }, [handleReset, handleStepNext, handleStepPrev, rangeItems.length]);

  return (
    <main className="layout">
      <section className="panel controls">
        <h1>Fund-Link Frames Viewer</h1>
        <div className="control-grid">
          <label>
            Start Date
            <input
              type="date"
              value={query.start_date}
              onChange={(event) => updateQuery("start_date", event.target.value)}
            />
          </label>
          <label>
            End Date
            <input
              type="date"
              value={query.end_date}
              onChange={(event) => updateQuery("end_date", event.target.value)}
            />
          </label>
          <label>
            Window Size
            <input
              type="number"
              min={1}
              value={query.window_size}
              onChange={(event) => updateQuery("window_size", Number(event.target.value))}
            />
          </label>
          <label>
            Method
            <input
              type="text"
              value={query.method}
              onChange={(event) => updateQuery("method", event.target.value)}
            />
          </label>
        </div>
        <div className="button-row">
          <button className={loading ? "is-loading" : ""} onClick={() => void handleLoad()} disabled={loading}>
            {loading ? "Loading..." : "Load"}
          </button>
          <button onClick={() => setIsPlaying(true)} disabled={isPlaying || rangeItems.length === 0}>
            Play
          </button>
          <button onClick={() => setIsPlaying(false)} disabled={!isPlaying}>
            Pause
          </button>
          <button onClick={handleStepPrev} disabled={rangeItems.length === 0 || currentIndex <= 0}>
            Prev
          </button>
          <button onClick={handleStepNext} disabled={rangeItems.length === 0 || currentIndex >= sliderMax}>
            Next
          </button>
          <button onClick={handleReset} disabled={rangeItems.length === 0}>
            Reset
          </button>
        </div>

        <label className="slider-wrap">
          Playback Index
          <input
            type="range"
            min={0}
            max={sliderMax}
            value={currentIndex}
            onChange={(event) => {
              setIsPlaying(false);
              setCurrentIndex(Number(event.target.value));
              setHighlightEdgeKey(null);
            }}
            disabled={rangeItems.length === 0}
          />
          <span>
            {rangeItems.length ? `${currentIndex + 1} / ${rangeItems.length}` : "0 / 0"}
          </span>
        </label>

        <label className="speed-wrap">
          Speed: {playbackIntervalMs}ms
          <input
            type="range"
            min={MIN_PLAYBACK_INTERVAL_MS}
            max={MAX_PLAYBACK_INTERVAL_MS}
            step={100}
            value={playbackIntervalMs}
            onChange={(event) => setPlaybackIntervalMs(Number(event.target.value))}
          />
        </label>
      </section>

      <section className="panel status">
        <div>End Date: {currentFrame?.end_date ?? "-"}</div>
        <div>Snapshot ID: {currentFrame?.snapshot_id ?? "-"}</div>
        <div>Method: {currentFrame?.method ?? "-"}</div>
        <div>Job Type: {currentFrame?.job_type ?? "-"}</div>
        <div>Nodes: {currentFrame?.nodes.length ?? 0}</div>
        <div>Edges: {currentFrame?.edges.length ?? 0}</div>
      </section>

      {errorMessage && (
        <section className="panel error toast" role="alert">
          {errorMessage}
        </section>
      )}

      <section className="panel graph">
        <Graph
          frame={currentFrame}
          highlightEdgeKey={highlightEdgeKey ?? undefined}
          diff={diffKeys}
          ghostRemovedEdges={edgeDiff.removed}
        />
      </section>

      <section className="panel diff">
        <h2>ΔEdges</h2>
        {!previousFrame || !currentFrame ? (
          <div className="diff-empty">前フレームとの比較は index 2 以降で表示されます</div>
        ) : (
          <div className="diff-grid">
            <div className="diff-group">
              <div className="diff-title">Added ({edgeDiff.added.length})</div>
              {edgeDiff.added.slice(0, DIFF_PREVIEW_LIMIT).map((edge) => {
                const key = toEdgeKey(edge);
                return (
                  <button
                    key={key}
                    className={`diff-row ${highlightEdgeKey === key ? "active" : ""}`}
                    onClick={() => setHighlightEdgeKey((prev) => (prev === key ? null : key))}
                  >
                    <span className="mono">{`+ ${edge.src}->${edge.dst}`}</span>
                    <span>{`w=${toFixed3(edge.weight)} p=${toFixed3(edge.p_value)} lag=${edge.lag}`}</span>
                  </button>
                );
              })}
            </div>

            <div className="diff-group">
              <div className="diff-title">Removed ({edgeDiff.removed.length})</div>
              {edgeDiff.removed.slice(0, DIFF_PREVIEW_LIMIT).map((edge) => {
                const key = toEdgeKey(edge);
                return (
                  <button
                    key={key}
                    className={`diff-row ${highlightEdgeKey === key ? "active" : ""}`}
                    onClick={() => setHighlightEdgeKey((prev) => (prev === key ? null : key))}
                  >
                    <span className="mono">{`- ${edge.src}->${edge.dst}`}</span>
                    <span>{`w=${toFixed3(edge.weight)} p=${toFixed3(edge.p_value)} lag=${edge.lag}`}</span>
                  </button>
                );
              })}
            </div>

            <div className="diff-group">
              <div className="diff-title">Changed ({edgeDiff.changed.length})</div>
              {edgeDiff.changed.slice(0, DIFF_PREVIEW_LIMIT).map((entry) => (
                <button
                  key={entry.key}
                  className={`diff-row ${highlightEdgeKey === entry.key ? "active" : ""}`}
                  onClick={() => setHighlightEdgeKey((prev) => (prev === entry.key ? null : entry.key))}
                >
                  <span className="mono">{`↕ ${entry.current.src}->${entry.current.dst}`}</span>
                  <span>{`Δw=${toSignedFixed3(entry.deltaWeight)} (${toFixed3(entry.previous.weight)}->${toFixed3(entry.current.weight)}) lag=${entry.current.lag}`}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
