-- ============================================================
-- ARGUS-Trader — SCHEMA DB2 (argus-global-data)
-- ------------------------------------------------------------
-- Узел "global": SOL/BNB + Asia markets.
-- Изолирован от основной базы. Расширяем постепенно.
-- ============================================================

-- SOL/BNB: свечи
CREATE TABLE IF NOT EXISTS candles (
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    open         NUMERIC(20, 8),
    high         NUMERIC(20, 8),
    low          NUMERIC(20, 8),
    close        NUMERIC(20, 8),
    volume       NUMERIC(20, 8),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_ts
    ON candles (symbol, timestamp DESC);

-- SOL/BNB: funding
CREATE TABLE IF NOT EXISTS funding_rates (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    rate         NUMERIC(20, 10),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_funding_ts
    ON funding_rates (symbol, timestamp DESC);

-- SOL/BNB: open interest
CREATE TABLE IF NOT EXISTS open_interest (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    oi           NUMERIC(20, 4),
    oi_value     NUMERIC(20, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_oi_ts
    ON open_interest (symbol, timestamp DESC);

-- ASIA: Nikkei, Shanghai, HangSeng, USD/CNY
CREATE TABLE IF NOT EXISTS asia_market (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    close        NUMERIC(20, 6),
    change_pct   NUMERIC(10, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_asia_ts
    ON asia_market (symbol, timestamp DESC);

-- ASIA: лог алертов (>2%)
CREATE TABLE IF NOT EXISTS asia_alerts (
    id           SERIAL PRIMARY KEY,
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    change_pct   NUMERIC(10, 4),
    direction    TEXT,
    sent_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts
    ON asia_alerts (sent_at DESC);

-- AUDIT: журнал сбора
CREATE TABLE IF NOT EXISTS collect_log (
    id             SERIAL PRIMARY KEY,
    job_name       TEXT NOT NULL,
    metric         TEXT,
    symbol         TEXT,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    records_added  INTEGER DEFAULT 0,
    source_used    TEXT,
    status         TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_glog_started
    ON collect_log (started_at DESC);