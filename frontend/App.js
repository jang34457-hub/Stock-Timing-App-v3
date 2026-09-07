import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { api, formatTradingValue, formatWon } from './src/api';
import StockChart from './src/StockChart';

const COLORS = {
  bg: '#0f172a',
  card: '#ffffff',
  primary: '#2563eb',
  buy: '#dc2626',
  sell: '#2563eb',
  text: '#0f172a',
  sub: '#64748b',
  gold: '#f59e0b',
};

function Badge({ label, color }) {
  return (
    <View style={[styles.badge, { backgroundColor: `${color}22` }]}>
      <Text style={[styles.badgeText, { color }]}>{label}</Text>
    </View>
  );
}

function Top20Screen({ watchlistCodes, onSelect }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .top20()
      .then(setItems)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorView message={error} />;
  if (!items) return <Loading />;

  return (
    <ScrollView contentContainerStyle={styles.listContent}>
      <Text style={styles.sectionTitle}>거래대금 상위 TOP20</Text>
      <Text style={styles.sectionSub}>최근 3개월 일평균 거래대금 기준</Text>
      {items.map((item) => {
        const starred = watchlistCodes.includes(item.stock_code);
        return (
          <Pressable
            key={item.stock_code}
            style={styles.row}
            onPress={() => onSelect(item.stock_code)}
          >
            <Text style={styles.rank}>{item.rank}</Text>
            <View style={{ flex: 1 }}>
              <View style={styles.rowTitle}>
                <Text style={styles.stockName}>
                  {starred ? '★ ' : ''}
                  {item.stock_name}
                </Text>
                <Badge
                  label={item.market}
                  color={item.market === 'KOSPI' ? COLORS.primary : COLORS.gold}
                />
              </View>
              <Text style={styles.stockCode}>{item.stock_code}</Text>
            </View>
            <Text style={styles.tradingValue}>
              {formatTradingValue(item.avg_trading_value)}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

function DetailScreen({ code, onBack }) {
  const [detail, setDetail] = useState(null);
  const [chart, setChart] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api.stock(code), api.chart(code)])
      .then(([d, c]) => {
        setDetail(d);
        setChart(c);
      })
      .catch((e) => setError(e.message));
  }, [code]);

  if (error) return <ErrorView message={error} />;
  if (!detail || !chart) return <Loading />;

  return (
    <ScrollView contentContainerStyle={styles.listContent}>
      <Pressable onPress={onBack} style={styles.backBtn}>
        <Text style={styles.backText}>← TOP20</Text>
      </Pressable>
      <Text style={styles.detailName}>{detail.stock_name}</Text>
      <Text style={styles.stockCode}>
        {detail.stock_code} · {detail.market}
      </Text>

      <View style={styles.statsRow}>
        <Stat label="종가" value={formatWon(detail.latest_close)} />
        <Stat label="MA3" value={formatWon(detail.latest_ma3)} />
      </View>

      <View style={styles.chartCard}>
        <Text style={styles.cardTitle}>6개월 차트 (종가 · MA3)</Text>
        <StockChart data={chart} />
      </View>

      <View style={styles.levelsCard}>
        <Text style={styles.cardTitle}>최근 3개월 신호 기준</Text>
        <LevelRow
          label="MA3 최고 / 최저"
          value={`${formatWon(detail.ma3_high_3m)} / ${formatWon(
            detail.ma3_low_3m
          )}`}
        />
        <LevelRow
          label="매도 기준 (X1 10% / X2 20%)"
          value={detail.sell_levels.map(formatWon).join(' / ')}
          color={COLORS.sell}
        />
        <LevelRow
          label="매수 기준 (Y1 10% / Y2 20%)"
          value={detail.buy_levels.map(formatWon).join(' / ')}
          color={COLORS.buy}
        />
      </View>
    </ScrollView>
  );
}

function SignalsScreen() {
  const [signals, setSignals] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .signals()
      .then(setSignals)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorView message={error} />;
  if (!signals) return <Loading />;

  return (
    <ScrollView contentContainerStyle={styles.listContent}>
      <Text style={styles.sectionTitle}>매수 · 매도 신호</Text>
      <Text style={styles.sectionSub}>관심종목 대상 · 돌파 시점만 기록</Text>
      {signals.length === 0 && (
        <Text style={styles.sectionSub}>발생한 신호가 없습니다.</Text>
      )}
      {signals.map((s, i) => {
        const isBuy = s.signal_type === 'BUY';
        return (
          <View key={`${s.stock_code}-${i}`} style={styles.row}>
            <Badge
              label={`${isBuy ? '매수' : '매도'}${s.signal_level}`}
              color={isBuy ? COLORS.buy : COLORS.sell}
            />
            <View style={{ flex: 1, marginLeft: 12 }}>
              <Text style={styles.stockName}>{s.stock_name}</Text>
              <Text style={styles.stockCode}>{s.signal_date}</Text>
            </View>
            <View style={{ alignItems: 'flex-end' }}>
              <Text style={styles.tradingValue}>{formatWon(s.ma3)}</Text>
              <Text style={styles.stockCode}>
                기준 {formatWon(s.reference_price)}
              </Text>
            </View>
          </View>
        );
      })}
    </ScrollView>
  );
}

function Stat({ label, value }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={styles.statValue}>{value}</Text>
    </View>
  );
}

function LevelRow({ label, value, color }) {
  return (
    <View style={styles.levelRow}>
      <Text style={styles.levelLabel}>{label}</Text>
      <Text style={[styles.levelValue, color && { color }]}>{value}</Text>
    </View>
  );
}

function Loading() {
  return (
    <View style={styles.center}>
      <ActivityIndicator size="large" color={COLORS.primary} />
    </View>
  );
}

function ErrorView({ message }) {
  return (
    <View style={styles.center}>
      <Text style={{ color: COLORS.buy, textAlign: 'center' }}>
        백엔드 연결 오류{'\n'}
        {message}
      </Text>
      <Text style={[styles.sectionSub, { marginTop: 8 }]}>
        FastAPI 서버(:8000)가 실행 중인지 확인하세요.
      </Text>
    </View>
  );
}

export default function App() {
  const [tab, setTab] = useState('top20');
  const [selectedCode, setSelectedCode] = useState(null);
  const [watchlistCodes, setWatchlistCodes] = useState([]);

  useEffect(() => {
    api
      .watchlist()
      .then((w) => setWatchlistCodes(w.map((x) => x.stock_code)))
      .catch(() => setWatchlistCodes([]));
  }, []);

  const goDetail = useCallback((code) => setSelectedCode(code), []);
  const goBack = useCallback(() => setSelectedCode(null), []);

  let content;
  if (selectedCode) {
    content = <DetailScreen code={selectedCode} onBack={goBack} />;
  } else if (tab === 'top20') {
    content = (
      <Top20Screen watchlistCodes={watchlistCodes} onSelect={goDetail} />
    );
  } else {
    content = <SignalsScreen />;
  }

  return (
    <View style={styles.app}>
      <StatusBar style="light" />
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Stock Timing</Text>
        <Text style={styles.headerSub}>KOSPI · KOSDAQ MA3 신호</Text>
      </View>

      <View style={styles.body}>{content}</View>

      <View style={styles.tabBar}>
        <TabButton
          label="TOP20"
          active={!selectedCode && tab === 'top20'}
          onPress={() => {
            setSelectedCode(null);
            setTab('top20');
          }}
        />
        <TabButton
          label="신호"
          active={!selectedCode && tab === 'signals'}
          onPress={() => {
            setSelectedCode(null);
            setTab('signals');
          }}
        />
      </View>
    </View>
  );
}

function TabButton({ label, active, onPress }) {
  return (
    <Pressable style={styles.tabButton} onPress={onPress}>
      <Text style={[styles.tabLabel, active && styles.tabLabelActive]}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: '#f1f5f9' },
  header: {
    backgroundColor: COLORS.bg,
    paddingTop: 48,
    paddingBottom: 16,
    paddingHorizontal: 20,
  },
  headerTitle: { color: '#fff', fontSize: 22, fontWeight: '700' },
  headerSub: { color: '#94a3b8', fontSize: 13, marginTop: 2 },
  body: { flex: 1 },
  listContent: { padding: 16, paddingBottom: 32 },
  sectionTitle: { fontSize: 18, fontWeight: '700', color: COLORS.text },
  sectionSub: { fontSize: 13, color: COLORS.sub, marginBottom: 12, marginTop: 2 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    shadowColor: '#000',
    shadowOpacity: 0.05,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 1,
  },
  rank: {
    width: 28,
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
    textAlign: 'center',
    marginRight: 8,
  },
  rowTitle: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  stockName: { fontSize: 16, fontWeight: '600', color: COLORS.text },
  stockCode: { fontSize: 12, color: COLORS.sub, marginTop: 2 },
  tradingValue: { fontSize: 15, fontWeight: '600', color: COLORS.text },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  badgeText: { fontSize: 11, fontWeight: '700' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  backBtn: { marginBottom: 8 },
  backText: { color: COLORS.primary, fontSize: 15, fontWeight: '600' },
  detailName: { fontSize: 24, fontWeight: '800', color: COLORS.text },
  statsRow: { flexDirection: 'row', gap: 12, marginTop: 16 },
  stat: {
    flex: 1,
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
  },
  statLabel: { fontSize: 12, color: COLORS.sub },
  statValue: { fontSize: 18, fontWeight: '700', color: COLORS.text, marginTop: 4 },
  chartCard: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    marginTop: 16,
  },
  cardTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: COLORS.text,
    marginBottom: 10,
  },
  levelsCard: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    marginTop: 16,
  },
  levelRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#e2e8f0',
  },
  levelLabel: { fontSize: 13, color: COLORS.sub, flex: 1 },
  levelValue: { fontSize: 14, fontWeight: '600', color: COLORS.text },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: COLORS.card,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#e2e8f0',
    paddingBottom: 8,
  },
  tabButton: { flex: 1, alignItems: 'center', paddingVertical: 14 },
  tabLabel: { fontSize: 14, color: COLORS.sub, fontWeight: '600' },
  tabLabelActive: { color: COLORS.primary },
});
