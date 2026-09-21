-- ============================================================
-- ARGUS-Trader — СХЕМА БД
-- ------------------------------------------------------------
-- Все таблицы модуля. Запускается один раз в Supabase SQL Editor.
-- Идемпотентно: повторный запуск не сломает существующие данные.
-- ------------------------------------------------------------
-- v1: начальная версия
-- ============================================================

-- ============================================================
-- RAW: СЫРЬЁ (retention 90 дней)
-- ============================================================

-- Свечи OHLCV (основная таблица)
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

-- Funding rate (каждые 8 часов)
CREATE TABLE IF NOT EXISTS funding_rates (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    rate         NUMERIC(20, 10),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_funding_symbol_ts
    ON funding_rates (symbol, timestamp DESC);

-- Open Interest
CREATE TABLE IF NOT EXISTS open_interest (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    oi           NUMERIC(20, 4),
    oi_value     NUMERIC(20, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

-- Long/Short ratio
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

-- Taker buy/sell volume
CREATE TABLE IF NOT EXISTS taker_flow (
    symbol       TEXT NOT NULL,
    timestamp    TIMESTAMPTZ NOT NULL,
    buy_vol      NUMERIC(20, 4),
    sell_vol     NUMERIC(20, 4),
    source       TEXT,
    inserted_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);

-- Liquidations (позже через WebSocket)
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

-- ============================================================
-- CONTEXT: КОНТЕКСТ РЫНКА (CoinGecko)
-- ============================================================

CREATE TABLE IF NOT EXISTS market_context (
    timestamp    TIMESTAMPTZ PRIMARY KEY,
    btc_mcap     NUMERIC(30, 2),
    eth_mcap     NUMERIC(30, 2),
    btc_dominance NUMERIC(10, 4),
    total_mcap   NUMERIC(30, 2),
    total_volume_24h NUMERIC(30, 2),
    btc_price_usd NUMERIC(20, 8),
    eth_price_usd NUMERIC(20, 8),
    source       TEXT DEFAULT 'coingecko',
    inserted_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- AUDIT: СЛУЖЕБНЫЕ
-- ============================================================

-- Журнал сбора
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

-- Карантин: битые/подозрительные данные
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

-- Cross-check: сверка цен между биржами
CREATE TABLE IF NOT EXISTS cross_check (
    symbol          TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    source_primary  TEXT NOT NULL,
    source_secondary TEXT NOT NULL,
    price_primary   NUMERIC(20, 8),
    price_secondary NUMERIC(20, 8),
    diff_pct        NUMERIC(10, 4),
    is_anomaly      BOOLEAN DEFAULT FALSE,
    checked_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp, source_primary, source_secondary)
);
CREATE INDEX IF NOT EXISTS idx_cross_check_anomaly
    ON cross_check (symbol, timestamp DESC) WHERE is_anomaly = TRUE;

-- Аномалии (детектированные события)
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

-- Журнал удалений (retention)
CREATE TABLE IF NOT EXISTS retention_log (
    id             SERIAL PRIMARY KEY,
    table_name     TEXT NOT NULL,
    rows_deleted   INTEGER,
    older_than     TIMESTAMPTZ,
    executed_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- PRODUCTION: ПРИЗНАКИ, ПАТТЕРНЫ, СОБЫТИЯ, ML
-- ============================================================
-- Создаём сразу, чтобы не мигрировать позже.
-- Наполняются на Фазе 2-5.

-- Признаки (по каждому часу)
CREATE TABLE IF NOT EXISTS features_hourly (
    symbol           TEXT NOT NULL,
    timestamp        TIMESTAMPTZ NOT NULL,

    -- Движение цены
    change_pct       NUMERIC(10, 4),
    range_pct        NUMERIC(10, 4),
    body_pct         NUMERIC(10, 4),
    upper_wick_pct   NUMERIC(10, 4),
    lower_wick_pct   NUMERIC(10, 4),

    -- Объём
    volume_ratio_24h NUMERIC(10, 4),

    -- Волатильность
    volatility_24h   NUMERIC(10, 4),
    volatility_7d    NUMERIC(10, 4),

    -- Тренды
    change_4h        NUMERIC(10, 4),
    change_24h       NUMERIC(10, 4),
    change_7d        NUMERIC(10, 4),

    -- Деривативы
    funding_rate     NUMERIC(20, 10),
    funding_trend    NUMERIC(20, 10),
    oi_change_pct    NUMERIC(10, 4),
    ls_ratio         NUMERIC(20, 6),
    taker_ratio      NUMERIC(10, 4),

    -- Цель для ML (что было через час)
    next_change_pct  NUMERIC(10, 4),
    next_direction   SMALLINT,

    computed_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_features_symbol_ts
    ON features_hourly (symbol, timestamp DESC);

-- 0/1 графики
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

-- События (движения > N%)
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

-- Lead indicators (что предшествовало)
CREATE TABLE IF NOT EXISTS causal_links (
    id              SERIAL PRIMARY KEY,
    event_id        INTEGER REFERENCES events(id) ON DELETE CASCADE,
    hours_before    INTEGER,
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

-- Предсказания модели
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

-- Модели (метаданные)
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

-- ============================================================
-- КОНЕЦ
-- ============================================================
-- Проверка:
--   SELECT tablename FROM pg_tables WHERE schemaname = 'public';
-- Ожидаемые таблицы: 17 штук.
-- ============================================================