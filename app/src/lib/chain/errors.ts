/**
 * Zincir/cüzdan hataları — tek sınıf `ChainError`, tek eşleyici `toChainError`.
 *
 * Kaynaklar: EIP-1193 sağlayıcı hataları (`{ code }` nesneleri), viem hata sınıfları,
 * WalletConnect metinleri, kontrat revert verisi (`decodeVaultError`).
 * `ApiError` (HTTP) burada eşlenmez; `lib/errors.ts` (`userMessage`) iki dünyayı birleştirir.
 *
 * Arayüz metinleri İngilizce (04 §2.5 tablo).
 */
import {
  BaseError,
  ChainDisconnectedError,
  ChainMismatchError,
  ContractFunctionExecutionError,
  ContractFunctionRevertedError,
  HttpRequestError,
  InsufficientFundsError,
  LimitExceededRpcError,
  ProviderDisconnectedError,
  ResourceUnavailableRpcError,
  SwitchChainError,
  TimeoutError,
  UnauthorizedProviderError,
  UnsupportedProviderMethodError,
  UserRejectedRequestError,
  WaitForTransactionReceiptTimeoutError,
  type Hex,
} from 'viem';

import { decodeVaultError } from './abi';

export type ChainErrorCode =
  | 'USER_REJECTED' // EIP-1193 4001, viem UserRejectedRequestError, WC "User rejected"
  | 'WRONG_NETWORK' // 4901 chainDisconnected, viem ChainMismatchError, oturum zinciri ≠ 10143 ve switch başarısız
  | 'CHAIN_NOT_ADDED' // 4902 (MetaMask), viem SwitchChainError, wagmi ChainNotConfiguredError → addEthereumChain de reddedildi
  | 'WRONG_ACCOUNT' // cüzdan adresi ≠ UnsignedTxOut.from_address / oturum adresi
  | 'UNAUTHORIZED' // 4100
  | 'UNSUPPORTED_METHOD' // 4200 (ör. cüzdan wallet_switchEthereumChain bilmiyor)
  | 'DISCONNECTED' // 4900, WC session_delete
  | 'REQUEST_PENDING' // -32002 (MetaMask: bir istek zaten açık)
  | 'INSUFFICIENT_FUNDS' // -32000 + /insufficient funds/i, viem InsufficientFundsError → MON yok (faucet linki)
  | 'CONTRACT_REVERT' // ContractFunctionRevertedError / -32603 data → decodeVaultError → details.errorName
  | 'TX_REVERTED' // receipt.status === 'reverted' (pre_step approve için)
  | 'TX_TIMEOUT' // WaitForTransactionReceiptTimeoutError / poll süresi doldu
  | 'RPC_ERROR' // HttpRequestError, TimeoutError, LimitExceededRpcError (429)
  | 'SERVER_CHAIN_MISMATCH' // /config.chain.chain_id ≠ 10143
  | 'INVALID_ADDRESS'
  | 'NOT_CONNECTED'
  | 'NOT_AVAILABLE'
  | 'MISSING_CONFIG'
  | 'EXPIRED'
  | 'UNKNOWN';

export interface ChainErrorDetails {
  /** Decode edilen kontrat/ERC-20 hata adı (ör. 'Paused', 'ERC20InsufficientAllowance'). */
  errorName?: string;
  hash?: Hex;
  cause?: unknown;
  chainId?: number;
}

export class ChainError extends Error {
  constructor(
    message: string,
    public readonly code: ChainErrorCode,
    public readonly details?: ChainErrorDetails,
  ) {
    super(message);
    this.name = 'ChainError';
  }
}

