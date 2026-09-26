import type { Address } from 'viem';
import { create } from 'zustand';

import { ApiError, authApi, isSessionEndCode, registerAuthBridge, usersApi } from '@/lib/api';
import type { LoginOut, MeOut, RegisterIn, UserRole } from '@/lib/api/types';
import { isExpired, loginWithSiwe } from '@/lib/auth';
import { CHAIN_ID, ChainError, flushTxOutbox } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { debugError, debugLog } from '@/lib/log';
import { STORAGE_KEYS, plainStorage, secureStorage } from '@/lib/storage';
import { getLocalWallet, restoreWalletMode, wallet } from '@/lib/wallet';
import type { NativeWalletMode, WalletEvent, WalletKind } from '@/lib/wallet';

/**
 * Oturum durumu — cüzdan adresi, zincir, rol, JWT (docs/monad/04-frontend-tasarim.md §4.2).
 * Rol yönlendirmesi app/index.tsx'te bu store'a göre yapılır.
 *
 * Sunucu `LoginOut` ile token'ın yanında `registered` ve `user` döndürüyor; kayıtsız cüzdanda
 * `role` null kalır ve kullanıcı kayıt akışına düşer. JWT süresi dolmadan `POST /auth/refresh`
 * ile yenilenir — bu, cüzdanda yeni bir imza (SIWE) istemez.
 *
 * Cüzdan bağlama **yalnız** `connectWallet({ mode | connectorId })` ile ve açık seçimle olur;
 * `signIn()` bağlamaz (inceleme §D hata 5 — sessiz yerel cüzdan yok).
 */
export type SessionStatus = 'booting' | 'signed_out' | 'wallet_connected' | 'signed_in';

interface SessionState {
  status: SessionStatus;
  /** checksum adres */
  address: Address | null;
  /** Cüzdanın zinciri; ChainBanner `CHAIN_ID` ile karşılaştırır. */
  chainId: number | null;
  walletKind: WalletKind | null;
  /** 'MetaMask', 'In-app wallet' … */
  walletName: string | null;
  /** JWT var ama cüzdan bağlantısı düşmüş olabilir. */
  walletConnected: boolean;
  role: UserRole | null;
  profile: MeOut | null;
  /** Cüzdan sunucuda kayıtlı mı (rol + kullanıcı adı verilmiş mi). */
  registered: boolean;
  /** JWT'nin bitiş anı (ms epoch). */
  expiresAt: number | null;
  /** WalletConnect eşleşme URI'si — UI QR/deep link gösterir. */
  pairingUri: string | null;
  walletMode: NativeWalletMode | null;
  onboardingSeen: boolean;
  error: string | null;

  hydrate: () => Promise<void>;
  markOnboardingSeen: () => Promise<void>;
  connectWallet: (options?: {
    mode?: NativeWalletMode;
    connectorId?: 'injected' | 'walletConnect';
  }) => Promise<Address>;
  cancelPairing: () => void;
  signIn: () => Promise<void>;
  register: (payload: RegisterIn) => Promise<void>;
  refreshProfile: () => Promise<void>;
  switchToAppChain: () => Promise<void>;
  signOut: () => Promise<void>;
  forgetLocalWallet: () => Promise<void>;
  clearError: () => void;
}

interface PersistedSession {
  address: string;
  role: UserRole | null;
  registered: boolean;
  expiresAt: number | null;
  walletKind: WalletKind | null;
}

const SESSION_EXPIRED_MESSAGE = 'Your session expired. Sign in again with your wallet.';

async function persist(session: PersistedSession): Promise<void> {
  await secureStorage.set(STORAGE_KEYS.session, JSON.stringify(session));
}

async function clearStoredSession(): Promise<void> {
  await Promise.all([
    secureStorage.remove(STORAGE_KEYS.jwt),
    secureStorage.remove(STORAGE_KEYS.session),
  ]);
}

const WALLET_KINDS: readonly string[] = ['injected', 'walletconnect', 'local'];

/** Bozuk JSON ya da şema dışı değer → null (inceleme §D hata 3). Eski kayıtta `registered` yoksa rolden türetilir (hata 4). */
export function safeParsePersisted(raw: string | null): PersistedSession | null {
  if (!raw) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== 'object') return null;
    const o = v as Record<string, unknown>;
    if (typeof o.address !== 'string' || !/^0x[0-9a-fA-F]{40}$/.test(o.address)) return null;
    const role = o.role === 'customer' || o.role === 'trader' ? o.role : null;
    const expiresAt = typeof o.expiresAt === 'number' && Number.isFinite(o.expiresAt) ? o.expiresAt : null;
    const registered = typeof o.registered === 'boolean' ? o.registered : role !== null;
    const walletKind =
      typeof o.walletKind === 'string' && WALLET_KINDS.includes(o.walletKind)
        ? (o.walletKind as WalletKind)
        : null;
    return { address: o.address, role, registered, expiresAt, walletKind };
  } catch {
    return null;
  }
}

function sameAddress(a?: string | null, b?: string | null): boolean {
  return !!a && !!b && a.toLowerCase() === b.toLowerCase();
}

