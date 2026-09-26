import { env } from '@/lib/env';
import { debugError, debugLog } from '@/lib/log';
import { STORAGE_KEYS, secureStorage } from '@/lib/storage';

/**
 * Backend HTTP istemcisi. Tüm çağrılar JSON; JWT varsa Authorization başlığına eklenir.
 * Endpoint listesi: ./endpoints.ts (docs/monad/02-api-sozlesme.md ile birebir).
 *
 * 401 gelirse `authBridge` üzerinden bir kez `POST /auth/refresh` denenir; yenileme
 * başarısızsa oturum kapatılır ve hata ekrana düşer. Refresh isteğinin kendisi köprüyü
 * `skipAuthBridge` ile atlar (aksi hâlde köprü kendini bekler — inceleme §D hata 2).
 */
export class ApiError extends Error {
  constructor(
    /** HTTP durum kodu; 0 = ağ hatası (sunucuya hiç ulaşılamadı). */
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
    /** Sunucunun makine okunur hata kodu (ör. "validation_error", "use_reserved_action"). */
    public readonly code?: string,
    /** Hata zarfının `details` alanı (ör. `{action, reservation_id}`, `{retry_after_seconds}`). */
    public readonly details: Record<string, unknown> = {},
    /** 429: `Retry-After` başlığı ya da `details.retry_after_seconds`. */
    public readonly retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Oturum store'u ile istemci arasındaki köprü (döngüsel import olmasın diye).
 * `session.ts` uygulama açılışında kendini kaydeder.
 */
export interface AuthBridge {
  /** 401 sonrası yeni JWT almayı dener; alınamazsa null. */
  refresh(): Promise<string | null>;
  /** Yenileme başarısız oldu → oturumu kapat, girişe yönlendir. */
  onSessionExpired(): void;
}

let authBridge: AuthBridge | null = null;

export function registerAuthBridge(bridge: AuthBridge): void {
  authBridge = bridge;
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export type Query = Record<string, string | number | boolean | undefined | null>;

export interface RequestOptions {
  body?: unknown;
  auth?: boolean;
  query?: Query;
  /** İçeride kullanılır: 401 sonrası tekrar denemede sonsuz döngüyü engeller. */
  retried?: boolean;
  /** 401'de köprüyü (refresh) çağırma — `POST /auth/refresh` için. */
  skipAuthBridge?: boolean;
}

/** `http.*` çağrılarında `auth` boolean'ı (geriye uyum) ya da seçenek nesnesi. */
export type CallOptions = boolean | { auth?: boolean; skipAuthBridge?: boolean };

function normalize(opts: CallOptions | undefined): Pick<RequestOptions, 'auth' | 'skipAuthBridge'> {
  if (typeof opts === 'boolean') return { auth: opts };
  return { auth: opts?.auth ?? true, skipAuthBridge: opts?.skipAuthBridge ?? false };
}

const SESSION_END_CODES = new Set(['session_expired', 'token_expired', 'token_invalid', 'missing_token']);

/** Köprünün "oturumu kapat" kararı verdiği 401 kodları (04 §4.2 #2). */
export function isSessionEndCode(code: string | undefined): boolean {
  return code !== undefined && SESSION_END_CODES.has(code);
}

async function request<T>(method: Method, path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, query, retried = false, skipAuthBridge = false } = options;
  // Not: `new URL(path, base)` taban yolundaki öneki (/api/v1) yutar; elle birleştiriyoruz.
  const url = new URL(`${env.apiBaseUrl.replace(/\/+$/, '')}${path}`);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }

  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth) {
    const jwt = await secureStorage.get(STORAGE_KEYS.jwt);
    if (jwt) headers.Authorization = `Bearer ${jwt}`;
  }

  let res: Response;
  try {
    res = await fetch(url.toString(), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    debugError('api', `${method} ${url.pathname} — sunucuya ulaşılamadı`, err);
    throw new ApiError(0, err instanceof Error ? err.message : `${method} ${path} failed`);
  }

  const text = await res.text();
  const data = text ? safeJson(text) : null;

  if (res.status === 401 && auth && !retried && !skipAuthBridge && authBridge) {
    const token = await authBridge.refresh().catch(() => null);
    if (token) return request<T>(method, path, { ...options, retried: true });
    authBridge.onSessionExpired();
  }

  if (!res.ok) {
    const error = toApiError(method, path, res, data);
    debugError('api', `${method} ${url.pathname} → ${res.status}`, error);
    throw error;
  }
  debugLog('api', `${method} ${url.pathname} → ${res.status}`);
  return data as T;
}

function toApiError(method: Method, path: string, res: Response, data: unknown): ApiError {
  const envelope = data && typeof data === 'object' ? (data as Record<string, unknown>) : null;
  const serverMessage = envelope && 'message' in envelope ? String(envelope.message) : '';
  const code = envelope && typeof envelope.code === 'string' ? envelope.code : undefined;
  const details =
    envelope && envelope.details && typeof envelope.details === 'object'
      ? (envelope.details as Record<string, unknown>)
      : {};
  const message = serverMessage || `${method} ${path} → ${res.status}`;

  let retryAfterSeconds: number | null = null;
  if (res.status === 429) {
    const header = res.headers.get('Retry-After');
    const fromHeader = header ? Number(header) : NaN;
    const fromDetails = details.retry_after_seconds;
    if (Number.isFinite(fromHeader)) retryAfterSeconds = fromHeader;
    else if (typeof fromDetails === 'number') retryAfterSeconds = fromDetails;
  }

  return new ApiError(res.status, message, data, code, details, retryAfterSeconds);
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const http = {
  get: <T>(path: string, query?: Query, opts?: CallOptions) =>
    request<T>('GET', path, { query, ...normalize(opts) }),
  post: <T>(path: string, body?: unknown, opts?: CallOptions) =>
    request<T>('POST', path, { body, ...normalize(opts) }),
  put: <T>(path: string, body?: unknown, opts?: CallOptions) =>
    request<T>('PUT', path, { body, ...normalize(opts) }),
  patch: <T>(path: string, body?: unknown, opts?: CallOptions) =>
    request<T>('PATCH', path, { body, ...normalize(opts) }),
  delete: <T>(path: string, opts?: CallOptions) => request<T>('DELETE', path, normalize(opts)),
};
