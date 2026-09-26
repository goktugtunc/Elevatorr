import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { Eye, Heart, MessageSquare } from 'lucide-react-native';
import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { OfferSheet } from '@/components/discover';
import { Screen, TopBar } from '@/components/layout';
import { TxProgressSheet, verifyUnsignedTx } from '@/components/tx';
import {
  Avatar,
  Button,
  Card,
  ExplorerLink,
  Pill,
  RiskBadge,
  Segmented,
  SlideToConfirm,
  Stat,
  Text,
  initialsOf,
} from '@/components/ui';
import { ApiError, discoverApi, listingsApi, metaApi, offersApi } from '@/lib/api';
import type { ListingDetailOut, OfferCreateIn, OfferOut, UnsignedTxOut } from '@/lib/api/types';
import { applyServerConfig, formatAmount, useTxExecutor } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { formatBps, formatDuration, formatRelative } from '@/lib/format';
import { colors, layout, radius, spacing } from '@/theme';
import { useSession } from '@/store/session';

/**
 * İlan detayı (FE-41, 04 §6.2) — Figma 6c/6d. Sahip: teklifler (kabul/ret) ve ilgi sekmeleri;
 * ziyaretçi: teklif ver / teklif iste / bekleyen teklifini geri çek. Sahibin sermaye ilanı
 * kilitlenmemişse `reserve` (approve + reserve) buradan da yapılabilir.
 */
const MARKET_LABEL: Record<string, string> = {
  crypto: 'Crypto',
  stable_fx: 'Stable / FX',
  defi: 'DeFi',
};
const STATUS_LABEL: Record<ListingDetailOut['status'], string> = {
  draft: 'Draft',
  active: 'Active',
  paused: 'Paused',
  closed: 'Closed',
};
const OFFER_STATUS_LABEL: Record<OfferOut['status'], string> = {
  pending: 'Pending',
  accepted: 'Accepted',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
  expired: 'Expired',
};

type Tab = 'offers' | 'interest';

