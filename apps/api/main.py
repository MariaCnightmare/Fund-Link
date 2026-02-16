from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import Job, NetworkEdge, NetworkSnapshot, Symbol
from apps.api.db.session import get_db_session

app = FastAPI(title="Fund-Link API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FrameNode(BaseModel):
    symbol: str = Field(..., description="Ticker symbol")


class FrameEdge(BaseModel):
    src: str = Field(..., description="Source ticker")
    dst: str = Field(..., description="Target ticker")
    weight: float = Field(..., description="Computed as 1.0 - p_value")
    p_value: float
    lag: int


class FrameResponse(BaseModel):
    snapshot_id: int
    end_date: date
    window_size: int
    method: str
    job_type: str | None
    nodes: list[FrameNode]
    edges: list[FrameEdge]


class FrameIndexItem(BaseModel):
    snapshot_id: int
    end_date: date


class FrameRangeMeta(BaseModel):
    start_date: date
    end_date: date
    window_size: int
    method: str
    count: int


class FrameRangeResponse(BaseModel):
    schema_version: str = "frames_index.v1"
    meta: FrameRangeMeta
    items: list[FrameIndexItem]


async def _build_frame_response(
    db: AsyncSession,
    *,
    snapshot: NetworkSnapshot,
    job: Job | None,
    p_threshold: float,
    max_lag: int,
) -> FrameResponse:
    edge_stmt = select(NetworkEdge).where(
        and_(
            NetworkEdge.snapshot_id == snapshot.id,
            NetworkEdge.p_value <= Decimal(str(p_threshold)),
            NetworkEdge.lag <= max_lag,
        )
    )
    edges = (await db.execute(edge_stmt)).scalars().all()

    symbol_ids = {edge.source_symbol_id for edge in edges} | {edge.target_symbol_id for edge in edges}
    symbol_map: dict[int, str] = {}
    if symbol_ids:
        symbol_stmt = select(Symbol).where(Symbol.id.in_(symbol_ids))
        symbols = (await db.execute(symbol_stmt)).scalars().all()
        symbol_map = {symbol.id: symbol.ticker for symbol in symbols}

    frame_edges: list[FrameEdge] = []
    for edge in edges:
        src = symbol_map.get(edge.source_symbol_id)
        dst = symbol_map.get(edge.target_symbol_id)
        if src is None or dst is None:
            continue

        p_value_float = float(edge.p_value)
        frame_edges.append(
            FrameEdge(
                src=src,
                dst=dst,
                p_value=p_value_float,
                lag=edge.lag,
                weight=1.0 - p_value_float,
            )
        )

    unique_symbols = sorted({item.src for item in frame_edges} | {item.dst for item in frame_edges})
    nodes = [FrameNode(symbol=symbol) for symbol in unique_symbols]

    return FrameResponse(
        snapshot_id=snapshot.id,
        end_date=snapshot.end_date,
        window_size=snapshot.window_size,
        method=snapshot.method,
        job_type=job.job_type if job is not None else None,
        nodes=nodes,
        edges=frame_edges,
    )


async def _fetch_frames(
    db: AsyncSession,
    *,
    end_date_from: date,
    end_date_to: date,
    window_size: int,
    method: str | None,
    p_threshold: float,
    max_lag: int,
    job_type: str | None,
) -> list[FrameResponse]:
    snapshot_stmt: Select[tuple[NetworkSnapshot, Job | None]] = (
        select(NetworkSnapshot, Job)
        .outerjoin(Job, NetworkSnapshot.job_id == Job.id)
        .where(
            and_(
                NetworkSnapshot.end_date >= end_date_from,
                NetworkSnapshot.end_date <= end_date_to,
                NetworkSnapshot.window_size == window_size,
            )
        )
        .order_by(NetworkSnapshot.end_date.asc(), NetworkSnapshot.id.asc())
    )

    if method is not None:
        snapshot_stmt = snapshot_stmt.where(NetworkSnapshot.method == method)

    if job_type is not None:
        snapshot_stmt = snapshot_stmt.where(Job.job_type == job_type)

    snapshot_rows = (await db.execute(snapshot_stmt)).all()
    frames: list[FrameResponse] = []

    for snapshot, job in snapshot_rows:
        frames.append(
            await _build_frame_response(
                db,
                snapshot=snapshot,
                job=job,
                p_threshold=p_threshold,
                max_lag=max_lag,
            )
        )

    return frames


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/frames", response_model=FrameResponse)
async def get_frame(
    end_date: date,
    window_size: int = Query(..., ge=1),
    p_threshold: float = Query(0.05, ge=0.0, le=1.0),
    max_lag: int = Query(5, ge=0),
    method: str | None = None,
    job_type: str | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> FrameResponse:
    frames = await _fetch_frames(
        db,
        end_date_from=end_date,
        end_date_to=end_date,
        window_size=window_size,
        method=method,
        p_threshold=p_threshold,
        max_lag=max_lag,
        job_type=job_type,
    )
    if not frames:
        raise HTTPException(status_code=404, detail="No matching frame snapshot")
    return frames[0]


@app.get("/frames/range", response_model=FrameRangeResponse)
async def get_frame_range(
    start_date: date,
    end_date: date,
    window_size: int = Query(..., ge=1),
    method: str = "granger",
    db: AsyncSession = Depends(get_db_session),
) -> FrameRangeResponse:
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    snapshot_stmt = (
        select(NetworkSnapshot.id, NetworkSnapshot.end_date)
        .where(
            and_(
                NetworkSnapshot.end_date >= start_date,
                NetworkSnapshot.end_date <= end_date,
                NetworkSnapshot.window_size == window_size,
                NetworkSnapshot.method == method,
            )
        )
        .order_by(NetworkSnapshot.end_date.asc(), NetworkSnapshot.id.asc())
    )
    rows = (await db.execute(snapshot_stmt)).all()
    items = [FrameIndexItem(snapshot_id=snapshot_id, end_date=snapshot_end_date) for snapshot_id, snapshot_end_date in rows]

    return FrameRangeResponse(
        meta=FrameRangeMeta(
            start_date=start_date,
            end_date=end_date,
            window_size=window_size,
            method=method,
            count=len(items),
        ),
        items=items,
    )


@app.get("/frames/{snapshot_id}", response_model=FrameResponse)
async def get_frame_by_snapshot_id(
    snapshot_id: int,
    p_threshold: float = Query(0.05, ge=0.0, le=1.0),
    max_lag: int = Query(5, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> FrameResponse:
    snapshot_stmt: Select[tuple[NetworkSnapshot, Job | None]] = (
        select(NetworkSnapshot, Job)
        .outerjoin(Job, NetworkSnapshot.job_id == Job.id)
        .where(NetworkSnapshot.id == snapshot_id)
    )
    row = (await db.execute(snapshot_stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Frame snapshot {snapshot_id} not found")

    snapshot, job = row
    return await _build_frame_response(
        db,
        snapshot=snapshot,
        job=job,
        p_threshold=p_threshold,
        max_lag=max_lag,
    )
