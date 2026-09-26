import { http } from './client';
import type {
  ActivityItemOut,
  AgreementOut,
  AgreementStatus,
  AssetOut,
  AuthMeOut,
  ConfigOut,
  ConversationOut,
  ConversationReadOut,
  ConversationsUnreadOut,
  CustomerDashboardOut,
  DashboardOut,
  DepositInfoOut,
  DiscoverActionOut,
  DiscoverFeedOut,
  DiscoverRemainingOut,
  FaucetOut,
  FollowOut,
  FxConvertOut,
  FxOut,
  HealthChainOut,
  HealthOut,
  InteractionAction,
  InteractionTargetType,
  ListingCountsOut,
  ListingCreateIn,
  ListingDetailOut,
  ListingKind,
  ListingOut,
  ListingSort,
  ListingStatus,
  ListingUpdateIn,
  LoginOut,
  MarkReadOut,
  MeOut,
  MessageOut,
  MessagesPageOut,
  NonceOut,
  NotificationCategory,
  NotificationOut,
  OfferAcceptOut,
  OfferBox,
  OfferCreateIn,
  OfferOut,
  OfferStatsOut,
  OfferStatus,
  Page,
  PageParams,
  QuoteOut,
  RatingCreatedOut,
  RatingCreateIn,
  RatingOut,
  RegisterIn,
  RegisterOut,
  TraderCardOut,
  TraderDashboardOut,
  TraderProfileOut,
  TraderSort,
  TradeNoteIn,
  TradeOut,
  TradeTxIn,
  TxAction,
  TxActionIn,
  TxStatusOut,
  UnreadCountOut,
  UnsignedTxOut,
  UserOut,
  UserRole,
  UserUpdateIn,
  ValueHistoryOut,
  ValueRange,
  WalletOut,
  WalletTransferIn,
  WalletTransferOut,
} from './types';

/**
 * TraderKirala API — Monad sürümü (docs/monad/02-api-sozlesme.md).
 * Tüm uçlar `/api/v1` öneki altında; `/health*` önek dışındadır.
 * Tipler `./types.ts` → `./schema.d.ts` (openapi-typescript) ile sunucu şemasıyla birebir.
 *
 * Sayfalama: `limit/offset` → `Page<T>` (`paging.ts` → `nextOffset`). İstisnalar: `GET /discover`
 * (`cursor`), `GET /conversations/{id}/messages` (`after/before/has_more`).
 */
const V1 = '/api/v1';
const NO_AUTH = { auth: false } as const;

// --- Meta ---
export const metaApi = {
  /** Zincir, kontrat adresleri, allow-list varlıklar, SIWE ayarları (02 §4). Açılışta tek çağrı. */
  config: () => http.get<ConfigOut>(`${V1}/config`, undefined, NO_AUTH),
  health: () => http.get<HealthOut>('/health', undefined, NO_AUTH),
  /** RPC erişimi ve zincir kimliği (02 §9). 503 → `ok:false`. */
  healthChain: () => http.get<HealthChainOut>('/health/chain', undefined, NO_AUTH),
};

// --- Varlıklar ---
export const assetsApi = {
  list: (params?: { base_only?: boolean; onchain_only?: boolean }) =>
    http.get<AssetOut[]>(`${V1}/assets`, params),
  byId: (assetId: string) => http.get<AssetOut>(`${V1}/assets/${assetId}`),
};

// --- Kimlik doğrulama (SIWE, EIP-4361) — 02 §1 ---
export const authApi = {
  /** Sunucu SIWE mesajını üretir; cüzdan `personal_sign` ile imzalar. Rate limit: IP başına 20/dk. */
  nonce: (address: string) => http.post<NonceOut>(`${V1}/auth/nonce`, { address }, NO_AUTH),
  /** `NonceOut.message` birebir + 0x imza → JWT + kayıt durumu. */
  verify: (payload: { message: string; signature: string }) =>
    http.post<LoginOut>(`${V1}/auth/verify`, payload, NO_AUTH),
  me: () => http.get<AuthMeOut>(`${V1}/auth/me`),
  /**
   * JWT süresi dolmadan yenile — cüzdanda yeni imza istemez. 401 köprüsünü **atlar**
   * (aksi hâlde köprü kendini bekler). `session_expired` → istemci girişe döner.
   */
  refresh: () =>
    http.post<LoginOut>(`${V1}/auth/refresh`, undefined, { auth: true, skipAuthBridge: true }),
};

