import { StyleSheet, View } from 'react-native';
import { WebView } from 'react-native-webview';

import { ChartPoint, SignalItem } from '@/lib/api';

type Props = {
  points: ChartPoint[];
  markers: SignalItem[];
  buy1: number;
  buy2: number;
  sell1: number;
  sell2: number;
};

export function PriceChart({ points, markers, buy1, buy2, sell1, sell2 }: Props) {
  const payload = JSON.stringify({ points, markers, buy1, buy2, sell1, sell2 });
  const html = `<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
<style>html,body,#c{margin:0;height:100%;width:100%;background:#fff;}</style>
</head>
<body>
<div id="c"></div>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script>
const data = ${payload};
const chart = LightweightCharts.createChart(document.getElementById('c'), {
  autoSize: true,
  layout: { background: { color: '#ffffff' }, textColor: '#667085' },
  grid: { vertLines: { color: '#f2f4f7' }, horzLines: { color: '#f2f4f7' } },
  timeScale: { timeVisible: false }
});
const closeSeries = chart.addLineSeries({ color: '#1d4ed8', lineWidth: 2, title: '종가', priceFormat: { type: 'price', precision: 0, minMove: 1 } });
const ma3Series = chart.addLineSeries({ color: '#d97706', lineWidth: 2, title: 'MA3', priceFormat: { type: 'price', precision: 0, minMove: 1 } });
closeSeries.setData(data.points.map(p => ({ time: p.date, value: p.close })));
ma3Series.setData(data.points.filter(p => p.ma3 != null).map(p => ({ time: p.date, value: p.ma3 })));
[
  [data.sell1, '#f04438', '매도1'],
  [data.sell2, '#912018', '매도2'],
  [data.buy1, '#12b76a', '매수1'],
  [data.buy2, '#054f31', '매수2'],
].forEach(([price, color, title]) => {
  ma3Series.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
});
ma3Series.setMarkers(data.markers.map(item => {
  const buy = String(item.signal_type).toLowerCase() === 'buy';
  return {
    time: item.signal_date,
    position: buy ? 'belowBar' : 'aboveBar',
    color: buy ? '#12b76a' : '#f04438',
    shape: buy ? 'arrowUp' : 'arrowDown',
    text: (buy ? '매수' : '매도') + item.signal_level
  };
}));
chart.timeScale().fitContent();
</script>
</body>
</html>`;

  return (
    <View style={styles.box}>
      <WebView
        originWhitelist={['*']}
        source={{ html }}
        style={styles.web}
        scrollEnabled={false}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  box: { height: 280, width: '100%', overflow: 'hidden' },
  web: { flex: 1, backgroundColor: 'transparent' },
});
