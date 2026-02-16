from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import yfinance as yf
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from apps.api.db.models import PriceDaily, Symbol
from apps.api.db.session import AsyncSessionLocal

DEFAULT_TICKERS = ("^N225", "^TOPX", "USDJPY=X")
FALLBACK_TICKERS: dict[str, tuple[str, ...]] = {
    "^TOPX": ("1306.T",),
}
ASSET_CLASS_BY_TICKER: dict[str, str] = {
    "^N225": "index",
    "^TOPX": "index",
    "USDJPY=X": "fx",
}
NAME_BY_TICKER: dict[str, str] = {
    "^N225": "Nikkei 225",
    "^TOPX": "TOPIX",
    "USDJPY=X": "USD/JPY",
}


@dataclass(slots=True)
class DownloadResult:
    requested_ticker: str
    used_ticker: str
    frame: pd.DataFrame
    currency: str | None


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_TICKERS)
    parsed = [item.strip() for item in raw.split(",") if item.strip()]
    return parsed or list(DEFAULT_TICKERS)


def _to_decimal(value: object | None) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(str(round(float(value), 6)))


def _to_volume(value: object | None) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(round(float(value)))


def _normalize_download_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw.empty:
        return raw

    frame = raw
    if isinstance(frame.columns, pd.MultiIndex):
        level0 = frame.columns.get_level_values(0)
        if ticker in level0:
            frame = frame.xs(ticker, axis=1, level=0)
        else:
            frame.columns = frame.columns.get_level_values(-1)

    frame = frame.rename(columns={column: str(column).strip() for column in frame.columns})

    expected_columns = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    available = [column for column in expected_columns if column in frame.columns]
    frame = frame[available]
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"], how="any")
    return frame


def _fetch_currency(ticker: str) -> str | None:
    try:
        info = yf.Ticker(ticker).fast_info
        currency = info.get("currency") if info is not None else None
        if currency:
            return str(currency)
    except Exception:
        return None
    return None


def _download_with_fallback(requested_ticker: str, start_date: date, end_date: date) -> DownloadResult | None:
    candidates = [requested_ticker, *FALLBACK_TICKERS.get(requested_ticker, ())]
    for candidate in candidates:
        frame = yf.download(
            tickers=candidate,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            group_by="ticker",
            interval="1d",
            progress=False,
            actions=False,
            threads=False,
        )
        normalized = _normalize_download_frame(frame, candidate)
        if normalized.empty:
            continue

        currency = _fetch_currency(candidate)
        return DownloadResult(
            requested_ticker=requested_ticker,
            used_ticker=candidate,
            frame=normalized,
            currency=currency,
        )
    return None


def _upsert_chunks(rows: list[dict[str, object]], chunk_size: int = 1000) -> list[list[dict[str, object]]]:
    return [rows[offset : offset + chunk_size] for offset in range(0, len(rows), chunk_size)]


async def fetch_yfinance_data(*, start_date: date, end_date: date, tickers: list[str], reset: bool) -> None:
    if end_date < start_date:
        raise ValueError("end must be on or after start")

    async with AsyncSessionLocal() as session:
        symbol_by_ticker: dict[str, Symbol] = {}
        for ticker in tickers:
            symbol = await session.scalar(select(Symbol).where(Symbol.ticker == ticker))
            if symbol is None:
                symbol = Symbol(
                    ticker=ticker,
                    name=NAME_BY_TICKER.get(ticker, ticker),
                    exchange="YAHOO",
                    sector=ASSET_CLASS_BY_TICKER.get(ticker, "equity"),
                    is_active=True,
                )
                session.add(symbol)
                await session.flush()
            else:
                symbol.name = symbol.name or NAME_BY_TICKER.get(ticker, ticker)
                symbol.exchange = symbol.exchange or "YAHOO"
                symbol.sector = symbol.sector or ASSET_CLASS_BY_TICKER.get(ticker, "equity")
                symbol.is_active = True
            symbol_by_ticker[ticker] = symbol

        symbol_ids = [symbol.id for symbol in symbol_by_ticker.values()]
        if reset and symbol_ids:
            await session.execute(
                delete(PriceDaily).where(
                    PriceDaily.symbol_id.in_(symbol_ids),
                    PriceDaily.date >= start_date,
                    PriceDaily.date <= end_date,
                )
            )

        all_rows: list[dict[str, object]] = []
        per_ticker_counts: dict[str, int] = {}
        used_ticker_map: dict[str, str] = {}

        for ticker in tickers:
            result = _download_with_fallback(ticker, start_date, end_date)
            if result is None:
                per_ticker_counts[ticker] = 0
                print(f"No rows downloaded for {ticker}")
                continue

            symbol = symbol_by_ticker[ticker]
            used_ticker_map[ticker] = result.used_ticker
            count = 0

            for index, row in result.frame.iterrows():
                ts = pd.Timestamp(index)
                trade_date = ts.date()
                open_value = _to_decimal(row.get("Open"))
                high_value = _to_decimal(row.get("High"))
                low_value = _to_decimal(row.get("Low"))
                close_value = _to_decimal(row.get("Close"))
                if open_value is None or high_value is None or low_value is None or close_value is None:
                    continue

                all_rows.append(
                    {
                        "symbol_id": symbol.id,
                        "date": trade_date,
                        "open": open_value,
                        "high": high_value,
                        "low": low_value,
                        "close": close_value,
                        "adj_close": _to_decimal(row.get("Adj Close")),
                        "volume": _to_volume(row.get("Volume")),
                        "source": "yfinance",
                        "currency": result.currency,
                    }
                )
                count += 1

            per_ticker_counts[ticker] = count

        inserted_or_updated = 0
        for chunk in _upsert_chunks(all_rows):
            stmt = insert(PriceDaily).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[PriceDaily.symbol_id, PriceDaily.date],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "adj_close": stmt.excluded.adj_close,
                    "volume": stmt.excluded.volume,
                    "source": stmt.excluded.source,
                    "currency": stmt.excluded.currency,
                },
            )
            await session.execute(stmt)
            inserted_or_updated += len(chunk)

        await session.commit()

    print(
        "Fetched yfinance prices "
        f"(start={start_date}, end={end_date}, reset={reset}) inserted_or_updated={inserted_or_updated}"
    )
    for ticker in tickers:
        used = used_ticker_map.get(ticker, ticker)
        print(f"  {ticker} <- {used}: {per_ticker_counts.get(ticker, 0)} rows")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch daily market prices from yfinance and upsert into prices_daily.")
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
        help="Delete existing prices for target tickers in the date range before upserting.",
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    start_date = _parse_date(args.start)
    end_date = _parse_date(args.end)
    tickers = _parse_tickers(args.tickers)
    asyncio.run(fetch_yfinance_data(start_date=start_date, end_date=end_date, tickers=tickers, reset=args.reset))


if __name__ == "__main__":
    main()
