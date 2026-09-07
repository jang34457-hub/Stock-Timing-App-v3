import React, { useEffect, useRef } from 'react';
import { View } from 'react-native';
import { createChart, LineSeries } from 'lightweight-charts';

// 6개월 종가 + MA3 라인차트 (웹: TradingView Lightweight Charts). 개발계획서 §10, STEP 11.
export default function StockChart({ data }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !data || data.length === 0) return undefined;

    const chart = createChart(containerRef.current, {
      height: 320,
      layout: { background: { color: '#ffffff' }, textColor: '#333' },
      grid: {
        vertLines: { color: '#f0f0f0' },
        horzLines: { color: '#f0f0f0' },
      },
      rightPriceScale: { borderColor: '#e0e0e0' },
      timeScale: { borderColor: '#e0e0e0' },
    });

    const closeSeries = chart.addSeries(LineSeries, {
      color: '#2962FF',
      lineWidth: 2,
      title: '종가',
    });
    const ma3Series = chart.addSeries(LineSeries, {
      color: '#E91E63',
      lineWidth: 2,
      title: 'MA3',
    });

    closeSeries.setData(
      data.map((p) => ({ time: p.date, value: p.close }))
    );
    ma3Series.setData(
      data
        .filter((p) => p.ma3 != null)
        .map((p) => ({ time: p.date, value: p.ma3 }))
    );
    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    handleResize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [data]);

  return <View ref={containerRef} style={{ width: '100%', height: 320 }} />;
}
