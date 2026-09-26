import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';

import { AmountField, BottomSheet, Button, Chip, Field, Pill, Switch, Text } from '@/components/ui';
import { agreementsApi } from '@/lib/api';
import type { AgreementOut, AssetBriefOut, AssetOut, TradeTxIn } from '@/lib/api/types';
import { formatAmount, getChainConfig, parseAmountInput, sameAddress } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { formatBps, formatBpsSigned, parseNumberInput } from '@/lib/format';
import { colors, radius, spacing } from '@/theme';

/**
 * "New trade" sheet (04 §6.3) — `POST /agreements/{id}/tx/trade` gövdesini toplar.
 * `token_in` sözleşme bakiyelerinden, `token_out` config allow-list'inden (`onchain_allowed`);
 * fiyat teklifi 500 ms gecikmeli `GET /agreements/{id}/quote`. Form durumu mount ile sıfırlanır
 * (çağıran `key` verir).
 */
export interface TradeSheetProps {
  agreement: AgreementOut;
  visible: boolean;
  onClose: () => void;
  onSubmit: (payload: TradeTxIn) => void;
  submitting?: boolean;
  error?: string | null;
}

const NOTE_MAX = 2000;
const DEADLINE_SECONDS = 300;
const QUOTE_DEBOUNCE_MS = 500;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function TradeSheet({
  agreement,
  visible,
  onClose,
  onSubmit,
  submitting,
  error,
}: TradeSheetProps) {
  const config = getChainConfig();
  const balances = useMemo(
    () =>
      agreement.balances.filter(
        (b) => (parseAmountInput(b.balance, b.asset.decimals)?.raw ?? 0n) > 0n,
      ),
    [agreement.balances],
  );
  const [tokenIn, setTokenIn] = useState<AssetBriefOut | null>(
    balances[0]?.asset ?? agreement.base_asset,
  );
  const outCandidates = useMemo<AssetOut[]>(
    () =>
      config.assets.filter(
        (a) => a.onchain_allowed && a.is_active && !sameAddress(a.address, tokenIn?.address),
      ),
    [config.assets, tokenIn],
  );
  const [tokenOutChoice, setTokenOut] = useState<AssetOut | null>(null);
  // token_in değişince aynı token çıkışta kalmasın (türetilir, effect yok)
  const tokenOut =
    tokenOutChoice && !sameAddress(tokenOutChoice.address, tokenIn?.address)
      ? tokenOutChoice
      : null;
  const [amount, setAmount] = useState('');
  const [slippage, setSlippage] = useState(String(config.defaultTradeSlippageBps / 100));
  const [note, setNote] = useState('');
  const [notify, setNotify] = useState(true);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const balanceIn = balances.find((b) => b.asset.id === tokenIn?.id)?.balance ?? null;
  const decimalsIn = tokenIn?.decimals ?? 18;
  const parsedAmount = tokenIn ? parseAmountInput(amount, decimalsIn) : null;
  const slippagePct = parseNumberInput(slippage);
  const slippageBps = slippagePct === null ? null : Math.round(slippagePct * 100);
  const slippageValid = slippageBps !== null && slippageBps >= 0 && slippageBps <= 5000;

  const quoteKey = useDebounced(
    tokenIn && tokenOut && parsedAmount && parsedAmount.raw > 0n && slippageValid
      ? {
          token_in: tokenIn.id,
          token_out: tokenOut.id,
          amount_in: parsedAmount.human,
          slippage_bps: slippageBps,
        }
      : null,
    QUOTE_DEBOUNCE_MS,
  );

  const quote = useQuery({
    queryKey: ['quote', agreement.id, quoteKey],
    queryFn: () =>
      agreementsApi.quote(agreement.id, {
        ...quoteKey!,
        slippage_bps: quoteKey!.slippage_bps ?? undefined,
        deadline_seconds: DEADLINE_SECONDS,
      }),
    enabled: visible && quoteKey !== null,
    staleTime: 10_000,
    retry: 0,
  });

  const submit = () => {
    const next: Record<string, string> = {};
    if (!tokenIn) next.tokenIn = 'Choose the token to sell.';
    if (!tokenOut) next.tokenOut = 'Choose the token to buy.';
    if (!parsedAmount || parsedAmount.raw <= 0n)
      next.amount = `Enter a valid amount (up to ${decimalsIn} decimals).`;
    else if (balanceIn) {
      const bal = parseAmountInput(balanceIn, decimalsIn);
      if (bal && parsedAmount.raw > bal.raw) next.amount = 'The agreement does not hold that much.';
    }
    if (!slippageValid) next.slippage = 'Enter a tolerance between 0% and 50%.';
    if (note.length > NOTE_MAX) next.note = `Keep the note under ${NOTE_MAX} characters.`;
    setErrors(next);
    if (Object.keys(next).length > 0 || !tokenIn || !tokenOut || !parsedAmount) return;

    onSubmit({
      token_in: tokenIn.id,
      token_out: tokenOut.id,
      amount_in: parsedAmount.human,
      slippage_bps: slippageBps,
      note: note.trim() || null,
      notify_investors: notify,
      deadline_seconds: DEADLINE_SECONDS,
    });
  };

  const q = quote.data;
  const quoteBlocked = q ? q.allowed === false : false;
  const symbol = agreement.base_asset.symbol;

  return (
    <BottomSheet
      visible={visible}
      onClose={onClose}
      title="New trade"
      subtitle={`Agreement #${agreement.onchain_id ?? '—'} · value ${formatAmount(agreement.current_value, symbol)}`}
      footer={
        <Button
          title="Review in wallet"
          fullWidth
          loading={submitting}
          disabled={quoteBlocked || quote.isFetching}
          onPress={submit}
        />
      }
    >
      {/* token_in */}
      <View style={styles.block}>
        <Text variant="captionStrong" color="text2">
          Sell
        </Text>
        {balances.length === 0 ? (
          <Text variant="caption" color="text3">
            The agreement has no token balance to trade yet.
          </Text>
        ) : (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chips}
          >
            {balances.map((b) => (
              <Chip
                key={b.asset.id}
                label={`${b.asset.symbol} · ${formatAmount(b.balance)}`}
                active={tokenIn?.id === b.asset.id}
                onPress={() => setTokenIn(b.asset)}
              />
            ))}
          </ScrollView>
        )}
        {errors.tokenIn ? (
          <Text variant="caption" color="loss">
            {errors.tokenIn}
          </Text>
        ) : null}
      </View>

      <AmountField
        label="Amount"
        value={amount}
        onChangeText={setAmount}
        decimals={decimalsIn}
        symbol={tokenIn?.symbol}
        max={balanceIn}
        error={errors.amount}
        placeholder="0.00"
        hint={balanceIn ? `Available: ${formatAmount(balanceIn, tokenIn?.symbol)}` : undefined}
      />

      {/* token_out */}
      <View style={styles.block}>
        <Text variant="captionStrong" color="text2">
          Buy
        </Text>
        {outCandidates.length === 0 ? (
          <Text variant="caption" color="text3">
            No other token is on the vault allow-list right now.
          </Text>
        ) : (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chips}
          >
            {outCandidates.map((a) => (
              <Chip
                key={a.id}
                label={a.symbol}
                active={tokenOut?.id === a.id}
                onPress={() => setTokenOut(a)}
              />
            ))}
          </ScrollView>
        )}
        {errors.tokenOut ? (
          <Text variant="caption" color="loss">
            {errors.tokenOut}
          </Text>
        ) : null}
      </View>

      <Field
        label="Slippage tolerance"
        value={slippage}
        onChangeText={setSlippage}
        keyboardType="decimal-pad"
        suffix="%"
        error={errors.slippage}
        hint="The swap reverts if the price moves more than this."
      />

      {/* Quote */}
      {quoteKey ? (
        <View style={[styles.quote, quoteBlocked && styles.quoteBlocked]}>
          <View style={styles.quoteHeader}>
            <Text variant="captionStrong" color="text2">
              Quote
            </Text>
            {q ? (
              <Pill
                label={q.source === 'router' ? 'Router price' : 'Test price'}
                tone={q.source === 'router' ? 'navy' : 'amber'}
              />
            ) : null}
          </View>
          {quote.isPending || quote.isFetching ? (
            <Text variant="caption" color="text3">
              Fetching price…
            </Text>
          ) : quote.isError ? (
            <Text variant="caption" color="loss">
              {userMessage(quote.error)}
            </Text>
          ) : q ? (
            <>
              <Row
                label="You receive (est.)"
                value={formatAmount(q.amount_out, q.token_out.symbol)}
              />
              <Row
                label="Minimum after slippage"
                value={formatAmount(q.min_out, q.token_out.symbol)}
              />
              <Row label="Price" value={q.price} />
              <Row
                label="Value after (est.)"
                value={formatAmount(q.value_after_estimate, symbol)}
              />
              <Row
                label="Drawdown headroom"
                value={`${formatAmount(q.headroom, symbol)} (${formatBpsSigned(q.headroom_bps)})`}
              />
              {q.price_impact_pct ? (
                <Row label="Price impact" value={`${q.price_impact_pct}%`} />
              ) : null}
              {quoteBlocked ? (
                <Text variant="caption" color="loss">
                  {q.reason ?? 'This trade would breach the max drawdown floor.'}
                </Text>
              ) : null}
            </>
          ) : null}
        </View>
      ) : null}

      <Field
        label="Note"
        value={note}
        onChangeText={setNote}
        placeholder="Why this trade? Investors see this on their activity feed."
        multiline
        maxLength={NOTE_MAX}
        error={errors.note}
        hint={`${note.trim().length}/${NOTE_MAX} · optional`}
      />

      <Switch
        value={notify}
        onValueChange={setNotify}
        label="Notify investors"
        hint="Send a push notification to the customer when the trade confirms."
      />

      {error ? (
        <View style={styles.error}>
          <Text variant="caption" color="loss">
            {error}
          </Text>
        </View>
      ) : null}

      <Text variant="caption" color="text3">
        Max drawdown {formatBps(agreement.max_drawdown_bps)} · floor{' '}
        {formatAmount(agreement.drawdown_floor, symbol)}. The vault rejects trades that would fall
        below the floor.
      </Text>
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
  block: { gap: spacing.sm },
  chips: { gap: spacing.sm, paddingVertical: 2 },
  quote: {
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceAlt,
  },
  quoteBlocked: { backgroundColor: colors.redBg },
  quoteHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  error: { padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.redBg },
});
