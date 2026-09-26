/**
 * viem publicClient (http). Frontend zincirden **okuma yapmaz** (bakiyeler, allowance,
 * anlaşma durumu backend'den gelir, K3); RPC limiti 25 rps (06). Bu istemci yalnız
 * receipt beklemek ve yerel cüzdanın MON bakiyesini kontrol etmek için kullanılır.
 */
import {
  createPublicClient,
  http,
  type Address,
  type Hex,
  type PublicClient,
  type TransactionReceipt,
} from 'viem';

import { getChain, getChainConfig, subscribe } from './config';
import { ChainError, toChainError } from './errors';

let client: PublicClient | null = null;
let clientRpcUrl: string | null = null;

// rpcUrl değişince (sunucu /config geldi) istemci yeniden kurulur.
subscribe((cfg) => {
  if (cfg.rpcUrl !== clientRpcUrl) client = null;
});

/** Tembel singleton. */
export function getPublicClient(): PublicClient {
  const { rpcUrl } = getChainConfig();
  if (!client || clientRpcUrl !== rpcUrl) {
    client = createPublicClient({
      chain: getChain(),
      transport: http(rpcUrl, { batch: true, retryCount: 3, timeout: 15_000 }),
    });
    clientRpcUrl = rpcUrl;
  }
  return client;
}

/**
 * Receipt bekler (1 onay, 1 sn poll). `reverted` → ChainError('TX_REVERTED');
 * süre dolarsa ChainError('TX_TIMEOUT') — işlem hâlâ bekliyor olabilir.
 */
export async function waitForReceipt(
  hash: Hex,
  opts: { timeoutMs?: number } = {},
): Promise<TransactionReceipt> {
  let receipt: TransactionReceipt;
  try {
    receipt = await getPublicClient().waitForTransactionReceipt({
      hash,
      confirmations: 1,
      pollingInterval: 1_000,
      timeout: opts.timeoutMs ?? 60_000,
    });
  } catch (err) {
    const mapped = toChainError(err);
    throw new ChainError(mapped.message, mapped.code, { ...mapped.details, hash });
  }
  if (receipt.status === 'reverted') {
    throw new ChainError('The transaction was included but reverted on-chain.', 'TX_REVERTED', {
      hash,
    });
  }
  return receipt;
}

/** Yalnız yerel cüzdanın gas ön kontrolü için (MON, wei). */
export async function getNativeBalance(address: Address): Promise<bigint> {
  try {
    return await getPublicClient().getBalance({ address });
  } catch (err) {
    throw toChainError(err);
  }
}
