/**
 * Native (iOS/Android) cüzdan dağıtıcısı — iki yol, mod **açıkça seçilir** (K9):
 *
 *   walletconnect → harici EVM cüzdan (MetaMask, Rainbow, Trust…) reown relay'i üzerinden
 *                   (./walletconnect.ts). `EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID` yoksa kapalıdır.
 *   local         → uygulama içi cüzdan (./local.ts), yalnız `mode: 'local'` ile istenince oluşturulur.
 *
 * Varsayılan mod YOKTUR: `connect()` mod almaz ve kayıtlı mod da yoksa MISSING_CONFIG fırlatır
 * (inceleme §D hata 5 — sessiz yerel cüzdan yok).
 *
 * Metro web hedefinde bu dosya yerine ./wallet.web.ts kullanılır.
 */
import { localWallet, localWalletAvailable } from './local';
import type { ConnectOptions, WalletAdapter, WalletEvent, WalletInfo, WalletKind } from './types';
import { walletConnectWallet } from './walletconnect';
import { CHAIN_ID } from '@/lib/chain/config';
import { ChainError } from '@/lib/chain/errors';
import { env } from '@/lib/env';
import { debugLog } from '@/lib/log';
import { STORAGE_KEYS, plainStorage } from '@/lib/storage';

export type NativeWalletMode = 'walletconnect' | 'local';

let mode: NativeWalletMode | null = null;

export function walletConnectAvailable(): boolean {
  return Boolean(env.walletConnectProjectId);
}

export { localWalletAvailable };

/** Native'de tarayıcı cüzdanı yoktur; web karşılığı (./wallet.web.ts) EIP-6963 listesini döner. */
export function listInjectedWallets(): WalletInfo[] {
  return [];
}

/** Kayıtlı modu okur; eski/geçersiz değerler silinir. Varsayılan yok → null. */
export async function restoreWalletMode(): Promise<NativeWalletMode | null> {
  const stored = await plainStorage.get(STORAGE_KEYS.walletMode);
  if (stored === 'walletconnect' || stored === 'local') {
    mode = stored;
    return mode;
  }
  if (stored) await plainStorage.remove(STORAGE_KEYS.walletMode).catch(() => undefined);
  mode = null;
  return null;
}

async function setMode(next: NativeWalletMode): Promise<void> {
  mode = next;
  await plainStorage.set(STORAGE_KEYS.walletMode, next);
}

/** `forgetLocalWallet()` sonrası çağrılır: kayıtlı mod silinir. */
export async function clearWalletMode(): Promise<void> {
  mode = null;
  await plainStorage.remove(STORAGE_KEYS.walletMode).catch(() => undefined);
}

function requireMode(): NativeWalletMode {
  if (!mode) throw new ChainError('Wallet is not connected.', 'NOT_CONNECTED');
  return mode;
}

export const wallet: WalletAdapter = {
  get available() {
    return walletConnectAvailable() || localWalletAvailable;
  },

  get kind(): WalletKind | null {
    return mode;
  },

  async connect(options?: ConnectOptions) {
    const requested = options?.mode ?? mode;
    debugLog('wallet', 'connect çağrıldı', { istenen: requested, kayıtlı: mode });

    if (!requested) {
      throw new ChainError(
        'Choose how to connect: WalletConnect or in-app wallet.',
        'MISSING_CONFIG',
      );
    }

    if (requested === 'walletconnect') {
      if (!walletConnectAvailable()) {
        throw new ChainError(
          'WalletConnect is not configured. Add EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID, or use the in-app wallet.',
          'MISSING_CONFIG',
        );
      }
      const result = await walletConnectWallet.connect(options);
      await setMode('walletconnect');
      return result;
    }

    // local — yalnız açık istekle
    const address = (await localWallet.address()) ?? (await localWallet.create());
    await setMode('local');
    debugLog('wallet', 'UYGULAMA İÇİ cüzdan kullanıldı', { address });
    return {
      address,
      chainId: CHAIN_ID,
      wallet: { id: 'local', kind: 'local', name: 'In-app wallet' },
    };
  },

  async abortPairing() {
    await walletConnectWallet.abortPairing?.();
  },

  async disconnect() {
    if (mode === 'walletconnect') await walletConnectWallet.disconnect();
    // Yerel cüzdan çıkışta silinmez: kullanıcı aynı adresle geri dönebilsin.
    // Silme ayrı: getLocalWallet().forget() + clearWalletMode() (04 §6.5).
  },

  async getAddress() {
    if (mode === 'walletconnect') return walletConnectWallet.getAddress();
    if (mode === 'local') return localWallet.address();
    return null;
  },

  async getChainId() {
    if (mode === 'walletconnect') return walletConnectWallet.getChainId();
    if (mode === 'local') return (await localWallet.exists()) ? CHAIN_ID : null;
    return null;
  },

  async switchChain(chainId: number) {
    if (requireMode() === 'walletconnect') return walletConnectWallet.switchChain(chainId);
    // Yerel cüzdan yalnız Monad Testnet'te çalışır.
    if (chainId !== CHAIN_ID) {
      throw new ChainError(
        `The in-app wallet only works on Monad Testnet (${CHAIN_ID}).`,
        'WRONG_NETWORK',
        { chainId },
      );
    }
  },

  async signMessage(message) {
    if (requireMode() === 'walletconnect') return walletConnectWallet.signMessage(message);
    return localWallet.signMessage(message);
  },

  async sendTransaction(tx) {
    if (requireMode() === 'walletconnect') return walletConnectWallet.sendTransaction(tx);
    return localWallet.sendTransaction(tx);
  },

  on(listener: (e: WalletEvent) => void) {
    // WC olayları iletilir; yerel cüzdanda olay yoktur (chainId sabit 10143).
    if (!walletConnectAvailable()) return () => undefined;
    return walletConnectWallet.on((e) => {
      if (mode === 'walletconnect') listener(e);
    });
  },
};
