-- STEP 18-1: make production Postgres match FastAPI / jobs (SQLite prices.db).
-- Empty project: creates the six app tables.
-- After 001_schema.sql: adds missing columns, relaxes CHECKs, TEXT user_id for user_id='local'.
-- Does not touch backtest SQLite.

CREATE TABLE IF NOT EXISTS public.stocks (
  stock_code TEXT PRIMARY KEY,
  stock_name TEXT NOT NULL DEFAULT '',
  market TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS public.daily_prices (
  stock_code TEXT NOT NULL,
  date DATE NOT NULL,
  close NUMERIC NOT NULL,
  volume BIGINT NOT NULL DEFAULT 0,
  trading_value NUMERIC NOT NULL DEFAULT 0,
  ma3 NUMERIC,
  PRIMARY KEY (stock_code, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON public.daily_prices (date);
CREATE INDEX IF NOT EXISTS idx_daily_prices_code_date ON public.daily_prices (stock_code, date);

CREATE TABLE IF NOT EXISTS public.top20_history (
  selection_date DATE NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT NOT NULL DEFAULT '',
  market TEXT NOT NULL DEFAULT '',
  rank INTEGER NOT NULL,
  avg_trading_value NUMERIC NOT NULL DEFAULT 0,
  window_start DATE,
  trading_days INTEGER,
  PRIMARY KEY (selection_date, stock_code)
);

CREATE INDEX IF NOT EXISTS idx_top20_history_date ON public.top20_history (selection_date);

CREATE TABLE IF NOT EXISTS public.user_settings (
  user_id TEXT PRIMARY KEY,
  phone_number TEXT,
  x1 NUMERIC NOT NULL DEFAULT 10,
  x2 NUMERIC NOT NULL DEFAULT 20,
  y1 NUMERIC NOT NULL DEFAULT 10,
  y2 NUMERIC NOT NULL DEFAULT 20,
  buy_alert BOOLEAN NOT NULL DEFAULT TRUE,
  sell_alert BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS public.user_watchlist (
  user_id TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  alert_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS public.signal_history (
  id BIGSERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  signal_date DATE NOT NULL,
  signal_type TEXT NOT NULL,
  signal_level INTEGER NOT NULL,
  ma3 NUMERIC NOT NULL,
  reference_price NUMERIC NOT NULL,
  threshold NUMERIC NOT NULL DEFAULT 0,
  sms_sent BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, stock_code, signal_date, signal_type, signal_level)
);

CREATE INDEX IF NOT EXISTS idx_signal_history_user_date
  ON public.signal_history (user_id, signal_date DESC);

-- --- Compat with 001_schema.sql (UUID auth.users, lowercase signal_type, no threshold) ---

ALTER TABLE public.stocks ALTER COLUMN stock_name SET DEFAULT '';
ALTER TABLE public.stocks ALTER COLUMN market SET DEFAULT '';

ALTER TABLE public.top20_history ADD COLUMN IF NOT EXISTS stock_name TEXT NOT NULL DEFAULT '';
ALTER TABLE public.top20_history ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT '';
ALTER TABLE public.top20_history ADD COLUMN IF NOT EXISTS window_start DATE;
ALTER TABLE public.top20_history ADD COLUMN IF NOT EXISTS trading_days INTEGER;

ALTER TABLE public.user_settings ADD COLUMN IF NOT EXISTS phone_number TEXT;
ALTER TABLE public.user_settings ADD COLUMN IF NOT EXISTS x1 NUMERIC NOT NULL DEFAULT 10;
ALTER TABLE public.user_settings ADD COLUMN IF NOT EXISTS x2 NUMERIC NOT NULL DEFAULT 20;
ALTER TABLE public.user_settings ADD COLUMN IF NOT EXISTS y1 NUMERIC NOT NULL DEFAULT 10;
ALTER TABLE public.user_settings ADD COLUMN IF NOT EXISTS y2 NUMERIC NOT NULL DEFAULT 20;
ALTER TABLE public.user_settings ADD COLUMN IF NOT EXISTS buy_alert BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE public.user_settings ADD COLUMN IF NOT EXISTS sell_alert BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE public.user_watchlist ADD COLUMN IF NOT EXISTS alert_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE public.user_watchlist ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE public.signal_history ADD COLUMN IF NOT EXISTS threshold NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE public.signal_history ADD COLUMN IF NOT EXISTS sms_sent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.signal_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE public.stocks DROP CONSTRAINT IF EXISTS stocks_market_check;
ALTER TABLE public.signal_history DROP CONSTRAINT IF EXISTS signal_history_signal_type_check;
ALTER TABLE public.signal_history DROP CONSTRAINT IF EXISTS signal_history_signal_level_check;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'user_settings'
      AND column_name = 'user_id' AND data_type = 'uuid'
  ) THEN
    ALTER TABLE public.user_watchlist DROP CONSTRAINT IF EXISTS user_watchlist_user_id_fkey;
    ALTER TABLE public.user_watchlist DROP CONSTRAINT IF EXISTS user_watchlist_stock_code_fkey;
    ALTER TABLE public.signal_history DROP CONSTRAINT IF EXISTS signal_history_user_id_fkey;
    ALTER TABLE public.signal_history DROP CONSTRAINT IF EXISTS signal_history_stock_code_fkey;
    ALTER TABLE public.user_settings DROP CONSTRAINT IF EXISTS user_settings_user_id_fkey;
    ALTER TABLE public.user_settings DROP CONSTRAINT IF EXISTS user_settings_pkey;

    ALTER TABLE public.user_settings ALTER COLUMN user_id TYPE TEXT USING user_id::text;
    ALTER TABLE public.user_settings ADD PRIMARY KEY (user_id);
    ALTER TABLE public.user_watchlist ALTER COLUMN user_id TYPE TEXT USING user_id::text;
    ALTER TABLE public.signal_history ALTER COLUMN user_id TYPE TEXT USING user_id::text;
  END IF;
END $$;
