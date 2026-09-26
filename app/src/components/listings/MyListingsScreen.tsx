import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { Plus } from 'lucide-react-native';
import { useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, StyleSheet, View } from 'react-native';

import { Screen, ScreenHeader } from '@/components/layout';
import { TxProgressSheet } from '@/components/tx';
import { Button, Card, Pill, RiskBadge, Segmented, Text } from '@/components/ui';
import { flattenPages, listingsApi, nextOffset } from '@/lib/api';
import type { ListingCountsOut, ListingOut, ListingStatus, UserRole } from '@/lib/api/types';
import { formatAmount, useTxExecutor } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { formatBps, formatDuration, formatRelative } from '@/lib/format';
import { colors, layout, radius, shadow, spacing } from '@/theme';

/**
 * İlanlarım (04 §6.2) — müşteri (capital) ve trader (service) için ortak ekran.
 * Sekmeler `listingsApi.mineCounts()`; liste `listingsApi.mine({status, limit, offset})` sonsuz sayfa.
 * Satır aksiyonları: pause/resume/close (API), Lock capital (`reserveTx`) ve Release capital
 * (`releaseTx` → releaseAll) `useTxExecutor` + `TxProgressSheet` ile.
 */
export function MyListingsScreen({ role }: { role: UserRole }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const tabs = useMemo<ListingStatus[]>(
    () =>
      role === 'customer'
        ? ['draft', 'active', 'paused', 'closed']
        : ['active', 'paused', 'closed'],
    [role],
  );
  const [tab, setTab] = useState<ListingStatus>('active');
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const counts = useQuery({
    queryKey: ['listings', 'mine', 'counts'],
    queryFn: listingsApi.mineCounts,
  });

  const list = useInfiniteQuery({
    queryKey: ['listings', 'mine', tab],
    queryFn: ({ pageParam }) => listingsApi.mine({ status: tab, limit: 20, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: nextOffset,
  });
  const items = flattenPages(list.data?.pages);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['listings'] });

  const statusAction = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'pause' | 'resume' | 'close' }) =>
      listingsApi[action](id),
    onMutate: ({ id }) => {
      setNotice(null);
      setBusyId(id);
    },
    onSuccess: invalidate,
    onError: (err) => setNotice(userMessage(err)),
    onSettled: () => setBusyId(null),
  });

  const tx = useTxExecutor({ invalidate: [['listings', 'mine', 'counts']] });
  const lockCapital = (l: ListingOut) => {
    setNotice(null);
    void tx.run(() => listingsApi.reserveTx(l.id));
  };
  const releaseCapital = (l: ListingOut) => {
    setNotice(null);
    void tx.run(() => listingsApi.releaseTx(l.id));
  };

  const options = tabs.map((s) => ({ value: s, label: tabLabel(s, counts.data) }));

  return (
    <Screen riskStrip={false} padded={false} scroll={false}>
      <ScreenHeader title="My Listings" />
      <View style={styles.body}>
        <Segmented options={options} value={tab} onChange={setTab} />

        {notice ? (
          <View style={styles.notice}>
            <Text variant="caption" color={colors.loss}>
              {notice}
            </Text>
          </View>
        ) : null}

        {list.isPending ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.navy900} />
            <Text variant="caption" color="text2">
              Loading listings…
            </Text>
          </View>
        ) : list.isError ? (
          <Card style={styles.state}>
            <Text variant="h2">Could not load listings</Text>
            <Text variant="body" color="text2">
              {userMessage(list.error)}
            </Text>
            <Button title="Try again" onPress={() => list.refetch()} />
          </Card>
        ) : items.length === 0 ? (
          <Card style={styles.state}>
            <Text variant="h2">You have no {tab} listings.</Text>
            <Text variant="body" color="text2">
              {role === 'customer'
                ? 'Publish a capital listing to let traders make you an offer.'
                : 'Publish a service listing so customers can find you.'}
            </Text>
            <Button title="Create listing" onPress={() => router.push('/listing/create')} />
          </Card>
        ) : (
          <FlatList
            data={items}
            keyExtractor={(l) => l.id}
            contentContainerStyle={styles.listContent}
            onEndReachedThreshold={0.4}
            onEndReached={() => {
              if (list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
            }}
            refreshing={list.isRefetching && !list.isFetchingNextPage}
            onRefresh={() => {
              void counts.refetch();
              void list.refetch();
            }}
            renderItem={({ item }) => (
              <ListingRow
                listing={item}
                busy={busyId === item.id || tx.isBusy}
                onOpen={() => router.push(`/listing/${item.id}`)}
                onPause={() => statusAction.mutate({ id: item.id, action: 'pause' })}
                onResume={() => statusAction.mutate({ id: item.id, action: 'resume' })}
                onClose={() => statusAction.mutate({ id: item.id, action: 'close' })}
                onLock={() => lockCapital(item)}
                onRelease={() => releaseCapital(item)}
              />
            )}
            ListFooterComponent={
              list.isFetchingNextPage ? (
                <ActivityIndicator color={colors.navy900} style={{ marginVertical: spacing.lg }} />
              ) : null
            }
          />
        )}
      </View>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Create listing"
        onPress={() => router.push('/listing/create')}
        style={({ pressed }) => [styles.fab, shadow.card, pressed && { opacity: 0.85 }]}
      >
        <Plus size={24} color={colors.onNavy} />
      </Pressable>

      {tx.progress.phase !== 'idle' ? (
        <TxProgressSheet progress={tx.progress} onClose={tx.reset} onRecheck={tx.recheck} />
      ) : null}
    </Screen>
  );
}