// --- Kullanıcı & profil ---
export const usersApi = {
  /** Adres gövdeye konmaz (JWT `sub`). Yanıt `RegisterOut {user, token, expires_at}` — token saklanır. */
  register: (payload: RegisterIn) => http.post<RegisterOut>(`${V1}/users/register`, payload),
  me: () => http.get<MeOut>(`${V1}/users/me`),
  updateMe: (payload: UserUpdateIn) => http.patch<MeOut>(`${V1}/users/me`, payload),
  byUsername: (username: string) => http.get<UserOut>(`${V1}/users/by-username/${username}`),
  byId: (userId: string) => http.get<UserOut>(`${V1}/users/${userId}`),
};

// --- Trader'lar ---
export const tradersApi = {
  list: (params?: PageParams & { q?: string; sort?: TraderSort }) =>
    http.get<Page<TraderCardOut>>(`${V1}/traders`, params),
  profile: (traderId: string) =>
    http.get<TraderProfileOut>(`${V1}/traders/${traderId}/profile`),
  follow: (traderId: string) => http.post<FollowOut>(`${V1}/traders/${traderId}/follow`),
  unfollow: (traderId: string) => http.delete<FollowOut>(`${V1}/traders/${traderId}/follow`),
  ratings: (traderId: string, params?: PageParams) =>
    http.get<Page<RatingOut>>(`${V1}/traders/${traderId}/ratings`, params),
};

// --- Keşfet (cursor sayfalaması korunur) ---
export const discoverApi = {
  /** Rol'e göre sunucu tarafında seçilen kart akışı (müşteriye hizmet, trader'a sermaye ilanları). */
  feed: (params?: { limit?: number; cursor?: string; market?: string }) =>
    http.get<DiscoverFeedOut>(`${V1}/discover`, params),
  remaining: () => http.get<DiscoverRemainingOut>(`${V1}/discover/remaining`),
  /** pass | like | save | follow | view | offer_request */
  action: (targetType: InteractionTargetType, targetId: string, action: InteractionAction) =>
    http.post<DiscoverActionOut>(`${V1}/discover/${targetType}/${targetId}/action`, { action }),
  unsaveListing: (listingId: string) =>
    http.delete<{ ok: boolean }>(`${V1}/discover/listing/${listingId}/save`),
};

// --- İlanlar ---
export const listingsApi = {
  list: (params?: PageParams & { kind?: ListingKind; sort?: ListingSort; market?: string }) =>
    http.get<Page<ListingOut>>(`${V1}/listings`, params),
  mine: (params?: PageParams & { status?: ListingStatus }) =>
    http.get<Page<ListingOut>>(`${V1}/listings/mine`, params),
  mineCounts: () => http.get<ListingCountsOut>(`${V1}/listings/mine/counts`),
  saved: (params?: PageParams) => http.get<Page<ListingOut>>(`${V1}/listings/saved`, params),
  byId: (id: string) => http.get<ListingDetailOut>(`${V1}/listings/${id}`),
  create: (payload: ListingCreateIn) => http.post<ListingOut>(`${V1}/listings`, payload),
  update: (id: string, payload: ListingUpdateIn) =>
    http.patch<ListingOut>(`${V1}/listings/${id}`, payload),
  pause: (id: string) => http.post<ListingOut>(`${V1}/listings/${id}/pause`),
  resume: (id: string) => http.post<ListingOut>(`${V1}/listings/${id}/resume`),
  close: (id: string) => http.post<ListingOut>(`${V1}/listings/${id}/close`),
  /** Sermaye ilanının anaparasını kasaya kilitle (`reserve`; pre_steps: approve) — 02 §3.3. */
  reserveTx: (id: string) => http.post<UnsignedTxOut>(`${V1}/listings/${id}/tx/reserve`),
  /** Kilitli sermayeyi geri al: gövde yok → `releaseAll`, `{amount}` → `release` — 02 §3.4. */
  releaseTx: (id: string, body?: { amount?: string | null }) =>
    http.post<UnsignedTxOut>(`${V1}/listings/${id}/tx/release`, body),
};

