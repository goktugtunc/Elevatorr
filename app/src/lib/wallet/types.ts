/**
 * Cüzdan soyutlaması (04 §3.1). Web'de wagmi (injected + WalletConnect), mobilde
 * WalletConnect `eip155:10143` ya da uygulama içi cüzdan (viem account + SecureStore).
 * Uygulamanın geri kalanı yalnızca bu arayüzü bilir; yalnız EVM (personal_sign / eth_sendTransaction).
 */
import type { Address, Hex } from 'viem';

import { ChainError } from '@/lib/chain/errors';

export type WalletKind = 'injected' | 'walletconnect' | 'local';

export interface WalletInfo {
  id: string;
  kind: WalletKind;
  /** 'MetaMask' | 'Rabby' | 'WalletConnect' | 'In-app wallet' … */
  name: string;
  icon?: string;
}

export interface ConnectOptions {
  /**
   * Native: hangi yol. Verilmez ve kayıtlı mod da yoksa ChainError('MISSING_CONFIG') —
   * sessizce yerel cüzdan AÇILMAZ (inceleme §D hata 5).
   */
  mode?: 'walletconnect' | 'local';
  /** Web: wagmi bağlayıcı kimliği; verilmezse 'injected' denenir, yoksa 'walletConnect'. */
  connectorId?: 'injected' | 'walletConnect';
  /** WalletConnect eşleşme URI'si (QR / deep link). */
  onUri?: (uri: string) => void;
  /**
   * Bağlandıktan sonra bu zincire geçilsin (varsayılan CHAIN_ID). Başarısızlık connect'i
   * düşürmez; chainId döner, UI banner gösterir.
   */
  targetChainId?: number;
}

export interface TxRequest {
  to: Address;
  data: Hex;
  value: bigint;
  gas?: bigint;
  from?: Address;
}

export type WalletEvent =
  | { type: 'accountsChanged'; accounts: Address[] }
  | { type: 'chainChanged'; chainId: number }
  | { type: 'disconnect' };

export interface WalletAdapter {
  /** Platformda en az bir yol var mı. */
  readonly available: boolean;
  /** Aktif yol (bağlı değilse null). */
  readonly kind: WalletKind | null;
  connect(options?: ConnectOptions): Promise<{ address: Address; chainId: number; wallet: WalletInfo }>;
  disconnect(): Promise<void>;
  /** Checksum adres (viem getAddress). */
  getAddress(): Promise<Address | null>;
  getChainId(): Promise<number | null>;
  /**
   * wallet_switchEthereumChain → 4902/unsupported → wallet_addEthereumChain(addEthereumChainParams())
   * → yine hata → ChainError('CHAIN_NOT_ADDED').
   */
  switchChain(chainId: number): Promise<void>;
  /** EIP-191 personal_sign; UTF-8 metin girer, 0x + 130 hex çıkar. */
  signMessage(message: string): Promise<Hex>;
  /** eth_sendTransaction; tx hash döner (receipt beklemez). */
  sendTransaction(tx: TxRequest): Promise<Hex>;
  /** Abonelikten çıkma fonksiyonu döner. */
  on(listener: (e: WalletEvent) => void): () => void;
  /** Yalnız WalletConnect. */
  abortPairing?(): Promise<void>;
}

/** Uygulama içi cüzdan yüzeyi (native `local.ts`; web'de yok). */
export interface LocalWallet {
  exists(): Promise<boolean>;
  address(): Promise<Address | null>;
  /** Mevcut varsa üstüne yazmaz. */
  create(): Promise<Address>;
  importPrivateKey(hex: string): Promise<Address>;
  /** Yedekleme ekranı; loglanmaz, ağa gitmez. */
  exportPrivateKey(): Promise<string | null>;
  forget(): Promise<void>;
  signMessage(message: string): Promise<Hex>;
  sendTransaction(tx: TxRequest): Promise<Hex>;
}

/**
 * Geçiş kolaylığı: eski `WalletError` adı `ChainError`'a takma addır.
 * Dalga 3'te import'lar `ChainError`'a çevrilir, alias silinir.
 */
export { ChainError as WalletError };
