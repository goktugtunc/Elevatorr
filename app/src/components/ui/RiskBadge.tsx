import { StyleSheet, View } from 'react-native';

import { Text } from './Text';
import { colors, radius, spacing } from '@/theme';

/**
 * Figma "Risk Badge".
 * Sunucu iki ayrı ölçek kullanıyor: ilanlarda `risk_profile`
 * (conservative | balanced | aggressive), trader profilinde `risk_level`
 * (low | medium | high). İkisi de aynı üç kademeye eşlenir.
 */
export type RiskTone = 'low' | 'mid' | 'high';

export type RiskInput =
  RiskTone | 'conservative' | 'balanced' | 'aggressive' | 'medium' | null | undefined;

const TONE: Record<string, RiskTone> = {
  low: 'low',
  conservative: 'low',
  mid: 'mid',
  medium: 'mid',
  balanced: 'mid',
  high: 'high',
  aggressive: 'high',
};

const LABEL: Record<RiskTone, string> = {
  low: 'Conservative',
  mid: 'Balanced',
  high: 'Aggressive',
};

export function riskTone(value: RiskInput): RiskTone | null {
  if (!value) return null;
  return TONE[value] ?? null;
}

export function RiskBadge({ level }: { level: RiskInput }) {
  const tone = riskTone(level);
  if (!tone) return null;
  const t = tones[tone];
  return (
    <View style={[styles.base, { backgroundColor: t.bg }]}>
      <View style={[styles.dot, { backgroundColor: t.dot }]} />
      <Text variant="captionStrong" color={t.fg}>
        {LABEL[tone]}
      </Text>
    </View>
  );
}

const tones: Record<RiskTone, { bg: string; fg: string; dot: string }> = {
  low: { bg: colors.greenBg, fg: colors.profit, dot: colors.profit },
  mid: { bg: colors.amberBg, fg: colors.amberInk, dot: colors.amber },
  high: { bg: colors.redBg, fg: colors.loss, dot: colors.loss },
};

const styles = StyleSheet.create({
  base: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs + 2,
    height: 19,
    paddingHorizontal: spacing.sm,
    borderRadius: radius.full,
    alignSelf: 'flex-start',
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
});
