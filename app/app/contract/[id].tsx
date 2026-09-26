import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocalSearchParams } from 'expo-router';
import { Clock, RefreshCw } from 'lucide-react-native';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';

import { SettleSheet, TradeSheet } from '@/components/contract';
import { Screen, TopBar } from '@/components/layout';
import { TxProgressSheet, verifyUnsignedTx } from '@/components/tx';
import {
  BottomSheet,
  Button,
  Card,
  ExplorerLink,
  KpiBox,
  Pill,
  RiskBadge,
  Segmented,
  SlideToConfirm,
  Sparkline,
  Stat,
  StatusChip,
  Text,
} from '@/components/ui';
import { ApiError, agreementsApi, metaApi, txApi } from '@/lib/api';
import type {
  AgreementOut,
  BalanceOut,
  TradeOut,
  TradeTxIn,
  TxAction,
  UnsignedTxOut,
  ValueRange,
} from '@/lib/api/types';
import { applyServerConfig, formatAmount, shortAddress, useTxExecutor } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { formatBps, formatBpsSigned, formatDuration, formatRelative } from '@/lib/format';
import { colors, layout, radius, spacing } from '@/theme';

/**
 * Sözleşme ekranı (FE-42, 04 §6.3). `available_actions` tek kaynaktır; rol/durum burada
 * hesaplanmaz. Zincir aksiyonları `agreementsApi.buildTx` → `useTxExecutor` → `TxProgressSheet`.
 * `?next=` (teklif kabulünden gelen `open | open_reserved | propose`) ilgili aksiyonu vurgular.
 */
const LIVE_STATUSES = new Set<AgreementOut['status']>(['draft', 'proposed', 'funded', 'active']);
const RANGES: { value: ValueRange; label: string }[] = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: 'all', label: 'All' },
];

