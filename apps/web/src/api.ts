import type { FrameResponse, FramesIndexResponse } from "./types";

const API_BASE_URL = "http://localhost:8000";

interface RangeQuery {
  start_date: string;
  end_date: string;
  window_size: number;
  method: string;
}

async function parseError(response: Response): Promise<never> {
  const fallback = `Request failed: ${response.status}`;
  let detail: string | undefined;
  try {
    const body = (await response.json()) as { detail?: string };
    detail = typeof body.detail === "string" ? body.detail : undefined;
  } catch {
    // Ignore JSON parse errors and fall back to status-based message.
  }
  throw new Error(detail ?? fallback);
}

export async function fetchFramesRange(query: RangeQuery): Promise<FramesIndexResponse> {
  const params = new URLSearchParams({
    start_date: query.start_date,
    end_date: query.end_date,
    window_size: String(query.window_size),
    method: query.method,
  });

  const response = await fetch(`${API_BASE_URL}/frames/range?${params.toString()}`);
  if (!response.ok) {
    return parseError(response);
  }
  return (await response.json()) as FramesIndexResponse;
}

export async function fetchFrame(snapshotId: number): Promise<FrameResponse> {
  const response = await fetch(`${API_BASE_URL}/frames/${snapshotId}`);
  if (!response.ok) {
    return parseError(response);
  }
  return (await response.json()) as FrameResponse;
}