// --- Teklifler ---
export const offersApi = {
  create: (payload: OfferCreateIn) => http.post<OfferOut>(`${V1}/offers`, payload),
  list: (params?: PageParams & { box?: OfferBox; status?: OfferStatus; listing_id?: string }) =>
    http.get<Page<OfferOut>>(`${V1}/offers`, params),
  byId: (id: string) => http.get<OfferOut>(`${V1}/offers/${id}`),
  /** `next_action`: open | open_reserved | propose (02 §8.4). */
  accept: (id: string) => http.post<OfferAcceptOut>(`${V1}/offers/${id}/accept`),
  reject: (id: string, body?: { reason?: string | null }) =>
    http.post<OfferOut>(`${V1}/offers/${id}/reject`, body),
  withdraw: (id: string) => http.post<OfferOut>(`${V1}/offers/${id}/withdraw`),
  stats: () => http.get<OfferStatsOut>(`${V1}/offers/stats`),
};

// --- Sözleşmeler (agreements) ---
export const agreementsApi = {
  list: (params?: PageParams & { role?: UserRole; status?: AgreementStatus | 'open' | 'closed' }) =>
    http.get<Page<AgreementOut>>(`${V1}/agreements`, params),
  /** `refresh=true` canlı okuma (10 sn önbellek). */
  byId: (id: string, params?: { refresh?: boolean }) =>
    http.get<AgreementOut>(`${V1}/agreements/${id}`, params),
  quote: (
    id: string,
    params: {
      token_in: string;
      token_out: string;
      amount_in: string;
      slippage_bps?: number;
      deadline_seconds?: number;
    },
  ) => http.get<QuoteOut>(`${V1}/agreements/${id}/quote`, params),
  trades: (id: string, params?: PageParams) =>
    http.get<Page<TradeOut>>(`${V1}/agreements/${id}/trades`, params),
  valueHistory: (id: string, params?: { range?: ValueRange }) =>
    http.get<ValueHistoryOut>(`${V1}/agreements/${id}/value-history`, params),
  /**
   * İmzasız calldata üretir (02 §3.1). `action` `available_actions`'tan seçilir; `open` yerine
   * `open_reserved` gerekirse 409 `use_reserved_action` (`details.action`) gelir.
   */
  buildTx: (id: string, action: TxAction, body?: TxActionIn) =>
    http.post<UnsignedTxOut>(`${V1}/agreements/${id}/tx/${action}`, body),
  buildTradeTx: (id: string, payload: TradeTxIn) =>
    http.post<UnsignedTxOut>(`${V1}/agreements/${id}/tx/trade`, payload),
  rate: (id: string, payload: RatingCreateIn) =>
    http.post<RatingCreatedOut>(`${V1}/agreements/${id}/rating`, payload),
  rating: (id: string) => http.get<RatingOut>(`${V1}/agreements/${id}/rating`),
};

// --- İşlemler (trade notu) ---
export const tradesApi = {
  update: (tradeId: string, payload: TradeNoteIn) =>
    http.patch<TradeOut>(`${V1}/trades/${tradeId}`, payload),
};

// --- Zincir işlemi bildirimi (02 §2.3–2.4) ---
export const txApi = {
  /** Cüzdanın yayınladığı hash'i bildir; receipt için en fazla `tx_submit_timeout_seconds` beklenir. */
  submit: (pendingTxId: string, txHash: string) =>
    http.post<TxStatusOut>(`${V1}/tx/submit`, { pending_tx_id: pendingTxId, tx_hash: txHash }),
  /** `submitted` satırlarda her çağrıda receipt sorulur. */
  status: (pendingTxId: string) => http.get<TxStatusOut>(`${V1}/tx/${pendingTxId}`),
};

