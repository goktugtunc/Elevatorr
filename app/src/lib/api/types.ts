import type { components } from './schema';

/**
 * Sunucu şemaları — `schema.d.ts` (openapi-typescript çıktısı) üzerinden yalnız **takma ad**.
 * Elle tip yazılmaz; tek istisna `Page<T>` generic'idir (OpenAPI `Page_ListingOut_` gibi somut
 * adlar üretir, `Page<T>` bunların ortak şeklidir).
 *
 * Para alanları **string** (insan okunur; `*_raw` string tam sayı), oranlar **bps**
 * (2000 = %20), adresler EIP-55 checksum, tx hash 0x + 64 hex. Bkz. docs/monad/02-api-sozlesme.md §0.
 */
export type Schemas = components['schemas'];

/** Sayfalı yanıt (02 §0). `paging.ts` → `nextOffset`. */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
/** Tip takma adı (interface değil): `Query` index imzasına örtük uyum için. */
export type PageParams = {
  limit?: number;
  offset?: number;
};

// --- enumlar
export type UserRole = Schemas['UserRole'];
export type RiskProfile = Schemas['RiskProfile'];
export type RiskLevel = Schemas['RiskLevel'];
export type MarketCategory = Schemas['MarketCategory'];
export type ListingKind = Schemas['ListingKind'];
export type ListingStatus = Schemas['ListingStatus'];
export type ListingSort = Schemas['ListingSort'];
export type InteractionTargetType = Schemas['InteractionTargetType'];
export type InteractionAction = Schemas['InteractionAction'];
export type OfferDirection = Schemas['OfferDirection'];
export type OfferStatus = Schemas['OfferStatus'];
export type OfferBox = Schemas['OfferBox'];
export type AgreementStatus = Schemas['AgreementStatus'];
export type NotificationCategory = Schemas['NotificationCategory'];
export type PendingTxKind = Schemas['PendingTxKind'];
export type PendingTxStatus = Schemas['PendingTxStatus'];
export type TxAction = Schemas['TxAction'];
export type ValueRange = Schemas['ValueRange'];
export type TraderSort = Schemas['TraderSort'];
export type PerformanceRange = Schemas['PerformanceRange'];

// --- ortak
export type ErrorBody = Schemas['ErrorBody'];

// --- auth (SIWE)
export type NonceIn = Schemas['NonceIn'];
export type NonceOut = Schemas['NonceOut'];
export type VerifyIn = Schemas['VerifyIn'];
export type LoginOut = Schemas['LoginOut'];
export type AuthMeOut = Schemas['AuthMeOut'];

// --- config / assets / fx / health
export type ConfigOut = Schemas['ConfigOut'];
export type ChainConfigOut = Schemas['ChainConfigOut'];
export type ContractsConfigOut = Schemas['ContractsConfigOut'];
export type ContractLimits = Schemas['ContractLimits'];
export type ContractConfigOut = Schemas['ContractConfigOut'];
export type AuthConfigOut = Schemas['AuthConfigOut'];
export type AssetOut = Schemas['AssetOut'];
export type AssetBriefOut = Schemas['AssetBriefOut'];
export type FxOut = Schemas['FxOut'];
export type FxConvertOut = Schemas['FxConvertOut'];
export type HealthOut = Schemas['HealthOut'];
export type HealthChainOut = Schemas['HealthChainOut'];

// --- users
export type TraderStats = Schemas['TraderStats'];
export type UserOut = Schemas['UserOut'];
export type MeOut = Schemas['MeOut'];
export type CustomerProfileIn = Schemas['CustomerProfileIn'];
export type TraderProfileIn = Schemas['TraderProfileIn'];
export type RegisterIn = Schemas['RegisterIn'];
export type RegisterOut = Schemas['RegisterOut'];
export type UserUpdateIn = Schemas['UserUpdateIn'];
export type TraderCardOut = Schemas['TraderCardOut'];
export type TraderProfileOut = Schemas['TraderProfileOut'];
export type PositionOut = Schemas['PositionOut'];
export type PositionBalanceOut = Schemas['PositionBalanceOut'];
export type TradeBriefOut = Schemas['TradeBriefOut'];
export type PerformancePoint = Schemas['PerformancePoint'];
export type FollowOut = Schemas['FollowOut'];
export type RatingOut = Schemas['RatingOut'];
export type RatingsSummary = Schemas['RatingsSummary'];
export type RatingCreateIn = Schemas['RatingCreateIn'];
export type RatingCreatedOut = Schemas['RatingCreatedOut'];

