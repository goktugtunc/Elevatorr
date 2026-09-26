/**
 * Web cüzdan adaptörü — wagmi 2.x + viem (FE-32). Metro web hedefinde `./wallet` yerine bu dosya çözülür.
 *
 * Bağlayıcılar: `injected` (MetaMask, Rabby, Phantom EVM… EIP-6963 keşfi) ve
 * `walletConnect` (projectId varsa; QR modalı wagmi'nin kendisinden gelir).
 * Hook ve `WagmiProvider` KULLANILMAZ — adaptör imperatiftir (`wagmi/actions`).
 * Oturum localStorage'da kalıcıdır; ilk `getAddress()` `reconnect()` bekler.
 * Uygulama içi cüzdan web'de yoktur (K9).
 */
import { createConfig, createStorage, http, type Config } from 'wagmi';
import {
  connect,
  disconnect,
  getAccount,
  getChainId,
  getConnectors,
  reconnect,
  sendTransaction,
  signMessage,
  switchChain,
  watchAccount,
  watchChainId,
} from 'wagmi/actions';
import { injected, walletConnect } from 'wagmi/connectors';
import { getAddress, type Address, type Chain } from 'viem';

import { localWalletAvailable } from './local';
import type { ConnectOptions, WalletAdapter, WalletEvent, WalletInfo, WalletKind } from './types';
import { CHAIN_ID, addEthereumChainParams, getChain, getChainConfig } from '@/lib/chain/config';
import { ChainError, toChainError } from '@/lib/chain/errors';
import { env } from '@/lib/env';
import { debugError, debugLog } from '@/lib/log';

export type NativeWalletMode = 'walletconnect' | 'local';

const APP_METADATA = {
  name: 'TraderKirala',
  description: 'Rent a trader; your capital stays locked in a vault on Monad.',
  url: 'https://traderkirala.app',
  icons: ['https://traderkirala.app/icon.png'],
};

let config: Config | null = null;
let reconnected: Promise<void> | null = null;

function hasWindow(): boolean {
  return typeof window !== 'undefined';
}

function hasInjectedProvider(): boolean {
  return hasWindow() && Boolean((window as { ethereum?: unknown }).ethereum);
}

/** Tembel kurulum: rpcUrl çalışma zamanı değerini (env ya da /config) alır. */
export function getWagmiConfig(): Config {
  if (config) return config;
  const chain = getChain() as Chain;
  const connectors = [
    injected({ shimDisconnect: true }),
    ...(env.walletConnectProjectId
      ? [
          walletConnect({
            projectId: env.walletConnectProjectId,
            showQrModal: true,
            metadata: APP_METADATA,
          }),
        ]
      : []),
  ];
  config = createConfig({
    chains: [chain],
    connectors,
    transports: { [CHAIN_ID]: http(getChainConfig().rpcUrl) },
    storage: createStorage({ storage: hasWindow() ? window.localStorage : undefined }),
    ssr: false,
    multiInjectedProviderDiscovery: true,
  });
  return config;
}

/** Kalıcı oturumu bir kez geri yükler. */
function ensureReconnected(): Promise<void> {
  if (!reconnected) {
    reconnected = reconnect(getWagmiConfig())
      .then(() => undefined)
      .catch((err) => {
        debugError('wallet:web', 'reconnect başarısız', err);
      });
  }
  return reconnected;
}

function kindOf(connectorType: string | undefined): WalletKind {
  return connectorType === 'walletConnect' ? 'walletconnect' : 'injected';
}

function pickConnector(connectorId: ConnectOptions['connectorId']) {
  const connectors = getConnectors(getWagmiConfig());
  const wanted: 'injected' | 'walletConnect' =
    connectorId ?? (hasInjectedProvider() ? 'injected' : 'walletConnect');

  if (wanted === 'injected') {
    if (!hasInjectedProvider()) {
      throw new ChainError(
        'No browser wallet found. Install MetaMask or Rabby, or use WalletConnect.',
        'NOT_AVAILABLE',
      );
    }
    // Genel `injected` bağlayıcı (window.ethereum). EIP-6963 ile keşfedilen cüzdanlar
    // `listInjectedWallets()` ile ayrıca listelenebilir.
    const c = connectors.find((x) => x.id === 'injected') ?? connectors.find((x) => x.type === 'injected');
    if (!c) throw new ChainError('No browser wallet found.', 'NOT_AVAILABLE');
    return c;
  }

  const wc = connectors.find((x) => x.id === 'walletConnect' || x.type === 'walletConnect');
  if (!wc) {
    throw new ChainError(
      'WalletConnect is not configured. Add EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID.',
      'MISSING_CONFIG',
    );
  }
  return wc;
}

/** EIP-6963 ile keşfedilen tarayıcı cüzdanları (UI listesi için; opsiyonel). */
export function listInjectedWallets(): WalletInfo[] {
  return getConnectors(getWagmiConfig())
    .filter((c) => c.type === 'injected' && c.id !== 'injected')
    .map((c) => ({ id: c.id, kind: 'injected' as const, name: c.name, icon: c.icon }));
}