/** Kod için varsayılan İngilizce metin (çağıran özel metin vermezse). */
export const CHAIN_ERROR_COPY: Record<ChainErrorCode, string> = {
  USER_REJECTED: 'The request was rejected in your wallet.',
  WRONG_NETWORK: 'Your wallet is on a different network. Switch it to Monad Testnet (chain 10143).',
  CHAIN_NOT_ADDED:
    'Monad Testnet is not in your wallet. Add it manually (RPC https://testnet-rpc.monad.xyz, chain 10143) or use the in-app wallet.',
  WRONG_ACCOUNT: 'The connected wallet account is not the one this action was prepared for.',
  UNAUTHORIZED: 'The wallet has not authorized this app. Reconnect the wallet.',
  UNSUPPORTED_METHOD: 'Your wallet does not support this request.',
  DISCONNECTED: 'The wallet disconnected. Reconnect and try again.',
  REQUEST_PENDING: 'Your wallet already has a request open. Finish it in the wallet first.',
  INSUFFICIENT_FUNDS: 'You need MON for gas. Get some from the Monad faucet.',
  CONTRACT_REVERT: 'The transaction was rejected by the contract.',
  TX_REVERTED: 'The transaction was included but reverted on-chain.',
  TX_TIMEOUT: 'The transaction is still pending. Check again in a moment.',
  RPC_ERROR: 'The network did not respond. Check your connection and try again.',
  SERVER_CHAIN_MISMATCH: 'The server is on a different chain than this app (Monad Testnet, 10143).',
  INVALID_ADDRESS: 'That is not a valid address.',
  NOT_CONNECTED: 'Wallet is not connected.',
  NOT_AVAILABLE: 'This wallet option is not available on this platform.',
  MISSING_CONFIG: 'The app is missing configuration for this action.',
  EXPIRED: 'This transaction request expired. Build it again.',
  UNKNOWN: 'Something went wrong with the wallet. Try again.',
};

export function chainError(code: ChainErrorCode, details?: ChainErrorDetails, message?: string): ChainError {
  return new ChainError(message ?? CHAIN_ERROR_COPY[code], code, details);
}

/** EIP-1193 / JSON-RPC sayısal kodları. */
function fromEip1193Code(code: number): ChainErrorCode | null {
  switch (code) {
    case 4001:
      return 'USER_REJECTED';
    case 4100:
      return 'UNAUTHORIZED';
    case 4200:
      return 'UNSUPPORTED_METHOD';
    case 4900:
      return 'DISCONNECTED';
    case 4901:
      return 'WRONG_NETWORK';
    case 4902:
      return 'CHAIN_NOT_ADDED';
    case -32002:
      return 'REQUEST_PENDING';
    case 429:
      return 'RPC_ERROR';
    default:
      return null;
  }
}

interface ErrorLike {
  code?: unknown;
  message?: unknown;
  data?: unknown;
  cause?: unknown;
}

function asErrorLike(err: unknown): ErrorLike | null {
  return err && typeof err === 'object' ? (err as ErrorLike) : null;
}

/** `{ data: '0x…' }` ya da `{ data: { data: '0x…' } }` içindeki revert verisini bulur. */
function revertDataOf(err: unknown, depth = 0): Hex | null {
  const e = asErrorLike(err);
  if (!e || depth > 4) return null;
  if (typeof e.data === 'string' && /^0x[0-9a-fA-F]*$/.test(e.data) && e.data.length >= 10) {
    return e.data as Hex;
  }
  if (e.data && typeof e.data === 'object') {
    const inner = revertDataOf(e.data, depth + 1);
    if (inner) return inner;
  }
  if (e.cause) return revertDataOf(e.cause, depth + 1);
  return null;
}

function messageOf(err: unknown): string {
  if (err instanceof Error) {
    // viem BaseError: shortMessage daha okunur
    const short = (err as Error & { shortMessage?: string }).shortMessage;
    return short || err.message;
  }
  const e = asErrorLike(err);
  if (e && typeof e.message === 'string') return e.message;
  return typeof err === 'string' ? err : '';
}

/**
 * Her türlü hatayı `ChainError`'a çevirir; `ChainError` girdisi aynen döner (idempotent).
 * Öncelik: viem sınıfları → EIP-1193 kodu → revert verisi → metin desenleri.
 */
