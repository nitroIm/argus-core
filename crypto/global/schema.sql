-- ============================================================
-- ARGUS-Trader — SCHEMA DB2 (argus-global-data)
-- ------------------------------------------------------------
-- v2: + features_hourly (38 cols, синхрон с features.py v7),
--     price_patterns, events, causal_links, predictions,
--     ml_models. Зеркало DB1 для будущего совместного learn.
--     Типы новых колонок — по аналогии с DB1 v2 (проверить).
-- v1: SOL/BNB + Asia markets. Изолирован от основной.
-- ============================================================

-- RAW: SOL/BNB свечи
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

-- RAW: SOL/BNB funding
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

-- RAW: SOL/BNB open interest
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

-- PRODUCTION: признаки (синхрон с features.py v7)
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

-- PRODUCTION: паттерны
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

-- PRODUCTION: события
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

-- PRODUCTION: причинные связи
CREATE TABLE IF NOT EXISTS causal_links (
    event_id        INTEGER
                    REFERENCES events(id) ON DELETE CASCADE,
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

-- PRODUCTION: предсказания
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
CREATE INDEX IF NOT EXISTS idx_pred_symbol_ts
    ON predictions (symbol, timestamp DESC);

-- PRODUCTION: реестр ML-моделей
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