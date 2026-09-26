import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as Clipboard from 'expo-clipboard';
import { useRouter } from 'expo-router';
import { Copy, ExternalLink, QrCode } from 'lucide-react-native';
import { useEffect, useMemo, useState } from 'react';
import { Linking, Platform, Pressable, StyleSheet, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';
import { parseUnits } from 'viem';

import { Screen, TopBar } from '@/components/layout';
import { TxProgressSheet } from '@/components/tx';
import { BottomSheet, Button, Card, Chip, Field, ListRow, Pill, Text } from '@/components/ui';
import { ChainBanner } from '@/components/wallet';
import { ApiError, walletApi } from '@/lib/api';
import { useServerConfig } from '@/lib/api/hooks';
import type { TokenBalanceOut, WalletOut, WalletTransferOut } from '@/lib/api/types';
import {
  CHAIN_ID,
  CHAIN_NAME,
  checksum,
  explorerAddressUrl,
  formatAmount,
  formatMon,
  formatRaw,
  getChainConfig,
  isEvmAddress,
  parseAmountInput,
  shortAddress,
  useTxExecutor,
} from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { formatRelative } from '@/lib/format';
import { getLocalWallet } from '@/lib/wallet';
import { useSession } from '@/store/session';
import { colors, layout, radius, spacing } from '@/theme';

/**
 * Cüzdan (04 §6.4) — MON + allow-list token bakiyeleri (`GET /wallet`, 15 sn), adres/QR,
 * faucet'ler (MON linki, test token mint), Send (`POST /wallet/tx/transfer` → `useTxExecutor`),
 * hareketler (`/wallet/transactions`, 404 → gizli), yerel cüzdan güvenliği ve Sign out.
 */
export default function WalletScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { address, walletKind, walletName, walletConnected, signOut, forgetLocalWallet } =
    useSession();
  const [notice, setNotice] = useState<{
    tone: 'ok' | 'error';
    text: string;
    url?: string | null;
  } | null>(null);
  const [showQr, setShowQr] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const [forgetOpen, setForgetOpen] = useState(false);
  const [backupOpen, setBackupOpen] = useState(false);

  const config = useServerConfig();
  const wallet = useQuery({
    queryKey: ['wallet'],
    queryFn: walletApi.get,
    refetchInterval: 15_000,
    enabled: !!address,
  });
  const deposit = useQuery({
    queryKey: ['wallet', 'deposit-info'],
    queryFn: walletApi.depositInfo,
    enabled: !!address,
  });
  const activity = useQuery({
    queryKey: ['wallet', 'transactions'],
    queryFn: () => walletApi.transactions({ limit: 20 }),
    enabled: !!address,
    retry: false,
  });
  const activityMissing =
    activity.isError && activity.error instanceof ApiError && activity.error.status === 404;

  const faucetUrl = config.data?.chain.faucet_url ?? getChainConfig().faucetUrl;
  const usdPrices = config.data?.usd_prices ?? {};
  const defaultAssetId = config.data?.default_base_asset_id ?? null;

  // Faucet
  const faucetAssets = deposit.data?.token_faucet.assets ?? [];
  const faucetAsset =
    faucetAssets.find((a) => a.asset_id === defaultAssetId) ?? faucetAssets[0] ?? null;
  // Sunucudan gelen `next_allowed_at`; mint ya da 429 sonrası yerel değer öne geçer.
  const [nextAllowedOverride, setNextAllowedAt] = useState<string | null>(null);
  const nextAllowedAt = latestIso(nextAllowedOverride, faucetAsset?.next_allowed_at ?? null);
  const countdown = useCountdown(nextAllowedAt);
  const faucetEnabled = (deposit.data?.token_faucet.enabled ?? false) && !!faucetAsset;

  const faucet = useMutation({
    mutationFn: (assetId: string) => walletApi.faucet({ asset_id: assetId }),
    onMutate: () => setNotice(null),
    onSuccess: async (out) => {
      setNextAllowedAt(out.next_allowed_at);
      setNotice({
        tone: 'ok',
        text:
          out.status === 'failed'
            ? `Minting ${out.symbol} failed on-chain.`
            : `${formatAmount(out.amount, out.symbol)} minted${out.status === 'submitted' ? ' (pending)' : ''}`,
        url: out.explorer_url,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['wallet'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      ]);
    },
    onError: (err) => {
      if (err instanceof ApiError && typeof err.details.next_allowed_at === 'string') {
        setNextAllowedAt(err.details.next_allowed_at);
      }
      setNotice({ tone: 'error', text: userMessage(err) });
    },
  });

  // Send
  const tx = useTxExecutor({ invalidate: [['wallet', 'transactions']] });

  const copyAddress = async () => {
    if (!address) return;
    await Clipboard.setStringAsync(checksum(address));
    setNotice({ tone: 'ok', text: 'Address copied.' });
  };

  const explorerUrl = wallet.data?.explorer_url ?? (address ? explorerAddressUrl(address) : null);
  const payUri = deposit.data?.pay_uri ?? (address ? `ethereum:${address}@${CHAIN_ID}` : '');
  const isLocal = Platform.OS !== 'web' && walletKind === 'local' && !!getLocalWallet();

  const onSignOut = async () => {
    await signOut();
    router.replace('/(auth)/login');
  };

  if (!address) {
    return (
      <Screen padded={false}>
        <TopBar title="Wallet" />
        <View style={styles.body}>
          <Card style={styles.state}>
            <Text variant="h2">No wallet connected</Text>
            <Text variant="body" color="text2">
              Sign in with a wallet to see balances.
            </Text>
            <Button title="Go to sign in" onPress={() => router.replace('/(auth)/login')} />
          </Card>
        </View>
      </Screen>
    );
  }

  return (
    <Screen padded={false}>
      <TopBar title="Wallet" />
      <View style={styles.body}>
        {/* Yanlış ağ şeridi — ortak bileşen (04 §6.1), switch/add chain'i kendi yürütür */}
        <ChainBanner />

        {notice ? (
          <View style={[styles.notice, notice.tone === 'ok' ? styles.noticeOk : styles.noticeErr]}>
            <Text
              variant="caption"
              color={notice.tone === 'ok' ? colors.profit : colors.loss}
              style={{ flex: 1 }}
            >
              {notice.text}
            </Text>
            {notice.url ? (
              <Pressable accessibilityRole="link" onPress={() => Linking.openURL(notice.url!)}>
                <Text variant="captionStrong" color="navy900">
                  Explorer
                </Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        {/* 1. Adres kartı */}
        <Card style={styles.card}>
          <View style={styles.rowBetween}>
            <Text variant="overline" color="text3">
              Address
            </Text>
            <Pill
              label={`${walletName ?? walletKindLabel(walletKind)} · ${CHAIN_NAME}`}
              tone="navy"
            />
          </View>
          <Text variant="captionStrong" selectable style={styles.mono}>
            {checksum(address)}
          </Text>
          <View style={styles.actionsRow}>
            <Button
              title="Copy"
              size="sm"
              variant="secondary"
              leftIcon={<Copy size={14} color={colors.navy900} />}
              onPress={() => void copyAddress()}
            />
            <Button
              title={showQr ? 'Hide QR' : 'Show QR'}
              size="sm"
              variant="secondary"
              leftIcon={<QrCode size={14} color={colors.navy900} />}
              onPress={() => setShowQr((v) => !v)}
            />
            {explorerUrl ? (
              <Button
                title="Explorer"
                size="sm"
                variant="ghost"
                leftIcon={<ExternalLink size={14} color={colors.navy900} />}
                onPress={() => Linking.openURL(explorerUrl)}
              />
            ) : null}
          </View>
          {showQr ? (
            <View style={styles.qr}>
              <QRCode
                value={payUri}
                size={180}
                backgroundColor={colors.surface}
                color={colors.navy900}
              />
              <Text variant="caption" color="text3" align="center">
                Scan to send funds on {CHAIN_NAME} (chain {CHAIN_ID}).
              </Text>
            </View>
          ) : null}
          {!walletConnected ? (
            <Text variant="caption" color="text2">
              Wallet connection is closed. Reconnect it before sending a transaction.
            </Text>
          ) : null}
        </Card>

        {/* 2–3. Bakiyeler */}
        {wallet.isPending ? (
          <Card style={styles.card}>
            <Text variant="caption" color="text2">
              Loading balances…
            </Text>
          </Card>
        ) : wallet.isError ? (
          <Card style={styles.state}>
            <Text variant="h2">Balances unavailable</Text>
            <Text variant="body" color="text2">
              {wallet.error instanceof ApiError && wallet.error.code === 'chain_error'
                ? 'Balances are temporarily unavailable (RPC).'
                : userMessage(wallet.error)}
            </Text>
            <Button title="Retry" variant="secondary" onPress={() => wallet.refetch()} />
          </Card>
        ) : (
          <Balances
            wallet={wallet.data}
            usdPrices={usdPrices}
            faucetUrl={faucetUrl}
            faucetLabel={faucetAsset ? `Get test ${faucetAsset.symbol}` : 'Get test tokens'}
            faucetEnabled={faucetEnabled}
            faucetBusy={faucet.isPending}
            countdown={countdown}
            onFaucet={() => faucetAsset && faucet.mutate(faucetAsset.asset_id)}
            onSend={() => setSendOpen(true)}
            canSend={walletConnected}
          />
        )}

        {/* 5. Hareketler */}
        {activityMissing ? null : (
          <Card style={styles.card}>
            <Text variant="overline" color="text3">
              Activity
            </Text>
            {activity.isPending ? (
              <Text variant="caption" color="text2">
                Loading…
              </Text>
            ) : activity.isError ? (
              <Text variant="caption" color="text2">
                {userMessage(activity.error)}
              </Text>
            ) : activity.data.items.length === 0 ? (
              <Text variant="caption" color="text2">
                No transfers yet.
              </Text>
            ) : (
              activity.data.items.map((t) => (
                <ActivityRow key={`${t.tx_hash}:${t.log_index}`} item={t} />
              ))
            )}
          </Card>
        )}

        {/* 6. Güvenlik (yalnız native yerel cüzdan) */}
        {isLocal ? (
          <Card style={styles.card}>
            <Text variant="overline" color="text3">
              Security
            </Text>
            <Text variant="caption" color="text2">
              This in-app wallet is for Monad Testnet only. Its private key lives on this device.
            </Text>
            <ListRow title="Back up private key" chevron onPress={() => setBackupOpen(true)} />
            <ListRow title="Forget this wallet" chevron onPress={() => setForgetOpen(true)} />
          </Card>
        ) : null}

        {/* 7. Sign out */}
        <Button title="Sign out" variant="secondary" onPress={() => void onSignOut()} />
        <Text variant="caption" color="text3" align="center">
          Signing out closes the session.{' '}
          {isLocal ? 'The in-app wallet key stays on this device.' : ''}
        </Text>
      </View>

      {wallet.data ? (
        <SendSheet
          visible={sendOpen}
          onClose={() => setSendOpen(false)}
          wallet={wallet.data}
          onSubmit={(payload) => {
            setSendOpen(false);
            void tx.run(() => walletApi.transfer(payload));
          }}
        />
      ) : null}

      {tx.progress.phase !== 'idle' ? (
        <TxProgressSheet progress={tx.progress} onClose={tx.reset} onRecheck={tx.recheck} />
      ) : null}

      {isLocal ? (
        <>
          <BackupSheet visible={backupOpen} onClose={() => setBackupOpen(false)} />
          <ForgetSheet
            visible={forgetOpen}
            onClose={() => setForgetOpen(false)}
            onBackup={() => {
              setForgetOpen(false);
              setBackupOpen(true);
            }}
            onForget={async () => {
              await forgetLocalWallet();
              setForgetOpen(false);
              router.replace('/(auth)/login');
            }}
          />
        </>
      ) : null}
    </Screen>
  );
}

// ---------------------------------------------------------------------------

function Balances({
  wallet,
  usdPrices,
  faucetUrl,
  faucetLabel,
  faucetEnabled,
  faucetBusy,
  countdown,
  onFaucet,
  onSend,
  canSend,
}: {
  wallet: WalletOut;
  usdPrices: Record<string, string | null>;
  faucetUrl: string;
  faucetLabel: string;
  faucetEnabled: boolean;
  faucetBusy: boolean;
  countdown: string | null;
  onFaucet: () => void;
  onSend: () => void;
  canSend: boolean;
}) {
  const monZero = !/[1-9]/.test(wallet.native.balance_raw);
  const usdTotal = useMemo(() => totalUsd(wallet.tokens, usdPrices), [wallet.tokens, usdPrices]);

  return (
    <>
      <Card style={styles.card}>
        <View style={styles.rowBetween}>
          <Text variant="overline" color="text3">
            {wallet.native.symbol} · gas
          </Text>
          <Text variant="caption" color="text3">
            Updated {formatRelative(wallet.updated_at)} ago
          </Text>
        </View>
        <Text variant="numeric">{formatMon(wallet.native.balance_raw)}</Text>
        {monZero ? (
          <View style={styles.hint}>
            <Text variant="caption" color={colors.amberInk}>
              You need MON to pay gas.
            </Text>
          </View>
        ) : null}
        <Button
          title="Get MON from faucet"
          size="sm"
          variant="secondary"
          leftIcon={<ExternalLink size={14} color={colors.navy900} />}
          onPress={() => Linking.openURL(faucetUrl)}
        />
      </Card>

      <Card style={styles.card}>
        <View style={styles.rowBetween}>
          <Text variant="overline" color="text3">
            Tokens
          </Text>
          {usdTotal ? (
            <Text variant="caption" color="text2">
              ≈ {usdTotal}
            </Text>
          ) : null}
        </View>
        {wallet.tokens.length === 0 ? (
          <Text variant="caption" color="text2">
            No tokens are enabled on this server yet.
          </Text>
        ) : (
          wallet.tokens.map((t) => (
            <ListRow
              key={t.asset_id}
              title={t.symbol}
              subtitle={shortAddress(t.address)}
              trailing={
                <View style={styles.tokenRight}>
                  <Text variant="numericSm">{formatAmount(t.balance, t.symbol)}</Text>
                  {t.is_base_allowed ? <Pill label="Base asset" tone="navy" /> : null}
                </View>
              }
            />
          ))
        )}
        <View style={styles.actionsRow}>
          <Button
            title={countdown ? `${faucetLabel} in ${countdown}` : faucetLabel}
            size="sm"
            onPress={onFaucet}
            disabled={!faucetEnabled || !!countdown || faucetBusy}
            loading={faucetBusy}
          />
          <Button title="Send" size="sm" variant="secondary" onPress={onSend} disabled={!canSend} />
        </View>
        {!faucetEnabled ? (
          <Text variant="caption" color="text3">
            The test token faucet is not enabled on this server.
          </Text>
        ) : null}
      </Card>
    </>
  );
}

function ActivityRow({ item }: { item: WalletTransferOut }) {
  const sign = item.direction === 'in' ? '+' : '-';
  const who = item.counterparty_label ?? shortAddress(item.counterparty);
  return (
    <ListRow
      title={`${item.direction === 'in' ? 'Received from' : 'Sent to'} ${who}`}
      subtitle={formatRelative(item.at)}
      value={`${sign}${formatAmount(item.amount, item.symbol)}`}
      signed={item.direction === 'in' ? 1 : -1}
      onPress={item.explorer_url ? () => Linking.openURL(item.explorer_url!) : undefined}
    />
  );
}

/** Token bakiyelerini `usd_prices` ile toplar (BigInt; fiyatı olmayan token atlanır). */
function totalUsd(tokens: TokenBalanceOut[], prices: Record<string, string | null>): string | null {
  const USD_DECIMALS = 18;
  let sum = 0n;
  let any = false;
  for (const t of tokens) {
    const price = prices[t.symbol];
    if (!price) continue;
    try {
      const priceRaw = parseUnits(price, USD_DECIMALS);
      const balance = BigInt(t.balance_raw);
      sum += (balance * priceRaw) / 10n ** BigInt(t.decimals);
      any = true;
    } catch {
      // fiyat ya da bakiye parse edilemedi — atla
    }
  }
  return any ? formatRaw(sum, USD_DECIMALS, 'USD', { maxFraction: 2, minFraction: 2 }) : null;
}

/** İki ISO zamanından ileride olanı (biri null ise diğeri). */
function latestIso(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) >= Date.parse(b) ? a : b;
}

function walletKindLabel(kind: string | null): string {
  if (kind === 'local') return 'In-app wallet';
  if (kind === 'walletconnect') return 'WalletConnect';
  if (kind === 'injected') return 'Browser wallet';
  return 'Wallet';
}

/** `next_allowed_at` gelecekteyse "23h 12m" gibi geri sayım; geçince null. */
function useCountdown(iso: string | null): string | null {
  const [now, setNow] = useState(() => Date.now());
  const target = iso ? Date.parse(iso) : NaN;
  const active = Number.isFinite(target) && target > now;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active, iso]);
  if (!active) return null;
  const secs = Math.max(0, Math.round((target - now) / 1000));
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ---------------------------------------------------------------------------
// Send sheet

function SendSheet({
  visible,
  onClose,
  wallet,
  onSubmit,
}: {
  visible: boolean;
  onClose: () => void;
  wallet: WalletOut;
  onSubmit: (payload: { asset_id: string | null; to: string; amount: string }) => void;
}) {
  const [assetId, setAssetId] = useState<string | null>(null); // null = MON
  const [to, setTo] = useState('');
  const [amount, setAmount] = useState('');
  const [errors, setErrors] = useState<{ to?: string; amount?: string }>({});

  const token = assetId ? (wallet.tokens.find((t) => t.asset_id === assetId) ?? null) : null;
  const symbol = token?.symbol ?? wallet.native.symbol;
  const decimals = token?.decimals ?? wallet.native.decimals;
  const available = token
    ? formatAmount(token.balance, token.symbol)
    : formatMon(wallet.native.balance_raw);

  const submit = () => {
    const next: { to?: string; amount?: string } = {};
    const trimmedTo = to.trim();
    if (!isEvmAddress(trimmedTo)) next.to = 'Enter a valid address (0x + 40 hex).';
    const parsed = parseAmountInput(amount, decimals);
    if (!parsed || parsed.raw <= 0n)
      next.amount = `Enter a valid amount (up to ${decimals} decimals).`;
    setErrors(next);
    if (next.to || next.amount || !parsed) return;
    onSubmit({ asset_id: assetId, to: checksum(trimmedTo), amount: parsed.human });
    setTo('');
    setAmount('');
  };

  return (
    <BottomSheet
      visible={visible}
      onClose={onClose}
      title="Send"
      subtitle={`From ${shortAddress(wallet.address)} on ${CHAIN_NAME}`}
      footer={<Button title={`Send ${symbol}`} onPress={submit} fullWidth />}
    >
      <View style={styles.chips}>
        <Chip
          label={wallet.native.symbol}
          active={assetId === null}
          onPress={() => setAssetId(null)}
        />
        {wallet.tokens.map((t) => (
          <Chip
            key={t.asset_id}
            label={t.symbol}
            active={assetId === t.asset_id}
            onPress={() => setAssetId(t.asset_id)}
          />
        ))}
      </View>
      <Field
        label="Recipient address"
        placeholder="0x…"
        value={to}
        onChangeText={setTo}
        autoCapitalize="none"
        autoCorrect={false}
        error={errors.to}
      />
      <Field
        label="Amount"
        placeholder="0.00"
        value={amount}
        onChangeText={setAmount}
        keyboardType="decimal-pad"
        suffix={symbol}
        error={errors.amount}
        hint={`Available: ${available}`}
      />
      <Text variant="caption" color="text3">
        Your wallet will ask you to confirm the transaction. Gas is paid in MON.
      </Text>
    </BottomSheet>
  );
}

// ---------------------------------------------------------------------------
// Local wallet security sheets (native only)

const REVEAL_SECONDS = 30;

function BackupSheet({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const [key, setKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [left, setLeft] = useState(REVEAL_SECONDS);

  const close = () => {
    setKey(null);
    setError(null);
    setLeft(REVEAL_SECONDS);
    onClose();
  };

  useEffect(() => {
    if (!key) return;
    const id = setInterval(() => {
      setLeft((v) => {
        if (v <= 1) {
          clearInterval(id);
          setKey(null);
          return REVEAL_SECONDS;
        }
        return v - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [key]);

  const reveal = async () => {
    setError(null);
    try {
      const pk = await getLocalWallet()?.exportPrivateKey();
      if (!pk) {
        setError('No in-app wallet key was found on this device.');
        return;
      }
      setLeft(REVEAL_SECONDS);
      setKey(pk);
    } catch (err) {
      setError(userMessage(err));
    }
  };

  return (
    <BottomSheet
      visible={visible}
      onClose={close}
      title="Back up private key"
      subtitle="Anyone with this key controls the wallet. Never share it."
    >
      {key ? (
        <>
          <Text variant="captionStrong" selectable style={styles.mono}>
            {key}
          </Text>
          <Text variant="caption" color="text3">
            Hidden again in {left}s.
          </Text>
          <Button
            title="Copy key"
            variant="secondary"
            leftIcon={<Copy size={14} color={colors.navy900} />}
            onPress={() => void Clipboard.setStringAsync(key)}
          />
        </>
      ) : (
        <>
          <Text variant="body" color="text2">
            The key is shown for {REVEAL_SECONDS} seconds. Store it somewhere safe; it is the only
            way to recover the MON and test tokens on this wallet.
          </Text>
          {error ? (
            <Text variant="caption" color="loss">
              {error}
            </Text>
          ) : null}
          <Button title="Reveal private key" variant="danger" onPress={() => void reveal()} />
        </>
      )}
    </BottomSheet>
  );
}

function ForgetSheet({
  visible,
  onClose,
  onBackup,
  onForget,
}: {
  visible: boolean;
  onClose: () => void;
  onBackup: () => void;
  onForget: () => Promise<void>;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setStep(1);
    setConfirm('');
    setBusy(false);
    setError(null);
  };
  const close = () => {
    if (busy) return;
    reset();
    onClose();
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await onForget();
    } catch (err) {
      setError(userMessage(err));
      setBusy(false);
    }
  };

  return (
    <BottomSheet
      visible={visible}
      onClose={close}
      title="Forget in-app wallet"
      subtitle={step === 1 ? 'Step 1 of 2' : 'Step 2 of 2'}
    >
      {step === 1 ? (
        <>
          <Text variant="body" color="text2">
            Your MON and test tokens stay on this address. Without the private key you cannot access
            them again. Back up first?
          </Text>
          <View style={styles.actionsRow}>
            <Button
              title="Back up"
              variant="secondary"
              onPress={() => {
                reset();
                onBackup();
              }}
              style={{ flex: 1 }}
            />
            <Button
              title="Continue"
              variant="danger"
              onPress={() => setStep(2)}
              style={{ flex: 1 }}
            />
          </View>
        </>
      ) : (
        <>
          <Field
            label="Type FORGET to confirm"
            placeholder="FORGET"
            value={confirm}
            onChangeText={setConfirm}
            autoCapitalize="characters"
            autoCorrect={false}
            error={error ?? undefined}
          />
          <Button
            title="Forget wallet"
            variant="danger"
            disabled={confirm.trim() !== 'FORGET' || busy}
            loading={busy}
            onPress={() => void run()}
          />
        </>
      )}
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: layout.screenPaddingH, paddingBottom: spacing.xl, gap: spacing.md },
  card: { gap: spacing.md },
  state: { gap: spacing.md, alignItems: 'flex-start' },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  actionsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  mono: {
    fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
  },
  qr: { alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.md },
  hint: {
    padding: spacing.sm,
    borderRadius: radius.sm,
    backgroundColor: colors.amberBg,
    alignSelf: 'flex-start',
  },
  tokenRight: { alignItems: 'flex-end', gap: spacing.xs },
  notice: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
  },
  noticeOk: { backgroundColor: colors.greenBg },
  noticeErr: { backgroundColor: colors.redBg },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
});
