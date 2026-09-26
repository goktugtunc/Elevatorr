/**
 * Web stub — uygulama içi cüzdan web'de YOKTUR (K9). Metro web hedefinde `./local` yerine
 * bu dosya çözülür; `viem/accounts` ve `expo-secure-store` web bundle'a girmez.
 */
import type { LocalWallet } from './types';
import { ChainError } from '@/lib/chain/errors';

export const localWalletAvailable = false;

function unavailable(): never {
  throw new ChainError(
    'The in-app wallet is only available in the mobile app. Use a browser wallet or WalletConnect.',
    'NOT_AVAILABLE',
  );
}

export const localWallet: LocalWallet = {
  async exists() {
    return false;
  },
  async address() {
    return null;
  },
  async create() {
    return unavailable();
  },
  async importPrivateKey() {
    return unavailable();
  },
  async exportPrivateKey() {
    return null;
  },
  async forget() {
    /* no-op */
  },
  async signMessage() {
    return unavailable();
  },
  async sendTransaction() {
    return unavailable();
  },
};

export function getLocalWallet(): LocalWallet | null {
  return null;
}
