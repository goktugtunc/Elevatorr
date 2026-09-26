import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { PendingOfferRow, PositionRow, signedAmount } from '@/components/dashboard';
import { Screen, ScreenHeader } from '@/components/layout';
import { Button, Card, KpiBox, ListRow, Progress, Stat, Text } from '@/components/ui';
import { dashboardApi, isTraderDashboard, offersApi } from '@/lib/api';
import type { PendingOfferBriefOut, TraderDashboardOut } from '@/lib/api/types';
import { formatAmount } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { formatBpsSigned, formatRelative } from '@/lib/format';
import { colors, layout, radius, spacing } from '@/theme';

/**
 * Panel · Trader (04 §6.6) — `GET /dashboard` (`role === 'trader'`), 30 sn'de bir yenilenir.
 * KPI'lar, bekleyen teklifler (Accept → `/contract/[id]?next=…`), pozisyonlar, profil kontrol listesi.
 */
export default function TraderDashboard() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [busyOffer, setBusyOffer] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ['dashboard'],
    queryFn: dashboardApi.get,
    refetchInterval: 30_000,
  });
  const data = q.data && isTraderDashboard(q.data) ? q.data : null;

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      queryClient.invalidateQueries({ queryKey: ['offers'] }),
      queryClient.invalidateQueries({ queryKey: ['agreements'] }),
    ]);

  const accept = useMutation({
    mutationFn: (offer: PendingOfferBriefOut) => offersApi.accept(offer.offer_id),
    onMutate: (offer) => {
      setNotice(null);
      setBusyOffer(offer.offer_id);
    },
    onSuccess: async (out) => {
      await invalidate();
      setNotice({ tone: 'ok', text: 'Offer accepted. Continue in the agreement.' });
      router.push(`/contract/${out.agreement.id}?next=${out.next_action}`);
    },
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
    onSettled: () => setBusyOffer(null),
  });

  const reject = useMutation({
    mutationFn: (offer: PendingOfferBriefOut) => offersApi.reject(offer.offer_id),
    onMutate: (offer) => {
      setNotice(null);
      setBusyOffer(offer.offer_id);
    },
    onSuccess: async () => {
      await invalidate();
      setNotice({ tone: 'ok', text: 'Offer rejected.' });
    },
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
    onSettled: () => setBusyOffer(null),
  });

  return (
    <Screen riskStrip={false} padded={false}>
      <ScreenHeader
        title="Dashboard"
        subtitle={data ? `Updated ${formatRelative(data.generated_at)} ago` : undefined}
      />
      <View style={styles.body}>
        {notice ? (
          <View style={[styles.notice, notice.tone === 'ok' ? styles.noticeOk : styles.noticeErr]}>
            <Text variant="caption" color={notice.tone === 'ok' ? colors.profit : colors.loss}>
              {notice.text}
            </Text>
          </View>
        ) : null}

        {q.isPending ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.navy900} />
            <Text variant="caption" color="text2">
              Loading your dashboard…
            </Text>
          </View>
        ) : q.isError ? (
          <Card style={styles.state}>
            <Text variant="h2">Could not load the dashboard</Text>
            <Text variant="body" color="text2">
              {userMessage(q.error)}
            </Text>
            <Button title="Try again" onPress={() => q.refetch()} />
          </Card>
        ) : !data ? (
          <Card style={styles.state}>
            <Text variant="h2">Unexpected dashboard</Text>
            <Text variant="body" color="text2">
              The server returned a dashboard for another role. Sign out and back in.
            </Text>
            <Button title="Refresh" variant="secondary" onPress={() => q.refetch()} />
          </Card>
        ) : (
          <Content
            data={data}
            busyOffer={busyOffer}
            onAccept={(o) => accept.mutate(o)}
            onReject={(o) => reject.mutate(o)}
            router={router}
          />
        )}
      </View>
    </Screen>
  );
}

