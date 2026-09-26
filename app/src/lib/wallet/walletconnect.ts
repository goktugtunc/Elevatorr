/**
 * WalletConnect v2 (`UniversalProvider`) ile harici EVM cüzdan — `eip155:10143` (FE-33, K9).
 *
 * Expo Go uyumlu: paket saf JS, native modül gerektirmez (polyfill'ler `src/polyfills.ts`).
 * Akış: connect() → `display_uri` → UI QR/deep link gösterir → kullanıcı cüzdanda onaylar →
 * oturum kurulur, adres CAIP hesabından okunur. Oturum WalletConnect'in kendi deposunda
 * (AsyncStorage) kalıcıdır. Proje kimliği yoksa yol kapalıdır (bkz. ./wallet.ts).
 *
 * Namespace **optional** verilir: Monad'ı tanımayan cüzdan da eşleşir, sonra
 * `switchChain` (→ `wallet_addEthereumChain`) denenir.
 */
import { UniversalProvider } from '@walletconnect/universal-provider';
import * as ExpoLinking from 'expo-linking';
import { Linking } from 'react-native';
import { getAddress, numberToHex, stringToHex, type Address, type Hex } from 'viem';

import { WC_WALLETS } from './deeplinks';
import type { ConnectOptions, TxRequest, WalletAdapter, WalletEvent, WalletInfo } from './types';
import { CHAIN_ID, CHAIN_ID_HEX, addEthereumChainParams, getChainConfig } from '@/lib/chain/config';
import { ChainError, toChainError } from '@/lib/chain/errors';
import { env } from '@/lib/env';
import { debugError, debugLog } from '@/lib/log';

/** Paket hem default hem adlandırılmış dışa aktarım yapıyor; tip için örnek türünü alıyoruz. */
type Provider = InstanceType<typeof UniversalProvider>;

const CAIP_CHAIN = `eip155:${CHAIN_ID}` as const;

const METHODS = [
  'personal_sign',
  'eth_sendTransaction',
  'eth_signTypedData_v4', // ileride permit için (K4 sonraki sprint)
  'wallet_switchEthereumChain',
  'wallet_addEthereumChain',
  'eth_chainId',
  'eth_accounts',
];
const EVENTS = ['accountsChanged', 'chainChanged'];

/**
 * Cüzdan imzadan sonra buraya geri döner. Expo Go'da uygulamanın kendi scheme'i
 * (traderkirala://) kayıtlı DEĞİLDİR; `Linking.createURL` Expo Go'da
 * `exp://<host>/--/` üretir, derlenmiş uygulamada `traderkirala://`.
 */
const RETURN_URL = ExpoLinking.createURL('/');

const APP_METADATA = {
  name: 'TraderKirala',
  description: 'Rent a trader; your capital stays locked in a vault on Monad.',
  url: 'https://traderkirala.app',
  icons: ['https://traderkirala.app/icon.png'],
  redirect: { native: RETURN_URL, universal: '' },
};

/** İstek yanıtsız kalırsa sonsuza kadar beklemeyelim. */
const REQUEST_TIMEOUT_MS = 120_000;

let providerPromise: Promise<Provider> | null = null;
const listeners = new Set<(e: WalletEvent) => void>();
let eventsBound = false;
/** `chainChanged` ile güncellenen, oturumdan türetilen zincir. */
let currentChainId: number | null = null;

function requireProjectId(): string {
  if (!env.walletConnectProjectId) {
    throw new ChainError(
      'WalletConnect project ID is missing. Add EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID to .env (free at dashboard.reown.com).',
      'MISSING_CONFIG',
    );
  }
  return env.walletConnectProjectId;
}

async function getProvider(): Promise<Provider> {
  if (!providerPromise) {
    providerPromise = UniversalProvider.init({
      projectId: requireProjectId(),
      metadata: APP_METADATA,
    }).then((p) => {
      bindEvents(p);
      return p;
    });
    providerPromise.catch(() => {
      providerPromise = null;
    });
  }
  return providerPromise;
}

function emit(e: WalletEvent): void {
  for (const l of listeners) {
    try {
      l(e);
    } catch (err) {
      debugError('wallet:wc', 'dinleyici hatası', err);
    }
  }
}

