import { Pressable, StyleSheet, Text } from 'react-native';

type Props = {
  on: boolean;
  onToggle: () => void;
  size?: number;
};

export function WatchStar({ on, onToggle, size = 22 }: Props) {
  return (
    <Pressable
      onPress={onToggle}
      hitSlop={8}
      accessibilityRole="button"
      accessibilityLabel={on ? '관심종목 해제' : '관심종목 추가'}
      style={styles.hit}>
      <Text style={[styles.star, { fontSize: size, color: on ? '#f79009' : '#98a2b3' }]}>
        {on ? '★' : '☆'}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  hit: { paddingHorizontal: 10, paddingVertical: 8 },
  star: { fontWeight: '700' },
});