function formatCountdown(sec: number): string {
  if (sec <= 0) return 'Ended';
  const d = Math.floor(sec / 86_400);
  const h = Math.floor((sec % 86_400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (d > 0) return `${d}d ${h}h ${String(m).padStart(2, '0')}m`;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function useCountdown(
  seconds: number | null | undefined,
  anchor: string | undefined,
): number | null {
  // Sunucu değeri (`seconds`, `anchor` = updated_at) her geldiğinde sayaç sıfırdan başlar;
  // yerel state yalnız geçen saniyeyi tutar (effect içinde doğrudan setState yok).
  const [ticks, setTicks] = useState({ anchor, elapsed: 0 });
  useEffect(() => {
    if (seconds === null || seconds === undefined || seconds <= 0) return;
    const t = setInterval(
      () =>
        setTicks((prev) =>
          prev.anchor === anchor ? { anchor, elapsed: prev.elapsed + 1 } : { anchor, elapsed: 1 },
        ),
      1000,
    );
    return () => clearInterval(t);
  }, [seconds, anchor]);
  if (seconds === null || seconds === undefined) return null;
  const elapsed = ticks.anchor === anchor ? ticks.elapsed : 0;
  return Math.max(seconds - elapsed, 0);
}

export default function ContractDetail() {
  const { id, next } = useLocalSearchParams<{ id: string; next?: string }>();
  const queryClient = useQueryClient();

  // Kontrat adresleri / allow-list (TradeSheet, SettleSheet, explorer) sunucudan gelir.
  useQuery({
    queryKey: ['config'],
    queryFn: async () => {
      const cfg = await metaApi.config();
      try {
        applyServerConfig(cfg);
      } catch {
        // Zincir uyuşmazlığı giriş ekranında ele alınır; burada yalnız yedek değerlerle devam.
      }
      return cfg;
    },
    staleTime: 5 * 60_000,
  });

  const agreement = useQuery({
    queryKey: ['agreement', id],
    queryFn: () => agreementsApi.byId(id),
    enabled: !!id,
    refetchInterval: (q) => {
      const a = q.state.data;
      if (!a) return false;
      const pendingOpen =
        a.pending_tx && (a.pending_tx.status === 'pending' || a.pending_tx.status === 'submitted');
      return LIVE_STATUSES.has(a.status) || pendingOpen ? 10_000 : false;
    },
    retry: (count, err) =>
      !(err instanceof ApiError && (err.status === 403 || err.status === 404)) && count < 1,
  });

  const trades = useQuery({
    queryKey: ['agreement', id, 'trades'],
    queryFn: () => agreementsApi.trades(id, { limit: 20 }),
    enabled: !!id && !!agreement.data,
  });

  const [range, setRange] = useState<ValueRange>('7d');
  const history = useQuery({
    queryKey: ['agreement', id, 'history', range],
    queryFn: () => agreementsApi.valueHistory(id, { range }),
    enabled: !!id && !!agreement.data,
  });

  const refetchAll = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['agreement', id] });
  }, [queryClient, id]);

  const tx = useTxExecutor({ onConfirmed: refetchAll });
  const lastBuildRef = useRef<(() => Promise<UnsignedTxOut>) | null>(null);
  const [tradeOpen, setTradeOpen] = useState(false);
  const [tradeKey, setTradeKey] = useState(0);
  const [settleOpen, setSettleOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  /** build → calldata doğrulaması → yürütücü. `invalid_state` gelirse sözleşme yenilenir. */
  const runAction = useCallback(
    (build: () => Promise<UnsignedTxOut>) => {
      const guarded = async () => {
        try {
          return verifyUnsignedTx(await build());
        } catch (err) {
          if (
            err instanceof ApiError &&
            (err.code === 'invalid_state' || err.code === 'wrong_party')
          ) {
            refetchAll();
          }
          throw err;
        }
      };
      lastBuildRef.current = guarded;
      setTradeOpen(false);
      setSettleOpen(false);
      setCancelOpen(false);
      return tx.run(guarded, {
        onUseReserved: async (action) =>
          verifyUnsignedTx(await agreementsApi.buildTx(id, action as TxAction)),
      });
    },
    [tx, id, refetchAll],
  );

  const retry = useCallback(() => {
    const build = lastBuildRef.current;
    if (!build) return;
    tx.reset(); // progress → idle (ref eşzamanlı güncellenir), ardından aynı build ile yeniden
    void tx.run(build, {
      onUseReserved: async (action) =>
        verifyUnsignedTx(await agreementsApi.buildTx(id, action as TxAction)),
    });
  }, [tx, id]);

  const checkPending = useCallback(async () => {
    const p = agreement.data?.pending_tx;
    if (!p) return;
    setChecking(true);
    try {
      const st = await txApi.status(p.id);
      setNotice(
        st.status === 'confirmed'
          ? 'Transaction confirmed.'
          : st.status === 'failed'
            ? `Transaction failed: ${st.error_message ?? st.error_code ?? 'reverted'}`
            : `Transaction is ${st.status}.`,
      );
      refetchAll();
    } catch (err) {
      setNotice(userMessage(err));
    } finally {
      setChecking(false);
    }
  }, [agreement.data?.pending_tx, refetchAll]);

  const a = agreement.data;
  const remaining = useCountdown(a?.seconds_remaining, a?.updated_at);

  // --- durumlar ---
  if (agreement.isPending) {
    return (
      <Screen padded={false}>
        <TopBar title="Agreement" />
        <View style={styles.center}>
          <ActivityIndicator color={colors.navy900} />
          <Text variant="caption" color="text2">
            Loading agreement…
          </Text>
        </View>
      </Screen>
    );
  }

  if (agreement.isError || !a) {
    const err = agreement.error;
    const notFound = err instanceof ApiError && err.status === 404;
    const forbidden = err instanceof ApiError && err.status === 403;
    return (
      <Screen padded={false}>
        <TopBar title="Agreement" />
        <View style={styles.body}>
          <Card style={styles.state}>
            <Text variant="h2">
              {notFound
                ? 'Agreement not found'
                : forbidden
                  ? 'Not your agreement'
                  : 'Could not load agreement'}
            </Text>
            <Text variant="body" color="text2">
              {notFound
                ? 'This agreement was not found.'
                : forbidden
                  ? 'You are not a party to this agreement.'
                  : userMessage(err)}
            </Text>
            {!notFound && !forbidden ? (
              <Button title="Try again" onPress={() => agreement.refetch()} />
            ) : null}
          </Card>
        </View>
      </Screen>
    );
  }

  const symbol = a.base_asset.symbol;
  const actions = new Set(a.available_actions);
  const pendingOpen =
    a.pending_tx && (a.pending_tx.status === 'pending' || a.pending_tx.status === 'submitted');
  const busy = tx.isBusy;
  const isSettled = a.status === 'settled';
  const noLimit = a.max_drawdown_bps >= 10_000;
  const points = history.data?.points.map((p) => p.return_bps) ?? [];

  const lockAction: 'open' | 'open_reserved' | null = actions.has('open')
    ? 'open'
    : actions.has('open_reserved')
      ? 'open_reserved'
      : null;
  const fundAction: 'fund' | 'fund_reserved' | null = actions.has('fund')
    ? 'fund'
    : actions.has('fund_reserved')
      ? 'fund_reserved'
      : null;

  const isNext = (action: string) =>
    next === action || (next === 'open' && action === 'open_reserved');

  return (
    <Screen padded={false}>
      <TopBar
        title={a.onchain_id !== null ? `Agreement #${a.onchain_id}` : 'Agreement'}
        right={
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Refresh"
            hitSlop={8}
            onPress={() => agreement.refetch()}
            style={({ pressed }) => [styles.iconButton, pressed && { opacity: 0.6 }]}
          >
            <RefreshCw size={18} color={colors.text2} />
          </Pressable>
        }
      />
      <View style={styles.body}>
        {/* 1. Başlık */}
        <Card style={styles.section}>
          <View style={styles.headerRow}>
            <StatusChip status={a.status} />
            {a.risk_profile ? <RiskBadge level={a.risk_profile} /> : null}
            {a.is_expired && !isSettled ? <Pill label="Period ended" tone="amber" /> : null}
          </View>
          <Party
            label="Customer"
            name={a.customer.display_name}
            address={a.customer.wallet_address}
            you={a.my_role === 'customer'}
          />
          <Party
            label="Trader"
            name={a.trader.display_name}
            address={a.trader.wallet_address}
            you={a.my_role === 'trader'}
          />
          {remaining !== null && a.status === 'active' ? (
            <View style={styles.countdown}>
              <Clock size={14} color={colors.text2} />
              <Text variant="caption" color="text2">
                {formatCountdown(remaining)} remaining
              </Text>
            </View>
          ) : null}
        </Card>

        {/* 6. Pending banner */}
        {pendingOpen && a.pending_tx ? (
          <View style={styles.pending}>
            <View style={{ flex: 1, gap: 2 }}>
              <Text variant="captionStrong" color="amberInk">
                Transaction pending · {a.pending_tx.action}
              </Text>
              {a.pending_tx.tx_hash ? (
                <ExplorerLink hash={a.pending_tx.tx_hash} />
              ) : (
                <Text variant="caption" color="amberInk">
                  Waiting for the wallet · expires {formatRelative(a.pending_tx.expires_at)}
                </Text>
              )}
            </View>
            <Button
              title="Check status"
              size="sm"
              variant="secondary"
              loading={checking}
              onPress={checkPending}
            />
          </View>
        ) : null}

        {notice ? (
          <View style={styles.notice}>
            <Text variant="caption" color="text2" style={{ flex: 1 }}>
              {notice}
            </Text>
            <Button title="Dismiss" size="sm" variant="ghost" onPress={() => setNotice(null)} />
          </View>
        ) : null}

        {/* 7. Aksiyonlar */}
        {actions.size > 0 ? (
          <Card style={styles.section}>
            <View style={styles.headerRow}>
              <Text variant="h2" style={{ flex: 1 }}>
                Your next step
              </Text>
              {next &&
              (isNext(lockAction ?? '') || isNext(fundAction ?? '') || isNext('propose')) ? (
                <Pill label="From offer" tone="amber" />
              ) : null}
            </View>

            {lockAction ? (
              <ActionBlock
                highlight={isNext(lockAction)}
                hint={
                  lockAction === 'open_reserved'
                    ? `Opens from your listing deposit — no new approval needed.`
                    : `Approve ${symbol} once, then the principal moves into the TraderKirala vault.`
                }
              >
                <SlideToConfirm
                  label={`Slide to lock ${formatAmount(a.principal, symbol)}`}
                  loading={busy}
                  disabled={!!pendingOpen}
                  onConfirm={() => void runAction(() => agreementsApi.buildTx(id, lockAction))}
                />
              </ActionBlock>
            ) : null}

            {actions.has('propose') ? (
              <ActionBlock
                highlight={isNext('propose')}
                hint="Writes the terms on-chain. The customer funds the agreement afterwards."
              >
                <SlideToConfirm
                  label="Slide to propose"
                  loading={busy}
                  disabled={!!pendingOpen}
                  onConfirm={() => void runAction(() => agreementsApi.buildTx(id, 'propose'))}
                />
              </ActionBlock>
            ) : null}

            {fundAction ? (
              <ActionBlock
                highlight={isNext(fundAction)}
                hint={
                  fundAction === 'fund_reserved'
                    ? 'Funds from your listing deposit.'
                    : `Approve ${symbol} once, then ${formatAmount(a.principal, symbol)} moves into the vault.`
                }
              >
                <SlideToConfirm
                  label="Slide to fund"
                  loading={busy}
                  disabled={!!pendingOpen}
                  onConfirm={() => void runAction(() => agreementsApi.buildTx(id, fundAction))}
                />
              </ActionBlock>
            ) : null}

            {actions.has('accept') ? (
              <ActionBlock hint="Starts the trading period. You can trade from the vault balance right away.">
                <SlideToConfirm
                  label="Slide to accept and start"
                  loading={busy}
                  disabled={!!pendingOpen}
                  onConfirm={() => void runAction(() => agreementsApi.buildTx(id, 'accept'))}
                />
              </ActionBlock>
            ) : null}

            <View style={styles.buttonRow}>
              {actions.has('trade') ? (
                <Button
                  title="New trade"
                  fullWidth
                  disabled={busy || !!pendingOpen}
                  onPress={() => {
                    setTradeKey((k) => k + 1);
                    setTradeOpen(true);
                  }}
                  style={{ flex: 1 }}
                />
              ) : null}
              {actions.has('settle') ? (
                <Button
                  title="Settle"
                  variant={actions.has('trade') ? 'secondary' : 'primary'}
                  fullWidth
                  disabled={busy || !!pendingOpen}
                  onPress={() => setSettleOpen(true)}
                  style={{ flex: 1 }}
                />
              ) : null}
            </View>
            {actions.has('cancel') ? (
              <Button
                title="Cancel agreement"
                variant="danger"
                fullWidth
                disabled={busy || !!pendingOpen}
                onPress={() => setCancelOpen(true)}
              />
            ) : null}
          </Card>
        ) : null}

        {/* 2. Terms */}
        <Card style={styles.section}>
          <Text variant="h2">Terms</Text>
          <View style={styles.statsRow}>
            <Stat label="Principal" value={formatAmount(a.principal, symbol)} />
            <Stat label="Duration" value={formatDuration(a.duration_days)} />
          </View>
          <View style={styles.statsRow}>
            <Stat label="Commission" value={`${formatBps(a.commission_bps)} of profit`} />
            <Stat
              label="Max drawdown"
              value={noLimit ? 'no limit' : formatBps(a.max_drawdown_bps)}
            />
          </View>
          <View style={styles.statsRow}>
            <Stat
              label="Drawdown floor"
              value={noLimit ? '—' : formatAmount(a.drawdown_floor, symbol)}
            />
            <Stat
              label="Proposed by"
              value={a.proposer_role === 'customer' ? 'Customer' : 'Trader'}
            />
          </View>
          <View style={styles.kv}>
            <Text variant="caption" color="text3">
              Vault
            </Text>
            <ExplorerLink address={a.vault_address} label={shortAddress(a.vault_address)} />
          </View>
          {a.created_tx ? (
            <View style={styles.kv}>
              <Text variant="caption" color="text3">
                Created
              </Text>
              <ExplorerLink hash={a.created_tx} />
            </View>
          ) : null}
          {a.activate_tx ? (
            <View style={styles.kv}>
              <Text variant="caption" color="text3">
                Started
              </Text>
              <ExplorerLink hash={a.activate_tx} />
            </View>
          ) : null}
        </Card>

        {/* 3. Value */}
        <Card style={styles.section}>
          <Text variant="h2">{isSettled ? 'Settlement' : 'Value'}</Text>
          {isSettled ? (
            <>
              <View style={styles.statsRow}>
                <KpiBox
                  label="Final value"
                  value={formatAmount(a.final_value, symbol)}
                  style={{ flex: 1 }}
                />
                <KpiBox
                  label="Profit"
                  value={formatAmount(a.profit, symbol)}
                  signed={a.pnl_bps ?? undefined}
                  style={{ flex: 1 }}
                />
              </View>
              <View style={styles.statsRow}>
                <Stat label="Trader fee" value={formatAmount(a.trader_fee, symbol)} />
                <Stat label="Platform fee" value={formatAmount(a.platform_fee, symbol)} />
              </View>
              <View style={styles.statsRow}>
                <Stat label="Customer payout" value={formatAmount(a.customer_payout, symbol)} />
                <Stat label="Settled by" value={a.settled_by ? shortAddress(a.settled_by) : '—'} />
              </View>
              {a.settle_tx ? (
                <View style={styles.kv}>
                  <Text variant="caption" color="text3">
                    Settle tx
                  </Text>
                  <ExplorerLink hash={a.settle_tx} />
                </View>
              ) : null}
            </>
          ) : (
            <>
              <View style={styles.statsRow}>
                <KpiBox
                  label="Current value"
                  value={formatAmount(a.current_value, symbol)}
                  sub={
                    a.value_updated_at
                      ? `Updated ${formatRelative(a.value_updated_at)} ago`
                      : undefined
                  }
                  style={{ flex: 1 }}
                />
                <KpiBox
                  label="P&L"
                  value={a.pnl ? formatAmount(a.pnl, symbol) : '—'}
                  signed={a.pnl_bps ?? undefined}
                  sub={a.pnl_bps !== null ? formatBpsSigned(a.pnl_bps) : undefined}
                  style={{ flex: 1 }}
                />
              </View>
              <View style={styles.statsRow}>
                <Stat
                  label="Drawdown"
                  value={formatBps(a.drawdown_bps, 1)}
                  signed={-a.drawdown_bps}
                />
                <Stat label="High-water" value={formatAmount(a.high_water_value, symbol)} />
              </View>
              {a.onchain_id !== null ? (
                <>
                  <Segmented options={RANGES} value={range} onChange={setRange} />
                  {history.isPending ? (
                    <ActivityIndicator color={colors.navy900} />
                  ) : history.isError ? (
                    <Text variant="caption" color="loss">
                      {userMessage(history.error)}
                    </Text>
                  ) : points.length < 2 ? (
                    <Text variant="caption" color="text3">
                      Not enough data points yet for this range.
                    </Text>
                  ) : (
                    <Sparkline data={points} height={64} />
                  )}
                </>
              ) : null}
            </>
          )}
        </Card>

        {/* 4. Balances */}
        <Card style={styles.section}>
          <Text variant="h2">Vault balances</Text>
          {a.balances.length === 0 ? (
            <Text variant="caption" color="text3">
              {a.status === 'draft' || a.status === 'proposed'
                ? 'Nothing is locked yet.'
                : 'No balances recorded.'}
            </Text>
          ) : (
            a.balances.map((b) => (
              <BalanceRow
                key={b.asset.id}
                balance={b}
                claimable={
                  isSettled && actions.has('claim') && a.claimable_assets.includes(b.asset.id)
                }
                busy={busy}
                onClaim={() =>
                  void runAction(() => agreementsApi.buildTx(id, 'claim', { asset_id: b.asset.id }))
                }
              />
            ))
          )}
        </Card>

        {/* 5. Trades */}
        <Card style={styles.section}>
          <Text variant="h2">Trades</Text>
          {trades.isPending ? (
            <ActivityIndicator color={colors.navy900} />
          ) : trades.isError ? (
            <View style={styles.inlineError}>
              <Text variant="caption" color="loss" style={{ flex: 1 }}>
                {userMessage(trades.error)}
              </Text>
              <Button
                title="Retry"
                size="sm"
                variant="secondary"
                onPress={() => trades.refetch()}
              />
            </View>
          ) : (trades.data?.items.length ?? 0) === 0 ? (
            <Text variant="caption" color="text3">
              No trades yet.
            </Text>
          ) : (
            trades.data!.items.map((t) => <TradeRow key={t.id} trade={t} symbol={symbol} />)
          )}
        </Card>
      </View>

      {/* Sheets */}
      <TradeSheet
        key={tradeKey}
        agreement={a}
        visible={tradeOpen}
        onClose={() => setTradeOpen(false)}
        onSubmit={(payload: TradeTxIn) =>
          void runAction(() => agreementsApi.buildTradeTx(id, payload))
        }
      />
      <SettleSheet
        agreement={a}
        visible={settleOpen}
        onClose={() => setSettleOpen(false)}
        onSubmit={(slippageBps) =>
          void runAction(() => agreementsApi.buildTx(id, 'settle', { slippage_bps: slippageBps }))
        }
      />
      <BottomSheet
        visible={cancelOpen}
        onClose={() => setCancelOpen(false)}
        title="Cancel agreement?"
        subtitle={
          a.status === 'funded' ? 'Funded: principal returns to the customer’s wallet' : undefined
        }
        footer={
          <>
            <Button
              title="Cancel agreement"
              variant="danger"
              fullWidth
              onPress={() => void runAction(() => agreementsApi.buildTx(id, 'cancel'))}
            />
            <Button
              title="Keep it"
              variant="ghost"
              fullWidth
              onPress={() => setCancelOpen(false)}
            />
          </>
        }
      >
        <Text variant="body" color="text2">
          {a.status === 'funded'
            ? `The vault sends ${formatAmount(a.principal, symbol)} back to the customer and closes this agreement. This cannot be undone.`
            : 'The proposal is withdrawn on-chain and this agreement closes. This cannot be undone.'}
        </Text>
      </BottomSheet>
      <TxProgressSheet
        progress={tx.progress}
        onClose={tx.reset}
        onRetry={retry}
        onRecheck={() => void tx.recheck()}
      />
    </Screen>
  );
}