function parseChainId(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const s = value.includes(':') ? value.split(':').pop()! : value;
    const n = s.startsWith('0x') ? parseInt(s, 16) : Number(s);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function toChecksum(value: string): Address | null {
  const raw = value.includes(':') ? value.split(':').pop()! : value;
  try {
    return getAddress(raw);
  } catch {
    return null;
  }
}

function bindEvents(provider: Provider): void {
  if (eventsBound) return;
  eventsBound = true;
  const p = provider as unknown as {
    on: (event: string, cb: (...args: unknown[]) => void) => void;
  };
  p.on('session_event', (payload) => {
    const params = (payload as { params?: { event?: { name?: string; data?: unknown } } })?.params;
    const name = params?.event?.name;
    const data = params?.event?.data;
    if (name === 'accountsChanged') {
      const accounts = (Array.isArray(data) ? data : [])
        .map((a) => toChecksum(String(a)))
        .filter((a): a is Address => a !== null);
      emit({ type: 'accountsChanged', accounts });
    } else if (name === 'chainChanged') {
      const id = parseChainId(data);
      if (id !== null) {
        currentChainId = id;
        emit({ type: 'chainChanged', chainId: id });
      }
    }
  });
  p.on('chainChanged', (value) => {
    const id = parseChainId(value);
    if (id !== null && id !== currentChainId) {
      currentChainId = id;
      emit({ type: 'chainChanged', chainId: id });
    }
  });
  p.on('accountsChanged', (value) => {
    const accounts = (Array.isArray(value) ? value : [])
      .map((a) => toChecksum(String(a)))
      .filter((a): a is Address => a !== null);
    if (accounts.length) emit({ type: 'accountsChanged', accounts });
  });
  const onGone = () => {
    currentChainId = null;
    emit({ type: 'disconnect' });
  };
  p.on('session_delete', onGone);
  p.on('session_expire', onGone);
}

/** Oturumdaki eip155 hesapları: `'eip155:10143:0x…'`. */
function sessionAccounts(provider: Provider): string[] {
  return provider.session?.namespaces?.eip155?.accounts ?? [];
}

/** Önce chain 10143 olan hesap, yoksa ilk eip155 hesabı. */
function pickAccount(provider: Provider): { address: Address; chainId: number } | null {
  const accounts = sessionAccounts(provider);
  if (accounts.length === 0) return null;
  const preferred = accounts.find((a) => a.startsWith(`${CAIP_CHAIN}:`)) ?? accounts[0];
  const [, chainPart, addressPart] = preferred.split(':');
  const address = toChecksum(addressPart ?? '');
  const chainId = parseChainId(chainPart);
  if (!address || chainId === null) return null;
  return { address, chainId };
}

function sessionHasChain(provider: Provider, chainId: number): boolean {
  const ns = provider.session?.namespaces?.eip155;
  const chains = ns?.chains ?? [];
  return (
    chains.includes(`eip155:${chainId}`) ||
    sessionAccounts(provider).some((a) => a.startsWith(`eip155:${chainId}:`))
  );
}

function walletInfo(provider: Provider): WalletInfo {
  const peer = provider.session?.peer?.metadata;
  return {
    id: 'walletconnect',
    kind: 'walletconnect',
    name: peer?.name || 'WalletConnect',
    icon: peer?.icons?.[0],
  };
}

/**
 * İmza isteği relay üzerinden cüzdana gider ama cüzdan uygulaması kendiliğinden
 * öne gelmez; dApp'in deep link ile açması gerekir. Adres:
 *   1. oturumdaki cüzdanın kendi `redirect.native`'i (en doğrusu)
 *   2. eşleşen cüzdanın kayıt defterindeki şeması (`peer.name` küçük harf `includes`)
 * Açılamazsa sessiz geçilir — kullanıcı cüzdanı elle açıp onaylayabilir.
 */
async function bringWalletToFront(provider: Provider): Promise<void> {
  const peer = provider.session?.peer?.metadata;
  const fromSession = peer?.redirect?.native || peer?.redirect?.universal;
  const peerName = peer?.name?.toLowerCase() ?? '';
  const known = WC_WALLETS.find((w) => peerName && peerName.includes(w.id));
  const candidates = [fromSession, known?.native, known?.universal].filter(
    (v): v is string => typeof v === 'string' && v.length > 0,
  );

  debugLog('wallet:wc', 'cüzdan öne getiriliyor', { peer: peer?.name ?? null, candidates });

  for (const link of candidates) {
    try {
      await Linking.openURL(link);
      return;
    } catch {
      // sıradaki adresi dene
    }
  }
  debugLog('wallet:wc', 'cüzdan açılamadı — kullanıcı elle açmalı');
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () =>
        reject(
          new ChainError(
            'The wallet did not respond. Open your wallet app and approve the request, then try again.',
            'RPC_ERROR',
          ),
        ),
      ms,
    );
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

async function requireSession(): Promise<{ provider: Provider; address: Address; chainId: number }> {
  const provider = await getProvider();
  const picked = pickAccount(provider);
  if (!provider.session || !picked) {
    throw new ChainError('Wallet is not connected.', 'NOT_CONNECTED');
  }
  return { provider, address: picked.address, chainId: currentChainId ?? picked.chainId };
}

/** İsteğin yönlendirileceği CAIP zinciri: oturum 10143'ü tanıyorsa o, yoksa aktif zincir. */
function requestChain(provider: Provider, chainId: number): string {
  return sessionHasChain(provider, CHAIN_ID) ? CAIP_CHAIN : `eip155:${chainId}`;
}

async function switchChainInternal(provider: Provider, fromChainId: number, target: number): Promise<void> {
  const chain = requestChain(provider, fromChainId);
  const params = [{ chainId: numberToHex(target) }];
  try {
    await withTimeout(
      provider.request({ method: 'wallet_switchEthereumChain', params }, chain),
      REQUEST_TIMEOUT_MS,
    );
  } catch (err) {
    const mapped = toChainError(err);
    const shouldAdd =
      target === CHAIN_ID &&
      (mapped.code === 'CHAIN_NOT_ADDED' ||
        mapped.code === 'UNSUPPORTED_METHOD' ||
        mapped.code === 'WRONG_NETWORK' ||
        /unrecognized chain/i.test(mapped.message));
    if (!shouldAdd) throw mapped;
    debugLog('wallet:wc', 'zincir yok, wallet_addEthereumChain deneniyor');
    try {
      await withTimeout(
        provider.request({ method: 'wallet_addEthereumChain', params: [addEthereumChainParams()] }, chain),
        REQUEST_TIMEOUT_MS,
      );
      // Bazı cüzdanlar ekledikten sonra otomatik geçer; geçmeyenler için bir kez daha dene.
      await withTimeout(
        provider.request({ method: 'wallet_switchEthereumChain', params }, chain),
        REQUEST_TIMEOUT_MS,
      ).catch(() => undefined);
    } catch (addErr) {
      const addMapped = toChainError(addErr);
      if (addMapped.code === 'USER_REJECTED') throw addMapped;
      throw new ChainError(
        `Add Monad Testnet to your wallet manually: RPC ${getChainConfig().rpcUrl}, chain ${CHAIN_ID} (${CHAIN_ID_HEX}) — or use the in-app wallet.`,
        'CHAIN_NOT_ADDED',
        { cause: addErr, chainId: target },
      );
    }
  }
  if (target === CHAIN_ID) provider.setDefaultChain(CAIP_CHAIN, getChainConfig().rpcUrl);
  currentChainId = target;
}

export const walletConnectWallet: WalletAdapter = {
  /** Proje kimliği yoksa bağlanma denenmez; UI kurulum mesajı gösterir. */
  get available() {
    return Boolean(env.walletConnectProjectId);
  },

  get kind() {
    return 'walletconnect' as const;
  },

  async connect(options?: ConnectOptions) {
    try {
      const provider = await getProvider();
      // Önceki deneme yarıda kaldıysa (kullanıcı QR ekranını kapattı) temizle;
      // aksi hâlde connect() promise'i askıda kalıp dinleyici biriktiriyor.
      try {
        provider.abortPairingAttempt();
      } catch {
        /* bekleyen deneme yoksa sorun değil */
      }

      let picked = pickAccount(provider);
      if (picked && provider.session) {
        debugLog('wallet:wc', 'MEVCUT oturum kullanıldı, yeni eşleşme yok', { address: picked.address });
      } else {
        debugLog('wallet:wc', 'kayıtlı oturum yok, yeni eşleşme gerekiyor', {
          chain: CAIP_CHAIN,
          methods: METHODS,
        });
        const onUri = (uri: string) => {
          debugLog('wallet:wc', 'display_uri alındı', { uri: uri.slice(0, 40) });
          options?.onUri?.(uri);
        };
        provider.on('display_uri', onUri);
        try {
          const session = await provider.connect({
            optionalNamespaces: {
              eip155: {
                chains: [CAIP_CHAIN],
                methods: METHODS,
                events: EVENTS,
                rpcMap: { [CHAIN_ID]: getChainConfig().rpcUrl },
              },
            },
          });
          debugLog('wallet:wc', 'oturum yanıtı', {
            accounts: session?.namespaces?.eip155?.accounts ?? null,
            peer: session?.peer?.metadata?.name ?? null,
          });
        } finally {
          provider.off('display_uri', onUri);
        }
        picked = pickAccount(provider);
        if (!picked) {
          throw new ChainError('The wallet did not return an EVM account.', 'NOT_CONNECTED');
        }
      }

      currentChainId = picked.chainId;
      if (sessionHasChain(provider, CHAIN_ID)) {
        provider.setDefaultChain(CAIP_CHAIN, getChainConfig().rpcUrl);
      }

      // Hedef zincire geçmeyi dene; başarısızlık connect'i düşürmez (UI banner gösterir).
      const target = options?.targetChainId ?? CHAIN_ID;
      let chainId = picked.chainId;
      if (chainId !== target) {
        try {
          await switchChainInternal(provider, chainId, target);
          chainId = target;
        } catch (err) {
          debugError('wallet:wc', 'bağlantı sonrası zincir geçişi başarısız', err);
        }
      }

      return { address: picked.address, chainId, wallet: walletInfo(provider) };
    } catch (err) {
      debugError('wallet:wc', 'bağlantı başarısız', err);
      throw toChainError(err);
    }
  },

  /** QR ekranı kapatıldığında bekleyen eşleşmeyi bırakır. */
  async abortPairing() {
    const provider = await getProvider().catch(() => null);
    if (!provider) return;
    try {
      provider.abortPairingAttempt();
    } catch {
      /* bekleyen deneme yoktu */
    }
    await provider.cleanupPendingPairings().catch(() => undefined);
  },

  async disconnect() {
    const provider = await getProvider().catch(() => null);
    currentChainId = null;
    if (!provider?.session) return;
    await provider.disconnect().catch(() => undefined);
  },

  async getAddress() {
    try {
      const provider = await getProvider();
      return pickAccount(provider)?.address ?? null;
    } catch {
      return null;
    }
  },

  async getChainId() {
    try {
      const provider = await getProvider();
      const picked = pickAccount(provider);
      if (!picked) return null;
      return currentChainId ?? picked.chainId;
    } catch {
      return null;
    }
  },

  async switchChain(chainId: number) {
    const { provider, chainId: current } = await requireSession();
    if (current === chainId) return;
    await switchChainInternal(provider, current, chainId);
  },

  /** EIP-191 personal_sign: mesaj hex-kodlu UTF-8, param sırası `[data, address]`. */
  async signMessage(message: string) {
    try {
      const { provider, address, chainId } = await requireSession();
      debugLog('wallet:wc', 'imza isteği gönderiliyor', { method: 'personal_sign' });
      const pending = provider.request<Hex>(
        { method: 'personal_sign', params: [stringToHex(message), address] },
        requestChain(provider, chainId),
      );
      void bringWalletToFront(provider);
      const signature = await withTimeout(pending, REQUEST_TIMEOUT_MS);
      if (!signature || typeof signature !== 'string') {
        throw new ChainError('The wallet returned an empty signature.', 'UNKNOWN');
      }
      return signature;
    } catch (err) {
      throw toChainError(err);
    }
  },

  /** eth_sendTransaction — hex quantity'ler öndeki sıfırsız (`0x0`). */
  async sendTransaction(tx: TxRequest) {
    try {
      const { provider, address, chainId } = await requireSession();
      if (chainId !== CHAIN_ID) {
        throw new ChainError(
          'Your wallet is on a different network. Switch it to Monad Testnet (chain 10143).',
          'WRONG_NETWORK',
          { chainId },
        );
      }
      const params = [
        {
          from: tx.from ?? address,
          to: tx.to,
          data: tx.data,
          value: numberToHex(tx.value),
          gas: tx.gas !== undefined ? numberToHex(tx.gas) : undefined,
        },
      ];
      debugLog('wallet:wc', 'işlem isteği gönderiliyor', { to: tx.to, gas: params[0].gas });
      const pending = provider.request<Hex>({ method: 'eth_sendTransaction', params }, CAIP_CHAIN);
      void bringWalletToFront(provider);
      const hash = await withTimeout(pending, REQUEST_TIMEOUT_MS);
      if (!hash || !/^0x[0-9a-fA-F]{64}$/.test(hash)) {
        throw new ChainError('The wallet did not return a transaction hash.', 'UNKNOWN');
      }
      return hash;
    } catch (err) {
      throw toChainError(err);
    }
  },

  on(listener) {
    listeners.add(listener);
    // Sağlayıcı zaten kuruluysa olaylar bağlıdır; değilse getProvider ilk kurulumda bağlar.
    void getProvider().catch(() => undefined);
    return () => {
      listeners.delete(listener);
    };
  },
};
