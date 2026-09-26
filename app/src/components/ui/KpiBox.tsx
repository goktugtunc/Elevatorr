import { StyleSheet, View, type ViewStyle } from 'react-native';

import { Text } from './Text';
import { colors, pnlColor, radius, spacing } from '@/theme';

/** Figma "KPI Box" — etiket + büyük numerik değer (+ opsiyonel alt satır). */
export interface KpiBoxProps {
  label: string;
  value: string;
  /** İşaretli değer verilirse renk otomatik (yeşil/kırmızı) seçilir. */
  signed?: number;
  sub?: string;
  style?: ViewStyle;
}

export function KpiBox({ label, value, signed, sub, style }: KpiBoxProps) {
  const valueColor = signed === undefined ? colors.text : pnlColor(signed);
  return (
    <View style={[styles.base, style]}>
      <Text variant="caption" color="text2">
        {label}
      </Text>
      <Text variant="numeric" color={valueColor} style={styles.value}>
        {value}
      </Text>
      {sub ? (
        <Text variant="caption" color="text3">
          {sub}
        </Text>
      ) : null}
    </View>
  );
}

/** Kart içi küçük istatistik (ör. Drawdown / Komisyon / Min. Sermaye). */
export function Stat({ label, value, signed }: { label: string; value: string; signed?: number }) {
  const valueColor = signed === undefined ? colors.text : pnlColor(signed);
  return (
    <View style={styles.stat}>
      <Text variant="caption" color="text3">
        {label}
      </Text>
      <Text variant="numericSm" color={valueColor}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.md,
    minWidth: 140,
    gap: 2,
  },
  value: { marginTop: 2 },
  stat: { gap: 2, flex: 1 },
});
