## Fund-Link Setup

### 1. DBを起動

```bash
docker-compose -f infra/db/docker-compose.yml up -d
```

### 2. マイグレーションを実行

```bash
export DATABASE_URL="postgresql+asyncpg://fundlink:fundlink@localhost:5433/fundlink"
.venv/bin/alembic upgrade head
```

### 3. APIを起動

```bash
.venv/bin/python -m uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. ヘルスチェック

```bash
curl http://localhost:8000/health
```

### 5. サンプルデータ投入

```bash
.venv/bin/python -m apps.worker.scripts.seed_sample
```

### 6. 実データ取得（yfinance -> prices_daily）

```bash
PYTHONPATH=. DATABASE_URL="postgresql+asyncpg://fundlink:fundlink@localhost:5433/fundlink" \
  .venv/bin/python apps/worker/scripts/fetch_yfinance.py --start 2023-01-01 --end 2026-02-16
```

### 7. 取得件数の確認（SQL）

```bash
PGPASSWORD=fundlink psql -h localhost -p 5433 -U fundlink -d fundlink -c \
  "select s.ticker as symbol, count(*) from prices_daily p join symbols s on s.id=p.symbol_id group by 1 order by 1;"
```

### 8. features_daily 生成

```bash
PYTHONPATH=. DATABASE_URL="postgresql+asyncpg://fundlink:fundlink@localhost:5433/fundlink" \
  .venv/bin/python apps/worker/scripts/build_features.py --start 2023-01-01 --end 2026-02-16
```

### 9. features件数の確認（SQL）

```bash
PGPASSWORD=fundlink psql -h localhost -p 5433 -U fundlink -d fundlink -c \
  "select s.ticker as symbol, count(*) from features_daily f join symbols s on s.id=f.symbol_id where f.feature_set_version='v1_market_daily' group by 1 order by 1;"
```

### 10. Frames API の確認

```bash
# 単日
curl "http://localhost:8000/frames?end_date=2026-02-10&window_size=30&method=granger&p_threshold=0.05&max_lag=2&job_type=granger"

# 期間(index)
curl "http://localhost:8000/frames/range?start_date=2026-02-10&end_date=2026-02-11&window_size=30&method=granger"
```

### 11. Range -> Detail -> UI再生（コピペ）

```bash
# 1) 必要ならseedを再現可能にリセット
.venv/bin/python apps/worker/scripts/seed_sample.py --reset

# 2) 期間indexを取得（軽量）
curl -s "http://localhost:8000/frames/range?start_date=2026-02-10&end_date=2026-02-11&window_size=30&method=granger" | jq

# 3) index先頭のsnapshot_idを取り出して詳細取得
SNAPSHOT_ID=$(curl -s "http://localhost:8000/frames/range?start_date=2026-02-10&end_date=2026-02-11&window_size=30&method=granger" | jq -r '.items[0].snapshot_id')
curl -s "http://localhost:8000/frames/${SNAPSHOT_ID}" | jq
```

### 12. UI（再生ビューワ）

```bash
# フロント依存インストール
cd apps/web
npm install

# 開発サーバ起動（http://localhost:5173）
npm run dev
```

### 13. UI 動作確認

```bash
# 1) range index が取れる
curl -s "http://localhost:8000/frames/range?start_date=2026-02-10&end_date=2026-02-11&window_size=30&method=granger" | jq

# 2) items[0] の snapshot_id で detail が取れる
SNAPSHOT_ID=$(curl -s "http://localhost:8000/frames/range?start_date=2026-02-10&end_date=2026-02-11&window_size=30&method=granger" | jq -r '.items[0].snapshot_id')
curl -s "http://localhost:8000/frames/${SNAPSHOT_ID}" | jq

# 3) ブラウザで http://localhost:5173 を開き、Load -> Play で end_date とグラフ更新を確認
```

### 14. UI 操作メモ

```text
- Speed スライダー: 200ms〜1500ms で再生間隔を変更
- Space: Play/Pause
- ← / →: 1ステップ戻る / 進む
- R: 先頭に戻る（Reset）
- ΔEdges パネル: Added / Removed / Changed を前フレーム比較で表示
- ΔEdges の行クリック: 対応エッジをグラフ上でハイライト
- Graph は Added/Removed/Changed を視覚化し、Removed は薄く一瞬表示してフェードアウト
```