function Party({
  label,
  name,
  address,
  you,
}: {
  label: string;
  name: string;
  address: string;
  you: boolean;
}) {
  return (
    <View style={styles.party}>
      <Text variant="caption" color="text3" style={{ width: 72 }}>
        {label}
      </Text>
      <View style={{ flex: 1 }}>
        <Text variant="bodyStrong" numberOfLines={1}>
          {name}
          {you ? ' (you)' : ''}
        </Text>
        <ExplorerLink address={address} />
      </View>
    </View>
  );
}

function ActionBlock({
  children,
  hint,
  highlight,
}: {
  children: React.ReactNode;
  hint: string;
  highlight?: boolean;
}) {
  return (
    <View style={[styles.action, highlight && styles.actionHighlight]}>
      {children}
      <Text variant="caption" color="text2">
        {hint}
      </Text>
    </View>
  );
}

function BalanceRow({
  balance,
  claimable,
  busy,
  onClaim,
}: {
  balance: BalanceOut;
  claimable: boolean;
  busy: boolean;
  onClaim: () => void;
}) {
  return (
    <View style={styles.balanceRow}>
      <View style={{ flex: 1 }}>
        <Text variant="bodyStrong">{balance.asset.symbol}</Text>
        <Text variant="caption" color="text3">
          {balance.asset.name}
        </Text>
      </View>
      <Text variant="numericSm">{formatAmount(balance.balance)}</Text>
      {claimable ? <Button title="Claim" size="sm" disabled={busy} onPress={onClaim} /> : null}
    </View>
  );
}

