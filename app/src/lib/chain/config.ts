/**
 * Zincir yapılandırması — Monad Testnet (chainId 10143), 04 §2.1 / 06.
 *
 * Başlangıç değerleri env'den gelir; `metaApi.config()` başarılı olunca
 * `applyServerConfig(cfg)` sunucu değerleriyle üstüne yazar (sunucu her zaman öncelikli).
 * Kontrat adresleri ve varlık allow-list'i YALNIZ sunucudan gelir (K3, 02 §4).
 */
import { defineChain, type Address, type Chain } from 'viem';
import { monadTestnet as viemMonadTestnet } from 'viem/chains';

import { ChainError } from './errors';
import { checksum, isEvmAddress, sameAddress } from './format';
import type { AssetOut, ConfigOut } from '@/lib/api/types';
import { env } from '@/lib/env';

export const CHAIN_ID = 10143 as const;
export const CHAIN_ID_HEX = '0x279f' as const;
export const CHAIN_NAME = 'Monad Testnet';
export const NATIVE = { name: 'MON', symbol: 'MON', decimals: 18 } as const;
export const DEFAULT_FAUCET_URL = 'https://faucet.monad.xyz';

if (env.chainId !== CHAIN_ID) {
  // Yanlış derleme: uygulama açılışta durur (04 §7).
  throw new Error(
    `EXPO_PUBLIC_CHAIN_ID=${env.chainId} but this app is built for Monad Testnet (${CHAIN_ID}). Fix .env and restart with --clear.`,
  );
}

export interface RuntimeChainConfig {
  rpcUrl: string; // env → /config.chain.rpc_url
  explorerUrl: string; // env → /config.chain.explorer_url
  faucetUrl: string; // DEFAULT_FAUCET_URL → /config.chain.faucet_url
  vault: Address | null; // /config.contracts.vault
  router: Address | null; // /config.contracts.router
  multicall3: Address | null;
  assets: AssetOut[]; // /config.assets (allow-list; MON burada değildir, K6)
  defaultBaseAssetId: string | null;
  siweDomain: string | null; // /config.auth.siwe_domain — siwe.ts sağlama yapar
  settleSlippageBps: number; // /config.settle_slippage_bps (varsayılan 100)
  defaultTradeSlippageBps: number;
  pendingTxTtlSeconds: number;
  source: 'env' | 'server';
}

type Listener = (cfg: RuntimeChainConfig) => void;

let current: RuntimeChainConfig = {
  rpcUrl: env.rpcUrl,
  explorerUrl: stripSlash(env.explorerUrl),
  faucetUrl: DEFAULT_FAUCET_URL,
  vault: null,
  router: null,
  multicall3: viemMonadTestnet.contracts?.multicall3?.address ?? null,
  assets: [],
  defaultBaseAssetId: null,
  siweDomain: null,
  settleSlippageBps: 100,
  defaultTradeSlippageBps: 100,
  pendingTxTtlSeconds: 900,
  source: 'env',
};

const listeners = new Set<Listener>();

function stripSlash(url: string): string {
  return url.replace(/\/+$/, '');
}

function optionalAddress(value: string | null | undefined): Address | null {
  return value && isEvmAddress(value) ? checksum(value) : null;
}

export function getChainConfig(): RuntimeChainConfig {
  return current;
}

/** Değişiklik aboneliği; `client.ts` rpcUrl değişince istemciyi yeniden kurar. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function setConfig(next: RuntimeChainConfig): void {
  current = next;
  for (const l of listeners) l(current);
}

/**
 * `/config` yanıtını uygular. `chain.chain_id !== 10143` ise
 * ChainError('SERVER_CHAIN_MISMATCH') fırlatır; giriş ekranı mesajı gösterir, uygulama devam etmez.
 */
export function applyServerConfig(cfg: ConfigOut): RuntimeChainConfig {
  const serverChainId = Number(cfg.chain?.chain_id);
  if (serverChainId !== CHAIN_ID) {
    throw new ChainError(
      `Server is on chain ${Number.isFinite(serverChainId) ? serverChainId : '?'}, this app is built for Monad Testnet (${CHAIN_ID}).`,
      'SERVER_CHAIN_MISMATCH',
      { chainId: serverChainId },
    );
  }
  setConfig({
    rpcUrl: cfg.chain.rpc_url || current.rpcUrl,
    explorerUrl: stripSlash(cfg.chain.explorer_url || current.explorerUrl),
    faucetUrl: cfg.chain.faucet_url || DEFAULT_FAUCET_URL,
    vault: optionalAddress(cfg.contracts?.vault),
    router: optionalAddress(cfg.contracts?.router),
    multicall3: optionalAddress(cfg.contracts?.multicall3) ?? current.multicall3,
    assets: cfg.assets ?? [],
    defaultBaseAssetId: cfg.default_base_asset_id ?? null,
    siweDomain: cfg.auth?.siwe_domain ?? null,
    settleSlippageBps: cfg.settle_slippage_bps ?? 100,
    defaultTradeSlippageBps: cfg.default_trade_slippage_bps ?? 100,
    pendingTxTtlSeconds: cfg.pending_tx_ttl_seconds ?? 900,
    source: 'server',
  });
  return current;
}

/** viem zincir tanımı; RPC/explorer çalışma zamanı değerleriyle. */
export function getChain(): Chain {
  return defineChain({
    ...viemMonadTestnet,
    id: CHAIN_ID,
    name: CHAIN_NAME,
    nativeCurrency: NATIVE,
    rpcUrls: { default: { http: [current.rpcUrl] } },
    blockExplorers: { default: { name: 'Monad explorer', url: current.explorerUrl } },
  });
}

/** Sunucu hazır `explorer_url` verdiyse (TxStatusOut, TradeOut…) o kullanılır; bunlar yedektir. */
export function explorerTxUrl(hash: string): string {
  return `${current.explorerUrl}/tx/${hash}`;
}

export function explorerAddressUrl(address: string): string {
  return `${current.explorerUrl}/address/${address}`;
}

/** `wallet_addEthereumChain` parametreleri (06 §"Cüzdan ekleme"). WalletConnect ve wagmi yedeği kullanır. */
export function addEthereumChainParams() {
  return {
    chainId: CHAIN_ID_HEX,
    chainName: CHAIN_NAME,
    nativeCurrency: { ...NATIVE },
    rpcUrls: [current.rpcUrl],
    blockExplorerUrls: [current.explorerUrl],
  };
}

// --- Varlık yardımcıları (küçük harf karşılaştırma, K11) ---

export function assetBySymbol(symbol: string): AssetOut | null {
  const s = symbol.trim().toLowerCase();
  return current.assets.find((a) => a.symbol.toLowerCase() === s) ?? null;
}

export function assetById(id: string): AssetOut | null {
  return current.assets.find((a) => a.id === id) ?? null;
}

export function assetByAddress(address: string): AssetOut | null {
  return current.assets.find((a) => sameAddress(a.address, address)) ?? null;
}

export function defaultBaseAsset(): AssetOut | null {
  return (
    (current.defaultBaseAssetId ? assetById(current.defaultBaseAssetId) : null) ??
    current.assets.find((a) => a.is_base_allowed) ??
    null
  );
}
