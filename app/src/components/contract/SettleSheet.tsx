import { useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { BottomSheet, Button, Field, Text } from '@/components/ui';
import type { AgreementOut } from '@/lib/api/types';
import { formatAmount, getChainConfig, parseAmountInput, sameAddress } from '@/lib/chain';
import { formatBps, parseNumberInput } from '@/lib/format';
import { colors, radius, spacing } from '@/theme';

/**
 * "Settle" sheet (04 §6.3) — `slippage_bps` toplar (varsayılan `config.settle_slippage_bps`,
 * 0–5000). Açık pozisyonlar taban varlığa çevrilir; ücretler yalnız kârdan kesilir.
 */
export interface SettleSheetProps {
  agreement: AgreementOut;
  visible: boolean;
  onClose: () => void;
  onSubmit: (slippageBps: number) => void;
  submitting?: boolean;
  error?: string | null;
}

export function SettleSheet({
  agreement,
  visible,
  onClose,
  onSubmit,
  submitting,
  error,
}: SettleSheetProps) {
  const config = getChainConfig();
  const [slippage, setSlippage] = useState(String(config.settleSlippageBps / 100));
  const [fieldError, setFieldError] = useState<string | undefined>();
  const symbol = agreement.base_asset.symbol;

  const openPositions = agreement.balances.filter(
    (b) =>
      !sameAddress(b.asset.address, agreement.base_asset.address) &&
      (parseAmountInput(b.balance, b.asset.decimals)?.raw ?? 0n) > 0n,
  );

  const submit = () => {
    const pct = parseNumberInput(slippage);
    const bps = pct === null ? null : Math.round(pct * 100);
    if (bps === null || bps < 0 || bps > 5000) {
      setFieldError('Enter a tolerance between 0% and 50%.');
      return;
    }
    setFieldError(undefined);
    onSubmit(bps);
  };

  const canSettleNow = agreement.available_actions.includes('settle');

  return (
    <BottomSheet
      visible={visible}
      onClose={onClose}
      title="Settle agreement"
      subtitle={`#${agreement.onchain_id ?? '—'} · ${formatAmount(agreement.principal, symbol)} principal`}
      footer={
        <Button
          title="Review in wallet"
          fullWidth
          loading={submitting}
          disabled={!canSettleNow}
          onPress={submit}
        />
      }
    >
      <Text variant="body" color="text2">
        Positions are swapped back to {symbol}; fees apply only on profit. After settlement the
        customer claims the payout and the trader receives the commission.
      </Text>

      <View style={styles.summary}>
        <Row label="Current value" value={formatAmount(agreement.current_value, symbol)} />
        <Row label="Principal" value={formatAmount(agreement.principal, symbol)} />
        <Row label="Trader commission" value={`${formatBps(agreement.commission_bps)} of profit`} />
        {config.source === 'server' ? (
          <Row label="Platform fee" value="applies only on profit" />
        ) : null}
      </View>

      {openPositions.length > 0 ? (
        <View style={styles.positions}>
          <Text variant="captionStrong" color="text2">
            Positions to swap back
          </Text>
          {openPositions.map((b) => (
            <Row
              key={b.asset.id}
              label={b.asset.symbol}
              value={formatAmount(b.balance, b.asset.symbol)}
            />
          ))}
        </View>
      ) : (
        <Text variant="caption" color="text3">
          No open positions — nothing needs to be swapped.
        </Text>
      )}

      <Field
        label="Slippage tolerance per swap"
        value={slippage}
        onChangeText={setSlippage}
        keyboardType="decimal-pad"
        suffix="%"
        error={fieldError}
        hint={`Default ${formatBps(config.settleSlippageBps, 2)}. Each swap reverts if it receives less than quote × (1 − tolerance).`}
      />

      {!canSettleNow ? (
        <Text variant="caption" color="amberInk">
          Settlement is not available for your role in the current state.
        </Text>
      ) : null}

      {error ? (
        <View style={styles.error}>
          <Text variant="caption" color="loss">
            {error}
          </Text>
        </View>
      ) : null}
    </BottomSheet>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text variant="caption" color="text2" style={{ flex: 1 }}>
        {label}
      </Text>
      <Text variant="numericSm">{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  summary: {
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceAlt,
  },
  positions: { gap: spacing.sm },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  error: { padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.redBg },
});
