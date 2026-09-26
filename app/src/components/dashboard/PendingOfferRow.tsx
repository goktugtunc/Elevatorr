import { StyleSheet, View } from 'react-native';

import { Avatar, Button, Card, Pill, RiskBadge, Text, initialsOf } from '@/components/ui';
import type { PendingOfferBriefOut } from '@/lib/api/types';
import { formatAmount } from '@/lib/chain';
import { formatBps, formatDuration, formatRelative } from '@/lib/format';
import { spacing } from '@/theme';

/**
 * Trader paneli bekleyen teklif kartı (04 §6.6): teklif veren, tutar, süre, komisyon,
 * `Accept` / `Reject`. Aksiyonlar ekranda (`offersApi.accept/reject`) çalışır.
 */
export function PendingOfferRow({
  offer,
  onAccept,
  onReject,
  busy,
}: {
  offer: PendingOfferBriefOut;
  onAccept: () => void;
  onReject: () => void;
  /** Bu teklif için bir istek sürüyor. */
  busy?: boolean;
}) {
  const name = offer.from_display_name || `@${offer.from_username}`;
  return (
    <Card style={styles.card}>
      <View style={styles.head}>
        <Avatar initials={initialsOf(name)} size="md" />
        <View style={styles.headText}>
          <Text variant="bodyStrong" numberOfLines={1}>
            {name}
          </Text>
          <Text variant="caption" color="text2" numberOfLines={1}>
            @{offer.from_username} · {formatRelative(offer.created_at)} ago
          </Text>
        </View>
        <Text variant="numericSm">{formatAmount(offer.amount, offer.base_asset_code)}</Text>
      </View>

      <View style={styles.pills}>
        <Pill label={formatDuration(offer.duration_days)} />
        <Pill label={`${formatBps(offer.commission_bps)} commission`} tone="navy" />
        {offer.risk_profile ? <RiskBadge level={offer.risk_profile} /> : null}
        {offer.markets.slice(0, 2).map((m) => (
          <Pill key={m} label={m} />
        ))}
      </View>

      <Text variant="caption" color="text3">
        Expires {formatExpiry(offer.expires_at)}
      </Text>

      <View style={styles.actions}>
        <Button
          title="Reject"
          variant="secondary"
          size="sm"
          onPress={onReject}
          disabled={busy}
          style={styles.action}
        />
        <Button
          title="Accept"
          size="sm"
          onPress={onAccept}
          disabled={busy}
          loading={busy}
          style={styles.action}
        />
      </View>
    </Card>
  );
}

function formatExpiry(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return iso;
  const diffMin = Math.round((ms - Date.now()) / 60000);
  if (diffMin <= 0) return 'soon';
  if (diffMin < 60) return `in ${diffMin}m`;
  const h = Math.round(diffMin / 60);
  if (h < 48) return `in ${h}h`;
  return `in ${Math.round(h / 24)}d`;
}

const styles = StyleSheet.create({
  card: { gap: spacing.md },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  headText: { flex: 1, gap: 2 },
  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  actions: { flexDirection: 'row', gap: spacing.sm },
  action: { flex: 1 },
});
