import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

/**
 * Anahtar-değer saklama.
 * - secure: JWT gibi gizli değerler → native'de SecureStore, web'de localStorage
 *   (web'de daha güvenli seçenek yok; XSS'e karşı token süresi kısa tutulur).
 * - plain: onboarding görüldü mü, cüzdan modu, tx outbox gibi tercihler → AsyncStorage
 *   (her platformda). WalletConnect oturumu kendi AsyncStorage deposunu kullanır;
 *   wagmi (web) oturumu localStorage'da tutar.
 */
export const secureStorage = {
  async get(key: string): Promise<string | null> {
    if (Platform.OS === 'web') {
      try {
        return globalThis.localStorage?.getItem(key) ?? null;
      } catch {
        return null;
      }
    }
    return SecureStore.getItemAsync(key);
  },
  async set(key: string, value: string): Promise<void> {
    if (Platform.OS === 'web') {
      try {
        globalThis.localStorage?.setItem(key, value);
      } catch {
        /* private mode vb. */
      }
      return;
    }
    await SecureStore.setItemAsync(key, value);
  },
  async remove(key: string): Promise<void> {
    if (Platform.OS === 'web') {
      try {
        globalThis.localStorage?.removeItem(key);
      } catch {
        /* ignore */
      }
      return;
    }
    await SecureStore.deleteItemAsync(key);
  },
};

export const plainStorage = {
  get: (key: string) => AsyncStorage.getItem(key),
  set: (key: string, value: string) => AsyncStorage.setItem(key, value),
  remove: (key: string) => AsyncStorage.removeItem(key),
};

export const STORAGE_KEYS = {
  jwt: 'tk.jwt',
  session: 'tk.session',
  onboardingSeen: 'tk.onboardingSeen',
  /**
   * Uygulama içi cüzdanın gizli anahtarı (0x + 64 hex) — yalnızca native SecureStore
   * (`lib/wallet/local.ts`); web'de yerel cüzdan yoktur (K9).
   */
  walletSecret: 'tk.walletSecret',
  /** Son seçilen native cüzdan yolu: 'walletconnect' | 'local' (varsayılan yok; eski değerler silinir). */
  walletMode: 'tk.walletMode',
  /**
   * Cüzdana gönderilmiş ama sunucuya bildirilememiş işlemler:
   * JSON `{ pending_tx_id, tx_hash, at }[]` — `lib/chain/tx.ts` flushTxOutbox().
   */
  txOutbox: 'tk.txOutbox',
} as const;
