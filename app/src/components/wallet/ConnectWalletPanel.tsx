import * as Linking from 'expo-linking';
import { useEffect, useState } from 'react';
import { Platform, StyleSheet, View } from 'react-native';
import type { Address } from 'viem';

import { ChainBanner } from './ChainBanner';
import { WalletConnectSheet } from './WalletConnectSheet';
import { Button, Field, Text } from '@/components/ui';
import { CHAIN_NAME, ChainError, shortAddress } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import {
  getLocalWallet,
  listInjectedWallets,
  localWalletAvailable,
  walletConnectAvailable,
} from '@/lib/wallet';
import { useSession } from '@/store/session';
import { colors, radius, spacing } from '@/theme';

/**
 * Cüzdan bağlama paneli (04 §6.1, §6.7) — giriş ve kayıt ekranlarında ortak.
 *
 *   Web    → `Connect wallet` (injected: MetaMask, Rabby… EIP-6963/window.ethereum)
 *            + `WalletConnect` (projectId varsa; QR modalı wagmi'den gelir).
 *   Native → `Connect wallet` (WalletConnect; projectId yoksa gizli + uyarı)
 *            + `Use in-app wallet` (oluştur) / `Continue with in-app wallet · 0x…` (varsa)
 *            + `Import a private key` (yerel cüzdan yokken).
 *
 * Bağlanma yalnız `useSession.connectWallet({ mode | connectorId })` ile (açık seçim, K9);
 * SIWE girişi paneli çağıran ekrandadır (`onConnected`). Yanlış zincirde `ChainBanner` görünür.
 */
export interface ConnectWalletPanelProps {
  onConnected: (address: Address) => void | Promise<void>;
  /** Kayıt kartı gibi dar alanlar: yardım metinleri gizlenir. */
  compact?: boolean;
  /** Ör. sunucu zinciri uyuşmazlığında düğmeler kapalı. */
  disabled?: boolean;
}

type Path = 'injected' | 'walletconnect' | 'local' | 'import';

const METAMASK_DOWNLOAD_URL = 'https://metamask.io/download/';
const PRIVATE_KEY_RE = /^(0x)?[0-9a-fA-F]{64}$/;

function hasInjectedProvider(): boolean {
  return (
    Platform.OS === 'web' &&
    typeof window !== 'undefined' &&
    Boolean((window as { ethereum?: unknown }).ethereum)
  );
}