// --- listings
export type ListingCreateIn = Schemas['ListingCreateIn'];
export type ListingUpdateIn = Schemas['ListingUpdateIn'];
export type ListingOut = Schemas['ListingOut'];
export type ListingDetailOut = Schemas['ListingDetailOut'];
export type ListingCountsOut = Schemas['ListingCountsOut'];
export type ListingBriefOut = Schemas['ListingBriefOut'];
export type ReleaseIn = Schemas['ReleaseIn'];

// --- discover
export type DiscoverCardOut = Schemas['DiscoverCardOut'];
export type DiscoverFeedOut = Schemas['DiscoverFeedOut'];
export type DiscoverRemainingOut = Schemas['DiscoverRemainingOut'];
export type DiscoverActionOut = Schemas['DiscoverActionOut'];

// --- offers
export type OfferCreateIn = Schemas['OfferCreateIn'];
export type OfferRejectIn = Schemas['OfferRejectIn'];
export type OfferOut = Schemas['OfferOut'];
export type OfferAcceptOut = Schemas['OfferAcceptOut'];
export type OfferStatsOut = Schemas['OfferStatsOut'];
export type AgreementDraftOut = Schemas['AgreementDraftOut'];

// --- agreements / trades / activity
export type PartyOut = Schemas['PartyOut'];
export type BalanceOut = Schemas['BalanceOut'];
export type PendingTxBriefOut = Schemas['PendingTxBriefOut'];
export type AgreementOut = Schemas['AgreementOut'];
export type ValueHistoryOut = Schemas['ValueHistoryOut'];
export type ValuePointOut = Schemas['ValuePointOut'];
export type QuoteOut = Schemas['QuoteOut'];
export type TradeTxIn = Schemas['TradeTxIn'];
export type TradeOut = Schemas['TradeOut'];
export type TradeNoteIn = Schemas['TradeNoteIn'];
export type ActivityItemOut = Schemas['ActivityItemOut'];

// --- tx (02 §2)
export type TxActionIn = Schemas['TxActionIn'];
export type PreStepOut = Schemas['PreStepOut'];
export type UnsignedTxOut = Schemas['UnsignedTxOut'];
export type TxSubmitIn = Schemas['TxSubmitIn'];
export type TxStatusOut = Schemas['TxStatusOut'];
export type TxEventOut = Schemas['TxEventOut'];

// --- wallet (02 §7)
export type WalletOut = Schemas['WalletOut'];
export type NativeBalanceOut = Schemas['NativeBalanceOut'];
export type TokenBalanceOut = Schemas['TokenBalanceOut'];
export type DepositInfoOut = Schemas['DepositInfoOut'];
export type FaucetIn = Schemas['FaucetIn'];
export type FaucetOut = Schemas['FaucetOut'];
export type WalletTransferIn = Schemas['WalletTransferIn'];
export type WalletTransferOut = Schemas['WalletTransferOut'];

// --- dashboard
export type CustomerDashboardOut = Schemas['CustomerDashboardOut'];
export type TraderDashboardOut = Schemas['TraderDashboardOut'];
/** `role` ayrıştırıcıdır. */
export type DashboardOut = CustomerDashboardOut | TraderDashboardOut;
export type PositionBriefOut = Schemas['PositionBriefOut'];
export type PendingOfferBriefOut = Schemas['PendingOfferBriefOut'];
export type FollowedTraderOut = Schemas['FollowedTraderOut'];
export type ListingInteractionsOut = Schemas['ListingInteractionsOut'];
export type ProfileChecklistOut = Schemas['ProfileChecklistOut'];

// --- messages
export type MessageOut = Schemas['MessageOut'];
export type ConversationOut = Schemas['ConversationOut'];
export type MessagesPageOut = Schemas['MessagesPageOut'];
export type ConversationReadOut = Schemas['ConversationReadOut'];
export type ConversationsUnreadOut = Schemas['ConversationsUnreadOut'];

// --- notifications
export type NotificationOut = Schemas['NotificationOut'];
export type MarkReadIn = Schemas['MarkReadIn'];
export type MarkReadOut = Schemas['MarkReadOut'];
export type UnreadCountOut = Schemas['UnreadCountOut'];
