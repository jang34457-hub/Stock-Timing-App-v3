import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_URL = 'sta.apiUrl';
const STORAGE_KEY = 'sta.apiKey';
export const USER_ID = 'local';

function normalizeBase(url: string): string {
  return url.trim().replace(/\/+$/, '');
}

function envDefaultUrl(): string {
  return normalizeBase(process.env.EXPO_PUBLIC_API_URL ?? 'http://127.0.0.1:8000');
}

let apiUrl = envDefaultUrl();
let apiKey = (process.env.EXPO_PUBLIC_API_SECRET_KEY ?? '').trim();

export function getApiUrl(): string {
  return apiUrl;
}

export function getApiKey(): string {
  return apiKey;
}

export async function loadApiConfig(): Promise<void> {
  try {
    const [storedUrl, storedKey] = await Promise.all([
      AsyncStorage.getItem(STORAGE_URL),
      AsyncStorage.getItem(STORAGE_KEY),
    ]);
    if (storedUrl) apiUrl = normalizeBase(storedUrl);
    if (storedKey != null) apiKey = storedKey.trim();
  } catch {
    // web private mode etc.
  }
}

export async function saveApiConfig(url: string, key?: string): Promise<void> {
  apiUrl = normalizeBase(url || envDefaultUrl());
  await AsyncStorage.setItem(STORAGE_URL, apiUrl);
  if (key !== undefined) {
    apiKey = key.trim();
    await AsyncStorage.setItem(STORAGE_KEY, apiKey);
  }
}

function friendlyError(err: unknown, fallback: string): Error {
  if (err instanceof TypeError || (err instanceof Error && err.name === 'AbortError')) {
    return new Error(
      `서버에 연결하지 못했습니다 (${apiUrl}). 같은 Wi-Fi인지, 설정에서 PC LAN 주소가 맞는지 확인하세요.`
    );
  }
  if (err instanceof Error) return err;
  return new Error(fallback);
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 120_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${apiUrl}${path}`, {
      ...init,
      signal: init.signal ?? controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-User-Id': USER_ID,
        ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        ...(init.headers ?? {}),
      },
    });
  } catch (err) {
    throw friendlyError(err, '요청에 실패했습니다.');
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json() as Promise<T>;
}

export async function pingHealth(): Promise<{ ok: boolean; env?: string }> {
  return request('/health');
}

export type Top20Item = {
  rank: number;
  stock_code: string;
  stock_name: string;
  market: string;
  avg_trading_value: number;
  date?: string | null;
  close?: number | null;
  ma3?: number | null;
  last_signal?: SignalItem | null;
};

export type Top20Response = {
  selection_date: string;
  window_start: string;
  trading_days: number;
  items: Top20Item[];
  recent_signals?: SignalItem[];
};

export type WatchItem = {
  stock_code: string;
  stock_name: string;
  market: string;
  alert_enabled: boolean;
};

export type StockDetail = {
  stock_code: string;
  stock_name: string;
  market: string;
  date: string;
  close: number;
  volume: number;
  ma3: number | null;
  ma_high: number;
  ma_low: number;
  sell1: number;
  sell2: number;
  buy1: number;
  buy2: number;
};

export type ChartPoint = {
  date: string;
  close: number;
  volume: number;
  ma3: number | null;
};

export type SignalItem = {
  stock_code: string;
  stock_name?: string;
  signal_date: string;
  signal_type: string;
  signal_level: number;
  ma3: number;
  reference_price: number;
  threshold: number;
  sms_sent?: boolean;
};

export type Settings = {
  user_id: string;
  phone_number: string | null;
  x1: number;
  x2: number;
  y1: number;
  y2: number;
  buy_alert: boolean;
  sell_alert: boolean;
};

export const api = {
  sync: () =>
    request<{ ok: boolean; skipped?: string; session?: string; status?: string }>(
      '/jobs/sync',
      { method: 'POST' },
      180_000
    ),
  top20: () => request<Top20Response>('/top20', {}, 180_000),
  stock: (code: string) => request<StockDetail>(`/stocks/${code}`),
  chart: (code: string) =>
    request<
      StockDetail & { points: ChartPoint[]; signals: SignalItem[]; markers: SignalItem[] }
    >(`/stocks/${code}/chart`),
  watchlist: () => request<{ items: WatchItem[] }>('/watchlist'),
  addWatch: (stock_code: string, alert_enabled = true) =>
    request<WatchItem>('/watchlist', {
      method: 'POST',
      body: JSON.stringify({ stock_code, alert_enabled }),
    }),
  removeWatch: (stock_code: string) =>
    request<{ ok: boolean; stock_code: string }>(`/watchlist/${stock_code}`, {
      method: 'DELETE',
    }),
  signals: () => request<{ items: SignalItem[] }>('/signals'),
  getSettings: () => request<Settings>('/settings'),
  saveSettings: (body: Partial<Settings>) =>
    request<Settings>('/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
    testSms: (phone_number?: string, live = false) =>
    request<{ ok: boolean; status: string; to: string; text: string; provider: string }>(
      '/sms/test',
      {
        method: 'POST',
        body: JSON.stringify(phone_number ? { phone_number, live } : { live }),
      }
    ),
};

export function formatWon(value: number): string {
  if (value >= 1_000_000_000_000) {
    return `${(value / 1_000_000_000_000).toFixed(2)}조`;
  }
  if (value >= 100_000_000) {
    return `${(value / 100_000_000).toFixed(1)}억`;
  }
  return value.toLocaleString('ko-KR');
}

export function formatPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-';
  return value.toLocaleString('ko-KR');
}

export function isBuySignal(type: string | undefined): boolean {
  return String(type ?? '').toLowerCase() === 'buy';
}

export function signalLabel(item: Pick<SignalItem, 'signal_type' | 'signal_level'>): string {
  return `${isBuySignal(item.signal_type) ? '매수' : '매도'}${item.signal_level}`;
}

export function lastSixMonths<T extends { date: string }>(points: T[]): T[] {
  if (!points.length) return points;
  const [y, m, d] = points[points.length - 1].date.split('-').map(Number);
  const end = new Date(y, m - 1, d);
  const start = new Date(end);
  start.setMonth(start.getMonth() - 6);
  const from = [
    start.getFullYear(),
    String(start.getMonth() + 1).padStart(2, '0'),
    String(start.getDate()).padStart(2, '0'),
  ].join('-');
  return points.filter((p) => p.date >= from);
}
