import { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Switch, Text, View } from 'react-native';
import { Link, useFocusEffect } from 'expo-router';

import { Empty, Screen } from '@/components/Screen';
import { WatchStar } from '@/components/WatchStar';
import { api, WatchItem } from '@/lib/api';

export default function WatchlistScreen() {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (soft = false) => {
    if (soft) setRefreshing(true);
    try {
      const data = await api.watchlist();
      setItems(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '관심종목을 불러오지 못했습니다.');
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

  async function toggleSms(item: WatchItem, alert_enabled: boolean) {
    setItems((prev) =>
      prev.map((row) => (row.stock_code === item.stock_code ? { ...row, alert_enabled } : row))
    );
    try {
      await api.addWatch(item.stock_code, alert_enabled);
    } catch {
      await load();
    }
  }

  async function remove(code: string) {
    try {
      await api.removeWatch(code);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '관심종목을 해제하지 못했습니다.');
    }
  }

  return (
    <Screen
      loading={loading}
      error={error}
      onRetry={() => load()}
      refreshing={refreshing}
      onRefresh={() => load(true)}>
      <Text style={styles.title}>관심종목</Text>
      <Text style={styles.meta}>별은 관심 해제, 스위치는 종목별 SMS입니다.</Text>
      {items.length === 0 ? (
        <Empty text="홈에서 ☆를 눌러 관심종목을 추가하세요." />
      ) : (
        items.map((item) => (
          <View key={item.stock_code} style={styles.row}>
            <Link href={`/stock/${item.stock_code}`} asChild>
              <Pressable style={{ flex: 1 }}>
                <Text style={styles.name}>{item.stock_name}</Text>
                <Text style={styles.sub}>
                  {item.market} · {item.stock_code}
                </Text>
              </Pressable>
            </Link>
            <View style={styles.sms}>
              <Text style={styles.smsLabel}>{item.alert_enabled ? 'SMS ON' : 'SMS OFF'}</Text>
              <Switch
                value={item.alert_enabled}
                onValueChange={(on) => toggleSms(item, on)}
                accessibilityLabel={`${item.stock_name} SMS`}
              />
            </View>
            <WatchStar on onToggle={() => remove(item.stock_code)} />
          </View>
        ))
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 28, fontWeight: '700' },
  meta: { color: '#667085', marginBottom: 8 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#eaecf0',
    padding: 12,
    gap: 8,
  },
  name: { fontSize: 16, fontWeight: '600' },
  sub: { color: '#667085', fontSize: 12, marginTop: 2 },
  sms: { alignItems: 'center', minWidth: 76 },
  smsLabel: { color: '#175cd3', fontWeight: '600', fontSize: 11, marginBottom: 2 },
});
