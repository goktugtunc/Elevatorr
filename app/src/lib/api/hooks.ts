import { useQuery } from '@tanstack/react-query';

import { conversationsApi, metaApi, notificationsApi } from './endpoints';
import type { ConfigOut } from './types';
import { applyServerConfig } from '@/lib/chain';

/**
 * Ekranlar arası paylaşılan küçük react-query kancaları (04 §5.4).
 * Veri yalnız `endpoints.ts` üzerinden gelir; burada tip/anahtar sabitlenir.
 */

/** `GET /config` — faucet_url, default_base_asset_id, usd_prices. Zincir katmanına da uygulanır. */
export function useServerConfig() {
  return useQuery<ConfigOut>({
    queryKey: ['config'],
    queryFn: async () => {
      const cfg = await metaApi.config();
      applyServerConfig(cfg);
      return cfg;
    },
    staleTime: 5 * 60_000,
  });
}

export interface UnreadCounts {
  notifications: number;
  messages: number;
}

/** Bildirim + mesaj okunmamış sayaçları (tab rozetleri). 60 sn'de bir yenilenir. */
export function useUnreadCounts(enabled = true) {
  return useQuery<UnreadCounts>({
    queryKey: ['unread'],
    queryFn: async () => {
      const [n, c] = await Promise.all([
        notificationsApi.unreadCount(),
        conversationsApi.unreadCount(),
      ]);
      return { notifications: n.unread, messages: c.messages };
    },
    enabled,
    refetchInterval: 60_000,
  });
}
