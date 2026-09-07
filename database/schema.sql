-- Stock Timing App - Supabase (PostgreSQL) schema
-- 개발계획서 V3 §21 데이터베이스 설계 기준.
-- 이 파일은 Supabase SQL Editor 또는 psql 로 실행할 수 있습니다.

-- ---------------------------------------------------------------------------
-- stocks : 종목 기본정보
-- ---------------------------------------------------------------------------
create table if not exists stocks (
    stock_code  text primary key,
    stock_name  text not null,
    market      text not null check (market in ('KOSPI', 'KOSDAQ')),
    is_active   boolean not null default true,
    created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- daily_prices : 일별 가격/거래대금/MA3 (TOP20 종목만 수집)
-- ---------------------------------------------------------------------------
create table if not exists daily_prices (
    stock_code    text not null references stocks (stock_code) on delete cascade,
    date          date not null,
    close         numeric(18, 2) not null,
    volume        bigint,
    trading_value numeric(20, 2),
    ma3           numeric(18, 4),
    primary key (stock_code, date)
);

create index if not exists idx_daily_prices_date on daily_prices (date);
create index if not exists idx_daily_prices_code_date on daily_prices (stock_code, date desc);

-- ---------------------------------------------------------------------------
-- top20_history : 주간 TOP20 선정 기록
-- ---------------------------------------------------------------------------
create table if not exists top20_history (
    selection_date    date not null,
    stock_code        text not null references stocks (stock_code) on delete cascade,
    rank              integer not null check (rank between 1 and 20),
    avg_trading_value numeric(20, 2) not null,
    primary key (selection_date, stock_code)
);

create index if not exists idx_top20_history_date on top20_history (selection_date desc);

-- ---------------------------------------------------------------------------
-- user_settings : 사용자별 임계값/전화번호/알림 on-off
-- ---------------------------------------------------------------------------
create table if not exists user_settings (
    user_id      uuid primary key,
    phone_number text,
    x1           numeric(5, 2) not null default 10,  -- 매도 1차 %
    x2           numeric(5, 2) not null default 20,  -- 매도 2차 %
    y1           numeric(5, 2) not null default 10,  -- 매수 1차 %
    y2           numeric(5, 2) not null default 20,  -- 매수 2차 %
    buy_alert    boolean not null default true,
    sell_alert   boolean not null default true,
    updated_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- user_watchlist : 관심종목
-- ---------------------------------------------------------------------------
create table if not exists user_watchlist (
    user_id       uuid not null,
    stock_code    text not null references stocks (stock_code) on delete cascade,
    alert_enabled boolean not null default true,
    created_at    timestamptz not null default now(),
    primary key (user_id, stock_code)
);

-- ---------------------------------------------------------------------------
-- signal_history : 신호 발생 이력 (중복 SMS 방지에 사용)
-- ---------------------------------------------------------------------------
create table if not exists signal_history (
    id              bigint generated always as identity primary key,
    user_id         uuid not null,
    stock_code      text not null references stocks (stock_code) on delete cascade,
    signal_date     date not null,
    signal_type     text not null check (signal_type in ('BUY', 'SELL')),
    signal_level    integer not null check (signal_level in (1, 2)),
    ma3             numeric(18, 4) not null,
    reference_price numeric(18, 2) not null,
    sms_sent        boolean not null default false,
    created_at      timestamptz not null default now(),
    unique (user_id, stock_code, signal_date, signal_type, signal_level)
);

create index if not exists idx_signal_history_user on signal_history (user_id, signal_date desc);

-- ---------------------------------------------------------------------------
-- Row Level Security (개발계획서 STEP 2)
--   사용자별 데이터(user_*)는 본인 행만 접근. 시세/종목 데이터는 읽기 공개.
-- ---------------------------------------------------------------------------
alter table user_settings  enable row level security;
alter table user_watchlist enable row level security;
alter table signal_history enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where policyname = 'own_settings') then
        create policy own_settings on user_settings
            using (auth.uid() = user_id) with check (auth.uid() = user_id);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'own_watchlist') then
        create policy own_watchlist on user_watchlist
            using (auth.uid() = user_id) with check (auth.uid() = user_id);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'own_signals') then
        create policy own_signals on signal_history
            using (auth.uid() = user_id) with check (auth.uid() = user_id);
    end if;
end $$;