function parseExpiresAt(value: string | null | undefined): number | null {
  const ms = value ? Date.parse(value) : NaN;
  return Number.isFinite(ms) ? ms : null;
}

/** `LoginOut`/`RegisterOut` sonrası ortak: token sakla, kalıcı kaydı ve state'i güncelle. */
async function applyLogin(
  set: (partial: Partial<SessionState>) => void,
  get: () => SessionState,
  login: Pick<LoginOut, 'token' | 'expires_at' | 'user'> & { address: string; registered: boolean },
): Promise<void> {
  const expiresAt = parseExpiresAt(login.expires_at);
  const role = login.user?.role ?? null;
  const address = login.address as Address;
  await secureStorage.set(STORAGE_KEYS.jwt, login.token);
  await persist({ address, role, registered: login.registered, expiresAt, walletKind: get().walletKind });
  set({
    status: 'signed_in',
    address,
    role,
    profile: login.user,
    registered: login.registered,
    expiresAt,
    error: null,
  });
}

let unsubscribeWallet: (() => void) | null = null;

function subscribeWalletEvents(): void {
  unsubscribeWallet?.();
  unsubscribeWallet = wallet.on((e: WalletEvent) => {
    const s = useSession.getState();
    if (e.type === 'accountsChanged') {
      const next = e.accounts[0] ?? null;
      if (!next) {
        useSession.setState({ walletConnected: false });
        return;
      }
      if (s.address && !sameAddress(next, s.address)) {
        void s.signOut().then(() =>
          useSession.setState({
            error: 'Wallet account changed. Sign in again with the new account.',
          }),
        );
      }
    } else if (e.type === 'chainChanged') {
      useSession.setState({ chainId: e.chainId });
    } else if (e.type === 'disconnect') {
      // JWT korunur; işlem denemesinde "Reconnect wallet" istenir.
      useSession.setState({ walletConnected: false });
    }
  });
}

