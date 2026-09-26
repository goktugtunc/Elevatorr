/**
 * Geliştirme günlüğü — yalnızca `__DEV__` derlemesinde yazar, üretim paketinde
 * tamamen sessizdir. Çıktı Metro terminaline (ve dolayısıyla dev sunucusu
 * loguna) düşer, böylece cihazdaki akış makineden izlenebilir.
 *
 * **Asla loglanmayacaklar:** gizli anahtar (0x…), JWT, SIWE imzası / calldata'nın
 * tamamı, kullanıcı mesajları. Adres (0x…) ve kısaltılmış URI güvenli sayılır.
 */
const MAX_VALUE = 120;

function short(value: unknown): unknown {
  if (typeof value === 'string') {
    return value.length > MAX_VALUE
      ? `${value.slice(0, MAX_VALUE)}… (${value.length} karakter)`
      : value;
  }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = short(v);
    return out;
  }
  return value;
}

export function describeError(err: unknown): Record<string, unknown> {
  if (err instanceof Error) {
    const extra = err as Error & { code?: unknown; status?: unknown };
    return {
      name: err.name,
      message: err.message,
      ...(extra.code !== undefined ? { code: extra.code } : {}),
      ...(extra.status !== undefined ? { status: extra.status } : {}),
    };
  }
  return { value: String(err) };
}

export function debugLog(scope: string, message: string, data?: unknown): void {
  if (!__DEV__) return;
  if (data === undefined) console.log(`[${scope}] ${message}`);
  else console.log(`[${scope}] ${message}`, short(data));
}

export function debugError(scope: string, message: string, err: unknown): void {
  if (!__DEV__) return;
  console.warn(`[${scope}] ${message}`, describeError(err));
}