// --- Panel & hareketler ---
export const dashboardApi = {
  /** `role` alanıyla `CustomerDashboardOut | TraderDashboardOut` ayrıştırılır. */
  get: () => http.get<DashboardOut>(`${V1}/dashboard`),
};

export function isCustomerDashboard(d: DashboardOut): d is CustomerDashboardOut {
  return d.role === 'customer';
}
export function isTraderDashboard(d: DashboardOut): d is TraderDashboardOut {
  return d.role === 'trader';
}

export const activityApi = {
  feed: (params?: PageParams & { state?: 'open' | 'closed' }) =>
    http.get<Page<ActivityItemOut>>(`${V1}/activity`, params),
};

// --- Mesajlar ---
export const conversationsApi = {
  list: (params?: PageParams) => http.get<Page<ConversationOut>>(`${V1}/conversations`, params),
  /** Teklife bağlı olmayan doğrudan sohbet. */
  start: (payload: { user_id: string }) =>
    http.post<ConversationOut>(`${V1}/conversations`, payload),
  unreadCount: () =>
    http.get<ConversationsUnreadOut>(`${V1}/conversations/unread-count`),
  byId: (id: string) => http.get<ConversationOut>(`${V1}/conversations/${id}`),
  /** `after`/`before` mesaj id'si; yanıt kronolojik + `has_more`. */
  messages: (id: string, params?: { after?: string; before?: string; limit?: number }) =>
    http.get<MessagesPageOut>(`${V1}/conversations/${id}/messages`, params),
  send: (id: string, body: string) =>
    http.post<MessageOut>(`${V1}/conversations/${id}/messages`, { body }),
  markRead: (id: string) => http.post<ConversationReadOut>(`${V1}/conversations/${id}/read`),
};

// --- Bildirimler ---
export const notificationsApi = {
  list: (params?: PageParams & { category?: NotificationCategory; unread_only?: boolean }) =>
    http.get<Page<NotificationOut>>(`${V1}/notifications`, params),
  byId: (id: string) => http.get<NotificationOut>(`${V1}/notifications/${id}`),
  unreadCount: () => http.get<UnreadCountOut>(`${V1}/notifications/unread-count`),
  /** `MarkReadIn` gövdesi zorunlu: `{all: true, category?}`. */
  markAllRead: (category?: NotificationCategory) =>
    http.post<MarkReadOut>(`${V1}/notifications/read`, { all: true, category }),
  markRead: (id: string) => http.post<MarkReadOut>(`${V1}/notifications/${id}/read`),
  /** `null` cihazı kayıttan düşürür. */
  setPushToken: (token: string | null) =>
    http.put<MeOut>(`${V1}/notifications/push-token`, { expo_push_token: token }),
};

// --- Cüzdan (02 §7) ---
export const walletApi = {
  /** MON + allow-list token bakiyeleri (sıfır dahil). 502 `chain_error` → RPC yok. */
  get: () => http.get<WalletOut>(`${V1}/wallet`),
  depositInfo: () => http.get<DepositInfoOut>(`${V1}/wallet/deposit-info`),
  /** `asset_id: null` → native MON; aksi ERC-20 `transfer`. Yanıt imzasız calldata. */
  transfer: (payload: WalletTransferIn) =>
    http.post<UnsignedTxOut>(`${V1}/wallet/tx/transfer`, payload),
  /** Test token mint (günde 1). 429 → `details.next_allowed_at`. */
  faucet: (payload: { asset_id: string }) => http.post<FaucetOut>(`${V1}/wallet/faucet`, payload),
  /** Opsiyonel uç (sprint sonu); 404 tolere edilir. */
  transactions: (params?: PageParams) =>
    http.get<Page<WalletTransferOut>>(`${V1}/wallet/transactions`, params),
};

// --- Kur ---
export const fxApi = {
  rates: () => http.get<FxOut>(`${V1}/fx`, undefined, NO_AUTH),
  convert: (params: { amount_usd: string | number }) =>
    http.get<FxConvertOut>(`${V1}/fx/convert`, params, NO_AUTH),
};