export function ConnectWalletPanel({ onConnected, compact, disabled }: ConnectWalletPanelProps) {
  const connectWallet = useSession((s) => s.connectWallet);
  const pairingUri = useSession((s) => s.pairingUri);
  const cancelPairing = useSession((s) => s.cancelPairing);

  const [busy, setBusy] = useState<Path | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noInjected, setNoInjected] = useState(
    () => Platform.OS === 'web' && !hasInjectedProvider(),
  );
  const [localAddress, setLocalAddress] = useState<Address | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [importKey, setImportKey] = useState('');
  const [importError, setImportError] = useState<string | null>(null);

  const isWeb = Platform.OS === 'web';
  const wcAvailable = walletConnectAvailable();
  const localAvailable = !isWeb && localWalletAvailable;
  // EIP-6963 ile keşfedilen tarayıcı cüzdanları (yalnız bilgi; bağlantı genel `injected` yoludur).
  const detected = isWeb && !noInjected ? listInjectedWallets().map((w) => w.name) : [];

  // Yerel cüzdan var mı? Etiket: "Continue with in-app wallet · 0x67aD…FF19"
  useEffect(() => {
    if (!localAvailable) return;
    let cancelled = false;
    getLocalWallet()
      ?.address()
      .then((addr) => {
        if (!cancelled) setLocalAddress(addr);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [localAvailable]);

  // Web: uzantı sayfa yüklendikten sonra enjekte edilebilir; bir kez daha bak.
  useEffect(() => {
    if (!isWeb || !noInjected) return;
    const id = setTimeout(() => setNoInjected(!hasInjectedProvider()), 500);
    return () => clearTimeout(id);
  }, [isWeb, noInjected]);

  const run = async (path: Path, action: () => Promise<Address>) => {
    setBusy(path);
    setError(null);
    try {
      const address = await action();
      await onConnected(address);
    } catch (err) {
      if (err instanceof ChainError && err.code === 'NOT_AVAILABLE' && path === 'injected') {
        setNoInjected(true);
      }
      setError(userMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const onInjected = () => run('injected', () => connectWallet({ connectorId: 'injected' }));
  const onWalletConnectWeb = () =>
    run('walletconnect', () => connectWallet({ connectorId: 'walletConnect' }));
  const onWalletConnectNative = () =>
    run('walletconnect', () => connectWallet({ mode: 'walletconnect' }));
  const onLocal = () => run('local', () => connectWallet({ mode: 'local' }));

  const onImport = () => {
    const key = importKey.trim();
    if (!PRIVATE_KEY_RE.test(key)) {
      setImportError('Enter a 64-character hex private key (with or without 0x).');
      return;
    }
    setImportError(null);
    void run('import', async () => {
      const local = getLocalWallet();
      if (!local) {
        throw new ChainError(
          'The in-app wallet is not available on this platform.',
          'NOT_AVAILABLE',
        );
      }
      await local.importPrivateKey(key.startsWith('0x') ? key : `0x${key}`);
      setImportKey('');
      setShowImport(false);
      return connectWallet({ mode: 'local' });
    });
  };

  const anyBusy = busy !== null;
  const lock = disabled || anyBusy;

  return (
    <View style={styles.root}>
      <ChainBanner />

      {isWeb ? (
        <>
          <Button
            title="Connect wallet"
            onPress={onInjected}
            loading={busy === 'injected'}
            disabled={lock || noInjected}
            fullWidth
          />
          {!compact ? (
            <Text variant="caption" color="text3">
              {noInjected
                ? 'No browser wallet found.'
                : detected.length > 0
                  ? `Detected: ${detected.slice(0, 3).join(', ')}${detected.length > 3 ? '…' : ''}`
                  : 'MetaMask, Rabby or any browser wallet'}
            </Text>
          ) : null}
          {noInjected ? (
            <Button
              title="Install MetaMask"
              variant="ghost"
              onPress={() => Linking.openURL(METAMASK_DOWNLOAD_URL)}
              disabled={anyBusy}
              fullWidth
            />
          ) : null}
          {wcAvailable ? (
            <Button
              title="WalletConnect"
              variant="secondary"
              onPress={onWalletConnectWeb}
              loading={busy === 'walletconnect'}
              disabled={lock}
              fullWidth
            />
          ) : null}
        </>
      ) : (
        <>
          {wcAvailable ? (
            <>
              <Button
                title="Connect wallet"
                onPress={onWalletConnectNative}
                loading={busy === 'walletconnect'}
                disabled={lock}
                fullWidth
              />
              {!compact ? (
                <Text variant="caption" color="text3">
                  Opens MetaMask, Rainbow or Trust over WalletConnect
                </Text>
              ) : null}
            </>
          ) : !compact ? (
            <Text variant="caption" color="text3">
              WalletConnect is not configured (EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID). Use the in-app
              wallet below.
            </Text>
          ) : null}

          {localAvailable ? (
            <>
              <Button
                title={
                  localAddress
                    ? `Continue with in-app wallet · ${shortAddress(localAddress, 6, 4)}`
                    : 'Use in-app wallet'
                }
                variant={wcAvailable ? 'secondary' : 'primary'}
                onPress={onLocal}
                loading={busy === 'local'}
                disabled={lock}
                fullWidth
              />
              {!compact ? (
                <Text variant="caption" color="text3">
                  Testnet only — key stays on this device
                </Text>
              ) : null}
              {!localAddress ? (
                <Button
                  title={showImport ? 'Cancel import' : 'Import a private key'}
                  variant="ghost"
                  size="sm"
                  onPress={() => {
                    setShowImport((v) => !v);
                    setImportError(null);
                  }}
                  disabled={lock}
                  fullWidth
                />
              ) : null}
              {showImport && !localAddress ? (
                <View style={styles.importBox}>
                  <Field
                    label="Private key"
                    value={importKey}
                    onChangeText={setImportKey}
                    placeholder="0x…"
                    autoCapitalize="none"
                    autoCorrect={false}
                    secureTextEntry
                    error={importError ?? undefined}
                    hint={`Stored in the device keychain; used only on ${CHAIN_NAME}.`}
                  />
                  <Button
                    title="Import and continue"
                    variant="secondary"
                    onPress={onImport}
                    loading={busy === 'import'}
                    disabled={lock}
                    fullWidth
                  />
                </View>
              ) : null}
            </>
          ) : null}
        </>
      )}

      {error ? (
        <View style={styles.error}>
          <Text variant="caption" color="loss">
            {error}
          </Text>
        </View>
      ) : null}

      {!isWeb ? <WalletConnectSheet uri={pairingUri} onClose={cancelPairing} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { alignSelf: 'stretch', gap: spacing.sm },
  importBox: { gap: spacing.sm, paddingTop: spacing.xs },
  error: { padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.redBg },
});
