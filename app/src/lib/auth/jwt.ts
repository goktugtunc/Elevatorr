import { Buffer } from 'buffer';

/**
 * JWT yardımcıları — yalnızca **okuma** amaçlı (süre kontrolü, adres eşleşmesi).
 * İmza doğrulaması sunucudadır; istemci token'ın içeriğine güvenmez, sadece
 * "ne zaman dolacak" sorusunu yanıtlamak için `exp` claim'ini okur.
 */
export interface JwtPayload {
  /** Cüzdan adresi (küçük harf) */
  sub?: string;
  /** Kullanıcı id (kayıtlıysa) */
  uid?: string;
  role?: 'customer' | 'trader' | null;
  /** Unix saniye */
  exp?: number;
  iat?: number;
  /** İlk SIWE doğrulama anı (Unix saniye); refresh'te değişmez — 30 gün sonra `session_expired`. */
  auth_time?: number;
  jti?: string;
  /** `siwe_domain` */
  iss?: string;
  [claim: string]: unknown;
}

function base64UrlDecode(input: string): string | null {
  const padded = input.replace(/-/g, '+').replace(/_/g, '/');
  const withPad = padded + '='.repeat((4 - (padded.length % 4)) % 4);
  try {
    // `buffer` paketi hem web'de hem native'de var (bkz. src/polyfills.ts);
    // atob'un aksine UTF-8 claim'leri doğru çözer.
    return Buffer.from(withPad, 'base64').toString('utf8');
  } catch {
    return null;
  }
}

export function decodeJwt(token: string): JwtPayload | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  const json = base64UrlDecode(parts[1]);
  if (!json) return null;
  try {
    const payload: unknown = JSON.parse(json);
    return payload && typeof payload === 'object' ? (payload as JwtPayload) : null;
  } catch {
    return null;
  }
}

/** Token'ın bitiş anı (ms epoch). `exp` yoksa null. */
export function jwtExpiresAt(token: string): number | null {
  const exp = decodeJwt(token)?.exp;
  return typeof exp === 'number' ? exp * 1000 : null;
}

/**
 * Süre doldu mu? `skewMs` kadar erken "doldu" sayılır; böylece istek yolda
 * iken token'ın ölmesi yerine önceden yenileme yapılır.
 */
export function isExpired(expiresAt: number | null, skewMs = 30_000): boolean {
  if (expiresAt === null) return false; // süre bilinmiyorsa 401'e bakılır
  return Date.now() + skewMs >= expiresAt;
}
