/**
 * Zincir katmanı dışa açılan yüzey (04 §2). Ekranlar `@/lib/chain`'den import eder.
 */
export {
  CHAIN_ID,
  CHAIN_ID_HEX,
  CHAIN_NAME,
  NATIVE,
  DEFAULT_FAUCET_URL,
  getChainConfig,
  subscribe as subscribeChainConfig,
  applyServerConfig,
  getChain,
  explorerTxUrl,
  explorerAddressUrl,
  addEthereumChainParams,
  assetBySymbol,
  assetById,
  assetByAddress,
  defaultBaseAsset,
} from './config';
export type { RuntimeChainConfig } from './config';

export { getPublicClient, waitForReceipt, getNativeBalance } from './client';

export {
  traderVaultAbi,
  erc20Abi,
  erc20ErrorsAbi,
  vaultAndErc20Abi,
  decodeVaultError,
  decodeCalldataFunction,
} from './abi';

export {
  shortAddress,
  formatTxHash,
  checksum,
  isEvmAddress,
  sameAddress,
  formatAmount,
  formatRaw,
  formatMon,
  parseAmountInput,
} from './format';
export type { FormatOpts } from './format';

export { ChainError, toChainError, vaultErrorCopy, chainError, CHAIN_ERROR_COPY } from './errors';
export type { ChainErrorCode, ChainErrorDetails } from './errors';

export {
  executeUnsignedTx,
  flushTxOutbox,
  txStatusErrorMessage,
  TxAborted,
  INITIAL_TX_PROGRESS,
  TERMINAL_PHASES,
} from './tx';
export type { TxPhase, TxProgress, ExecuteOptions } from './tx';

export { useTxExecutor } from './useTxExecutor';
export type { UseTxExecutorOptions, RunOptions } from './useTxExecutor';
