-- Stock Timing App — Row Level Security
-- service_role (FastAPI 백엔드)은 RLS를 우회하므로 별도 write 정책이 필요 없습니다.
-- 앱(anon/authenticated)은 아래 정책만 적용됩니다.

ALTER TABLE public.stocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.top20_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_watchlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.signal_history ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.stocks FORCE ROW LEVEL SECURITY;
ALTER TABLE public.daily_prices FORCE ROW LEVEL SECURITY;
ALTER TABLE public.top20_history FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings FORCE ROW LEVEL SECURITY;
ALTER TABLE public.user_watchlist FORCE ROW LEVEL SECURITY;
ALTER TABLE public.signal_history FORCE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 공개 시세 데이터: 로그인한 사용자는 읽기만
-- ---------------------------------------------------------------------------
CREATE POLICY stocks_select_authenticated
  ON public.stocks
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY daily_prices_select_authenticated
  ON public.daily_prices
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY top20_history_select_authenticated
  ON public.top20_history
  FOR SELECT
  TO authenticated
  USING (true);

-- ---------------------------------------------------------------------------
-- user_settings: 본인 행만 CRUD (전화번호 포함)
-- ---------------------------------------------------------------------------
CREATE POLICY user_settings_select_own
  ON public.user_settings
  FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY user_settings_insert_own
  ON public.user_settings
  FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY user_settings_update_own
  ON public.user_settings
  FOR UPDATE
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY user_settings_delete_own
  ON public.user_settings
  FOR DELETE
  TO authenticated
  USING (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- user_watchlist: 본인 관심종목만 CRUD
-- ---------------------------------------------------------------------------
CREATE POLICY user_watchlist_select_own
  ON public.user_watchlist
  FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY user_watchlist_insert_own
  ON public.user_watchlist
  FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY user_watchlist_update_own
  ON public.user_watchlist
  FOR UPDATE
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY user_watchlist_delete_own
  ON public.user_watchlist
  FOR DELETE
  TO authenticated
  USING (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- signal_history: 본인 신호만 조회
-- 삽입/수정(sms_sent 등)은 백엔드 service_role만 수행
-- ---------------------------------------------------------------------------
CREATE POLICY signal_history_select_own
  ON public.signal_history
  FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);
