import { StyleSheet, Text, View } from 'react-native';

import { isBuySignal, SignalItem, signalLabel } from '@/lib/api';

export function SignalTag({ item }: { item: Pick<SignalItem, 'signal_type' | 'signal_level'> }) {
  const buy = isBuySignal(item.signal_type);
  return (
    <View style={[styles.tag, { backgroundColor: buy ? '#ecfdf3' : '#fef3f2' }]}>
      <Text style={{ color: buy ? '#067647' : '#b42318', fontWeight: '700' }}>{signalLabel(item)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  tag: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, alignSelf: 'flex-start' },
});