export const useSession = create<SessionState>((set, get) => ({
  status: 'booting',
  address: null,
  chainId: null,
  walletKind: null,
  walletName: null,
  walletConnected: false,
  role: null,
  profile: null,
  registered: false,
  expiresAt: null,
  pairingUri: null,
  walletMode: null,
  onboardingSeen: false,
  error: null,

  async hydrate() {
    const [seen, jwt, raw, walletMode] = await Promise.all([
      plainStorage.get(STORAGE_KEYS.onboardingSeen),
      secureStorage.get(STORAGE_KEYS.jwt),
      secureStorage.get(STORAGE_KEYS.session),
      restoreWalletMode(),
    ]);
    const onboardingSeen = !!seen;
    set({ walletMode });

    // Yarım kalmış hash bildirimleri (04 §2.6 adım 7) — hata oturumu etkilemez.
    void flushTxOutbox().catch((err: unknown) => debugError('tx', 'outbox boşaltılamadı', err));

    const persisted = safeParsePersisted(raw);
    if (raw && !persisted) {
      debugError('session', 'kalıcı oturum kaydı bozuk, siliniyor', new Error('invalid persisted session'));
      await clearStoredSession();
    }

    if (!jwt || !persisted) {
      set({ status: 'signed_out', onboardingSeen });
      return;
    }

    if (isExpired(persisted.expiresAt)) {
      await clearStoredSession();
      set({
        status: 'signed_out',
        onboardingSeen,
        address: persisted.address as Address,
        error: SESSION_EXPIRED_MESSAGE,
      });
      return;
    }

    set({
      status: 'signed_in',
      address: persisted.address as Address,
      role: persisted.role,
      registered: persisted.registered,
      expiresAt: persisted.expiresAt,
      walletKind: persisted.walletKind,
      onboardingSeen,
    });

    // Cüzdan bağlantısı hâlâ duruyor mu (web: wagmi reconnect; WC: kayıtlı oturum)?
    try {
      const current = await wallet.getAddress();
      const connected = !!current && sameAddress(current, persisted.address);
      set({ walletConnected: connected, chainId: connected ? await wallet.getChainId() : null });
      if (connected) subscribeWalletEvents();
    } catch (err) {
      debugError('session', 'cüzdan durumu okunamadı', err);
      set({ walletConnected: false });
    }

    // Profil ve kayıt durumu tazelensin; 401 gelirse köprü devreye girer.
    get()
      .refreshProfile()
      .catch(() => undefined);
  },

  async markOnboardingSeen() {
    await plainStorage.set(STORAGE_KEYS.onboardingSeen, '1');
    set({ onboardingSeen: true });
  },

  async connectWallet(options) {
    set({ error: null, pairingUri: null });
    try {
      const { address, chainId, wallet: info } = await wallet.connect({
        mode: options?.mode,
        connectorId: options?.connectorId,
        onUri: (uri) => set({ pairingUri: uri }),
        targetChainId: CHAIN_ID,
      });
      subscribeWalletEvents();
      const nextMode: NativeWalletMode | null =
        options?.mode ?? (info.kind === 'walletconnect' || info.kind === 'local' ? info.kind : null);
      set({
        address,
        chainId,
        walletKind: info.kind,
        walletName: info.name,
        walletConnected: true,
        pairingUri: null,
        walletMode: nextMode ?? get().walletMode,
      });
      if (get().status !== 'signed_in') set({ status: 'wallet_connected' });
      return address;
    } catch (err) {
      set({ pairingUri: null });
      throw err;
    }
  },

  cancelPairing() {
    set({ pairingUri: null });
    void wallet.abortPairing?.();
  },

  async signIn() {
    const { address } = get();
    if (!address) {
      const err = new ChainError('No wallet connected. Connect a wallet first.', 'NOT_CONNECTED');
      set({ error: userMessage(err) });
      throw err;
    }
    set({ error: null });
    try {
      const session = await loginWithSiwe(wallet, address);
      await applyLogin(set, get, {
        token: session.token,
        expires_at: session.expiresAt ? new Date(session.expiresAt).toISOString() : '',
        address: session.address,
        registered: session.registered,
        user: session.user,
      });
    } catch (err) {
      set({ error: userMessage(err) });
      throw err;
    }
  },

  async register(payload) {
    // RegisterOut {user, token, expires_at}: token saklanır, rol buradan okunur (inceleme §D hata 1).
    const out = await usersApi.register(payload);
    const address = (get().address ?? out.user.wallet_address) as Address;
    await applyLogin(set, get, {
      token: out.token,
      expires_at: out.expires_at,
      address,
      registered: true,
      user: out.user,
    });
  },

  async refreshProfile() {
    const me = await authApi.me();
    const role = me.user?.role ?? null;
    set({
      profile: me.user,
      role,
      registered: me.registered,
      address: me.address as Address,
    });
    await persist({
      address: me.address,
      role,
      registered: me.registered,
      expiresAt: parseExpiresAt(me.token_expires_at) ?? get().expiresAt,
      walletKind: get().walletKind,
    });
  },

  async switchToAppChain() {
    set({ error: null });
    try {
      await wallet.switchChain(CHAIN_ID);
      set({ chainId: (await wallet.getChainId()) ?? CHAIN_ID });
    } catch (err) {
      set({ error: userMessage(err) });
      throw err;
    }
  },

  async signOut() {
    unsubscribeWallet?.();
    unsubscribeWallet = null;
    await clearStoredSession();
    // WC oturumu / wagmi bağlantısı kapanır; yerel cüzdan anahtarı KORUNUR (forgetLocalWallet ayrı).
    await wallet.disconnect().catch(() => undefined);
    set({
      status: 'signed_out',
      address: null,
      chainId: null,
      walletKind: null,
      walletName: null,
      walletConnected: false,
      role: null,
      profile: null,
      registered: false,
      expiresAt: null,
      pairingUri: null,
      error: null,
    });
  },

  async forgetLocalWallet() {
    const local = getLocalWallet();
    if (!local) {
      throw new ChainError('The in-app wallet is not available on this platform.', 'NOT_AVAILABLE');
    }
    await get().signOut();
    await local.forget();
    await plainStorage.remove(STORAGE_KEYS.walletMode);
    set({ walletMode: null });
  },

  clearError() {
    set({ error: null });
  },
}));

/**
 * 401 köprüsü: önce `POST /auth/refresh` denenir (cüzdan imzası gerekmez; istek köprüyü atlar).
 * Refresh 401 `session_expired`/`token_*` verirse null → oturum kapatılır. Başka hata (ağ, 5xx)
 * da null döner ama oturum **kapatılmaz**; asıl istek hatası ekrana düşer.
 * Eşzamanlı 401'ler tek yenileme isteğinde birleşir; `refreshInFlight` `finally`'de sıfırlanır.
 */
let refreshInFlight: Promise<string | null> | null = null;
let sessionEnded = false;

registerAuthBridge({
  async refresh() {
    if (refreshInFlight) return refreshInFlight;
    sessionEnded = false;
    refreshInFlight = (async () => {
      try {
        const login = await authApi.refresh();
        await applyLogin(
          (partial) => useSession.setState(partial),
          () => useSession.getState(),
          login,
        );
        debugLog('auth', 'JWT yenilendi');
        return login.token;
      } catch (err) {
        debugError('auth', 'JWT yenilenemedi', err);
        if (err instanceof ApiError && err.status === 401 && isSessionEndCode(err.code)) {
          sessionEnded = true;
        }
        return null;
      }
    })();
    try {
      return await refreshInFlight;
    } finally {
      refreshInFlight = null;
    }
  },

  onSessionExpired() {
    // Yalnız oturum gerçekten bittiyse (session_expired / token_*) kapat; ağ hatasında dokunma.
    if (!sessionEnded) return;
    sessionEnded = false;
    void clearStoredSession();
    useSession.setState({
      status: 'signed_out',
      role: null,
      profile: null,
      registered: false,
      expiresAt: null,
      error: 'Your session has ended. Sign in again with your wallet.',
    });
  },
});
