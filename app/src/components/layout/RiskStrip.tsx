import { AlertTriangle } from 'lucide-react-native';
import { StyleSheet, View } from 'react-native';

import { Text } from '@/components/ui';
import { RISK_DISCLAIMER, colors, layout, spacing } from '@/theme';

/** Figma "Risk Strip" — her ekranın altındaki zorunlu yasal uyarı şeridi. */
export function RiskStrip() {
  return (
    <View style={styles.strip} accessibilityRole="text">
      <AlertTriangle size={13} color={colors.amberInk} />
      <Text variant="caption" color="amberInk" style={styles.text} numberOfLines={2}>
        {RISK_DISCLAIMER}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  strip: {
    minHeight: layout.riskStripHeight,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm - 2,
    paddingHorizontal: spacing.lg + 2,
    paddingVertical: spacing.xs + 2,
    backgroundColor: colors.amberBg,
  },
  text: { flex: 1, fontSize: 11, lineHeight: 14 },
});
