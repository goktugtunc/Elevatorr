import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { PositionRow, signedAmount } from '@/components/dashboard';
import { Screen, ScreenHeader } from '@/components/layout';
import { Button, Card, KpiBox, ListRow, Stat, Text, initialsOf } from '@/components/ui';
import { dashboardApi, isCustomerDashboard } from '@/lib/api';
import type { CustomerDashboardOut } from '@/lib/api/types';
import { formatAmount } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { formatBps, formatBpsSigned, formatRelative } from '@/lib/format';
import { colors, layout, spacing } from '@/theme';

/**
 * Panel · Müşteri (04 §6.6) — `GET /dashboard` (`role === 'customer'`), 30 sn'de bir yenilenir.
 * KPI'lar + açık pozisyonlar (`/contract/[id]`) + takip edilen trader'lar + ilan etkileşimleri.
 */
export default function CustomerDashboard() {
  const router = useRouter();
  const q = useQuery({
    queryKey: ['dashboard'],
    queryFn: dashboardApi.get,
    refetchInterval: 30_000,
  });

  const data = q.data && isCustomerDashboard(q.data) ? q.data : null;

  return (
    <Screen riskStrip={false} padded={false}>
      <ScreenHeader
        title="Dashboard"
        subtitle={data ? `Updated ${formatRelative(data.generated_at)} ago` : undefined}
      />
      <View style={styles.body}>
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
          <Content data={data} onRefresh={() => q.refetch()} router={router} />
        )}
      </View>
    </Screen>
  );
}

function Content({
  data,
  router,
}: {
  data: CustomerDashboardOut;
  onRefresh: () => void;
  router: ReturnType<typeof useRouter>;
}) {
  const sym = data.base_asset_code;
  const li = data.listing_interactions;
  return (
    <>
      <View style={styles.kpis}>
        <KpiBox
          label="Portfolio value"
          value={formatAmount(data.portfolio_value, sym)}
          sub={data.wallet_error ? 'wallet balance unavailable' : undefined}
          style={styles.kpi}
        />
        <KpiBox
          label="Invested"
          value={formatAmount(data.invested_principal, sym)}
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
          label="This month"
          value={signedAmount(data.month_pnl, data.month_change_bps, sym)}
          signed={data.month_change_bps}
          sub={formatBpsSigned(data.month_change_bps)}
          style={styles.kpi}
        />
      </View>

      <Section title="Open positions" count={data.positions.length}>
        {data.positions.length === 0 ? (
          <Card style={styles.state}>
            <Text variant="bodyStrong">No agreements yet</Text>
            <Text variant="body" color="text2">
              Find a trader that fits your risk profile and send an offer request.
            </Text>
            <Button
              title="Discover traders"
              variant="secondary"
              onPress={() => router.push('/(customer)/discover')}
            />
          </Card>
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

      <Section title="Following" count={data.followed_count}>
        {data.followed.length === 0 ? (
          <Text variant="caption" color="text2">
            You are not following any traders yet.
          </Text>
        ) : (
          <Card style={styles.list}>
            {data.followed.map((t) => (
              <ListRow
                key={t.trader_id}
                title={t.display_name || `@${t.username}`}
                initials={initialsOf(t.display_name || t.username)}
                subtitle={[
                  t.commission_bps !== null ? `${formatBps(t.commission_bps)} commission` : null,
                  t.invested ? `Invested ${formatAmount(t.invested_principal, sym)}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
                value={formatBpsSigned(t.monthly_return_bps)}
                signed={t.monthly_return_bps}
                meta="30d"
                chevron
                onPress={() => router.push(`/trader/${t.trader_id}`)}
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
  kpis: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  kpi: { flexGrow: 1, flexBasis: '45%', minWidth: 0 },
  section: { gap: spacing.sm },
  sectionHead: { flexDirection: 'row', alignItems: 'baseline', gap: spacing.sm },
  list: { paddingVertical: 0 },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
});