export default function ListingDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const role = useSession((s) => s.role);
  const [tab, setTab] = useState<Tab>('offers');
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [offerOpen, setOfferOpen] = useState(false);

  useQuery({
    queryKey: ['config'],
    queryFn: async () => {
      const cfg = await metaApi.config();
      try {
        applyServerConfig(cfg);
      } catch {
        // zincir uyuşmazlığı giriş ekranında ele alınır
      }
      return cfg;
    },
    staleTime: 5 * 60_000,
  });

  const listing = useQuery({
    queryKey: ['listing', id],
    queryFn: () => listingsApi.byId(id),
    enabled: !!id,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 1,
  });

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['listing', id] });
    void queryClient.invalidateQueries({ queryKey: ['listings'] });
  }, [queryClient, id]);

  const accept = useMutation({
    mutationFn: (offerId: string) => offersApi.accept(offerId),
    onSuccess: (out) => {
      invalidate();
      setNotice({ tone: 'ok', text: 'Offer accepted' });
      router.push({
        pathname: '/contract/[id]',
        params: { id: out.agreement.id, next: out.next_action },
      });
    },
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });
  const reject = useMutation({
    mutationFn: (offerId: string) => offersApi.reject(offerId),
    onSuccess: () => {
      invalidate();
      setNotice({ tone: 'ok', text: 'Offer rejected.' });
    },
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });
  const withdraw = useMutation({
    mutationFn: (offerId: string) => offersApi.withdraw(offerId),
    onSuccess: () => {
      invalidate();
      setNotice({ tone: 'ok', text: 'Your offer was withdrawn.' });
    },
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });
  const requestOffer = useMutation({
    mutationFn: () => discoverApi.action('listing', id, 'offer_request'),
    onSuccess: () => setNotice({ tone: 'ok', text: 'Your interest was sent to the trader.' }),
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });
  const createOffer = useMutation({
    mutationFn: (draft: OfferCreateIn) => offersApi.create(draft),
    onSuccess: () => {
      setOfferOpen(false);
      invalidate();
      setNotice({ tone: 'ok', text: 'Your offer was sent to the customer.' });
    },
  });

  // Sermaye kilidi (sahip, capital, kilitlenmemiş)
  const tx = useTxExecutor({ onConfirmed: invalidate });
  const buildRef = useRef<(() => Promise<UnsignedTxOut>) | null>(null);
  const lock = useCallback(() => {
    const build = async () => verifyUnsignedTx(await listingsApi.reserveTx(id));
    buildRef.current = build;
    void tx.run(build);
  }, [id, tx]);
  const retry = useCallback(() => {
    const build = buildRef.current;
    if (!build) return;
    tx.reset();
    void tx.run(build);
  }, [tx]);

  if (listing.isPending) {
    return (
      <Screen padded={false}>
        <TopBar title="Listing" />
        <View style={styles.center}>
          <ActivityIndicator color={colors.navy900} />
          <Text variant="caption" color="text2">
            Loading listing…
          </Text>
        </View>
      </Screen>
    );
  }

  if (listing.isError || !listing.data) {
    const notFound = listing.error instanceof ApiError && listing.error.status === 404;
    return (
      <Screen padded={false}>
        <TopBar title="Listing" />
        <View style={styles.body}>
          <Card style={styles.state}>
            <Text variant="h2">{notFound ? 'Not available' : 'Could not load listing'}</Text>
            <Text variant="body" color="text2">
              {notFound ? 'This listing is not public.' : userMessage(listing.error)}
            </Text>
            {!notFound ? <Button title="Try again" onPress={() => listing.refetch()} /> : null}
          </Card>
        </View>
      </Screen>
    );
  }

  const l = listing.data;
  const isOwner = !!l.is_owner;
  const isCapital = l.kind === 'capital';
  const symbol = l.base_asset?.symbol ?? '';
  const owner = l.owner;
  const ownerName = owner?.display_name || owner?.username || '—';
  const offers = l.offers ?? [];
  const pendingOffers = offers.filter((o) => o.status === 'pending');
  const otherOffers = offers.filter((o) => o.status !== 'pending');
  const reserved = !!l.reserved_amount && l.reserved_amount !== '0';
  const canLock = isOwner && isCapital && !reserved && l.status !== 'closed';
  const canOffer = !isOwner && role === 'trader' && isCapital && l.status === 'active';
  const canRequest = !isOwner && role === 'customer' && !isCapital && l.status === 'active';

  return (
    <Screen padded={false}>
      <TopBar title={isCapital ? 'Capital listing' : 'Service listing'} />
      <View style={styles.body}>
        {/* Başlık */}
        <Card style={styles.section}>
          <View style={styles.pills}>
            <Pill
              label={STATUS_LABEL[l.status]}
              tone={l.status === 'active' ? 'navy' : l.status === 'paused' ? 'amber' : 'neutral'}
            />
            {l.risk_profile ? <RiskBadge level={l.risk_profile} /> : null}
            {l.markets.map((m) => (
              <Pill key={m} label={MARKET_LABEL[m] ?? m} />
            ))}
          </View>
          <Text variant="h1">{l.title}</Text>
          {l.description ? (
            <Text variant="body" color="text2">
              {l.description}
            </Text>
          ) : null}
          <View style={styles.owner}>
            <Avatar initials={initialsOf(ownerName)} size="md" />
            <View style={{ flex: 1 }}>
              <Text variant="bodyStrong" numberOfLines={1}>
                {ownerName}
                {isOwner ? ' (you)' : ''}
              </Text>
              <Text variant="caption" color="text3">
                @{owner?.username ?? '—'} · {owner?.role === 'trader' ? 'Trader' : 'Customer'}
              </Text>
            </View>
            {owner?.wallet_address ? <ExplorerLink address={owner.wallet_address} /> : null}
          </View>
        </Card>

        {/* Şartlar */}
        <Card style={styles.section}>
          <Text variant="h2">Terms</Text>
          {isCapital ? (
            <>
              <View style={styles.statsRow}>
                <Stat label="Capital" value={formatAmount(l.amount, symbol)} />
                <Stat label="Duration" value={formatDuration(l.duration_days)} />
              </View>
              <View style={styles.statsRow}>
                <Stat
                  label="Max loss"
                  value={l.max_loss_bps ? formatBps(l.max_loss_bps) : 'No limit'}
                />
                <Stat label="Base asset" value={symbol || '—'} />
              </View>
              <View style={styles.pills}>
                {reserved ? (
                  <Pill label={`Locked ${formatAmount(l.reserved_amount, symbol)}`} tone="navy" />
                ) : (
                  <Pill label="Deposit pending" tone="amber" />
                )}
                {l.reservation_id !== null ? (
                  <Pill label={`Reservation #${l.reservation_id}`} />
                ) : null}
                {l.is_funded ? <Pill label="Funded" tone="navy" /> : null}
              </View>
            </>
          ) : (
            <>
              <View style={styles.statsRow}>
                <Stat label="Commission" value={formatBps(l.commission_bps)} />
                <Stat
                  label="Min. capital"
                  value={l.min_capital ? formatAmount(l.min_capital, symbol) : '—'}
                />
              </View>
              <View style={styles.statsRow}>
                <Stat
                  label="Expected return"
                  value={
                    l.expected_return_min_bps !== null || l.expected_return_max_bps !== null
                      ? `${formatBps(l.expected_return_min_bps)} – ${formatBps(l.expected_return_max_bps)}`
                      : '—'
                  }
                />
                <Stat label="Base asset" value={symbol || '—'} />
              </View>
            </>
          )}
          <View style={styles.counts}>
            <Count
              icon={<Eye size={14} color={colors.text3} />}
              value={l.view_count}
              label="views"
            />
            <Count
              icon={<Heart size={14} color={colors.text3} />}
              value={l.like_count}
              label="likes"
            />
            <Count
              icon={<MessageSquare size={14} color={colors.text3} />}
              value={l.offer_count}
              label="offers"
            />
            <Text variant="caption" color="text3">
              {formatRelative(l.created_at)}
            </Text>
          </View>
        </Card>

        {notice ? (
          <View style={[styles.notice, notice.tone === 'ok' ? styles.noticeOk : styles.noticeErr]}>
            <Text
              variant="caption"
              color={notice.tone === 'ok' ? 'profit' : 'loss'}
              style={{ flex: 1 }}
            >
              {notice.text}
            </Text>
            <Button title="Dismiss" size="sm" variant="ghost" onPress={() => setNotice(null)} />
          </View>
        ) : null}

        {/* Sahip: sermaye kilidi */}
        {canLock ? (
          <Card style={styles.section}>
            <Text variant="h2">Lock capital</Text>
            <Text variant="body" color="text2">
              Your {formatAmount(l.amount, symbol)} moves into the TraderKirala vault. Trades can
              only start from this locked amount.
            </Text>
            <SlideToConfirm
              label={`Slide to lock ${formatAmount(l.amount, symbol)}`}
              loading={tx.isBusy}
              onConfirm={lock}
            />
          </Card>
        ) : null}

        {/* Sahip: teklifler / ilgi */}
        {isOwner ? (
          <Card style={styles.section}>
            <Segmented<Tab>
              options={[
                {
                  value: 'offers',
                  label: `${isCapital ? 'Offers' : 'Requests'} (${pendingOffers.length})`,
                },
                { value: 'interest', label: 'Interest' },
              ]}
              value={tab}
              onChange={setTab}
            />
            {tab === 'offers' ? (
              offers.length === 0 ? (
                <Text variant="caption" color="text3">
                  {isCapital
                    ? 'No offers yet. Traders who see your listing can make one.'
                    : 'No requests yet. Customers can ask you for an offer from Discover.'}
                </Text>
              ) : (
                <>
                  {pendingOffers.map((o) => (
                    <OfferRow
                      key={o.id}
                      offer={o}
                      busy={accept.isPending || reject.isPending}
                      onAccept={() => accept.mutate(o.id)}
                      onReject={() => reject.mutate(o.id)}
                    />
                  ))}
                  {otherOffers.map((o) => (
                    <OfferRow key={o.id} offer={o} />
                  ))}
                </>
              )
            ) : (
              <View style={styles.statsRow}>
                <Stat label="Views" value={String(l.view_count)} />
                <Stat label="Likes" value={String(l.like_count)} />
                <Stat label="Pending offers" value={String(l.pending_offers)} />
              </View>
            )}
          </Card>
        ) : null}

        {/* Ziyaretçi aksiyonları */}
        {!isOwner ? (
          <Card style={styles.section}>
            {l.my_offer_id ? (
              <>
                <Text variant="bodyStrong">Your offer is pending</Text>
                <Text variant="caption" color="text2">
                  The owner has not responded yet. You can withdraw it any time.
                </Text>
                <Button
                  title="Withdraw offer"
                  variant="secondary"
                  fullWidth
                  loading={withdraw.isPending}
                  onPress={() => l.my_offer_id && withdraw.mutate(l.my_offer_id)}
                />
              </>
            ) : canOffer ? (
              <Button title="Make offer" fullWidth onPress={() => setOfferOpen(true)} />
            ) : canRequest ? (
              <Button
                title="Request offer"
                fullWidth
                loading={requestOffer.isPending}
                onPress={() => requestOffer.mutate()}
              />
            ) : (
              <Text variant="caption" color="text3">
                {l.status !== 'active'
                  ? 'This listing is not accepting offers right now.'
                  : role === 'trader'
                    ? 'Service listings are published by traders; customers request offers from them.'
                    : 'Capital listings are published by customers; traders make offers on them.'}
              </Text>
            )}
          </Card>
        ) : null}
      </View>

      <OfferSheet
        key={offerOpen ? `offer-${l.id}` : 'offer-closed'}
        listing={l}
        visible={offerOpen}
        onClose={() => {
          setOfferOpen(false);
          createOffer.reset();
        }}
        submitting={createOffer.isPending}
        error={createOffer.isError ? userMessage(createOffer.error) : null}
        onSubmit={(draft) => createOffer.mutate(draft)}
      />
      <TxProgressSheet
        progress={tx.progress}
        onClose={tx.reset}
        onRetry={retry}
        onRecheck={() => void tx.recheck()}
      />
    </Screen>
  );
}

