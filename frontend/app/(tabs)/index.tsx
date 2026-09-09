import { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Link, useFocusEffect } from 'expo-router';

import { Empty, Screen } from '@/components/Screen';
import { SignalTag } from '@/components/SignalTag';
import { WatchStar } from '@/components/WatchStar';
import { api, formatPrice, formatWon, getApiUrl, SignalItem, Top20Item, WatchItem } from '@/lib/api';

export default function HomeScreen() {
  const [items, setItems] = useState<Top20Item[]>([]);
  const [recent, setRecent] = useState<SignalItem[]>([]);
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [meta, setMeta] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (soft = false) => {
    setError(null);
    if (soft) setRefreshing(true);
    try {
      await api.sync().catch(() => undefined);
      const [top, watch] = await Promise.all([api.top20(), api.watchlist()]);
      setItems(top.items);
      setRecent(top.recent_signals ?? []);
      setWatched(new Set(watch.items.map((w: WatchItem) => w.stock_code)));
      setMeta(`${top.selection_date} · ${top.trading_days}거래일`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '목록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  async function toggleStar(code: string) {
    const on = watched.has(code);
    setWatched((prev) => {
      const next = new Set(prev);
      if (on) next.delete(code);
      else next.add(code);
      return next;
    });
    try {
      if (on) await api.removeWatch(code);
      else await api.addWatch(code, true);
    } catch {
      await load();
    }
  }

  return (
    <Screen
      loading={loading}
      error={error}
      onRetry={() => load()}
      refreshing={refreshing}
      onRefresh={() => load(true)}>
      <Text style={styles.kicker}>서버 {getApiUrl()}</Text>
      <Text style={styles.title}>홈</Text>
      <Text style={styles.meta}>{meta || 'TOP20 선정일'}</Text>

      <Text style={styles.section}>최근 신호</Text>
      {recent.length === 0 ? (
        <Empty text="아직 최근 신호가 없습니다." />
      ) : (
        recent.map((item, idx) => (
          <Link
            key={`${item.stock_code}-${item.signal_date}-${idx}`}
            href={`/stock/${item.stock_code}`}
            asChild>
            <Pressable style={styles.signalRow}>
              <SignalTag item={item} />
              <View style={{ flex: 1 }}>
                <Text style={styles.name}>{item.stock_name ?? item.stock_code}</Text>
                <Text style={styles.sub}>{item.signal_date}</Text>
              </View>
            </Pressable>
          </Link>
        ))
      )}

      <Text style={styles.section}>TOP20</Text>
      {items.length === 0 ? (
        <Empty text="아직 선정된 종목이 없습니다. API 서버를 확인하세요." />
      ) : (
        items.map((item) => {
          const on = watched.has(item.stock_code);
          return (
            <View key={item.stock_code} style={styles.row}>
              <Link href={`/stock/${item.stock_code}`} asChild>
                <Pressable style={styles.main}>
                  <Text style={styles.rank}>{item.rank}</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.name}>{item.stock_name}</Text>
                    <Text style={styles.sub}>
                      {item.market} · {item.stock_code}
                    </Text>
                    <Text style={styles.quote}>
                      현재가 {formatPrice(item.close)} · MA3 {formatPrice(item.ma3)}
                    </Text>
                    {item.last_signal ? (
                      <Text style={styles.sub}>
                        최근 신호 {item.last_signal.signal_date} ·{' '}
                        {item.last_signal.signal_type.toUpperCase()}
                        {item.last_signal.signal_level}
                      </Text>
                    ) : (
                      <Text style={styles.sub}>최근 신호 없음</Text>
                    )}
                  </View>
                  <Text style={styles.value}>{formatWon(item.avg_trading_value)}</Text>
                </Pressable>
              </Link>
              <WatchStar on={on} onToggle={() => toggleStar(item.stock_code)} />
            </View>
          );
        })
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  kicker: { color: '#667085', fontSize: 13 },
  title: { fontSize: 28, fontWeight: '700' },
  meta: { color: '#667085' },
  section: { fontSize: 16, fontWeight: '700', marginTop: 8 },
  signalRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#eaecf0',
    padding: 12,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#eaecf0',
    paddingVertical: 10,
    paddingLeft: 12,
  },
  main: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 10 },
  rank: { width: 28, fontWeight: '700', color: '#175cd3' },
  name: { fontSize: 16, fontWeight: '600' },
  sub: { color: '#667085', fontSize: 12, marginTop: 2 },
  quote: { color: '#344054', fontSize: 13, marginTop: 4, fontWeight: '600' },
  value: { fontWeight: '600', fontSize: 12 },
});