export const wallet: WalletAdapter = {
  /** Tarayıcı cüzdanı ya da WalletConnect: web'de her zaman en az bir yol denenebilir. */
  get available() {
    return hasInjectedProvider() || Boolean(env.walletConnectProjectId);
  },

  get kind(): WalletKind | null {
    const acc = getAccount(getWagmiConfig());
    if (acc.status !== 'connected' || !acc.connector) return null;
    return kindOf(acc.connector.type);
  },

  async connect(options?: ConnectOptions) {
    try {
      const cfg = getWagmiConfig();
      await ensureReconnected();
      const connector = pickConnector(options?.connectorId);

      const existing = getAccount(cfg);
      let address: Address;
      if (existing.status === 'connected' && existing.address && existing.connector?.id === connector.id) {
        address = existing.address;
        debugLog('wallet:web', 'MEVCUT wagmi bağlantısı kullanıldı', { connector: connector.id });
      } else {
        if (existing.status === 'connected') await disconnect(cfg).catch(() => undefined);
        const result = await connect(cfg, { connector });
        address = result.accounts[0];
        debugLog('wallet:web', 'bağlandı', { connector: connector.id, chainId: result.chainId });
      }

      // Hedef zincire geçmeyi dene; reddedilirse connect başarılıdır, chainId farklı döner (UI ChainBanner).
      const target = options?.targetChainId ?? CHAIN_ID;
      let chainId = getChainId(cfg);
      if (chainId !== target) {
        try {
          await switchChain(cfg, {
            chainId: target as typeof CHAIN_ID,
            addEthereumChainParameter: addEthereumChainParams(),
          });
          chainId = target;
        } catch (err) {
          debugError('wallet:web', 'bağlantı sonrası zincir geçişi başarısız', err);
          chainId = getChainId(cfg);
        }
      }

      const info: WalletInfo = {
        id: connector.id,
        kind: kindOf(connector.type),
        name: connector.name,
        icon: connector.icon,
      };
      return { address: getAddress(address), chainId, wallet: info };
    } catch (err) {
      throw toChainError(err);
    }
  },

  /** Web'de bekleyen eşleşme kavramı yok; WalletConnect modalı wagmi yönetir. */
  async abortPairing() {
    /* no-op */
  },

  async disconnect() {
    const cfg = getWagmiConfig();
    await disconnect(cfg).catch(() => undefined);
  },

  async getAddress() {
    await ensureReconnected();
    const acc = getAccount(getWagmiConfig());
    return acc.address ? getAddress(acc.address) : null;
  },

  async getChainId() {
    await ensureReconnected();
    const acc = getAccount(getWagmiConfig());
    if (acc.status !== 'connected') return null;
    return acc.chainId ?? getChainId(getWagmiConfig());
  },

  async switchChain(chainId: number) {
    try {
      await switchChain(getWagmiConfig(), {
        chainId: chainId as typeof CHAIN_ID,
        addEthereumChainParameter: chainId === CHAIN_ID ? addEthereumChainParams() : undefined,
      });
    } catch (err) {
      const mapped = toChainError(err);
      if (mapped.code === 'WRONG_NETWORK' || mapped.code === 'UNKNOWN') {
        throw new ChainError(
          `Add Monad Testnet to your wallet manually: RPC ${getChainConfig().rpcUrl}, chain ${CHAIN_ID}.`,
          'CHAIN_NOT_ADDED',
          { cause: err, chainId },
        );
      }
      throw mapped;
    }
  },

  async signMessage(message: string) {
    try {
      return await signMessage(getWagmiConfig(), { message });
    } catch (err) {
      throw toChainError(err);
    }
  },

  /** gas backend'den; wagmi/viem yeniden tahmin etmez. */
  async sendTransaction(tx) {
    try {
      return await sendTransaction(getWagmiConfig(), {
        to: tx.to,
        data: tx.data,
        value: tx.value,
        gas: tx.gas,
        chainId: CHAIN_ID,
      });
    } catch (err) {
      throw toChainError(err);
    }
  },

  on(listener: (e: WalletEvent) => void) {
    const cfg = getWagmiConfig();
    const unwatchAccount = watchAccount(cfg, {
      onChange(account, prev) {
        if (account.status === 'disconnected' && prev.status !== 'disconnected') {
          listener({ type: 'disconnect' });
          return;
        }
        if (account.address && account.address !== prev.address) {
          listener({ type: 'accountsChanged', accounts: [getAddress(account.address)] });
        }
      },
    });
    const unwatchChain = watchChainId(cfg, {
      onChange(chainId) {
        listener({ type: 'chainChanged', chainId });
      },
    });
    return () => {
      unwatchAccount();
      unwatchChain();
    };
  },
};

export function walletConnectAvailable(): boolean {
  return Boolean(env.walletConnectProjectId);
}

export { localWalletAvailable };

/** Web'de native mod kavramı yok. */
export async function restoreWalletMode(): Promise<NativeWalletMode | null> {
  return null;
}

export async function clearWalletMode(): Promise<void> {
  /* no-op */
}
