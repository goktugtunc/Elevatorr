import { StyleSheet, View } from 'react-native';

import { ListRow, StatusChip, Text } from '@/components/ui';
import type { PositionBriefOut } from '@/lib/api/types';
import { formatAmount } from '@/lib/chain';
import { formatBpsSigned } from '@/lib/format';
import { spacing } from '@/theme';

/**
 * Panel pozisyon satırı (04 §6.6): karşı taraf + durum çipi + K/Z (bps ile renk).
 * Tıklama → `/contract/[agreement_id]` (ekran verir).
 */
export function PositionRow({
  position,
  onPress,
}: {
  position: PositionBriefOut;
  onPress?: () => void;
}) {
  const pnl = signedAmount(position.pnl, position.pnl_bps, position.base_asset_code);
  return (
    <ListRow
      title={position.counterparty_display_name || `@${position.counterparty_username}`}
      initials={initials(position.counterparty_display_name || position.counterparty_username)}
      subtitle={`${formatAmount(position.principal, position.base_asset_code)} · ${position.duration_days}d`}
      trailing={
        <View style={styles.right}>
          <Text variant="numericSm" color={pnlTone(position.pnl_bps)}>
            {pnl}
          </Text>
          <View style={styles.meta}>
            <Text variant="caption" color="text3">
              {formatBpsSigned(position.pnl_bps)}
            </Text>
            <StatusChip status={position.status} />
          </View>
        </View>
      }
      chevron={!!onPress}
      onPress={onPress}
    />
  );
}

/** '12.5' + 860 bps → '+12.5 tUSDC'; negatif tutar zaten '-' ile gelir. */
export function signedAmount(human: string, bps: number, symbol: string): string {
  const text = formatAmount(human, symbol);
  return bps > 0 && !text.startsWith('-') && !text.startsWith('+') ? `+${text}` : text;
}

function pnlTone(bps: number): 'profit' | 'loss' | 'text2' {
  if (bps > 0) return 'profit';
  if (bps < 0) return 'loss';
  return 'text2';
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

const styles = StyleSheet.create({
  right: { alignItems: 'flex-end', gap: 2 },
  meta: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
});
