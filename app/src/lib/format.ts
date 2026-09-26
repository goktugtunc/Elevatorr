/**
 * Sayı/yüzde/süre biçimlendirme (arayüz dili İngilizce → en-US):
 *   +8.4% · -11.2% · 1,250 shares · 312.40 · 20% · 3 months
 *
 * **Token tutarları burada biçimlenmez** (K12: `Number()` tutarlar için yasak).
 * Tutarlar için `@/lib/chain` → `formatAmount`, `formatRaw`, `parseAmountInput`, `formatMon`.
 * Bu dosyadaki `parseNumberInput` yalnız bps/yüzde/gün girişleri içindir.
 */
const trNumber = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });

/** +8.4% / -3.1% (işaret önde — K/Z için). */
export function formatPnlPct(pct: number, digits = 1): string {
  const sign = pct > 0 ? '+' : pct < 0 ? '-' : '';
  return `${sign}${Math.abs(pct).toFixed(digits)}%`;
}

/** 20% / 15% – 20% (oranlar). */
export function formatRatePct(pct: number): string {
  return `${trNumber.format(pct)}%`;
}

export function formatRateRange([min, max]: [number, number]): string {
  return `${formatRatePct(min)} – ${formatRatePct(max)}`;
}

export function formatQuantity(qty: number, unit: string): string {
  return `${trNumber.format(qty)} ${unit}`;
}

export function formatPrice(price: number, digits = 2): string {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(price);
}

/** "2m", "1h", "Yesterday", "Sep 12" */
export function formatRelative(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const diffMin = Math.round((now.getTime() - d.getTime()) / 60000);
  if (diffMin < 1) return 'now';
  if (diffMin < 60) return `${diffMin}m`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH}h`;
  const diffD = Math.round(diffH / 24);
  if (diffD === 1) return 'Yesterday';
  return new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short' }).format(d);
}

/**
 * Kullanıcının yazdığı sayıyı (12.5 · 30 · 2,000) sayıya çevirir — **yalnız bps/yüzde/gün**
 * girişleri için. Token tutarları için `parseAmountInput` (`@/lib/chain`) kullanılır.
 * Arayüz İngilizce olduğundan virgül binlik ayırıcı, nokta ondalık ayırıcıdır.
 * Geçersizse null döner — form doğrulaması bunu "zorunlu/geçersiz" olarak gösterir.
 */
export function parseNumberInput(input: string): number | null {
  const cleaned = input.trim().replace(/[\s,]/g, '');
  if (!cleaned || !/^\d+(\.\d+)?$/.test(cleaned)) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/** Sunucu oranları basis point tutar: 2000 → "20%". */
export function formatBps(bps: number | null | undefined, digits = 0): string {
  if (bps === null || bps === undefined) return '—';
  return `${(bps / 100).toFixed(digits)}%`;
}

/** İşaretli bps (K/Z): 860 → "+8.6%". */
export function formatBpsSigned(bps: number | null | undefined, digits = 1): string {
  if (bps === null || bps === undefined) return '—';
  return formatPnlPct(bps / 100, digits);
}

/** "30" gün → "1 mo" gibi kısa süre etiketi. */
export function formatDuration(days: number | null | undefined): string {
  if (!days) return '—';
  if (days % 30 === 0) {
    const months = days / 30;
    return months === 1 ? '1 month' : `${months} months`;
  }
  return days === 1 ? '1 day' : `${days} days`;
}