function TradeRow({ trade, symbol }: { trade: TradeOut; symbol: string }) {
  return (
    <View style={styles.tradeRow}>
      <View style={{ flex: 1, gap: 2 }}>
        <Text variant="bodyStrong">
          {trade.token_in.symbol} → {trade.token_out.symbol}
        </Text>
        <Text variant="caption" color="text2">
          {formatAmount(trade.amount_in, trade.token_in.symbol)} →{' '}
          {formatAmount(trade.amount_out, trade.token_out.symbol)}
        </Text>
        {trade.note ? (
          <Text variant="caption" color="text3" numberOfLines={2}>
            {trade.note}
          </Text>
        ) : null}
        {trade.tx_hash || trade.explorer_url ? (
          <ExplorerLink hash={trade.tx_hash} url={trade.explorer_url} />
        ) : null}
      </View>
      <View style={{ alignItems: 'flex-end', gap: 2 }}>
        {trade.value_after ? (
          <Text variant="numericSm" color={colors.text}>
            {formatAmount(trade.value_after, symbol)}
          </Text>
        ) : null}
        <Text variant="caption" color="text3">
          {formatRelative(trade.created_at)}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  body: {
    paddingHorizontal: layout.screenPaddingH,
    paddingBottom: spacing['3xl'],
    gap: spacing.md,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    minHeight: 240,
  },
  state: { gap: spacing.md, alignItems: 'flex-start' },
  section: { gap: spacing.md },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  party: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  countdown: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  pending: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.amberBg,
  },
  notice: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.sm,
    paddingLeft: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceAlt,
  },
  action: { gap: spacing.sm, padding: spacing.xs, borderRadius: radius.lg },
  actionHighlight: { backgroundColor: colors.amberBg, padding: spacing.sm },
  buttonRow: { flexDirection: 'row', gap: spacing.sm },
  statsRow: { flexDirection: 'row', gap: spacing.md },
  kv: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  balanceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.xs,
  },
  tradeRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.md,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  inlineError: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconButton: { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
});
