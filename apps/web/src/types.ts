export type MethodType = string;

export interface FrameNode {
  symbol: string;
}

export interface FrameEdge {
  src: string;
  dst: string;
  weight: number;
  p_value: number;
  lag: number;
}

export interface FrameResponse {
  snapshot_id: number;
  end_date: string;
  window_size: number;
  method: MethodType;
  job_type: string | null;
  nodes: FrameNode[];
  edges: FrameEdge[];
}

export interface FramesIndexItem {
  snapshot_id: number;
  end_date: string;
}

export interface FramesIndexMeta {
  start_date: string;
  end_date: string;
  window_size: number;
  method: MethodType;
  count: number;
}

export interface FramesIndexResponse {
  schema_version: "frames_index.v1";
  meta: FramesIndexMeta;
  items: FramesIndexItem[];
}
