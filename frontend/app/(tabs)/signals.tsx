import { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Link, useFocusEffect } from 'expo-router';

import { Empty, Screen } from '@/components/Screen';
import { SignalTag } from '@/components/SignalTag';
import { api, formatPrice, SignalItem } from '@/lib/api';

export default function SignalsScreen() {
  const [items, setItems] = useState<SignalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (soft = false) => {
    if (soft) setRefreshing(true);
    try {
      const data = await api.signals();
      setItems(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '신호를 불러오지 못했습니다.');
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

  return (
    <Screen
      loading={loading}
      error={error}
      onRetry={() => load()}
      refreshing={refreshing}
      onRefresh={() => load(true)}>
      <Text style={styles.title}>신호 이력</Text>
      <Text style={styles.meta}>날짜 · 종목 · 매수/매도 · 단계. 같은 조건의 반복 SMS는 없습니다.</Text>
      {items.length === 0 ? (
        <Empty text="아직 저장된 신호가 없습니다." />
      ) : (
        items.map((item, idx) => (
          <Link
            key={`${item.stock_code}-${item.signal_date}-${item.signal_type}-${item.signal_level}-${idx}`}
            href={`/stock/${item.stock_code}`}
            asChild>
            <Pressable style={styles.row}>
              <SignalTag item={item} />
              <View style={{ flex: 1 }}>
                <Text style={styles.name}>{item.stock_name ?? item.stock_code}</Text>
                <Text style={styles.sub}>
                  {item.stock_code} · MA3 {formatPrice(item.ma3)}
                </Text>
              </View>
              <View style={styles.right}>
                <Text style={styles.date}>{item.signal_date}</Text>
                <Text style={styles.sub}>{item.sms_sent ? 'SMS 발송' : 'SMS 없음'}</Text>
              </View>
            </Pressable>
          </Link>
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
    gap: 10,
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#eaecf0',
    padding: 12,
  },
  name: { fontSize: 16, fontWeight: '600' },
  sub: { color: '#667085', fontSize: 12, marginTop: 2 },
  right: { alignItems: 'flex-end' },
  date: { fontWeight: '600', fontSize: 13 },
});
