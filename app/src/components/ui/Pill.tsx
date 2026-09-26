import { StyleSheet, View } from 'react-native';

import { Text } from './Text';
import { colors, radius, spacing } from '@/theme';

/** Figma "Pill" — bilgi etiketi (ör. "Düşük Drawdown", "12 Ay+", "Yeni"). Tıklanmaz. */
export interface PillProps {
  label: string;
  tone?: 'neutral' | 'navy' | 'amber';
}

export function Pill({ label, tone = 'neutral' }: PillProps) {
  const t = tones[tone];
  return (
    <View style={[styles.base, { backgroundColor: t.bg }]}>
      <Text variant="captionStrong" color={t.fg}>
        {label}
      </Text>
    </View>
  );
}

const tones = {
  neutral: { bg: colors.surfaceAlt, fg: colors.text2 },
  navy: { bg: colors.navy050, fg: colors.navy900 },
  amber: { bg: colors.amberBg, fg: colors.amberInk },
} as const;

const styles = StyleSheet.create({
  base: {
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.xs,
    borderRadius: radius.full,
    alignSelf: 'flex-start',
  },
});
