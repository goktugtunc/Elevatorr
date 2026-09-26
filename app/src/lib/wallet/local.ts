/**
 * Uygulama içi cüzdan (native, FE-34, K9) — özel anahtar cihazda üretilir ve **cihazda kalır**.
 *
 * Anahtar (0x + 64 hex) `expo-secure-store` ile saklanır (iOS Keychain / Android Keystore,
 * WHEN_UNLOCKED_THIS_DEVICE_ONLY); sunucuya ya da üçüncü tarafa gitmez. İmza cihazda atılır.
 * Yalnız kullanıcı açıkça "In-app wallet" seçince oluşturulur (sessiz oluşturma yok).
 *
 * Web'de bu dosya yerine `./local.web.ts` (stub) çözülür; yerel cüzdan kodu web bundle'a girmez.
 * Kapsam: testnet demosu. Ana ağda harici cüzdan önerilir.
 */
import * as SecureStore from 'expo-secure-store';
import { createWalletClient, http, type Address, type Hex } from 'viem';
import { generatePrivateKey, privateKeyToAccount, type PrivateKeyAccount } from 'viem/accounts';

import type { LocalWallet, TxRequest } from './types';
import { getNativeBalance, getPublicClient } from '@/lib/chain/client';
import { getChain, getChainConfig } from '@/lib/chain/config';
import { ChainError, toChainError } from '@/lib/chain/errors';
import { debugLog } from '@/lib/log';
import { STORAGE_KEYS } from '@/lib/storage';

const KEY = STORAGE_KEYS.walletSecret;
const PK_RE = /^0x[0-9a-fA-F]{64}$/;
const STORE_OPTS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export const localWalletAvailable = true;

let cached: PrivateKeyAccount | null = null;

async function load(): Promise<PrivateKeyAccount | null> {
  if (cached) return cached;
  const secret = await SecureStore.getItemAsync(KEY);
  if (!secret) return null;
  if (!PK_RE.test(secret)) {
    // Bozuk ya da eski biçimde (0x + 64 hex olmayan) kayıt: temizle, kullanıcı yeniden oluştursun.
    await SecureStore.deleteItemAsync(KEY);
    return null;
  }
  cached = privateKeyToAccount(secret as Hex);
  return cached;
}

async function store(pk: Hex): Promise<PrivateKeyAccount> {
  await SecureStore.setItemAsync(KEY, pk, STORE_OPTS);
  cached = privateKeyToAccount(pk);
  return cached;
}

function requireAccount(account: PrivateKeyAccount | null): PrivateKeyAccount {
  if (!account) throw new ChainError('No in-app wallet on this device.', 'NOT_CONNECTED');
  return account;
}

export const localWallet: LocalWallet = {
  async exists() {
    return (await load()) !== null;
  },

  async address(): Promise<Address | null> {
    return (await load())?.address ?? null;
  },

  async create(): Promise<Address> {
    const existing = await load();
    if (existing) return existing.address;
    const account = await store(generatePrivateKey());
    debugLog('wallet:local', 'yeni cüzdan üretildi', { address: account.address });
    return account.address;
  },

  async importPrivateKey(hex: string): Promise<Address> {
    const trimmed = hex.trim();
    const normalized = trimmed.startsWith('0x') ? trimmed : `0x${trimmed}`;
    if (!PK_RE.test(normalized)) {
      throw new ChainError('That is not a valid private key (0x + 64 hex).', 'INVALID_ADDRESS');
    }
    const account = await store(normalized as Hex);
    return account.address;
  },

  async exportPrivateKey(): Promise<string | null> {
    if (!(await load())) return null;
    return SecureStore.getItemAsync(KEY);
  },

  async forget(): Promise<void> {
    cached = null;
    await SecureStore.deleteItemAsync(KEY);
  },

  async signMessage(message: string): Promise<Hex> {
    const account = requireAccount(await load());
    return account.signMessage({ message });
  },

  /**
   * viem nonce ve `maxFeePerGas/maxPriorityFeePerGas`'ı RPC'den alır; `gas` backend'den gelir
   * (yeniden tahmin yok — Monad `gas_limit` üzerinden ücretlendirir, 06). Gönderimden önce
   * MON bakiyesi `gas × maxFeePerGas`'ın altındaysa INSUFFICIENT_FUNDS (faucet linki UI'da).
   */
  async sendTransaction(tx: TxRequest): Promise<Hex> {
    const account = requireAccount(await load());
    try {
      const publicClient = getPublicClient();
      const fees = await publicClient.estimateFeesPerGas();
      if (tx.gas !== undefined && fees.maxFeePerGas !== undefined) {
        const balance = await getNativeBalance(account.address);
        const needed = tx.gas * fees.maxFeePerGas + tx.value;
        if (balance < needed) {
          throw new ChainError(
            'You need MON for gas. Get some from the Monad faucet.',
            'INSUFFICIENT_FUNDS',
          );
        }
      }
      const walletClient = createWalletClient({
        account,
        chain: getChain(),
        transport: http(getChainConfig().rpcUrl),
      });
      return await walletClient.sendTransaction({
        to: tx.to,
        data: tx.data,
        value: tx.value,
        gas: tx.gas,
        type: 'eip1559',
        maxFeePerGas: fees.maxFeePerGas,
        maxPriorityFeePerGas: fees.maxPriorityFeePerGas,
      });
    } catch (err) {
      throw toChainError(err);
    }
  },
};

/** Ekranlar yerel cüzdana `wallet.kind === 'local'` ve bu fonksiyon üzerinden ulaşır (web'de null). */
export function getLocalWallet(): LocalWallet | null {
  return localWallet;
}
