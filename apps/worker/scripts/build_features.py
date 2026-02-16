from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from apps.api.db.models import FeatureDaily, PriceDaily, Symbol
from apps.api.db.session import AsyncSessionLocal

DEFAULT_TICKERS = ("^N225", "^TOPX", "USDJPY=X")
FALLBACK_TICKERS: dict[str, tuple[str, ...]] = {
    "^TOPX": ("1306.T",),
    "1306.T": ("^TOPX",),
}
FEATURE_SET_VERSION = "v1_market_daily"


@dataclass(slots=True)
class ResolvedSeries:
    requested_ticker: str
    used_ticker: str
    symbol: Symbol
    frame: pd.DataFrame


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_TICKERS)
    parsed = [item.strip() for item in raw.split(",") if item.strip()]
    return parsed or list(DEFAULT_TICKERS)


def _chunk_rows(rows: list[dict[str, object]], chunk_size: int = 1000) -> list[list[dict[str, object]]]:
    return [rows[offset : offset + chunk_size] for offset in range(0, len(rows), chunk_size)]


def _build_features(price_frame: pd.DataFrame) -> pd.DataFrame:
    work = price_frame.copy()
    work["log_close"] = np.log(work["price"])
    work["return_1d"] = work["log_close"].diff()
    work["return_5d"] = work["log_close"].diff(5)
    work["rolling_mean_20d"] = work["return_1d"].rolling(window=20).mean()
    work["vol_20d"] = work["return_1d"].rolling(window=20).std()
    work["zscore_20d"] = (work["return_1d"] - work["rolling_mean_20d"]) / work["vol_20d"]
    result = work[["return_1d", "return_5d", "vol_20d", "zscore_20d"]].dropna(how="any")
    return result


async def _resolve_series(
    session,
    ticker: str,
    start_date: date,
    end_date: date,
    source: str,
) -> ResolvedSeries | None:
    candidates = [ticker, *FALLBACK_TICKERS.get(ticker, ())]
    for candidate in candidates:
        symbol = await session.scalar(select(Symbol).where(Symbol.ticker == candidate))
        if symbol is None:
            continue

        rows = (
            await session.execute(
                select(PriceDaily.date, func.coalesce(PriceDaily.adj_close, PriceDaily.close).label("price"))
                .where(
                    PriceDaily.symbol_id == symbol.id,
                    PriceDaily.date >= start_date,
                    PriceDaily.date <= end_date,
                    PriceDaily.source == source,
                )
                .order_by(PriceDaily.date.asc())
            )
        ).all()

        if not rows:
            continue

        frame = pd.DataFrame(rows, columns=["date", "price"])
        frame = frame.dropna(subset=["price"]).drop_duplicates(subset=["date"])
        if frame.empty:
            continue

        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date").sort_index()
        frame["price"] = frame["price"].astype(float)
        return ResolvedSeries(
            requested_ticker=ticker,
            used_ticker=candidate,
            symbol=symbol,
            frame=frame,
        )

    return None


async def build_features(*, start_date: date, end_date: date, tickers: list[str], reset: bool, source: str) -> None:
    if end_date < start_date:
        raise ValueError("end must be on or after start")

    async with AsyncSessionLocal() as session:
        resolved: list[ResolvedSeries] = []
        for ticker in tickers:
            series = await _resolve_series(
                session,
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                source=source,
            )
            if series is None:
                print(f"No prices found for {ticker} (including fallback)")
                continue
            resolved.append(series)

        if not resolved:
            raise RuntimeError("No ticker series could be resolved from prices_daily")

        per_symbol_features: dict[int, pd.DataFrame] = {}

        for series in resolved:
            features = _build_features(series.frame)
            if features.empty:
                print(f"No feature rows after dropna for {series.requested_ticker} <- {series.used_ticker}")
                continue

            per_symbol_features[series.symbol.id] = features
        if not per_symbol_features:
            raise RuntimeError("No feature rows available after feature generation")
        symbol_ids = list(per_symbol_features.keys())

        if reset and symbol_ids:
            await session.execute(
                delete(FeatureDaily).where(
                    FeatureDaily.symbol_id.in_(symbol_ids),
                    FeatureDaily.date >= start_date,
                    FeatureDaily.date <= end_date,
                    FeatureDaily.feature_set_version == FEATURE_SET_VERSION,
                )
            )

        rows_to_upsert: list[dict[str, object]] = []
        per_symbol_counts: dict[int, int] = {}

        for series in resolved:
            features = per_symbol_features.get(series.symbol.id)
            if features is None:
                continue

            count = 0
            for ts, values in features.iterrows():
                rows_to_upsert.append(
                    {
                        "symbol_id": series.symbol.id,
                        "date": ts.date(),
                        "feature_set_version": FEATURE_SET_VERSION,
                        "feature_values": {
                            "return_1d": float(values["return_1d"]),
                            "return_5d": float(values["return_5d"]),
                            "vol_20d": float(values["vol_20d"]),
                            "zscore_20d": float(values["zscore_20d"]),
                        },
                    }
                )
                count += 1
            per_symbol_counts[series.symbol.id] = count

        total_upserted = 0
        for chunk in _chunk_rows(rows_to_upsert):
            stmt = insert(FeatureDaily).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    FeatureDaily.symbol_id,
                    FeatureDaily.date,
                    FeatureDaily.feature_set_version,
                ],
                set_={
                    "feature_values": stmt.excluded.feature_values,
                },
            )
            await session.execute(stmt)
            total_upserted += len(chunk)

        await session.commit()

    print(
        "Built features_daily "
        f"(start={start_date}, end={end_date}, source={source}, reset={reset}, "
        f"feature_set_version={FEATURE_SET_VERSION}) upserted={total_upserted}"
    )
    for series in resolved:
        print(
            f"  {series.requested_ticker} <- {series.used_ticker}: "
            f"{per_symbol_counts.get(series.symbol.id, 0)} rows"
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build daily features from prices_daily and upsert into features_daily.")
    parser.add_argument("--start", required=True, help="Start date in YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date in YYYY-MM-DD")
    parser.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TICKERS),
        help='Comma-separated tickers (default: "^N225,^TOPX,USDJPY=X")',
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing features for target tickers/date range before upserting.",
    )
    parser.add_argument(
        "--source",
        default="yfinance",
        help='Filter prices_daily rows by source (default: "yfinance").',
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    asyncio.run(
        build_features(
            start_date=_parse_date(args.start),
            end_date=_parse_date(args.end),
            tickers=_parse_tickers(args.tickers),
            reset=args.reset,
            source=args.source,
        )
    )


if __name__ == "__main__":
    main()
