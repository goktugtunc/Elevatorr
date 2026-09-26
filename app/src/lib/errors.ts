import { ApiError } from '@/lib/api/client';
import { SiweError } from '@/lib/auth/siwe';
import { shortAddress } from '@/lib/chain';
import { ChainError, vaultErrorCopy } from '@/lib/chain/errors';
import { env } from '@/lib/env';

/**
 * Hata → kullanıcıya gösterilecek metin (tek kaynak; docs/monad/04-frontend-tasarim.md §8.3).
 * Ekranlar hata nesnesini yorumlamaz, bu fonksiyonu çağırır; böylece "yanlış ağ",
 * "imza reddedildi", "sunucuya ulaşılamadı" her yerde aynı okunur.
 */
export function userMessage(err: unknown): string {
  if (err instanceof ChainError) return chainMessage(err);
  if (err instanceof SiweError) return siweMessage(err);
  if (err instanceof ApiError) return apiMessage(err);
  if (err instanceof Error) return err.message;
  return 'Something went wrong.';
}

export function networkLabel(): string {
  return 'Monad Testnet';
}

function chainMessage(err: ChainError): string {
  switch (err.code) {
    case 'USER_REJECTED':
      return 'The request was rejected in your wallet.';
    case 'WRONG_NETWORK':
      return 'Your wallet is on another network. Switch it to Monad Testnet (chain 10143).';
    case 'CHAIN_NOT_ADDED':
      return `Monad Testnet is not in your wallet yet. Approve adding it, or add it manually: RPC ${env.rpcUrl}, chain ID 10143, symbol MON.`;
    case 'WRONG_ACCOUNT': {
      const expected = err.details?.errorName ?? extractAddress(err.message);
      return expected
        ? `Your wallet is on a different account. Switch to ${shortAddress(expected)}.`
        : 'Your wallet is on a different account. Switch to the account you signed in with.';
    }
    case 'INSUFFICIENT_FUNDS':
      return 'Not enough MON to pay for gas. Get test MON from the Monad faucet.';
    case 'CONTRACT_REVERT':
    case 'TX_REVERTED':
      return err.details?.errorName
        ? vaultErrorCopy(err.details.errorName)
        : 'The transaction was rejected by the contract.';
    case 'TX_TIMEOUT':
      return 'The transaction is taking longer than usual. Check the explorer; it may still confirm.';
    case 'RPC_ERROR':
      return 'The Monad RPC did not respond. Try again in a moment.';
    case 'REQUEST_PENDING':
      return 'Your wallet already has a request open. Finish it there first.';
    case 'NOT_CONNECTED':
      return 'No wallet connected. Connect a wallet first.';
    case 'SERVER_CHAIN_MISMATCH':
      return `The server is on chain ${err.details?.chainId ?? '?'}; this app is built for Monad Testnet (10143).`;
    case 'DISCONNECTED':
      return 'The wallet connection was closed. Reconnect your wallet.';
    case 'UNAUTHORIZED':
      return 'The wallet did not authorize this app. Connect it again.';
    case 'UNSUPPORTED_METHOD':
      return 'Your wallet does not support this request. Try another wallet.';
    case 'INVALID_ADDRESS':
      return err.message || 'That is not a valid address (0x + 40 hex).';
    case 'NOT_AVAILABLE':
    case 'MISSING_CONFIG':
    case 'EXPIRED':
      return err.message;
    default:
      return err.message || 'Wallet error.';
  }
}

function siweMessage(err: SiweError): string {
  switch (err.code) {
    case 'WRONG_NETWORK':
      return 'The sign-in message is for another chain. This app runs on Monad Testnet.';
    case 'DOMAIN_MISMATCH':
      return 'The sign-in message was issued by another domain. Check EXPO_PUBLIC_API_BASE_URL.';
    case 'ADDRESS_MISMATCH':
      return 'The sign-in message is for a different wallet address. Check the connected account.';
    case 'EXPIRED':
      return 'The sign-in request expired. Please try again.';
    case 'BACKEND_MISSING':
    case 'INVALID_MESSAGE':
    case 'NO_TOKEN':
    default:
      return err.message;
  }
}

/** Zincir/vault hata kodlarını `vaultErrorCopy` ile metne çevirir (`vault:Paused`, `receipt_mismatch`…). */
const VAULT_DICTIONARY_CODES = new Set([
  'receipt_mismatch',
  'tx_hash_conflict',
  'invalid_tx_hash',
  'not_included',
  'reorged',
  'out_of_gas',
  'reverted',
]);

