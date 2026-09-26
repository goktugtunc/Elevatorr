import { StyleSheet, View, type ViewProps } from 'react-native';

import { colors, radius, shadow, spacing } from '@/theme';

/** Figma "Tile" / "İlan Kartı" yüzeyi — beyaz, 12px radius, ince kenarlık. */
export function Card({ style, ...rest }: ViewProps & { raised?: boolean }) {
  const { raised, ...viewProps } = rest;
  return <View {...viewProps} style={[styles.card, raised && shadow.card, style]} />;
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
  },
});