function Content({
  data,
  busyOffer,
  onAccept,
  onReject,
  router,
}: {
  data: TraderDashboardOut;
  busyOffer: string | null;
  onAccept: (o: PendingOfferBriefOut) => void;
  onReject: (o: PendingOfferBriefOut) => void;
  router: ReturnType<typeof useRouter>;
}) {
  const sym = data.base_asset_code;
  const li = data.listing_interactions;
  const cl = data.profile_checklist;
  const missing: { label: string; onPress: () => void }[] = [];
  if (!cl.has_strategy) {
    missing.push({
      label: 'Describe your strategy',
      onPress: () => router.push('/(trader)/profile'),
    });
  }
  if (!cl.has_avatar) {
    missing.push({ label: 'Add a profile photo', onPress: () => router.push('/(trader)/profile') });
  }
  if (!cl.has_service_listing) {
    missing.push({
      label: 'Publish a service listing',
      onPress: () => router.push('/listing/create'),
    });
  }
  if (!cl.wallet_connected) {
    missing.push({ label: 'Connect your wallet', onPress: () => router.push('/wallet') });
  }
  const noInvestors = data.positions.length === 0 && data.pending_offers.length === 0;

  return (
    <>
      <View style={styles.kpis}>
        <KpiBox
          label="Managed capital"
          value={formatAmount(data.managed_capital, sym)}
          style={styles.kpi}
        />
        <KpiBox
          label="Open P&L"
          value={signedAmount(data.open_pnl, data.open_pnl_bps, sym)}
          signed={data.open_pnl_bps}
          sub={formatBpsSigned(data.open_pnl_bps)}
          style={styles.kpi}
        />
        <KpiBox
          label="Active investors"
          value={String(data.active_investors)}
          sub={`${data.settled_agreements} settled`}
          style={styles.kpi}
        />
        <KpiBox
          label="Commission this month"
          value={formatAmount(data.month_commission, sym)}
          sub={`Total ${formatAmount(data.total_commission, sym)}`}
          style={styles.kpi}
        />
      </View>

      {cl.completion_pct < 100 ? (
        <Card style={styles.checklist}>
          <Progress
            value={cl.completion_pct}
            label="Strengthen your profile"
            trailing={`${cl.completion_pct}%`}
          />
          {missing.map((m) => (
            <ListRow key={m.label} title={m.label} chevron onPress={m.onPress} />
          ))}
        </Card>
      ) : null}

      <Section title="Pending offers" count={data.pending_offers_count}>
        {data.pending_offers.length === 0 ? (
          <Text variant="caption" color="text2">
            No offers waiting for your answer.
          </Text>
        ) : (
          <View style={styles.offers}>
            {data.pending_offers.map((o) => (
              <PendingOfferRow
                key={o.offer_id}
                offer={o}
                busy={busyOffer === o.offer_id}
                onAccept={() => onAccept(o)}
                onReject={() => onReject(o)}
              />
            ))}
          </View>
        )}
      </Section>

      <Section title="Active investors" count={data.positions.length}>
        {noInvestors ? (
          <Card style={styles.state}>
            <Text variant="bodyStrong">No investors yet</Text>
            <Text variant="body" color="text2">
              Publish a service listing so customers can find you and send offers.
            </Text>
            <Button
              title="Create service listing"
              variant="secondary"
              onPress={() => router.push('/listing/create')}
            />
          </Card>
        ) : data.positions.length === 0 ? (
          <Text variant="caption" color="text2">
            No open agreements. Accept a pending offer to start.
          </Text>
        ) : (
          <Card style={styles.list}>
            {data.positions.map((p) => (
              <PositionRow
                key={p.agreement_id}
                position={p}
                onPress={() => router.push(`/contract/${p.agreement_id}`)}
              />
            ))}
          </Card>
        )}
      </Section>

      <Section title="Your listings">
        <Card style={styles.stats}>
          <Stat label="Listings" value={String(li.listings)} />
          <Stat label="Views" value={String(li.views)} />
          <Stat label="Likes" value={String(li.likes)} />
          <Stat label="Offers" value={String(li.offers)} />
          <Stat label="Pending" value={String(li.pending_offers)} />
        </Card>
      </Section>
    </>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHead}>
        <Text variant="h2">{title}</Text>
        {count !== undefined ? (
          <Text variant="caption" color="text3">
            {count}
          </Text>
        ) : null}
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: layout.screenPaddingH, paddingBottom: spacing.xl, gap: spacing.xl },
  center: { paddingVertical: 48, alignItems: 'center', gap: spacing.sm },
  state: { gap: spacing.md, alignItems: 'flex-start' },
  notice: { padding: spacing.md, borderRadius: radius.md },
  noticeOk: { backgroundColor: colors.greenBg },
  noticeErr: { backgroundColor: colors.redBg },
  kpis: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  kpi: { flexGrow: 1, flexBasis: '45%', minWidth: 0 },
  checklist: { gap: spacing.sm },
  section: { gap: spacing.sm },
  sectionHead: { flexDirection: 'row', alignItems: 'baseline', gap: spacing.sm },
  offers: { gap: spacing.md },
  list: { paddingVertical: 0 },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
});
