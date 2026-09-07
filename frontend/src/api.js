import { API_BASE } from './config';

async function request(path, options) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`${options?.method || 'GET'} ${path} -> ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => request('/health'),
  top20: () => request('/top20'),
  stock: (code) => request(`/stocks/${code}`),
  chart: (code) => request(`/stocks/${code}/chart`),
  signals: () => request('/signals'),
  watchlist: () => request('/watchlist'),
  addWatchlist: (stock_code, alert_enabled = true) =>
    request('/watchlist', {
      method: 'POST',
      body: JSON.stringify({ stock_code, alert_enabled }),
    }),
};

export function formatWon(value) {
  if (value == null) return '-';
  return `${Math.round(value).toLocaleString('ko-KR')}원`;
}

export function formatTradingValue(value) {
  if (value == null) return '-';
  const eok = value / 1e8; // 억 단위
  return `${Math.round(eok).toLocaleString('ko-KR')}억`;
}
