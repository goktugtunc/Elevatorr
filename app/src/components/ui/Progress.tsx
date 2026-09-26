import { StyleSheet, View, type ViewStyle } from 'react-native';

import { Text } from './Text';
import { colors, spacing } from '@/theme';

/**
 * Figma "Progress" (FE-07) — Kayıt adımları, İlan Oluştur sihirbazı ve
 * profil tamamlama çubuğu. `value` 0–1 aralığına kırpılır.
 */
export interface ProgressProps {
  value: number;
  /** Çubuğun üstünde solda görünen metin (ör. "Adım 1/2 · Rol Seçimi"). */
  label?: string;
  /** Sağda görünen metin (ör. "%50"). */
  trailing?: string;
  tone?: 'navy' | 'profit';
  style?: ViewStyle;
}

export function Progress({ value, label, trailing, tone = 'navy', style }: ProgressProps) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const fillColor = tone === 'profit' ? colors.profit : colors.navy900;

  return (
    <View
      accessibilityRole="progressbar"
      accessibilityValue={{ min: 0, max: 100, now: Math.round(clamped * 100) }}
      style={[styles.wrap, style]}
    >
      {label || trailing ? (
        <View style={styles.labels}>
          {label ? (
            <Text variant="caption" color="text2" style={styles.label}>
              {label}
            </Text>
          ) : null}
          {trailing ? (
            <Text variant="captionStrong" color="text2">
              {trailing}
            </Text>
          ) : null}
        </View>
      ) : null}
      <View style={styles.track}>
        <View style={[styles.fill, { width: `${clamped * 100}%`, backgroundColor: fillColor }]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.sm },
  labels: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  label: { flex: 1 },
  track: {
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.surfaceSunken,
    overflow: 'hidden',
  },
  fill: { height: 4, borderRadius: 2 },
});
