-- ============================================================
-- ARGUS-Trader — SCHEMA DB2 (nitroIm's Project)
-- ------------------------------------------------------------
-- v3: зеркало DB1 для крипто-таблиц.
--     + orderbook_snapshots (зеркало DB1).
--     Некрипто — только Asia (asia_market, asia_market_daily,
--     asia_alerts, asia_patterns, impact_vectors).
-- ============================================================

-- RAW: свечи
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
CREATE INDEX IF NOT EXISTS idx_candles_symbol_ts
    ON candles (symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS candles_daily (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    open         NUMERIC(20, 8),
    high         NUMERIC(20, 8),
    low          NUMERIC(20, 8),
    close        NUMERIC(20, 8),
    volume       NUMERIC(20, 8),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    rate         NUMERIC(20, 10),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS open_interest (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    oi           NUMERIC(20, 4),
    oi_value     NUMERIC(20, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS long_short_ratio (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    ls_ratio     NUMERIC(20, 6),
    long_pct     NUMERIC(10, 4),
    short_pct    NUMERIC(10, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS taker_flow (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    buy_vol      NUMERIC(20, 4),
    sell_vol     NUMERIC(20, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS liquidations (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    side         TEXT,
    price        NUMERIC(20, 8),
    quantity     NUMERIC(20, 8),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp, side, price, quantity)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    bid_vol      DOUBLE PRECISION,
    ask_vol      DOUBLE PRECISION,
    bid_pct      DOUBLE PRECISION,
    ask_pct      DOUBLE PRECISION,
    spread_pct   DOUBLE PRECISION,
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

-- ASIA (только DB2)
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

CREATE TABLE IF NOT EXISTS asia_market_daily (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    close        NUMERIC(20, 6),
    change_pct   NUMERIC(10, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

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

CREATE TABLE IF NOT EXISTS impact_vectors (
    id              SERIAL PRIMARY KEY,
    source_symbol   TEXT,
    target_symbol   TEXT,
    lag_hours       INTEGER,
    corr            NUMERIC,
    impact_pct      NUMERIC,
    samples         INTEGER,
    window_days     INTEGER,
    computed_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS asia_patterns (
    id              SERIAL PRIMARY KEY,
    source_symbol   TEXT,
    target_symbol   TEXT,
    condition_pct   NUMERIC,
    direction       TEXT,
    lag_hours       INTEGER,
    samples         INTEGER,
    hit_rate        NUMERIC,
    avg_impact_pct  NUMERIC,
    window_days     INTEGER,
    computed_at     TIMESTAMPTZ DEFAULT NOW()
);

-- AUDIT
CREATE TABLE IF NOT EXISTS collect_log (
    id             SERIAL PRIMARY KEY,
    job_name       TEXT NOT NULL,
    metric         TEXT,
    symbol         TEXT,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    records_added  INTEGER DEFAULT 0,
    source_used    TEXT,
    fallback_count INTEGER DEFAULT 0,
    status         TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_collect_log_started
    ON collect_log (started_at DESC);

CREATE TABLE IF NOT EXISTS rejected_data (
    id             SERIAL PRIMARY KEY,
    job_name       TEXT,
    metric         TEXT,
    symbol         TEXT,
    raw_data       JSONB,
    reason         TEXT,
    source         TEXT,
    rejected_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rejected_at
    ON rejected_data (rejected_at DESC);

CREATE TABLE IF NOT EXISTS cross_check (
    symbol           TEXT NOT NULL,
    timestamp        TIMESTAMPTZ NOT NULL,
    source_primary   TEXT NOT NULL,
    source_secondary TEXT NOT NULL,
    price_primary    NUMERIC(20, 8),
    price_secondary  NUMERIC(20, 8),
    diff_pct         NUMERIC(10, 4),
    is_anomaly       BOOLEAN DEFAULT FALSE,
    checked_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp, source_primary, source_secondary)
);

CREATE TABLE IF NOT EXISTS anomaly_log (
    id             SERIAL PRIMARY KEY,
    symbol         TEXT,
    timestamp      TIMESTAMPTZ,
    anomaly_type   TEXT,
    severity       TEXT,
    details        JSONB,
    notified       BOOLEAN DEFAULT FALSE,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_anomaly_ts
    ON anomaly_log (timestamp DESC);

CREATE TABLE IF NOT EXISTS retention_log (
    id             SERIAL PRIMARY KEY,
    table_name     TEXT NOT NULL,
    rows_deleted   INTEGER,
    older_than     TIMESTAMPTZ,
    executed_at    TIMESTAMPTZ DEFAULT NOW()
);

-- PRODUCTION: фичи
CREATE TABLE IF NOT EXISTS features_hourly (
    symbol            TEXT NOT NULL,
    timestamp         TIMESTAMPTZ NOT NULL,
    change_pct        NUMERIC(10, 4),
    range_pct         NUMERIC(10, 4),
    body_pct          NUMERIC(10, 4),
    upper_wick_pct    NUMERIC(10, 4),
    lower_wick_pct    NUMERIC(10, 4),
    volume_ratio_24h  NUMERIC(10, 4),
    volatility_24h    NUMERIC(10, 4),
    volatility_7d     NUMERIC(10, 4),
    change_4h         NUMERIC(10, 4),
    change_24h        NUMERIC(10, 4),
    change_7d         NUMERIC(10, 4),
    change_1d         NUMERIC(10, 4),
    change_3d         NUMERIC(10, 4),
    trend_up          SMALLINT,
    hour_of_day       SMALLINT,
    day_of_week       SMALLINT,
    funding_rate      NUMERIC(20, 10),
    funding_trend     NUMERIC(20, 10),
    oi_change_pct     NUMERIC(10, 4),
    ls_ratio          NUMERIC(20, 6),
    taker_ratio       NUMERIC(10, 4),
    ema9_dist_pct     NUMERIC(10, 4),
    ema21_dist_pct    NUMERIC(10, 4),
    ema50_dist_pct    NUMERIC(10, 4),
    macd              NUMERIC(20, 8),
    macd_signal       NUMERIC(20, 8),
    bb_upper_dist     NUMERIC(10, 4),
    bb_lower_dist     NUMERIC(10, 4),
    bb_width_pct      NUMERIC(10, 4),
    dist_high_24h_pct NUMERIC(10, 4),
    dist_low_24h_pct  NUMERIC(10, 4),
    consecutive_up    SMALLINT,
    session           SMALLINT,
    next_change_pct   NUMERIC(10, 4),
    next_direction    SMALLINT,
    computed_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_feat_symbol_ts
    ON features_hourly (symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS price_patterns (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    pattern_1h   SMALLINT,
    pattern_4h   TEXT,
    pattern_24h  TEXT,
    pattern_7d   TEXT,
    computed_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS events (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    event_type      TEXT,
    change_pct      NUMERIC(10, 4),
    magnitude       NUMERIC(10, 4),
    duration_hours  INTEGER,
    detected_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_symbol_ts
    ON events (symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS causal_links (
    event_id        INTEGER REFERENCES events(id) ON DELETE CASCADE,
    hours_before    INTEGER NOT NULL,
    funding_rate    NUMERIC(20, 10),
    oi_change_pct   NUMERIC(10, 4),
    ls_ratio        NUMERIC(20, 6),
    taker_ratio     NUMERIC(10, 4),
    volume_ratio    NUMERIC(10, 4),
    volatility      NUMERIC(10, 4),
    change_pct      NUMERIC(10, 4),
    is_anomaly      BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (event_id, hours_before)
);

CREATE TABLE IF NOT EXISTS predictions (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    predicted_dir   SMALLINT,
    confidence      NUMERIC(5, 4),
    model_version   TEXT,
    actual_dir      SMALLINT,
    was_correct     BOOLEAN,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_ts
    ON predictions (symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS ml_models (
    id              SERIAL PRIMARY KEY,
    version         TEXT UNIQUE,
    model_type      TEXT,
    trained_at      TIMESTAMPTZ,
    accuracy        NUMERIC(5, 4),
    precision_score NUMERIC(5, 4),
    recall_score    NUMERIC(5, 4),
    f1_score        NUMERIC(5, 4),
    features_count  INTEGER,
    samples_count   INTEGER,
    metadata        JSONB,
    is_active       BOOLEAN DEFAULT FALSE
);