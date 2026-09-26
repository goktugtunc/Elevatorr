// TEMPORARY until gen:api
//
// Bu dosya normalde `npm run gen:api` (openapi-typescript) ile backend'in /openapi.json'ından
// üretilir. Monad backend'i henüz yayında olmadığı için docs/monad/02-api-sozlesme.md'ye göre
// elle yazılmış iskelettir; ad ve şekiller üretilen dosyayla aynıdır (components.schemas.*).
// `paths` / `operations` bloğu üretimde dolar — bugün yalnız yer tutucudur.
//
// Kurallar (02 §0): tutarlar string (insan okunur), `*_raw` string tam sayı, oranlar bps int,
// adresler EIP-55 checksum, tx hash 0x + 64 hex, zamanlar ISO 8601 UTC.

export interface paths {
  [path: string]: Record<string, unknown>;
}

export interface webhooks {
  [name: string]: never;
}

export interface components {
  schemas: {
    // --- enumlar -------------------------------------------------------------------------------
    UserRole: 'customer' | 'trader';
    RiskProfile: 'conservative' | 'balanced' | 'aggressive';
    RiskLevel: 'low' | 'medium' | 'high';
    MarketCategory: 'crypto' | 'stable_fx' | 'defi';
    ListingKind: 'capital' | 'service';
    ListingStatus: 'draft' | 'active' | 'paused' | 'closed';
    ListingSort: 'newest' | 'popular' | 'amount';
    InteractionTargetType: 'listing' | 'user';
    InteractionAction: 'pass' | 'like' | 'save' | 'follow' | 'view' | 'offer_request';
    OfferDirection: 'trader_to_customer' | 'customer_to_trader';
    OfferStatus: 'pending' | 'accepted' | 'rejected' | 'withdrawn' | 'expired';
    OfferBox: 'inbox' | 'outbox' | 'all';
    AgreementStatus:
      | 'draft'
      | 'proposed'
      | 'funded'
      | 'active'
      | 'settled'
      | 'cancelled'
      | 'failed';
    NotificationCategory: 'listing' | 'offer' | 'agreement' | 'wallet' | 'system';
    /** 02 §2.1 — `payment`/`trustline` silindi. */
    PendingTxKind:
      | 'open'
      | 'open_reserved'
      | 'propose'
      | 'fund'
      | 'fund_reserved'
      | 'accept'
      | 'cancel'
      | 'settle'
      | 'claim'
      | 'trade'
      | 'reserve'
      | 'release'
      | 'transfer'
      | 'admin';
    /** 02 §2.5 */
    PendingTxStatus: 'pending' | 'submitted' | 'confirmed' | 'failed' | 'expired';
    /** 02 §3.1 — `POST /agreements/{id}/tx/{action}` path enum'u. */
    TxAction:
      | 'propose'
      | 'open'
      | 'open_reserved'
      | 'fund'
      | 'fund_reserved'
      | 'accept'
      | 'cancel'
      | 'settle'
      | 'claim';
    ValueRange: '24h' | '7d' | '30d' | '90d' | 'all';
    TraderSort: 'rating' | 'return' | 'capital' | 'followers' | 'newest';
    PerformanceRange: '7d' | '30d' | '90d' | '1y' | 'all';
    ActivityRelation: 'party' | 'following';

    // --- ortak ---------------------------------------------------------------------------------
    ErrorBody: { code: string; message: string; details: Record<string, unknown> };
    Message: { message: string };
    IdResponse: { id: string };

    // --- auth (02 §1) ---------------------------------------------------------------------------
    NonceIn: { address: string };
    NonceOut: {
      nonce: string;
      /** EIP-4361 mesajı; istemci byte'ı byte'ına imzalar ve aynen geri gönderir. */
      message: string;
      expires_at: string;
      chain_id: number;
      domain: string;
    };
    VerifyIn: {
      message: string;
      /** 0x + 130 hex (65 byte EIP-191 imzası) */
      signature: string;
    };
    LoginOut: {
      token: string;
      expires_at: string;
      /** checksum adres */
      address: string;
      /** false ise `user` null; istemci `POST /users/register`'a gider. */
      registered: boolean;
      user: components['schemas']['MeOut'] | null;
    };
    AuthMeOut: {
      address: string;
      registered: boolean;
      user: components['schemas']['MeOut'] | null;
      token_expires_at: string;
    };

    // --- config / assets / fx (02 §4, §5) -------------------------------------------------------
    ChainConfigOut: {
      chain_id: number;
      name: string;
      rpc_url: string;
      ws_url: string | null;
      explorer_url: string;
      native_symbol: string;
      native_decimals: number;
      faucet_url: string | null;
    };
    ContractsConfigOut: {
      vault: string;
      router: string | null;
      multicall3: string | null;
    };
    ContractLimits: {
      max_tokens: number;
      min_duration_days: number;
      max_duration_days: number;
      max_commission_bps: number;
      max_platform_fee_bps: number;
      min_drawdown_bps: number;
      max_drawdown_bps: number;
      max_settle_slippage_bps: number;
    };
    ContractConfigOut: {
      owner: string;
      router: string;
      platform_fee_bps: number;
      fee_recipient: string;
      paused: boolean;
      settle_slippage_bps: number;
    };
    AuthConfigOut: {
      siwe_domain: string;
      siwe_uri: string;
      siwe_statement: string;
      nonce_ttl_seconds: number;
      access_token_ttl_seconds: number;
      refresh_max_age_seconds: number;
    };
    ConfigOut: {
      version: string;
      api_prefix: string;
      chain: components['schemas']['ChainConfigOut'];
      contracts: components['schemas']['ContractsConfigOut'];
      assets: components['schemas']['AssetOut'][];
      default_base_asset_code: string;
      default_base_asset_id: string | null;
      platform_fee_bps: number | null;
      settle_slippage_bps: number;
      default_trade_slippage_bps: number;
      tx_submit_timeout_seconds: number;
      pending_tx_ttl_seconds: number;
      limits: components['schemas']['ContractLimits'];
      contract: components['schemas']['ContractConfigOut'] | null;
      contract_error: string | null;
      auth: components['schemas']['AuthConfigOut'];
      fx_cache_seconds: number;
      /** anahtar symbol; fiyat bilinmiyorsa null (MON) */
      usd_prices: { [symbol: string]: string | null };
    };
    AssetOut: {
      id: string;
      chain_id: number;
      /** ERC-20 adresi (checksum) */
      address: string;
      symbol: string;
      /** @deprecated symbol ile aynı değer */
      code: string;
      name: string;
      decimals: number;
      category: components['schemas']['MarketCategory'];
      is_base_allowed: boolean;
      is_active: boolean;
      onchain_allowed: boolean;
      icon_url: string | null;
      /** her zaman false — MON asset tablosunda yer almaz (K6) */
      is_native: boolean;
      created_at: string;
    };
    AssetBriefOut: {
      id: string;
      symbol: string;
      code: string;
      address: string;
      decimals: number;
      name: string;
      icon_url: string | null;
      category: components['schemas']['MarketCategory'];
    };
    FxOut: {
      pair: string;
      base: string;
      quote: string;
      rate: string;
      source: string;
      fetched_at: string;
      stale: boolean;
      cache_seconds: number;
      usd_prices: { [symbol: string]: string | null };
      usd_prices_indicative: boolean;
      note: string;
    };
    FxConvertOut: { amount_usd: string; amount_try: string; rate: string; stale: boolean };

    // --- health (02 §9) ---------------------------------------------------------------------------
    HealthOut: { status: string; db?: string; version?: string };
    HealthChainOut: {
      ok: boolean;
      chain_id: number;
      block_number?: number;
      latency_ms?: number;
      rpc_url: string;
      error?: string;
    };

    // --- users (02 §6) --------------------------------------------------------------------------
    TraderStats: {
      total_return_bps: number;
      monthly_return_bps: number;
      max_drawdown_bps: number;
      win_rate_bps: number;
      managed_capital: string;
      active_agreements: number;
      rating_avg: string;
      rating_count: number;
    };
    UserOut: {
      id: string;
      /** EIP-55 checksum adres */
      wallet_address: string;
      role: components['schemas']['UserRole'];
      username: string;
      display_name: string;
      avatar_url: string | null;
      bio: string | null;
      markets: string[];
      created_at: string;
      budget_amount: string | null;
      risk_profile: components['schemas']['RiskProfile'] | null;
      strategy_summary: string | null;
      portfolio: string | null;
      commission_bps: number | null;
      min_capital: string | null;
      risk_level: components['schemas']['RiskLevel'] | null;
      stats: components['schemas']['TraderStats'] | null;
    };
    MeOut: components['schemas']['UserOut'] & {
      expo_push_token: string | null;
      is_admin: boolean;
      is_active: boolean;
      last_login_at: string | null;
      updated_at: string | null;
    };
    CustomerProfileIn: {
      budget_amount: string;
      risk_profile: components['schemas']['RiskProfile'];
      markets: components['schemas']['MarketCategory'][];
    };
    TraderProfileIn: {
      markets: components['schemas']['MarketCategory'][];
      strategy_summary: string;
      commission_bps: number;
      min_capital: string;
      risk_level: components['schemas']['RiskLevel'];
    };
    /** Adres gövdede yok; JWT `sub`'dan alınır (02 §13 #33). */
    RegisterIn: {
      role: components['schemas']['UserRole'];
      username: string;
      display_name: string;
      bio?: string | null;
      avatar_url?: string | null;
      customer?: components['schemas']['CustomerProfileIn'] | null;
      trader?: components['schemas']['TraderProfileIn'] | null;
    };
    RegisterOut: { user: components['schemas']['MeOut']; token: string; expires_at: string };
    UserUpdateIn: {
      display_name?: string | null;
      bio?: string | null;
      avatar_url?: string | null;
      expo_push_token?: string | null;
      markets?: components['schemas']['MarketCategory'][] | null;
      budget_amount?: string | null;
      risk_profile?: components['schemas']['RiskProfile'] | null;
      strategy_summary?: string | null;
      portfolio?: string | null;
      commission_bps?: number | null;
      min_capital?: string | null;
      risk_level?: components['schemas']['RiskLevel'] | null;
    };
    PerformancePoint: { at: string; value: string; principal: string; return_bps: number };
    PositionBalanceOut: {
      asset_id: string;
      symbol: string;
      code: string;
      address: string;
      balance: string;
    };
    PositionOut: {
      agreement_id: string;
      onchain_id: number | null;
      customer_id: string;
      customer_username: string;
      customer_display_name: string;
      base_asset_code: string;
      principal: string;
      current_value: string | null;
      pnl_bps: number | null;
      start_time: string | null;
      end_time: string | null;
      balances: components['schemas']['PositionBalanceOut'][];
    };
    TradeBriefOut: {
      id: string;
      agreement_id: string;
      tx_hash: string | null;
      block_number: number | null;
      symbol_label: string;
      token_in_code: string;
      token_out_code: string;
      amount_in: string;
      amount_out: string;
      value_after: string | null;
      note: string | null;
      created_at: string;
    };
    RatingOut: {
      id: string;
      agreement_id: string;
      customer_id: string;
      customer_username: string | null;
      customer_display_name: string | null;
      score: number;
      comment: string | null;
      created_at: string;
    };
    RatingsSummary: { avg: string; count: number; distribution: { [score: string]: number } };
    TraderProfileOut: {
      user: components['schemas']['UserOut'];
      stats: components['schemas']['TraderStats'];
      follower_count: number;
      is_following: boolean | null;
      active_listings: number;
      performance_range: string;
      performance: components['schemas']['PerformancePoint'][];
      positions: components['schemas']['PositionOut'][];
      recent_trades: components['schemas']['TradeBriefOut'][];
      ratings: components['schemas']['RatingsSummary'];
      recent_ratings: components['schemas']['RatingOut'][];
    };
    TraderCardOut: {
      user: components['schemas']['UserOut'];
      follower_count: number;
      is_following: boolean | null;
    };
    FollowOut: { trader_id: string; following: boolean; follower_count: number };
    RatingCreateIn: { score: number; comment?: string | null };
    RatingCreatedOut: {
      rating: components['schemas']['RatingOut'];
      trader_id: string;
      rating_avg: string;
      rating_count: number;
    };

    // --- listings (02 §8.3) ---------------------------------------------------------------------
    ListingCreateIn: {
      kind?: components['schemas']['ListingKind'] | null;
      title: string;
      description?: string;
      markets?: components['schemas']['MarketCategory'][] | null;
      risk_profile?: components['schemas']['RiskProfile'] | null;
      amount?: string | null;
      base_asset_id?: string | null;
      duration_days?: number | null;
      max_loss_bps?: number | null;
      commission_bps?: number | null;
      min_capital?: string | null;
      expected_return_min_bps?: number | null;
      expected_return_max_bps?: number | null;
    };
    ListingUpdateIn: {
      title?: string | null;
      description?: string | null;
      markets?: components['schemas']['MarketCategory'][] | null;
      risk_profile?: components['schemas']['RiskProfile'] | null;
      amount?: string | null;
      base_asset_id?: string | null;
      duration_days?: number | null;
      max_loss_bps?: number | null;
      commission_bps?: number | null;
      min_capital?: string | null;
      expected_return_min_bps?: number | null;
      expected_return_max_bps?: number | null;
    };
    ListingOut: {
      id: string;
      owner_id: string;
      owner: components['schemas']['UserOut'];
      kind: components['schemas']['ListingKind'];
      title: string;
      description: string;
      risk_profile: components['schemas']['RiskProfile'] | null;
      markets: string[];
      status: components['schemas']['ListingStatus'];
      amount: string | null;
      base_asset_id: string | null;
      base_asset: components['schemas']['AssetOut'] | null;
      duration_days: number | null;
      max_loss_bps: number | null;
      reservation_id: number | null;
      reserved_amount: string | null;
      is_funded: boolean;
      commission_bps: number | null;
      min_capital: string | null;
      expected_return_min_bps: number | null;
      expected_return_max_bps: number | null;
      view_count: number;
      like_count: number;
      offer_count: number;
      closed_at: string | null;
      created_at: string;
      updated_at: string | null;
      is_owner: boolean | null;
      is_saved: boolean | null;
      is_liked: boolean | null;
    };
    ListingDetailOut: components['schemas']['ListingOut'] & {
      /** yalnız sahibi görür */
      offers: components['schemas']['OfferOut'][] | null;
      pending_offers: number;
      my_offer_id: string | null;
    };
    ListingCountsOut: { draft: number; active: number; paused: number; closed: number };
    /** `POST /listings/{id}/tx/release` gövdesi (02 §3.4) — `amount` yok/null → releaseAll */
    ReleaseIn: { amount?: string | null };

    // --- discover ---------------------------------------------------------------------------------
    DiscoverCardOut: {
      target_type: components['schemas']['InteractionTargetType'];
      target_id: string;
      owner_target_id: string;
      kind: components['schemas']['ListingKind'];
      listing: components['schemas']['ListingOut'];
      is_following: boolean;
      is_saved: boolean;
      tags: string[];
    };
    DiscoverFeedOut: {
      items: components['schemas']['DiscoverCardOut'][];
      next_cursor: string | null;
      remaining: number;
      kind: components['schemas']['ListingKind'];
    };
    DiscoverRemainingOut: { remaining: number; kind: components['schemas']['ListingKind'] };
    DiscoverActionIn: { action: components['schemas']['InteractionAction'] };
    DiscoverActionOut: {
      target_type: components['schemas']['InteractionTargetType'];
      target_id: string;
      action: components['schemas']['InteractionAction'];
      created: boolean;
      remaining: number;
      following: boolean | null;
      saved: boolean | null;
      like_count: number | null;
    };

    // --- offers -----------------------------------------------------------------------------------
    OfferCreateIn: {
      listing_id: string;
      amount?: string | null;
      base_asset_id?: string | null;
      duration_days?: number | null;
      commission_bps?: number | null;
      max_drawdown_bps?: number | null;
      expected_return_min_bps?: number | null;
      expected_return_max_bps?: number | null;
      note?: string | null;
      expires_in_hours?: number;
    };
    OfferRejectIn: { reason?: string | null };
    ListingBriefOut: {
      id: string;
      owner_id: string;
      kind: components['schemas']['ListingKind'];
      title: string;
      status: components['schemas']['ListingStatus'];
      risk_profile: components['schemas']['RiskProfile'] | null;
      markets: string[];
      amount: string | null;
      duration_days: number | null;
      max_loss_bps: number | null;
      commission_bps: number | null;
      min_capital: string | null;
    };
    OfferOut: {
      id: string;
      listing_id: string;
      listing: components['schemas']['ListingBriefOut'];
      from_user_id: string;
      to_user_id: string;
      from_user: components['schemas']['UserOut'];
      to_user: components['schemas']['UserOut'];
      direction: components['schemas']['OfferDirection'];
      amount: string;
      base_asset_id: string;
      base_asset: components['schemas']['AssetOut'];
      duration_days: number;
      commission_bps: number;
      max_drawdown_bps: number;
      expected_return_min_bps: number | null;
      expected_return_max_bps: number | null;
      note: string | null;
      status: components['schemas']['OfferStatus'];
      expires_at: string;
      responded_at: string | null;
      agreement_id: string | null;
      created_at: string;
      updated_at: string | null;
      conversation_id: string | null;
      is_incoming: boolean | null;
    };
    AgreementDraftOut: {
      id: string;
      onchain_id: number | null;
      offer_id: string | null;
      listing_id: string | null;
      customer_id: string;
      trader_id: string;
      customer: components['schemas']['UserOut'];
      trader: components['schemas']['UserOut'];
      base_asset_id: string;
      base_asset: components['schemas']['AssetOut'];
      principal: string;
      duration_secs: number;
      commission_bps: number;
      max_drawdown_bps: number;
      risk_profile: components['schemas']['RiskProfile'] | null;
      listing_ref: string;
      status: components['schemas']['AgreementStatus'];
      proposer_role: components['schemas']['UserRole'];
      created_at: string;
    };
    OfferAcceptOut: {
      offer: components['schemas']['OfferOut'];
      agreement: components['schemas']['AgreementDraftOut'];
      conversation_id: string;
      /** 02 §8.4 */
      next_action: 'open' | 'open_reserved' | 'propose';
    };
    OfferStatsOut: { pending_inbox: number; pending_outbox: number };

    // --- agreements (02 §8.1) -------------------------------------------------------------------
    PartyOut: {
      id: string;
      username: string;
      display_name: string;
      avatar_url: string | null;
      wallet_address: string;
      role: components['schemas']['UserRole'];
    };
    BalanceOut: {
      asset: components['schemas']['AssetBriefOut'];
      balance: string;
      updated_at: string | null;
      value_try: string | null;
    };
    TlOut: {
      rate: string;
      rate_source: string;
      stale: boolean;
      base_usd_price: string | null;
      base_price_source: string | null;
      principal_try: string | null;
      current_value_try: string | null;
      pnl_try: string | null;
      final_value_try: string | null;
      customer_payout_try: string | null;
    };
    PendingTxBriefOut: {
      id: string;
      kind: components['schemas']['PendingTxKind'];
      action: string;
      status: components['schemas']['PendingTxStatus'];
      tx_hash: string | null;
      created_at: string;
      expires_at: string;
    };
    AgreementOut: {
      id: string;
      onchain_id: number | null;
      offer_id: string | null;
      listing_id: string | null;
      status: components['schemas']['AgreementStatus'];
      proposer_role: components['schemas']['UserRole'];
      customer: components['schemas']['PartyOut'];
      trader: components['schemas']['PartyOut'];
      base_asset: components['schemas']['AssetBriefOut'];
      principal: string;
      duration_secs: number;
      duration_days: number;
      commission_bps: number;
      max_drawdown_bps: number;
      risk_profile: components['schemas']['RiskProfile'] | null;
      listing_ref: string;
      created_tx: string | null;
      activate_tx: string | null;
      cancel_tx: string | null;
      settle_tx: string | null;
      start_time: string | null;
      end_time: string | null;
      seconds_remaining: number | null;
      is_expired: boolean;
      current_value: string | null;
      value_updated_at: string | null;
      high_water_value: string | null;
      pnl: string | null;
      pnl_bps: number | null;
      drawdown_floor: string;
      drawdown_bps: number;
      final_value: string | null;
      profit: string | null;
      trader_fee: string | null;
      platform_fee: string | null;
      customer_payout: string | null;
      settled_at: string | null;
      settled_by: string | null;
      last_event_block_number: number | null;
      vault_address: string;
      balances: components['schemas']['BalanceOut'][];
      my_role: components['schemas']['UserRole'] | null;
      /** open, open_reserved, propose, fund, fund_reserved, accept, cancel, trade, settle, claim */
      available_actions: string[];
      claimable_assets: string[];
      pending_tx: components['schemas']['PendingTxBriefOut'] | null;
      tl: components['schemas']['TlOut'] | null;
      created_at: string;
      updated_at: string;
    };
    ValuePointOut: { at: string; value: string; return_bps: number };
    ValueHistoryOut: {
      agreement_id: string;
      range: components['schemas']['ValueRange'];
      principal: string;
      current_value: string | null;
      high_water_value: string | null;
      points: components['schemas']['ValuePointOut'][];
    };

    // --- trades / activity (02 §8.2) ------------------------------------------------------------
    QuoteOut: {
      agreement_id: string;
      onchain_id: number;
      token_in: components['schemas']['AssetBriefOut'];
      token_out: components['schemas']['AssetBriefOut'];
      amount_in: string;
      amount_out: string;
      min_out: string;
      slippage_bps: number;
      price: string;
      /** "router" | "fake" */
      source: string;
      api_amount_out: string | null;
      price_impact_pct: string | null;
      balance_in: string;
      value_before: string;
      value_after_estimate: string;
      principal: string;
      max_drawdown_bps: number;
      drawdown_floor: string;
      headroom: string;
      headroom_bps: number;
      allowed: boolean;
      reason: string | null;
      deadline_seconds: number;
      quoted_at: string;
    };
    TradeTxIn: {
      /** asset uuid | 0x adres | symbol */
      token_in: string;
      token_out: string;
      amount_in: string;
      slippage_bps?: number | null;
      note?: string | null;
      notify_investors?: boolean;
      deadline_seconds?: number;
    };
    TradeOut: {
      id: string;
      agreement_id: string;
      onchain_id: number | null;
      log_index: number | null;
      tx_hash: string | null;
      block_number: number | null;
      explorer_url: string | null;
      trader_id: string | null;
      token_in: components['schemas']['AssetBriefOut'];
      token_out: components['schemas']['AssetBriefOut'];
      amount_in: string;
      amount_out: string;
      price: string | null;
      value_after: string | null;
      note: string | null;
      symbol_label: string | null;
      notify_investors: boolean;
      created_at: string;
    };
    TradeNoteIn: { note?: string | null; notify_investors?: boolean | null };
    ActivityItemOut: {
      trade: components['schemas']['TradeOut'];
      agreement_status: components['schemas']['AgreementStatus'];
      base_asset_code: string;
      trader: components['schemas']['PartyOut'];
      customer: components['schemas']['PartyOut'] | null;
      relation: components['schemas']['ActivityRelation'];
    };

    // --- tx (02 §2) -------------------------------------------------------------------------------
    TxActionIn: { slippage_bps?: number | null; asset_id?: string | null };
    PreStepOut: {
      kind: 'approve';
      to: string;
      data: string;
      value: string;
      gas: string;
      description: string;
      spender: string;
      asset_id: string;
      symbol: string;
      amount: string;
      amount_raw: string;
    };
    UnsignedTxOut: {
      /** admin uçlarında null */
      pending_tx_id: string;
      kind: components['schemas']['PendingTxKind'];
      /** ABI fonksiyon adı (camelCase) */
      action: string;
      agreement_id: string | null;
      listing_id: string | null;
      chain_id: number;
      from_address: string;
      to: string;
      /** 0x hex calldata; native transferde "0x" */
      data: string;
      /** wei, ondalık string */
      value: string;
      /** ondalık string; istemci BigInt(gas) */
      gas: string;
      description: string;
      pre_steps: components['schemas']['PreStepOut'][];
      summary: Record<string, unknown>;
      expires_at: string;
    };
    TxSubmitIn: { pending_tx_id: string; tx_hash: string };
    TxEventOut: { name: string; args: Record<string, unknown> };
    TxStatusOut: {
      pending_tx_id: string;
      kind: components['schemas']['PendingTxKind'];
      action: string;
      status: components['schemas']['PendingTxStatus'];
      tx_hash: string | null;
      block_number: number | null;
      confirmations: number | null;
      /** "vault:<Ad>" | "erc20:<reason>" | reverted | receipt_mismatch | not_included | … (02 §2.6) */
      error_code: string | null;
      error_message: string | null;
      contract_error_code: number | null;
      explorer_url: string | null;
      agreement_id: string | null;
      agreement_status: components['schemas']['AgreementStatus'] | null;
      onchain_id: number | null;
      trade_id: string | null;
      events: components['schemas']['TxEventOut'][];
      submitted_at: string | null;
      updated_at: string;
    };

    // --- wallet (02 §7) ---------------------------------------------------------------------------
    NativeBalanceOut: { symbol: string; decimals: number; balance: string; balance_raw: string };
    TokenBalanceOut: {
      asset_id: string;
      address: string;
      symbol: string;
      decimals: number;
      balance: string;
      balance_raw: string;
      is_base_allowed: boolean;
    };
    WalletOut: {
      address: string;
      chain_id: number;
      native: components['schemas']['NativeBalanceOut'];
      tokens: components['schemas']['TokenBalanceOut'][];
      explorer_url: string;
      updated_at: string;
    };
    TokenFaucetAssetOut: {
      asset_id: string;
      symbol: string;
      amount: string;
      daily_limit: number;
      next_allowed_at: string | null;
    };
    TokenFaucetOut: { enabled: boolean; assets: components['schemas']['TokenFaucetAssetOut'][] };
    DepositInfoOut: {
      address: string;
      chain_id: number;
      /** EIP-681 (QR) */
      pay_uri: string;
      faucet_url: string | null;
      token_faucet: components['schemas']['TokenFaucetOut'];
      instructions: string[];
    };
    FaucetIn: { asset_id: string };
    FaucetOut: {
      tx_hash: string;
      status: 'submitted' | 'confirmed' | 'failed';
      asset_id: string;
      symbol: string;
      amount: string;
      amount_raw: string;
      next_allowed_at: string | null;
      explorer_url: string | null;
    };
    WalletTransferIn: { asset_id?: string | null; to: string; amount: string };
    WalletTransferOut: {
      tx_hash: string;
      block_number: number;
      log_index: number;
      /** null → MON */
      asset_id: string | null;
      symbol: string;
      direction: 'in' | 'out';
      counterparty: string;
      counterparty_label: string | null;
      amount: string;
      amount_raw: string;
      at: string;
      explorer_url: string | null;
    };

    // --- dashboard ---------------------------------------------------------------------------------
    ListingInteractionsOut: {
      listings: number;
      views: number;
      likes: number;
      offers: number;
      pending_offers: number;
    };
    PositionBriefOut: {
      agreement_id: string;
      onchain_id: number | null;
      status: components['schemas']['AgreementStatus'];
      counterparty_id: string;
      counterparty_username: string;
      counterparty_display_name: string;
      counterparty_avatar_url: string | null;
      base_asset_code: string;
      principal: string;
      current_value: string;
      pnl: string;
      pnl_bps: number;
      commission_bps: number;
      duration_days: number;
      start_time: string | null;
      end_time: string | null;
    };
    FollowedTraderOut: {
      trader_id: string;
      username: string;
      display_name: string;
      avatar_url: string | null;
      risk_level: components['schemas']['RiskLevel'] | null;
      commission_bps: number | null;
      total_return_bps: number;
      monthly_return_bps: number;
      rating_avg: string;
      invested: boolean;
      invested_principal: string;
      open_pnl_bps: number | null;
    };
    CustomerDashboardOut: {
      role: 'customer';
      generated_at: string;
      base_asset_code: string;
      portfolio_value: string;
      wallet_balance: string | null;
      wallet_error: string | null;
      invested_principal: string;
      positions_value: string;
      open_pnl: string;
      open_pnl_bps: number;
      month_pnl: string;
      month_change_bps: number;
      positions: components['schemas']['PositionBriefOut'][];
      followed: components['schemas']['FollowedTraderOut'][];
      followed_count: number;
      invested_count: number;
      listing_interactions: components['schemas']['ListingInteractionsOut'];
    };
    PendingOfferBriefOut: {
      offer_id: string;
      listing_id: string;
      from_user_id: string;
      from_username: string;
      from_display_name: string;
      from_avatar_url: string | null;
      amount: string;
      base_asset_code: string;
      duration_days: number;
      commission_bps: number;
      markets: string[];
      risk_profile: components['schemas']['RiskProfile'] | null;
      expires_at: string;
      created_at: string;
    };
    ProfileChecklistOut: {
      wallet_connected: boolean;
      has_avatar: boolean;
      has_strategy: boolean;
      has_service_listing: boolean;
      has_trade: boolean;
      completion_pct: number;
    };
    TraderDashboardOut: {
      role: 'trader';
      generated_at: string;
      base_asset_code: string;
      managed_capital: string;
      invested_principal: string;
      open_pnl: string;
      open_pnl_bps: number;
      active_investors: number;
      pending_offers_count: number;
      month_commission: string;
      total_commission: string;
      settled_agreements: number;
      positions: components['schemas']['PositionBriefOut'][];
      pending_offers: components['schemas']['PendingOfferBriefOut'][];
      listing_interactions: components['schemas']['ListingInteractionsOut'];
      profile_checklist: components['schemas']['ProfileChecklistOut'];
    };

    // --- messages ----------------------------------------------------------------------------------
    MessageOut: {
      id: string;
      conversation_id: string;
      sender_id: string;
      body: string;
      created_at: string;
      read_at: string | null;
      is_mine: boolean | null;
    };
    MessageCreateIn: { body: string };
    ConversationCreateIn: { user_id: string };
    ConversationOut: {
      id: string;
      offer_id: string | null;
      agreement_id: string | null;
      other_user: components['schemas']['UserOut'];
      last_message: components['schemas']['MessageOut'] | null;
      last_message_at: string | null;
      unread_count: number;
      created_at: string;
    };
    MessagesPageOut: {
      conversation_id: string;
      /** kronolojik (eskiden yeniye) */
      items: components['schemas']['MessageOut'][];
      has_more: boolean;
    };
    ConversationReadOut: { conversation_id: string; updated: number };
    ConversationsUnreadOut: { conversations: number; messages: number };

    // --- notifications -----------------------------------------------------------------------------
    NotificationOut: {
      id: string;
      category: components['schemas']['NotificationCategory'];
      type: string;
      title: string;
      body: string;
      data: Record<string, unknown>;
      read_at: string | null;
      created_at: string;
    };
    MarkReadIn: {
      ids?: string[];
      all?: boolean;
      category?: components['schemas']['NotificationCategory'] | null;
    };
    MarkReadOut: { updated: number };
    UnreadCountOut: { unread: number; by_category: { [category: string]: number } };
    PushTokenIn: { expo_push_token: string | null };

    // --- sayfalama (02 §0) — OpenAPI'de `Page_ListingOut_` gibi somut adlar üretilir -----------
    Page_ListingOut_: {
      items: components['schemas']['ListingOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_TraderCardOut_: {
      items: components['schemas']['TraderCardOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_RatingOut_: {
      items: components['schemas']['RatingOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_OfferOut_: {
      items: components['schemas']['OfferOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_AgreementOut_: {
      items: components['schemas']['AgreementOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_TradeOut_: {
      items: components['schemas']['TradeOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_ActivityItemOut_: {
      items: components['schemas']['ActivityItemOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_ConversationOut_: {
      items: components['schemas']['ConversationOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_NotificationOut_: {
      items: components['schemas']['NotificationOut'][];
      total: number;
      limit: number;
      offset: number;
    };
    Page_WalletTransferOut_: {
      items: components['schemas']['WalletTransferOut'][];
      total: number;
      limit: number;
      offset: number;
    };
  };
  responses: never;
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}

export type $defs = Record<string, never>;

export interface operations {
  [operationId: string]: Record<string, unknown>;
}