function apiMessage(err: ApiError): string {
  const d = err.details;
  switch (err.code) {
    case 'session_expired':
      return 'Your session has ended. Sign in again with your wallet.';
    case 'token_expired':
    case 'token_invalid':
    case 'missing_token':
      return 'Your session could not be verified. Sign in again with your wallet.';
    case 'siwe_invalid':
    case 'siwe_domain_mismatch':
    case 'siwe_uri_mismatch':
    case 'siwe_chain_mismatch':
    case 'siwe_expired':
    case 'siwe_not_yet_valid':
      return `Sign-in failed: the message did not pass the server's checks (${err.code}). Try again.`;
    case 'nonce_invalid':
    case 'nonce_used':
    case 'nonce_expired':
      return 'The sign-in request is no longer valid. Start again.';
    case 'signature_invalid':
      return 'The signature does not match the wallet address.';
    case 'invalid_address':
      return 'That is not a valid address (0x + 40 hex).';
    case 'not_registered':
      return 'Finish sign-up first.';
    case 'account_disabled':
      return 'This account has been disabled.';
    case 'rate_limited': {
      if (typeof d.next_allowed_at === 'string') {
        return `Next test tokens available at ${formatTime(d.next_allowed_at)}.`;
      }
      const secs = err.retryAfterSeconds ?? asNumber(d.retry_after_seconds);
      return secs ? `Too many requests. Try again in ${secs}s.` : 'Too many requests. Wait a moment and try again.';
    }
    case 'chain_error':
      return typeof d.error_code === 'string'
        ? vaultErrorCopy(d.error_code)
        : 'The Monad RPC is unavailable right now. Try again shortly.';
    case 'use_reserved_action':
      // Kullanıcıya gösterilmez; yürütücü `details.action` ile yeniler.
      return 'Retrying with your listing deposit…';
    case 'reservation_insufficient':
      return 'Your listing deposit does not cover this principal. Release it and open from your wallet.';
    case 'pending_tx_expired':
      return 'This transaction request expired before it was sent. Build it again.';
    case 'pending_tx_not_found':
    case 'not_owner':
      return 'This transaction belongs to another session.';
    case 'too_many_decimals':
      return typeof d.decimals === 'number'
        ? `Use at most ${d.decimals} decimals for this asset.`
        : 'Too many decimals for this asset.';
    case 'amount_locked':
      return 'The amount cannot change while capital is locked. Release it first.';
    case 'reservation_locked':
      return 'Release the locked capital before closing the listing.';
    case 'no_reservation':
      return 'This listing has no locked capital.';
    case 'insufficient_funds':
      return d.needed !== undefined && d.available !== undefined
        ? `Not enough balance: need ${String(d.needed)}, have ${String(d.available)}.`
        : 'Not enough balance for this transfer.';
    case 'asset_not_mintable':
    case 'faucet_disabled':
      return 'Test tokens are not available for this asset right now.';
    case 'asset_required':
      return 'Choose the token to claim.';
    case 'invalid_state':
    case 'wrong_party':
    case 'not_expired':
    case 'not_onchain':
      return "This action is not available in the agreement's current state. Refresh.";
    case 'agreement_not_found':
      return 'This agreement was not found.';
    case 'validation_error':
      return err.message; // alan hataları `details.errors` ile form altına
    default:
      if (err.code && VAULT_DICTIONARY_CODES.has(err.code)) return vaultErrorCopy(err.code);
      if (err.code?.startsWith('vault:') || err.code?.startsWith('erc20:')) {
        return vaultErrorCopy(err.code);
      }
      return statusMessage(err);
  }
}

function statusMessage(err: ApiError): string {
  switch (err.status) {
    case 0:
      return 'Could not reach the server. Is the backend running? (EXPO_PUBLIC_API_BASE_URL)';
    case 401:
      return 'Your session could not be verified. Sign in again with your wallet.';
    case 403:
      return 'You are not allowed to do that.';
    case 404:
      return 'Not found.';
    case 409:
      return err.message || 'This conflicts with the current state. Refresh and try again.';
    case 400:
    case 422:
      return err.message; // sunucunun alan bazlı mesajı
    case 429:
      return 'Too many requests. Wait a moment and try again.';
    default:
      return err.status >= 500 ? `Server error (${err.status}). Please try again.` : err.message;
  }
}

/** 422 `validation_error` → alan adı → mesaj (form altı gösterim). */
export function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError) || err.code !== 'validation_error') return {};
  const list = err.details.errors;
  if (!Array.isArray(list)) return {};
  const out: Record<string, string> = {};
  for (const item of list) {
    if (!item || typeof item !== 'object') continue;
    const e = item as { loc?: unknown; msg?: unknown; field?: unknown };
    const field =
      typeof e.field === 'string'
        ? e.field
        : Array.isArray(e.loc)
          ? String(e.loc.filter((p) => p !== 'body').at(-1) ?? '')
          : '';
    if (field && typeof e.msg === 'string' && !(field in out)) out[field] = e.msg;
  }
  return out;
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function extractAddress(text: string): string | null {
  const m = /0x[0-9a-fA-F]{40}/.exec(text);
  return m ? m[0] : null;
}

function formatTime(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? new Date(ms).toLocaleString() : iso;
}