function Count({ icon, value, label }: { icon: React.ReactNode; value: number; label: string }) {
  return (
    <View style={styles.count}>
      {icon}
      <Text variant="caption" color="text3">
        {value} {label}
      </Text>
    </View>
  );
}

function OfferRow({
  offer,
  busy,
  onAccept,
  onReject,
}: {
  offer: OfferOut;
  busy?: boolean;
  onAccept?: () => void;
  onReject?: () => void;
}) {
  const from = offer.from_user;
  const name = from?.display_name || from?.username || '—';
  const pending = offer.status === 'pending';
  return (
    <View style={styles.offer}>
      <View style={styles.offerHeader}>
        <Avatar initials={initialsOf(name)} size="sm" />
        <View style={{ flex: 1 }}>
          <Text variant="bodyStrong" numberOfLines={1}>
            {name}
          </Text>
          <Text variant="caption" color="text3">
            @{from?.username ?? '—'} · {formatRelative(offer.created_at)}
          </Text>
        </View>
        <Pill label={OFFER_STATUS_LABEL[offer.status]} tone={pending ? 'amber' : 'neutral'} />
      </View>
      <View style={styles.statsRow}>
        <Stat label="Amount" value={formatAmount(offer.amount, offer.base_asset.symbol)} />
        <Stat label="Duration" value={formatDuration(offer.duration_days)} />
      </View>
      <View style={styles.statsRow}>
        <Stat label="Commission" value={formatBps(offer.commission_bps)} />
        <Stat
          label="Max drawdown"
          value={offer.max_drawdown_bps >= 10_000 ? 'No limit' : formatBps(offer.max_drawdown_bps)}
        />
      </View>
      {offer.expected_return_min_bps !== null || offer.expected_return_max_bps !== null ? (
        <Text variant="caption" color="text2">
          Expected return {formatBps(offer.expected_return_min_bps)} –{' '}
          {formatBps(offer.expected_return_max_bps)}
        </Text>
      ) : null}
      {offer.note ? (
        <Text variant="caption" color="text2">
          “{offer.note}”
        </Text>
      ) : null}
      {pending ? (
        <Text variant="caption" color="text3">
          Expires {formatRelative(offer.expires_at)}
        </Text>
      ) : null}
      {pending && onAccept && onReject ? (
        <View style={styles.offerActions}>
          <Button
            title="Reject"
            variant="secondary"
            size="sm"
            disabled={busy}
            onPress={onReject}
            style={{ flex: 1 }}
          />
          <Button title="Accept" size="sm" disabled={busy} onPress={onAccept} style={{ flex: 1 }} />
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  body: {
    paddingHorizontal: layout.screenPaddingH,
    paddingBottom: spacing['3xl'],
    gap: spacing.md,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    minHeight: 240,
  },
  state: { gap: spacing.md, alignItems: 'flex-start' },
  section: { gap: spacing.md },
  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  owner: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  statsRow: { flexDirection: 'row', gap: spacing.md },
  counts: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, flexWrap: 'wrap' },
  count: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  notice: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.sm,
    paddingLeft: spacing.md,
    borderRadius: radius.md,
  },
  noticeOk: { backgroundColor: colors.greenBg },
  noticeErr: { backgroundColor: colors.redBg },
  offer: {
    gap: spacing.sm,
    paddingVertical: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  offerHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  offerActions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs },
});
