import { Pressable, StyleSheet } from 'react-native';

import { Text } from './Text';
import { colors, radius, spacing } from '@/theme';

/** Figma "Chip" — active=true|false. Seçilebilir filtre/etiket. */
export interface ChipProps {
  label: string;
  active?: boolean;
  onPress?: () => void;
  disabled?: boolean;
}

export function Chip({ label, active = false, onPress, disabled }: ChipProps) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: active, disabled }}
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.base,
        active ? styles.active : styles.inactive,
        pressed && { opacity: 0.85 },
        disabled && { opacity: 0.45 },
      ]}
    >
      <Text variant="captionStrong" color={active ? colors.onNavy : colors.text}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    height: 34,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.full,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  active: { backgroundColor: colors.navy900, borderColor: colors.navy900 },
  inactive: { backgroundColor: colors.surface, borderColor: colors.borderStrong },
});
