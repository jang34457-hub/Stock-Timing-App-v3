import { ReactNode } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

export function Screen({
  children,
  loading,
  error,
  onRetry,
  refreshing,
  onRefresh,
}: {
  children?: ReactNode;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  refreshing?: boolean;
  onRefresh?: () => void;
}) {
  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
        <Text style={styles.muted}>불러오는 중</Text>
      </View>
    );
  }
  if (error && !children) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error}</Text>
        {onRetry ? (
          <Pressable onPress={onRetry} style={styles.retry}>
            <Text style={styles.retryText}>다시 시도</Text>
          </Pressable>
        ) : null}
      </View>
    );
  }
  return (
    <ScrollView
      contentContainerStyle={styles.body}
      refreshControl={
        onRefresh ? <RefreshControl refreshing={!!refreshing} onRefresh={onRefresh} /> : undefined
      }>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {children}
    </ScrollView>
  );
}

export function Empty({ text }: { text: string }) {
  return <Text style={styles.muted}>{text}</Text>;
}

const styles = StyleSheet.create({
  body: {
    padding: 16,
    gap: 10,
    maxWidth: 520,
    width: '100%',
    alignSelf: 'center',
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    gap: 8,
  },
  muted: { color: '#667085', fontSize: 14 },
  error: { color: '#b42318', textAlign: 'center' },
  retry: {
    marginTop: 8,
    borderWidth: 1,
    borderColor: '#175cd3',
    borderRadius: 10,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  retryText: { color: '#175cd3', fontWeight: '600' },
});
