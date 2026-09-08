-- Stock Timing App — schema
-- Apply in Supabase SQL Editor (or supabase db push) in order: 001 then 002.
-- Runtime FastAPI still uses user_id TEXT ('local') until login lands: also apply 003_runtime_compat.sql.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Helper: updated_at
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- stocks: KOSPI/KOSDAQ 종목 마스터 (가격은 TOP20만 수집)
-- ---------------------------------------------------------------------------
CREATE TABLE public.stocks (
  stock_code TEXT PRIMARY KEY,
  stock_name TEXT NOT NULL,
  market TEXT NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ')),
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_stocks_market ON public.stocks (market);
CREATE INDEX idx_stocks_active ON public.stocks (is_active) WHERE is_active = true;

CREATE TRIGGER trg_stocks_updated_at
  BEFORE UPDATE ON public.stocks
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- daily_prices: 일별 종가·거래대금·MA3 (종목+날짜 단위 UPSERT)
-- ma3는 거래일 3일이 쌓이기 전에는 NULL
-- ---------------------------------------------------------------------------
CREATE TABLE public.daily_prices (
  stock_code TEXT NOT NULL REFERENCES public.stocks (stock_code) ON DELETE RESTRICT,
  date DATE NOT NULL,
  close NUMERIC(18, 2) NOT NULL CHECK (close >= 0),
  volume BIGINT NOT NULL CHECK (volume >= 0),
  trading_value NUMERIC(20, 0) NOT NULL CHECK (trading_value >= 0),
  ma3 NUMERIC(18, 2) CHECK (ma3 IS NULL OR ma3 >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (stock_code, date)
);

CREATE INDEX idx_daily_prices_date ON public.daily_prices (date DESC);

CREATE TRIGGER trg_daily_prices_updated_at
  BEFORE UPDATE ON public.daily_prices
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- top20_history: 주 1회 선정 스냅샷 (백테스트·변동 추적)
-- ---------------------------------------------------------------------------
CREATE TABLE public.top20_history (
  selection_date DATE NOT NULL,
  stock_code TEXT NOT NULL REFERENCES public.stocks (stock_code) ON DELETE RESTRICT,
  rank SMALLINT NOT NULL CHECK (rank BETWEEN 1 AND 20),
  avg_trading_value NUMERIC(20, 2) NOT NULL CHECK (avg_trading_value >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (selection_date, stock_code),
  UNIQUE (selection_date, rank)
);

CREATE INDEX idx_top20_history_selection_date
  ON public.top20_history (selection_date DESC);

-- ---------------------------------------------------------------------------
-- user_settings: 사용자별 X1/X2, Y1/Y2, SMS 수신
-- 기본값: X1=10, X2=20, Y1=10, Y2=20
-- ---------------------------------------------------------------------------
CREATE TABLE public.user_settings (
  user_id UUID PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  phone_number TEXT,
  x1 NUMERIC(6, 2) NOT NULL DEFAULT 10 CHECK (x1 > 0),
  x2 NUMERIC(6, 2) NOT NULL DEFAULT 20 CHECK (x2 > 0),
  y1 NUMERIC(6, 2) NOT NULL DEFAULT 10 CHECK (y1 > 0),
  y2 NUMERIC(6, 2) NOT NULL DEFAULT 20 CHECK (y2 > 0),
  buy_alert BOOLEAN NOT NULL DEFAULT true,
  sell_alert BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT user_settings_x_order CHECK (x2 > x1),
  CONSTRAINT user_settings_y_order CHECK (y2 > y1)
);

CREATE TRIGGER trg_user_settings_updated_at
  BEFORE UPDATE ON public.user_settings
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

-- 회원가입 시 기본 설정 행 생성
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.user_settings (user_id)
  VALUES (NEW.id);
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();

-- ---------------------------------------------------------------------------
-- user_watchlist: 관심종목 (SMS는 alert_enabled=true 만)
-- ---------------------------------------------------------------------------
CREATE TABLE public.user_watchlist (
  user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  stock_code TEXT NOT NULL REFERENCES public.stocks (stock_code) ON DELETE RESTRICT,
  alert_enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, stock_code)
);

CREATE INDEX idx_user_watchlist_user ON public.user_watchlist (user_id);

-- ---------------------------------------------------------------------------
-- signal_history: 매수/매도 신호 + SMS 중복 방지
-- signal_type: buy | sell
-- signal_level: 1 (X1/Y1), 2 (X2/Y2)
-- reference_price: 3개월 MA3 최고(H) 또는 최저(L)
-- ---------------------------------------------------------------------------
CREATE TABLE public.signal_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  stock_code TEXT NOT NULL REFERENCES public.stocks (stock_code) ON DELETE RESTRICT,
  signal_date DATE NOT NULL,
  signal_type TEXT NOT NULL CHECK (signal_type IN ('buy', 'sell')),
  signal_level SMALLINT NOT NULL CHECK (signal_level IN (1, 2)),
  ma3 NUMERIC(18, 2) NOT NULL,
  reference_price NUMERIC(18, 2) NOT NULL,
  sms_sent BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, stock_code, signal_date, signal_type, signal_level)
);

CREATE INDEX idx_signal_history_user_date
  ON public.signal_history (user_id, signal_date DESC);

CREATE INDEX idx_signal_history_stock_date
  ON public.signal_history (stock_code, signal_date DESC);
