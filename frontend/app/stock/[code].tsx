import { useCallback, useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';

import { Empty, Screen } from '@/components/Screen';
import { PriceChart } from '@/components/PriceChart';
import { SignalTag } from '@/components/SignalTag';
import { WatchStar } from '@/components/WatchStar';
import {
  api,
  ChartPoint,
  formatPrice,
  lastSixMonths,
  SignalItem,
  StockDetail,
} from '@/lib/api';

export default function StockScreen() {
  const { code } = useLocalSearchParams<{ code: string }>();
  const [detail, setDetail] = useState<
    (StockDetail & { points: ChartPoint[]; signals: SignalItem[]; markers: SignalItem[] }) | null
  >(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starred, setStarred] = useState(false);

  const load = useCallback(
    async (soft = false) => {
      if (!code) return;
      if (soft) setRefreshing(true);
      try {
        const [chart, watch] = await Promise.all([api.chart(code), api.watchlist()]);
        setDetail(chart);
        setStarred(watch.items.some((w) => w.stock_code === String(code).padStart(6, '0')));
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : '종목을 불러오지 못했습니다.');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [code]
  );

  useEffect(() => {
    load();
  }, [load]);

  if (!detail) {
    return <Screen loading={loading} error={error} onRetry={() => load()} />;
  }

  const points = lastSixMonths(detail.points);
  const from = points[0]?.date;
  const markers = (detail.markers ?? []).filter((m) => !from || m.signal_date >= from);
  const history = (detail.signals ?? []).slice(0, 12);

  return (
    <Screen
      loading={loading}
      error={error}
      onRetry={() => load()}
      refreshing={refreshing}
      onRefresh={() => load(true)}>
      <View style={styles.head}>
        <View style={{ flex: 1 }}>
          <Text style={styles.name}>{detail.stock_name}</Text>
          <Text style={styles.sub}>
            {detail.market} · {detail.stock_code} · {detail.date}
          </Text>
        </View>
        <WatchStar
          on={starred}
          size={24}
          onToggle={async () => {
            try {
              if (starred) await api.removeWatch(detail.stock_code);
              else await api.addWatch(detail.stock_code, true);
              setStarred(!starred);
            } catch {
              await load();
            }
          }}
        />
      </View>
      <Text style={styles.price}>{formatPrice(detail.close)}</Text>
      <Text style={styles.sub}>MA3 {formatPrice(detail.ma3)}</Text>

      <View style={styles.card}>
        <Text style={styles.legend}>6개월 주가 · 파랑 종가 · 주황 MA3</Text>
        <Text style={styles.legend}>점선 매수/매도 기준 · ▲매수 ▼매도 (최근 3개월 신호)</Text>
        <PriceChart
          points={points}
          markers={markers}
          buy1={detail.buy1}
          buy2={detail.buy2}
          sell1={detail.sell1}
          sell2={detail.sell2}
        />
      </View>

      <View style={styles.grid}>
        <Metric label="3개월 MA 최고(H)" value={formatPrice(detail.ma_high)} />
        <Metric label="3개월 MA 최저(L)" value={formatPrice(detail.ma_low)} />
        <Metric label="매도1 (H-X1)" value={formatPrice(detail.sell1)} />
        <Metric label="매도2 (H-X2)" value={formatPrice(detail.sell2)} />
        <Metric label="매수1 (L+Y1)" value={formatPrice(detail.buy1)} />
        <Metric label="매수2 (L+Y2)" value={formatPrice(detail.buy2)} />
      </View>

      <Text style={styles.section}>최근 신호</Text>
      {history.length === 0 ? (
        <Empty text="이 종목의 저장된 신호가 없습니다." />
      ) : (
        history.map((item, idx) => (
          <View
            key={`${item.signal_date}-${item.signal_type}-${item.signal_level}-${idx}`}
            style={styles.signalRow}>
            <SignalTag item={item} />
            <Text style={styles.sub}>{item.signal_date}</Text>
            <Text style={styles.sub}>MA3 {formatPrice(item.ma3)}</Text>
          </View>
        ))
      )}
    </Screen>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.sub}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  head: { flexDirection: 'row', alignItems: 'center' },
  name: { fontSize: 24, fontWeight: '700' },
  sub: { color: '#667085', fontSize: 13 },
  price: { fontSize: 32, fontWeight: '700', marginTop: 8 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#eaecf0',
    padding: 8,
    marginTop: 8,
  },
  legend: { color: '#667085', fontSize: 12, marginBottom: 4, marginLeft: 4 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  metric: {
    width: '48%',
    backgroundColor: '#fff',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#eaecf0',
    padding: 10,
  },
  metricValue: { fontWeight: '700', marginTop: 4 },
  section: { fontSize: 16, fontWeight: '700', marginTop: 8 },
  signalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: '#fff',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#eaecf0',
    padding: 10,
  },
});