function tabLabel(status: ListingStatus, counts: ListingCountsOut | undefined): string {
  const name = status.charAt(0).toUpperCase() + status.slice(1);
  return counts ? `${name} (${counts[status]})` : name;
}

/** `reserved_amount` sıfırdan büyük mü (string; sayıya çevrilmez). */
function hasReserved(l: ListingOut): boolean {
  return !!l.reserved_amount && /[1-9]/.test(l.reserved_amount);
}

function ListingRow({
  listing: l,
  busy,
  onOpen,
  onPause,
  onResume,
  onClose,
  onLock,
  onRelease,
}: {
  listing: ListingOut;
  busy: boolean;
  onOpen: () => void;
  onPause: () => void;
  onResume: () => void;
  onClose: () => void;
  onLock: () => void;
  onRelease: () => void;
}) {
  const symbol = l.base_asset?.symbol ?? '';
  const isCapital = l.kind === 'capital';
  const amount = isCapital ? formatAmount(l.amount, symbol) : formatAmount(l.min_capital, symbol);
  const amountLabel = isCapital ? 'Capital' : 'Min. capital';
  const reserved = hasReserved(l);

  return (
    <Card style={styles.row}>
      <Pressable
        accessibilityRole="button"
        onPress={onOpen}
        style={({ pressed }) => pressed && { opacity: 0.7 }}
      >
        <View style={styles.rowHead}>
          <View style={styles.rowTitle}>
            <Text variant="bodyStrong" numberOfLines={2}>
              {l.title}
            </Text>
            <Text variant="caption" color="text2">
              {l.kind === 'capital' ? 'Capital listing' : 'Service listing'} ·{' '}
              {formatRelative(l.created_at)}
            </Text>
          </View>
          <View style={styles.amount}>
            <Text variant="caption" color="text3">
              {amountLabel}
            </Text>
            <Text variant="numericSm">{amount}</Text>
          </View>
        </View>

        <View style={styles.pills}>
          <Pill
            label={l.status.charAt(0).toUpperCase() + l.status.slice(1)}
            tone={l.status === 'active' ? 'navy' : 'neutral'}
          />
          {isCapital ? (
            reserved ? (
              <Pill label={`Locked ${formatAmount(l.reserved_amount, symbol)}`} tone="navy" />
            ) : l.status !== 'closed' ? (
              <Pill label="Deposit pending" tone="amber" />
            ) : null
          ) : null}
          {!isCapital && l.commission_bps !== null ? (
            <Pill label={`${formatBps(l.commission_bps)} commission`} />
          ) : null}
          {isCapital && l.duration_days ? <Pill label={formatDuration(l.duration_days)} /> : null}
          {l.risk_profile ? <RiskBadge level={l.risk_profile} /> : null}
        </View>

        <Text variant="caption" color="text3">
          {l.offer_count} offers · {l.view_count} views · {l.like_count} likes
        </Text>
      </Pressable>

      <View style={styles.actions}>
        {isCapital && l.status === 'draft' ? (
          <Button
            title="Lock capital"
            size="sm"
            onPress={onLock}
            disabled={busy}
            style={styles.action}
          />
        ) : null}
        {isCapital && reserved && (l.status === 'active' || l.status === 'paused') ? (
          <Button
            title="Release capital"
            size="sm"
            variant="secondary"
            onPress={onRelease}
            disabled={busy}
            style={styles.action}
          />
        ) : null}
        {l.status === 'active' ? (
          <Button
            title="Pause"
            size="sm"
            variant="secondary"
            onPress={onPause}
            disabled={busy}
            style={styles.action}
          />
        ) : null}
        {l.status === 'paused' ? (
          <Button
            title="Resume"
            size="sm"
            variant="secondary"
            onPress={onResume}
            disabled={busy}
            style={styles.action}
          />
        ) : null}
        {l.status !== 'closed' ? (
          <Button
            title="Close"
            size="sm"
            variant="ghost"
            onPress={onClose}
            disabled={busy}
            style={styles.action}
          />
        ) : null}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  body: { flex: 1, paddingHorizontal: layout.screenPaddingH, gap: spacing.md },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  state: { gap: spacing.md, alignItems: 'flex-start' },
  notice: { padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.redBg },
  listContent: { gap: spacing.md, paddingBottom: 96 },
  row: { gap: spacing.md },
  rowHead: { flexDirection: 'row', gap: spacing.md, alignItems: 'flex-start' },
  rowTitle: { flex: 1, gap: 2 },
  amount: { alignItems: 'flex-end', gap: 2 },
  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.md },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  action: { flexGrow: 1 },
  fab: {
    position: 'absolute',
    right: layout.screenPaddingH,
    bottom: spacing.xl,
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.navy900,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