export function toChainError(err: unknown): ChainError {
  if (err instanceof ChainError) return err;

  const msg = messageOf(err);
  const cause = err;

  if (err instanceof BaseError) {
    const walk = (cls: new (...args: never[]) => Error) =>
      err.walk((e) => e instanceof cls) as Error | null;

    if (walk(UserRejectedRequestError)) return chainError('USER_REJECTED', { cause });
    if (walk(ChainMismatchError) || walk(ChainDisconnectedError))
      return chainError('WRONG_NETWORK', { cause });
    if (walk(SwitchChainError)) return chainError('CHAIN_NOT_ADDED', { cause });
    if (walk(UnauthorizedProviderError)) return chainError('UNAUTHORIZED', { cause });
    if (walk(UnsupportedProviderMethodError)) return chainError('UNSUPPORTED_METHOD', { cause });
    if (walk(ProviderDisconnectedError)) return chainError('DISCONNECTED', { cause });
    if (walk(ResourceUnavailableRpcError)) return chainError('REQUEST_PENDING', { cause });
    if (walk(InsufficientFundsError)) return chainError('INSUFFICIENT_FUNDS', { cause });
    if (walk(WaitForTransactionReceiptTimeoutError)) return chainError('TX_TIMEOUT', { cause });
    if (walk(LimitExceededRpcError) || walk(HttpRequestError) || walk(TimeoutError))
      return chainError('RPC_ERROR', { cause });

    const reverted = walk(ContractFunctionRevertedError) as ContractFunctionRevertedError | null;
    if (reverted) {
      const name = reverted.data?.errorName ?? reverted.reason ?? decodeVaultError(reverted.raw ?? '0x')?.name;
      return new ChainError(
        name ? vaultErrorCopy(name) : CHAIN_ERROR_COPY.CONTRACT_REVERT,
        'CONTRACT_REVERT',
        { errorName: name, cause },
      );
    }
    if (walk(ContractFunctionExecutionError)) {
      const data = revertDataOf(err);
      const decoded = data ? decodeVaultError(data) : null;
      return new ChainError(
        decoded ? vaultErrorCopy(decoded.name) : CHAIN_ERROR_COPY.CONTRACT_REVERT,
        'CONTRACT_REVERT',
        { errorName: decoded?.name, cause },
      );
    }
  }

  // EIP-1193 / JSON-RPC {code} nesneleri (MetaMask, WalletConnect provider, ham RPC)
  const e = asErrorLike(err);
  const numericCode =
    e && typeof e.code === 'number'
      ? e.code
      : e && typeof e.code === 'string' && /^-?\d+$/.test(e.code)
        ? Number(e.code)
        : null;
  if (numericCode !== null) {
    const mapped = fromEip1193Code(numericCode);
    if (mapped) return chainError(mapped, { cause });
    if (numericCode === -32000 && /insufficient funds/i.test(msg))
      return chainError('INSUFFICIENT_FUNDS', { cause });
    if (numericCode === -32603 || numericCode === -32000 || numericCode === 3) {
      const data = revertDataOf(err);
      const decoded = data ? decodeVaultError(data) : null;
      if (decoded) {
        return new ChainError(vaultErrorCopy(decoded.name), 'CONTRACT_REVERT', {
          errorName: decoded.name,
          cause,
        });
      }
      if (/revert|execution reverted/i.test(msg)) return chainError('CONTRACT_REVERT', { cause });
    }
  }

  // Metin desenleri (WalletConnect, bazı cüzdanlar kod göndermez)
  if (/user rejected|rejected|denied|cancel(l)?ed|closed/i.test(msg))
    return chainError('USER_REJECTED', { cause });
  if (/unrecognized chain|chain.*not (been )?added|4902/i.test(msg))
    return chainError('CHAIN_NOT_ADDED', { cause });
  if (/chain|namespace|unsupported/i.test(msg)) return chainError('WRONG_NETWORK', { cause });
  if (/insufficient funds/i.test(msg)) return chainError('INSUFFICIENT_FUNDS', { cause });
  if (/expire|timeout|timed out/i.test(msg)) return chainError('RPC_ERROR', { cause });
  if (/disconnect|session (was )?deleted|no matching (key|session)/i.test(msg))
    return chainError('DISCONNECTED', { cause });
  if (/execution reverted|revert/i.test(msg)) return chainError('CONTRACT_REVERT', { cause });

  return new ChainError(msg || CHAIN_ERROR_COPY.UNKNOWN, 'UNKNOWN', { cause });
}

