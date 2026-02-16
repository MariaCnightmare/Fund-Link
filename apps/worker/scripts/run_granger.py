from __future__ import annotations

import argparse
import asyncio
import warnings
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from statsmodels.tsa.stattools import grangercausalitytests

from apps.api.db.models import FeatureDaily, Job, NetworkEdge, NetworkSnapshot, Symbol
from apps.api.db.session import AsyncSessionLocal

DEFAULT_TICKERS = ("^N225", "^TOPX", "USDJPY=X")
FALLBACK_TICKERS: dict[str, tuple[str, ...]] = {
    "^TOPX": ("1306.T",),
}
DEFAULT_METHOD = "granger"
DEFAULT_FEATURE_SET_VERSION = "v1_market_daily"
DEFAULT_FEATURE_KEY = "return_1d"


@dataclass(slots=True)
class ResolvedSymbol:
    requested_ticker: str
    used_ticker: str
    symbol: Symbol


@dataclass(slots=True)
class CandidateEdge:
    src_symbol_id: int
    dst_symbol_id: int
    p_value: float
    lag: int


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_TICKERS)
    parsed = [item.strip() for item in raw.split(",") if item.strip()]
    return parsed or list(DEFAULT_TICKERS)


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(parsed) or np.isinf(parsed):
        return None
    return parsed


def _evaluate_pair(
    *,
    src_values: pd.Series,
    dst_values: pd.Series,
    max_lag: int,
) -> tuple[float, int] | None:
    if len(src_values) != len(dst_values):
        return None
    # statsmodels requires enough rows relative to max lag.
    effective_max_lag = min(max_lag, len(src_values) - 2)
    if effective_max_lag < 1:
        return None

    work = pd.concat([dst_values, src_values], axis=1)
    work.columns = ["dst", "src"]
    if work.isna().any().any():
        return None

    try:
        warnings.filterwarnings(
            "ignore",
            message="verbose is deprecated since functions should not print results",
            category=FutureWarning,
        )
        result = grangercausalitytests(work[["dst", "src"]], maxlag=effective_max_lag, verbose=False)
    except Exception:
        return None

    best_p = 1.0
    best_lag = 0
    for lag, lag_result in result.items():
        # ssr_ftest -> (F-stat, p-value, df_denom, df_num)
        p_value = float(lag_result[0]["ssr_ftest"][1])
        if p_value < best_p:
            best_p = p_value
            best_lag = int(lag)

    if best_lag == 0:
        return None
    return best_p, best_lag


async def _resolve_symbols(session, tickers: list[str]) -> list[ResolvedSymbol]:
    resolved: list[ResolvedSymbol] = []
    for ticker in tickers:
        candidates = [ticker, *FALLBACK_TICKERS.get(ticker, ())]
        picked: ResolvedSymbol | None = None
        for candidate in candidates:
            symbol = await session.scalar(select(Symbol).where(Symbol.ticker == candidate))
            if symbol is None:
                continue
            picked = ResolvedSymbol(
                requested_ticker=ticker,
                used_ticker=candidate,
                symbol=symbol,
            )
            break
        if picked is None:
            print(f"Skip ticker={ticker}: symbol not found (including fallback)")
            continue
        resolved.append(picked)
    return resolved


async def _load_feature_frame(
    session,
    *,
    resolved_symbols: list[ResolvedSymbol],
    start_date: date,
    end_date: date,
    feature_set_version: str,
    feature_key: str,
) -> pd.DataFrame:
    if not resolved_symbols:
        return pd.DataFrame()

    symbol_ids = [item.symbol.id for item in resolved_symbols]
    rows = (
        await session.execute(
            select(FeatureDaily.symbol_id, FeatureDaily.date, FeatureDaily.feature_values).where(
                FeatureDaily.symbol_id.in_(symbol_ids),
                FeatureDaily.date >= start_date,
                FeatureDaily.date <= end_date,
                FeatureDaily.feature_set_version == feature_set_version,
            )
        )
    ).all()

    if not rows:
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for symbol_id, feature_date, feature_values in rows:
        if not isinstance(feature_values, dict):
            continue
        value = _to_float(feature_values.get(feature_key))
        if value is None:
            continue
        records.append(
            {
                "symbol_id": symbol_id,
                "date": feature_date,
                "value": value,
            }
        )

    if not records:
        return pd.DataFrame()

    frame = pd.DataFrame.from_records(records)
    frame["date"] = pd.to_datetime(frame["date"])
    pivot = frame.pivot_table(index="date", columns="symbol_id", values="value", aggfunc="first")
    # inner join behavior across symbols
    pivot = pivot.dropna(how="any")
    pivot = pivot.sort_index()
    return pivot


