import React from 'react';
import { Text, View } from 'react-native';

// 네이티브(iOS/Android)에서는 별도 차트 라이브러리 연동 전까지 안내만 표시.
// 웹에서는 StockChart.web.js (Lightweight Charts) 가 사용된다.
export default function StockChart({ data }) {
  return (
    <View
      style={{
        height: 320,
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#fafafa',
        borderRadius: 8,
      }}
    >
      <Text style={{ color: '#888' }}>
        차트는 웹에서 표시됩니다 ({data ? data.length : 0} points)
      </Text>
    </View>
  );
}
