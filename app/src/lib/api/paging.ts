import type { Page } from './types';

/**
 * `limit/offset` sayfalaması (02 §0) için react-query yardımcısı:
 *
 *   useInfiniteQuery({ initialPageParam: 0, getNextPageParam: nextOffset, ... })
 *
 * Sonraki sayfa yoksa `undefined` döner (react-query "bitti" sayar).
 */
export function nextOffset(page: Page<unknown>): number | undefined {
  const next = page.offset + page.limit;
  return next < page.total ? next : undefined;
}

/** Sayfaları tek listeye düzler (`data.pages.flatMap(p => p.items)` kısayolu). */
export function flattenPages<T>(pages: Page<T>[] | undefined): T[] {
  return pages ? pages.flatMap((p) => p.items) : [];
}
