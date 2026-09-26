import { StyleSheet, View } from 'react-native';

import { Text } from './Text';
import { colors, radius, spacing } from '@/theme';
import { STATUS_LABEL, type ContractStatus } from '@/types';

/** Figma "Status Chip" — sözleşme (agreement) durumları; etiketler @/types'ta. */
export function StatusChip({ status }: { status: ContractStatus }) {
  const t = tones[status];
  return (
    <View style={[styles.base, { backgroundColor: t.bg }]}>
      <Text variant="captionStrong" color={t.fg}>
        {STATUS_LABEL[status]}
      </Text>
    </View>
  );
}

const tones: Record<ContractStatus, { bg: string; fg: string }> = {
  draft: { bg: colors.surfaceSunken, fg: colors.text2 },
  proposed: { bg: colors.navy050, fg: colors.navy900 },
  funded: { bg: colors.amberBg, fg: colors.amberInk },
  active: { bg: colors.greenBg, fg: colors.profit },
  settled: { bg: colors.surfaceSunken, fg: colors.text2 },
  cancelled: { bg: colors.redBg, fg: colors.loss },
  failed: { bg: colors.redBg, fg: colors.loss },
};

const styles = StyleSheet.create({
  base: {
    height: 21,
    paddingHorizontal: spacing.sm,
    borderRadius: radius.full,
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'flex-start',
  },
});
