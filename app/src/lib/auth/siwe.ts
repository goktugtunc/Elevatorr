import type { Address } from 'viem';
import { parseSiweMessage } from 'viem/siwe';

import { ApiError, authApi } from '@/lib/api';
import type { LoginOut, MeOut } from '@/lib/api/types';
import { debugError, debugLog } from '@/lib/log';
import type { WalletAdapter } from '@/lib/wallet/types';

/**
 * SIWE (EIP-4361) giriş akışı — docs/monad/02-api-sozlesme.md §1, 04 §4.1:
 *   1. `POST /api/v1/auth/nonce {address}` → sunucu mesajı üretir (`NonceOut.message`)
 *   2. Mesaj doğrulanır (körlemesine imza yok): adres, chainId, domain, nonce, süre
 *   3. Cüzdan `personal_sign(message)` (EIP-191)
 *   4. `POST /api/v1/auth/verify {message, signature}` → JWT + kayıt durumu
 *
 * İstemci mesajı **kurmaz ve değiştirmez**; sunucunun verdiği string byte'ı byte'ına imzalanır
 * ve aynen geri gönderilir. Yanıt (`LoginOut`) token'ın yanında `registered` ve varsa `user`
 * taşır; kayıtsız cüzdan için ayrı bir profil isteği gerekmez.
 */
export type SiweErrorCode =
  | 'BACKEND_MISSING'
  | 'INVALID_MESSAGE'
  | 'ADDRESS_MISMATCH'
  | 'WRONG_NETWORK'
  | 'DOMAIN_MISMATCH'
  | 'EXPIRED'
  | 'NO_TOKEN';

export class SiweError extends Error {
  constructor(
    message: string,
    public readonly code: SiweErrorCode,
  ) {
    super(message);
    this.name = 'SiweError';
  }
}

export interface SiweSession {
  token: string;
  /** ms epoch; sunucu `expires_at` verir. */
  expiresAt: number | null;
  /** checksum adres (sunucudan) */
  address: Address;
  registered: boolean;
  user: MeOut | null;
}

/** Uygulamanın derlendiği zincir; `@/lib/chain` `CHAIN_ID` ile aynı değer (04 §2.1). */
const CHAIN_ID = 10143;

function sameAddress(a?: string | null, b?: string | null): boolean {
  return !!a && !!b && a.toLowerCase() === b.toLowerCase();
}

export interface SiweOptions {
  /** `/config.auth.siwe_domain` yüklüyse sağlama için verilir. */
  expectedDomain?: string | null;
}

export async function loginWithSiwe(
  adapter: Pick<WalletAdapter, 'signMessage'>,
  address: Address,
  options: SiweOptions = {},
): Promise<SiweSession> {
  debugLog('auth:siwe', 'nonce isteniyor', { address });

  let nonce;
  try {
    nonce = await authApi.nonce(address);
  } catch (err) {
    debugError('auth:siwe', 'nonce alınamadı', err);
    if (err instanceof ApiError && err.status === 404) {
      throw new SiweError(
        'Sign-in endpoint not found (/auth/nonce). Is the server on the Monad build?',
        'BACKEND_MISSING',
      );
    }
    throw err;
  }

  assertValidSiweMessage(nonce.message, {
    address,
    nonce: nonce.nonce,
    domain: nonce.domain,
    expectedDomain: options.expectedDomain ?? null,
  });
  debugLog('auth:siwe', 'mesaj doğrulandı, cüzdandan imza isteniyor');

  const signature = await adapter.signMessage(nonce.message);
  debugLog('auth:siwe', 'imza alındı, doğrulamaya gönderiliyor');

  let login: LoginOut;
  try {
    login = await authApi.verify({ message: nonce.message, signature });
  } catch (err) {
    debugError('auth:siwe', 'imza doğrulanamadı', err);
    throw err;
  }

  if (!login.token) throw new SiweError('The server did not return a session token.', 'NO_TOKEN');

  const expiresAtMs = login.expires_at ? Date.parse(login.expires_at) : NaN;
  debugLog('auth:siwe', 'giriş tamam', {
    registered: login.registered,
    role: login.user?.role ?? null,
  });

  return {
    token: login.token,
    expiresAt: Number.isFinite(expiresAtMs) ? expiresAtMs : null,
    address: (login.address || address) as Address,
    registered: login.registered,
    user: login.user,
  };
}

/**
 * Sunucudan gelen EIP-4361 mesajının biçim kontrolü: bizim adresimize, bizim zincirimize,
 * bizim sunucumuza (domain) ve verilen nonce'a ait; süresi geçmemiş.
 */
export function assertValidSiweMessage(
  message: string,
  opts: { address: string; nonce: string; domain: string; expectedDomain?: string | null },
): void {
  let parsed: ReturnType<typeof parseSiweMessage>;
  try {
    parsed = parseSiweMessage(message);
  } catch {
    throw new SiweError('The sign-in message from the server could not be parsed.', 'INVALID_MESSAGE');
  }

  if (!parsed.address || !sameAddress(parsed.address, opts.address)) {
    throw new SiweError(
      'The sign-in message is for a different wallet address. Check the connected account.',
      'ADDRESS_MISMATCH',
    );
  }
  if (parsed.chainId !== CHAIN_ID) {
    throw new SiweError(
      `The sign-in message is for chain ${parsed.chainId ?? '?'}. This app runs on Monad Testnet (${CHAIN_ID}).`,
      'WRONG_NETWORK',
    );
  }
  if (
    !parsed.domain ||
    parsed.domain !== opts.domain ||
    (opts.expectedDomain && parsed.domain !== opts.expectedDomain)
  ) {
    throw new SiweError(
      'The sign-in message was issued by another domain. Check EXPO_PUBLIC_API_BASE_URL.',
      'DOMAIN_MISMATCH',
    );
  }
  if (parsed.nonce !== opts.nonce) {
    throw new SiweError('The sign-in message carries an unexpected nonce.', 'INVALID_MESSAGE');
  }
  if (parsed.expirationTime && parsed.expirationTime.getTime() <= Date.now()) {
    throw new SiweError('The sign-in request expired. Please try again.', 'EXPIRED');
  }
  if (parsed.notBefore && parsed.notBefore.getTime() > Date.now() + 60_000) {
    throw new SiweError('The sign-in request is not valid yet (clock skew?).', 'EXPIRED');
  }
}
