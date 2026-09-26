import { useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { BottomSheet, Button, Field, Text } from '@/components/ui';
import type { ListingOut, OfferCreateIn } from '@/lib/api/types';
import { formatAmount, parseAmountInput } from '@/lib/chain';
import { formatDuration, parseNumberInput } from '@/lib/format';
import { colors, radius, spacing } from '@/theme';

/**
 * "Teklif Ver" bottom sheet — `POST /api/v1/offers` gövdesini toplar.
 * Tutar ve süre ilandan ön-doldurulur; oranlar sunucuya **bps** olarak gider
 * (kullanıcı yüzde girer, 20 → 2000). Tutar `parseAmountInput` ile varlığın ondalığına
 * göre doğrulanır ve insan okunur string olarak gönderilir (K12: Number() yok).
 *
 * Form durumu mount ile sıfırlanır: çağıran taraf `key={listing.id}` verir.
 */
export type OfferDraft = OfferCreateIn;

export interface OfferSheetProps {
  listing: ListingOut | null;
  visible: boolean;
  onClose: () => void;
  onSubmit: (draft: OfferDraft) => void;
  submitting?: boolean;
  error?: string | null;
}

export function OfferSheet({
  listing,
  visible,
  onClose,
  onSubmit,
  submitting,
  error,
}: OfferSheetProps) {
  const symbol = listing?.base_asset?.symbol ?? '';
  const decimals = listing?.base_asset?.decimals ?? 18;
  const [amount, setAmount] = useState(listing?.amount ?? '');
  const [duration, setDuration] = useState(
    listing?.duration_days ? String(listing.duration_days) : '',
  );
  const [commission, setCommission] = useState('');
  const [returnMin, setReturnMin] = useState('');
  const [returnMax, setReturnMax] = useState('');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});

  const submit = () => {
    if (!listing) return;
    const next: Record<string, string> = {};

    const parsedAmount = parseAmountInput(amount, decimals);
    if (!parsedAmount) next.amount = `Enter a valid amount (up to ${decimals} decimals).`;
    else if (parsedAmount.raw <= 0n) next.amount = 'Enter an amount greater than zero.';

    const days = parseNumberInput(duration);
    if (days === null || days < 1) next.duration = 'Enter the duration in days.';

    const commissionPct = parseNumberInput(commission);
    if (commissionPct === null || commissionPct <= 0 || commissionPct > 50)
      next.commission = 'Enter a rate between 0% and 50%.';

    const min = parseNumberInput(returnMin);
    const max = parseNumberInput(returnMax);
    if (min !== null && max !== null && min > max)
      next.returnMax = 'The upper bound cannot be below the lower bound.';

    setErrors(next);
    if (Object.keys(next).length > 0 || !parsedAmount) return;

    onSubmit({
      listing_id: listing.id,
      amount: parsedAmount.human,
      base_asset_id: listing.base_asset_id ?? undefined,
      duration_days: Math.round(days as number),
      commission_bps: Math.round((commissionPct as number) * 100),
      expected_return_min_bps: min === null ? undefined : Math.round(min * 100),
      expected_return_max_bps: max === null ? undefined : Math.round(max * 100),
      note: note.trim() || undefined,
    });
  };

  return (
    <BottomSheet
      visible={visible}
      onClose={onClose}
      title="Make an offer"
      subtitle={listing?.title}
      footer={<Button title="Send offer" fullWidth loading={submitting} onPress={submit} />}
    >
      {listing ? (
        <Text variant="caption" color="text3">
          Listing asks for{' '}
          {formatAmount(listing.amount, symbol, { maxFraction: Math.min(decimals, 6) })} ·{' '}
          {formatDuration(listing.duration_days)}
        </Text>
      ) : null}

      <View style={styles.row}>
        <View style={styles.rowItem}>
          <Field
            label="Amount"
            value={amount}
            onChangeText={setAmount}
            placeholder="1000"
            keyboardType="decimal-pad"
            suffix={symbol}
            error={errors.amount}
          />
        </View>
        <View style={styles.rowItem}>
          <Field
            label="Duration"
            value={duration}
            onChangeText={setDuration}
            placeholder="30"
            keyboardType="number-pad"
            suffix="days"
            error={errors.duration}
          />
        </View>
      </View>

      <Field
        label="Commission rate"
        value={commission}
        onChangeText={setCommission}
        placeholder="20"
        keyboardType="decimal-pad"
        suffix="%"
        error={errors.commission}
        hint="Your share of the profit — written into the agreement."
      />

      <View style={styles.row}>
        <View style={styles.rowItem}>
          <Field
            label="Expected return (low)"
            value={returnMin}
            onChangeText={setReturnMin}
            placeholder="15"
            keyboardType="decimal-pad"
            suffix="%"
            error={errors.returnMin}
          />
        </View>
        <View style={styles.rowItem}>
          <Field
            label="Expected return (high)"
            value={returnMax}
            onChangeText={setReturnMax}
            placeholder="25"
            keyboardType="decimal-pad"
            suffix="%"
            error={errors.returnMax}
          />
        </View>
      </View>

      <Field
        label="Note"
        value={note}
        onChangeText={setNote}
        placeholder="Briefly describe your strategy and risk management."
        multiline
        maxLength={280}
        hint={`${note.trim().length}/280 · optional`}
      />

      {error ? (
        <View style={styles.error}>
          <Text variant="caption" color="loss">
            {error}
          </Text>
        </View>
      ) : null}

      <Text variant="caption" color="text3">
        If the offer is accepted, the agreement and escrow are created on-chain. This is not a
        guaranteed return — market risk stays with the customer.
      </Text>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: spacing.md },
  rowItem: { flex: 1 },
  error: { padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.redBg },
});
