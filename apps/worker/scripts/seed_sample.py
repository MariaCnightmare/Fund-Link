from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select

from apps.api.db.models import Job, NetworkEdge, NetworkSnapshot, Symbol
from apps.api.db.session import AsyncSessionLocal


async def seed_sample_data() -> None:
    async with AsyncSessionLocal() as session:
        symbols_payload = [
            {"ticker": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "sector": "Technology"},
            {"ticker": "MSFT", "name": "Microsoft Corp.", "exchange": "NASDAQ", "sector": "Technology"},
            {"ticker": "NVDA", "name": "NVIDIA Corp.", "exchange": "NASDAQ", "sector": "Technology"},
            {"ticker": "AMZN", "name": "Amazon.com Inc.", "exchange": "NASDAQ", "sector": "Consumer"},
        ]

        ticker_to_symbol: dict[str, Symbol] = {}
        for item in symbols_payload:
            existing = await session.scalar(select(Symbol).where(Symbol.ticker == item["ticker"]))
            symbol = existing
            if symbol is None:
                symbol = Symbol(**item)
                session.add(symbol)
                await session.flush()
            ticker_to_symbol[item["ticker"]] = symbol

        job = Job(job_type="granger", status="completed", payload={"seed": True}, result={"snapshots": 2})
        session.add(job)
        await session.flush()

        for end_date in (date(2026, 2, 10), date(2026, 2, 11)):
            snapshot = await session.scalar(
                select(NetworkSnapshot).where(
                    NetworkSnapshot.end_date == end_date,
                    NetworkSnapshot.window_size == 30,
                    NetworkSnapshot.method == "granger",
                )
            )
            if snapshot is None:
                snapshot = NetworkSnapshot(
                    as_of_date=end_date,
                    end_date=end_date,
                    window_size=30,
                    window_start=date(2026, 1, 1),
                    window_end=end_date,
                    method="granger",
                    node_count=4,
                    edge_count=4,
                    job_id=job.id,
                    metadata_json={"seed": True},
                )
                session.add(snapshot)
                await session.flush()
            else:
                snapshot.job_id = job.id

            await session.execute(delete(NetworkEdge).where(NetworkEdge.snapshot_id == snapshot.id))

            seed_edges = [
                ("AAPL", "MSFT", "0.012000", 1),
                ("MSFT", "NVDA", "0.024000", 2),
                ("NVDA", "AMZN", "0.041000", 1),
                ("AMZN", "AAPL", "0.130000", 3),
            ]

            for src, dst, p_value, lag in seed_edges:
                edge = NetworkEdge(
                    snapshot_id=snapshot.id,
                    source_symbol_id=ticker_to_symbol[src].id,
                    target_symbol_id=ticker_to_symbol[dst].id,
                    weight=Decimal("1.000000") - Decimal(p_value),
                    p_value=Decimal(p_value),
                    lag=lag,
                )
                session.add(edge)

        await session.commit()
        print("Seeded sample symbols/network_snapshots/network_edges")


if __name__ == "__main__":
    asyncio.run(seed_sample_data())
