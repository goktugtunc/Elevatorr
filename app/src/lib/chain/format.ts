/**
 * Adres ve tutar biçimlendirme (FE-39, K12). **`Number()` tutarlar için yasaktır**:
 * tutarlar string/bigint olarak taşınır, `formatUnits`/`parseUnits` ile dönüştürülür.
 * `Intl.NumberFormat` yalnız tam kısım gruplaması için (BigInt güvenli regex ile) değil,
 * bps/yüzde için kullanılır (`lib/format.ts`).
 */
import { formatUnits, getAddress, isAddress, parseUnits, type Address } from 'viem';

import { ChainError } from './errors';

export interface FormatOpts {
  /** Varsayılan min(decimals, 6); sondaki sıfırlar atılır. */
  maxFraction?: number;
  minFraction?: number;
  /** Binlik gruplama (varsayılan true). */
  grouping?: boolean;
}

/** '0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19' → '0x67aD…FF19' (head 0x + 4 karakter, 02 §13 #30). */
export function shortAddress(address: string, head = 6, tail = 4): string {
  if (!address || address.length <= head + tail + 1) return address;
  return `${address.slice(0, head)}…${address.slice(-tail)}`;
}

/** '0x5e1c…9a7b' */
export function formatTxHash(hash: string): string {
  return shortAddress(hash, 6, 4);
}

/** Checksum adres (EIP-55); geçersizse ChainError('INVALID_ADDRESS'). */
export function checksum(address: string): Address {
  const trimmed = address.trim();
  if (!isAddress(trimmed, { strict: false })) {
    throw new ChainError('That is not a valid address (0x + 40 hex).', 'INVALID_ADDRESS');
  }
  return getAddress(trimmed);
}

/** Karışık büyük/küçük harfte checksum'ı da doğrular (viem `isAddress` strict). */
export function isEvmAddress(value: string): value is Address {
  return typeof value === 'string' && isAddress(value.trim());
}

/** Küçük harf karşılaştırma (K11). İkisi de boşsa false. */
export function sameAddress(a?: string | null, b?: string | null): boolean {
  if (!a || !b) return false;
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** Tam kısmı BigInt güvenli biçimde gruplar: '1234567' → '1,234,567'. */
function groupInteger(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * İnsan okunur ondalık string'i ('1234.5', '-0.25') biçimler; sayıya çevirmez.
 * Sayı gibi görünmeyen girdi olduğu gibi döner; boş/null → '—'.
 */
export function formatAmount(
  human: string | null | undefined,
  symbol?: string,
  opts: FormatOpts = {},
): string {
  if (human === null || human === undefined || human === '') return '—';
  const raw = human.trim();
  const match = /^(-)?(\d+)(?:\.(\d*))?$/.exec(raw);
  if (!match) return symbol ? `${raw} ${symbol}` : raw;

  const sign = match[1] ?? '';
  const intPart = match[2].replace(/^0+(?=\d)/, '');
  let frac = match[3] ?? '';

  const maxFraction = opts.maxFraction ?? 6;
  const minFraction = opts.minFraction ?? 0;
  if (frac.length > maxFraction) frac = frac.slice(0, maxFraction);
  frac = frac.replace(/0+$/, '');
  if (frac.length < minFraction) frac = frac.padEnd(minFraction, '0');

  const grouped = opts.grouping === false ? intPart : groupInteger(intPart);
  const body = frac ? `${grouped}.${frac}` : grouped;
  const text = `${sign}${body}`;
  return symbol ? `${text} ${symbol}` : text;
}

/** Ham birim (wei benzeri) → biçimli metin. `raw` ondalık string ya da bigint. */
export function formatRaw(
  raw: string | bigint,
  decimals: number,
  symbol?: string,
  opts: FormatOpts = {},
): string {
  let value: bigint;
  try {
    value = typeof raw === 'bigint' ? raw : BigInt(raw.trim());
  } catch {
    return typeof raw === 'string' ? formatAmount(raw, symbol, opts) : '—';
  }
  const human = formatUnits(value, decimals);
  return formatAmount(human, symbol, {
    ...opts,
    maxFraction: opts.maxFraction ?? Math.min(decimals, 6),
  });
}

/** MON (18 ondalık, yalnız gas): en fazla 4 ondalık. */
export function formatMon(wei: string | bigint, opts: FormatOpts = {}): string {
  return formatRaw(wei, 18, 'MON', { maxFraction: 4, ...opts });
}

/**
 * Kullanıcı girişini ('1,250.5' · '0.25' · '500') tutara çevirir.
 * Fazla ondalık → null (sunucudaki `too_many_decimals` ile aynı kural). Geçersiz → null.
 * `human` API'ye gönderilen değerdir; `raw` yalnız cüzdana gösterim/karşılaştırma için.
 */
export function parseAmountInput(
  input: string,
  decimals: number,
): { human: string; raw: bigint } | null {
  const cleaned = input.trim().replace(/[\s,]/g, '');
  if (!cleaned) return null;
  const match = /^(\d*)(?:\.(\d*))?$/.exec(cleaned);
  if (!match) return null;
  const intRaw = match[1] ?? '';
  const fracRaw = match[2] ?? '';
  if (!intRaw && !fracRaw) return null;
  if (fracRaw.length > decimals) return null;

  const intPart = intRaw.replace(/^0+(?=\d)/, '') || '0';
  const frac = fracRaw.replace(/0+$/, '');
  const human = frac ? `${intPart}.${frac}` : intPart;
  try {
    return { human, raw: parseUnits(human, decimals) };
  } catch {
    return null;
  }
}
