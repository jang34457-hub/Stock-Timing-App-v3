import { useEffect, useRef } from 'react';
import { StyleSheet, View } from 'react-native';
import { ColorType, LineStyle, createChart, IChartApi, Time } from 'lightweight-charts';

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
  const host = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!host.current) return;

    const chart = createChart(host.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#667085',
        fontFamily: 'system-ui, sans-serif',
      },
      grid: {
        vertLines: { color: '#f2f4f7' },
        horzLines: { color: '#f2f4f7' },
      },
      rightPriceScale: { borderColor: '#eaecf0' },
      timeScale: { borderColor: '#eaecf0', timeVisible: false },
      crosshair: { horzLine: { labelBackgroundColor: '#175cd3' } },
    });
    chartRef.current = chart;

    const closeSeries = chart.addLineSeries({
      color: '#1d4ed8',
      lineWidth: 2,
      title: '종가',
      priceLineVisible: false,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });
    const ma3Series = chart.addLineSeries({
      color: '#d97706',
      lineWidth: 2,
      title: 'MA3',
      priceLineVisible: false,
      priceFormat: { type: 'price', precision: 0, minMove: 1 },
    });

    const closeData = points.map((p) => ({
      time: p.date as Time,
      value: p.close,
    }));
    const ma3Data = points
      .filter((p) => p.ma3 != null)
      .map((p) => ({ time: p.date as Time, value: p.ma3 as number }));

    closeSeries.setData(closeData);
    ma3Series.setData(ma3Data);

    const lines: [number, string, string][] = [
      [sell1, '#f04438', '매도1'],
      [sell2, '#912018', '매도2'],
      [buy1, '#12b76a', '매수1'],
      [buy2, '#054f31', '매수2'],
    ];
    lines.forEach(([price, color, title]) => {
      ma3Series.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title,
      });
    });

    const markerList = markers.map((item) => {
      const buy = String(item.signal_type).toLowerCase() === 'buy';
      return {
        time: item.signal_date as Time,
        position: buy ? 'belowBar' : 'aboveBar',
        color: buy ? '#12b76a' : '#f04438',
        shape: buy ? 'arrowUp' : 'arrowDown',
        text: `${buy ? '매수' : '매도'}${item.signal_level}`,
      };
    });
    (ma3Series as { setMarkers: (m: object[]) => void }).setMarkers(markerList);
    chart.timeScale().fitContent();

    const resize = () => chart.applyOptions({ autoSize: true });
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chart.remove();
      chartRef.current = null;
    };
  }, [points, markers, buy1, buy2, sell1, sell2]);

  return <View style={styles.box}>{/* @ts-expect-error web div */}
    <div ref={host} style={{ width: '100%', height: 280 }} />
  </View>;
}

const styles = StyleSheet.create({
  box: { height: 280, width: '100%' },
});
