/**
 * Cüzdan katmanı dışa açılan yüzey (04 §3.7). `localWallet` DOĞRUDAN export edilmez;
 * ekranlar yerel cüzdana `wallet.kind === 'local'` ve `getLocalWallet()` (web'de null) ile ulaşır.
 */
export {
  wallet,
  restoreWalletMode,
  clearWalletMode,
  walletConnectAvailable,
  localWalletAvailable,
  listInjectedWallets,
} from './wallet';
export type { NativeWalletMode } from './wallet';
export { getLocalWallet } from './local';
export { WC_WALLETS, pairingLinks, openPairing } from './deeplinks';
export type { WalletLinkTarget } from './deeplinks';
export { WalletError } from './types';
export type {
  WalletAdapter,
  ConnectOptions,
  TxRequest,
  WalletEvent,
  WalletKind,
  WalletInfo,
  LocalWallet,
} from './types';
export { ChainError } from '@/lib/chain/errors';
