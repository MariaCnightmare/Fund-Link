## Fund-Link Setup

### 1. DBを起動

```bash
docker compose -f infra/db/docker-compose.yml up -d
```

### 2. マイグレーションを実行

```bash
export DATABASE_URL="postgresql+asyncpg://fundlink:fundlink@localhost:5432/fundlink"
alembic upgrade head
```

### 3. APIを起動

```bash
uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. ヘルスチェック

```bash
curl http://localhost:8000/health
```

### 5. サンプルデータ投入

```bash
python -m apps.worker.scripts.seed_sample
```

### 6. Frames API の確認

```bash
# 単日
curl "http://localhost:8000/frames?end_date=2026-02-10&window_size=30&p_threshold=0.05&max_lag=2&job_type=granger"

# 期間
curl "http://localhost:8000/frames/range?start_date=2026-02-10&end_date=2026-02-11&window_size=30&p_threshold=0.05&max_lag=2&job_type=granger"
```