/**
 * Vault / ERC-20 hata sözlüğü (01 §5, 02 §2.6). Girdi `'vault:Paused'`, `'erc20:ERC20InsufficientBalance'`
 * ya da çıplak ad olabilir; ayrıca backend'in kontrat dışı kodları (`receipt_mismatch`, `not_included`…).
 * `{symbol}` yer tutucusu `symbol` verilirse doldurulur.
 */
export function vaultErrorCopy(name: string, symbol?: string): string {
  const bare = name.replace(/^(vault|erc20):/, '');
  const text = VAULT_ERROR_COPY[bare];
  if (text) return text.replace('{symbol}', symbol ?? 'this token');
  return `The transaction was rejected by the contract (${bare || 'reverted'}).`;
}

const APPROVAL = 'The token approval did not go through. Approve again.';
const RESERVATION =
  "The listing's locked capital cannot cover this. Lock more capital or open it from your wallet.";
const INVALID_ADDRESS = 'Invalid address in the request.';

const VAULT_ERROR_COPY: Record<string, string> = {
  Unauthorized: 'Only the party named in the agreement can do this. Check the connected account.',
  OwnableUnauthorizedAccount:
    'Only the party named in the agreement can do this. Check the connected account.',
  Paused: 'The vault is paused for maintenance. Cancel, settle and claim still work; try again later.',
  InvalidTerms: 'The agreement terms are outside the allowed limits.',
  TokenNotAllowed: "This token is not on the vault's allow-list.",
  NotFound: 'This agreement (or reservation) does not exist on-chain yet. Refresh and try again.',
  ReservationNotFound:
    'This agreement (or reservation) does not exist on-chain yet. Refresh and try again.',
  WrongStatus: 'The agreement moved to another state. Refresh to see the current actions.',
  Expired: 'The agreement period is over; trades are closed. Settle it instead.',
  NotExpired: 'The agreement has not ended yet; only the parties can settle now.',
  InsufficientBalance: 'The agreement does not hold enough of this token.',
  TooManyTokens: 'An agreement can hold at most 6 tokens. Close a position first.',
  DrawdownBreached: 'This would push the value below the max drawdown floor. Reduce the size.',
  SlippageExceeded: 'Price moved too much. Increase slippage tolerance or try again.',
  RouterError: 'The swap router rejected the trade. Try a smaller amount or a different pair.',
  NotParty: 'Only the agreement parties can cancel it.',
  ReservationClosed: RESERVATION,
  ReservationInsufficient: RESERVATION,
  ReservationMismatch: RESERVATION,
  ZeroAmount: 'Enter an amount greater than zero.',
  InvalidAmount: 'Enter an amount greater than zero.',
  ZeroAddress: INVALID_ADDRESS,
  InvalidRouter: INVALID_ADDRESS,
  InvalidToken: INVALID_ADDRESS,
  TransferFailed: APPROVAL,
  SafeERC20FailedOperation: APPROVAL,
  ERC20InsufficientAllowance: APPROVAL,
  ERC20InsufficientBalance:
    'Your wallet does not hold enough {symbol}. Get test tokens from the Wallet screen.',
  receipt_mismatch:
    'The transaction on-chain does not match what was prepared. Nothing was applied; build it again.',
  not_included: 'The transaction was not included in the chain. Build it again.',
  reorged: 'The transaction was not included in the chain. Build it again.',
  out_of_gas: 'The transaction ran out of gas. Try again; the wallet will re-estimate.',
};