async def _reset_scope(
    session,
    *,
    start_date: date,
    end_date: date,
    window_size: int,
    method: str,
) -> None:
    snapshot_ids = (
        await session.execute(
            select(NetworkSnapshot.id).where(
                NetworkSnapshot.end_date >= start_date,
                NetworkSnapshot.end_date <= end_date,
                NetworkSnapshot.window_size == window_size,
                NetworkSnapshot.method == method,
            )
        )
    ).scalars().all()
    if not snapshot_ids:
        return
    await session.execute(delete(NetworkEdge).where(NetworkEdge.snapshot_id.in_(snapshot_ids)))
    await session.execute(delete(NetworkSnapshot).where(NetworkSnapshot.id.in_(snapshot_ids)))
    await session.flush()


async def run_granger(
    *,
    start_date: date,
    end_date: date,
    window_size: int,
    max_lag: int,
    p_threshold: float,
    method: str,
    feature_set_version: str,
    feature_key: str,
    tickers: list[str],
    reset: bool,
) -> None:
    if end_date < start_date:
        raise ValueError("end must be on or after start")
    if window_size < 2:
        raise ValueError("window_size must be >= 2")
    if max_lag < 1:
        raise ValueError("max_lag must be >= 1")
    if not (0.0 <= p_threshold <= 1.0):
        raise ValueError("p_threshold must be in [0, 1]")

    async with AsyncSessionLocal() as session:
        job = Job(
            job_type=method,
            status="running",
            payload={
                "script": "run_granger",
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "window_size": window_size,
                "max_lag": max_lag,
                "p_threshold": p_threshold,
                "method": method,
                "feature_set_version": feature_set_version,
                "feature_key": feature_key,
                "tickers": tickers,
                "reset": reset,
            },
        )
        session.add(job)
        await session.flush()

        if reset:
            await _reset_scope(
                session,
                start_date=start_date,
                end_date=end_date,
                window_size=window_size,
                method=method,
            )

        resolved_symbols = await _resolve_symbols(session, tickers)
        if len(resolved_symbols) < 2:
            job.status = "failed"
            job.error_message = "At least 2 symbols are required after fallback resolution"
            await session.commit()
            raise RuntimeError(job.error_message)

        feature_frame = await _load_feature_frame(
            session,
            resolved_symbols=resolved_symbols,
            start_date=start_date,
            end_date=end_date,
            feature_set_version=feature_set_version,
            feature_key=feature_key,
        )
        if feature_frame.empty:
            job.status = "failed"
            job.error_message = (
                "No features found for requested symbols/date range "
                f"(feature_set_version={feature_set_version}, feature_key={feature_key})"
            )
            await session.commit()
            raise RuntimeError(job.error_message)

        symbol_ids = [item.symbol.id for item in resolved_symbols]
        feature_frame = feature_frame[[col for col in feature_frame.columns if col in symbol_ids]]
        if feature_frame.shape[1] < 2:
            job.status = "failed"
            job.error_message = "Not enough aligned symbol series after inner join"
            await session.commit()
            raise RuntimeError(job.error_message)

        generated_snapshot_ids: list[int] = []
        edge_counts_by_snapshot: dict[int, int] = {}

        date_index = [ts.date() for ts in feature_frame.index]
        for idx, snapshot_end_date in enumerate(date_index):
            if snapshot_end_date < start_date or snapshot_end_date > end_date:
                continue
            if idx + 1 < window_size:
                continue

            window = feature_frame.iloc[idx + 1 - window_size : idx + 1]
            if len(window) < window_size:
                continue

            candidate_edges: list[CandidateEdge] = []
            for src_symbol_id in window.columns:
                for dst_symbol_id in window.columns:
                    if src_symbol_id == dst_symbol_id:
                        continue
                    result = _evaluate_pair(
                        src_values=window[src_symbol_id],
                        dst_values=window[dst_symbol_id],
                        max_lag=max_lag,
                    )
                    if result is None:
                        continue
                    min_p_value, best_lag = result
                    if min_p_value <= p_threshold:
                        candidate_edges.append(
                            CandidateEdge(
                                src_symbol_id=int(src_symbol_id),
                                dst_symbol_id=int(dst_symbol_id),
                                p_value=min_p_value,
                                lag=best_lag,
                            )
                        )

            snapshot = await session.scalar(
                select(NetworkSnapshot).where(
                    NetworkSnapshot.end_date == snapshot_end_date,
                    NetworkSnapshot.window_size == window_size,
                    NetworkSnapshot.method == method,
                )
            )
            if snapshot is None:
                snapshot = NetworkSnapshot(
                    as_of_date=snapshot_end_date,
                    end_date=snapshot_end_date,
                    window_size=window_size,
                    window_start=window.index[0].date(),
                    window_end=window.index[-1].date(),
                    method=method,
                    node_count=len(window.columns),
                    edge_count=len(candidate_edges),
                    job_id=job.id,
                    metadata_json={
                        "tickers": tickers,
                        "resolved_tickers": {item.requested_ticker: item.used_ticker for item in resolved_symbols},
                        "feature_set_version": feature_set_version,
                        "feature_key": feature_key,
                        "max_lag": max_lag,
                        "p_threshold": p_threshold,
                        "window_size": window_size,
                    },
                )
                session.add(snapshot)
                await session.flush()
            else:
                snapshot.as_of_date = snapshot_end_date
                snapshot.window_start = window.index[0].date()
                snapshot.window_end = window.index[-1].date()
                snapshot.method = method
                snapshot.node_count = len(window.columns)
                snapshot.edge_count = len(candidate_edges)
                snapshot.job_id = job.id
                snapshot.metadata_json = {
                    "tickers": tickers,
                    "resolved_tickers": {item.requested_ticker: item.used_ticker for item in resolved_symbols},
                    "feature_set_version": feature_set_version,
                    "feature_key": feature_key,
                    "max_lag": max_lag,
                    "p_threshold": p_threshold,
                    "window_size": window_size,
                }
                await session.flush()

            await session.execute(delete(NetworkEdge).where(NetworkEdge.snapshot_id == snapshot.id))
            for edge in candidate_edges:
                p_value_decimal = Decimal(str(round(edge.p_value, 6)))
                session.add(
                    NetworkEdge(
                        snapshot_id=snapshot.id,
                        source_symbol_id=edge.src_symbol_id,
                        target_symbol_id=edge.dst_symbol_id,
                        weight=Decimal("1.000000") - p_value_decimal,
                        p_value=p_value_decimal,
                        lag=edge.lag,
                    )
                )

            generated_snapshot_ids.append(snapshot.id)
            edge_counts_by_snapshot[snapshot.id] = len(candidate_edges)

        job.status = "completed"
        job.result = {
            "snapshot_ids": generated_snapshot_ids,
            "edge_counts": edge_counts_by_snapshot,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "window_size": window_size,
            "method": method,
        }
        await session.commit()

    edge_pairs = sorted(edge_counts_by_snapshot.items(), key=lambda item: item[0])
    print(
        "Generated granger snapshots "
        f"(start={start_date}, end={end_date}, window_size={window_size}, "
        f"max_lag={max_lag}, p_threshold={p_threshold}, method={method}, reset={reset})"
    )
    print(f"snapshot_ids={generated_snapshot_ids}")
    print(f"edge_counts={edge_pairs}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Granger causality network snapshots from features_daily and persist into network tables."
    )
    parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD")
    parser.add_argument("--window_size", type=int, default=30, help="Rolling window size (default: 30)")
    parser.add_argument("--max_lag", type=int, default=3, help="Maximum lag for Granger tests (default: 3)")
    parser.add_argument("--p_threshold", type=float, default=0.05, help="P-value threshold (default: 0.05)")
    parser.add_argument("--method", default=DEFAULT_METHOD, help='Snapshot method tag (default: "granger")')
    parser.add_argument(
        "--feature_set_version",
        default=DEFAULT_FEATURE_SET_VERSION,
        help='Feature set version in features_daily (default: "v1_market_daily")',
    )
    parser.add_argument(
        "--feature_key",
        default=DEFAULT_FEATURE_KEY,
        help='Key inside feature_values JSON (default: "return_1d")',
    )
    parser.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TICKERS),
        help='Comma-separated tickers (default: "^N225,^TOPX,USDJPY=X")',
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing network_edges/network_snapshots in target scope before regeneration.",
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    asyncio.run(
        run_granger(
            start_date=_parse_date(args.start),
            end_date=_parse_date(args.end),
            window_size=args.window_size,
            max_lag=args.max_lag,
            p_threshold=args.p_threshold,
            method=args.method,
            feature_set_version=args.feature_set_version,
            feature_key=args.feature_key,
            tickers=_parse_tickers(args.tickers),
            reset=args.reset,
        )
    )


if __name__ == "__main__":
    main()
