import { useMutation, useQuery } from '@tanstack/react-query';
import { Check, Heart, RotateCcw, X } from 'lucide-react-native';
import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';

import { ListingCard, SwipeDeck, type SwipeDeckHandle } from '@/components/discover';
import { Screen, ScreenHeader } from '@/components/layout';
import { Button, Card, Text } from '@/components/ui';
import { discoverApi, tradersApi } from '@/lib/api';
import type { DiscoverCardOut, InteractionAction } from '@/lib/api/types';
import { userMessage } from '@/lib/errors';
import { colors, layout, radius, shadow, spacing } from '@/theme';

/**
 * Keşfet · Müşteri — `GET /api/v1/discover` (sunucu role göre hizmet ilanlarını döner).
 * Sağa kaydır = offer_request · Sola kaydır = pass · Kalp = follow.
 * Etkileşimler `POST /discover/{target_type}/{target_id}/action` ile kaydedilir.
 */
export default function CustomerDiscover() {
  const deckRef = useRef<SwipeDeckHandle>(null);
  const [top, setTop] = useState<DiscoverCardOut | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [deckKey, setDeckKey] = useState(0);

  const feed = useQuery({
    queryKey: ['discover', 'customer'],
    queryFn: () => discoverApi.feed({ limit: 20 }),
  });

  const act = useMutation({
    mutationFn: ({ card, action }: { card: DiscoverCardOut; action: InteractionAction }) =>
      discoverApi.action(card.target_type, card.target_id, action),
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });

  const follow = useMutation({
    mutationFn: (card: DiscoverCardOut) => tradersApi.follow(card.listing.owner_id),
    onSuccess: () => setNotice({ tone: 'ok', text: 'Trader added to your following list.' }),
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });

  const onSwipe = useCallback(
    (card: DiscoverCardOut, direction: 'left' | 'right') => {
      setNotice(null);
      const action: InteractionAction = direction === 'right' ? 'offer_request' : 'pass';
      act.mutate(
        { card, action },
        {
          onSuccess: () => {
            if (action === 'offer_request') {
              setNotice({ tone: 'ok', text: 'Your interest was sent to the trader.' });
            }
          },
        },
      );
    },
    [act],
  );

  const items = feed.data?.items ?? [];

  return (
    <Screen riskStrip={false} padded={false} scroll={false}>
      <ScreenHeader title="Discover" subtitle="Swipe through traders that fit you" />

      <View style={styles.body}>
        {feed.isPending ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.navy900} />
            <Text variant="caption" color="text2">
              Loading listings…
            </Text>
          </View>
        ) : feed.isError ? (
          <Card style={styles.state}>
            <Text variant="h2">Could not load listings</Text>
            <Text variant="body" color="text2">
              {userMessage(feed.error)}
            </Text>
            <Button title="Try again" onPress={() => feed.refetch()} />
          </Card>
        ) : items.length === 0 ? (
          <Card style={styles.state}>
            <Text variant="h2">Nothing here yet</Text>
            <Text variant="body" color="text2">
              Service listings from traders show up here. Publish a capital listing and traders can
              make you an offer too.
            </Text>
            <Button title="Refresh" variant="secondary" onPress={() => feed.refetch()} />
          </Card>
        ) : (
          <SwipeDeck<DiscoverCardOut>
            key={deckKey}
            ref={deckRef}
            data={items}
            keyExtractor={(c) => `${c.target_type}:${c.target_id}`}
            renderCard={(c) => <ListingCard card={c} />}
            onSwipe={onSwipe}
            onTopChange={setTop}
            rightLabel="INTERESTED"
            renderEmpty={() => (
              <Card style={styles.state}>
                <Text variant="h2">That’s everyone for now</Text>
                <Text variant="body" color="text2">
                  You have seen every listing. Refresh to check for new ones.
                </Text>
                <Button
                  title="Start over"
                  variant="secondary"
                  leftIcon={<RotateCcw size={16} color={colors.navy900} />}
                  onPress={() => {
                    setDeckKey((k) => k + 1);
                    feed.refetch();
                  }}
                />
              </Card>
            )}
          />
        )}

        {notice ? (
          <View style={[styles.notice, notice.tone === 'ok' ? styles.noticeOk : styles.noticeErr]}>
            <Text variant="caption" color={notice.tone === 'ok' ? colors.profit : colors.loss}>
              {notice.text}
            </Text>
          </View>
        ) : null}

        {items.length > 0 ? (
          <View style={styles.actions}>
            <ActionButton
              label="Skip"
              onPress={() => deckRef.current?.swipe('left')}
              disabled={!top}
              tint={colors.loss}
            >
              <X size={22} color={colors.loss} />
            </ActionButton>
            <ActionButton
              label="Follow"
              onPress={() => top && follow.mutate(top)}
              disabled={!top || follow.isPending}
              tint={colors.navy900}
            >
              <Heart size={20} color={colors.navy900} />
            </ActionButton>
            <ActionButton
              label="Interested"
              onPress={() => deckRef.current?.swipe('right')}
              disabled={!top || act.isPending}
              tint={colors.profit}
            >
              <Check size={22} color={colors.profit} />
            </ActionButton>
          </View>
        ) : null}
      </View>
    </Screen>
  );
}

function ActionButton({
  label,
  onPress,
  disabled,
  tint,
  children,
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  tint: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.action}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityState={{ disabled }}
        onPress={onPress}
        disabled={disabled}
        style={({ pressed }) => [
          styles.actionButton,
          shadow.card,
          { borderColor: tint },
          pressed && { opacity: 0.85 },
          disabled && { opacity: 0.4 },
        ]}
      >
        {children}
      </Pressable>
      <Text variant="caption" color="text2">
        {label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: {
    flex: 1,
    paddingHorizontal: layout.screenPaddingH,
    paddingBottom: spacing.md,
    gap: spacing.md,
  },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  state: { gap: spacing.md, alignItems: 'flex-start' },
  notice: { padding: spacing.md, borderRadius: radius.md },
  noticeOk: { backgroundColor: colors.greenBg },
  noticeErr: { backgroundColor: colors.redBg },
  actions: { flexDirection: 'row', justifyContent: 'space-evenly', paddingTop: spacing.xs },
  action: { alignItems: 'center', gap: spacing.xs },
  actionButton: {
    width: 56,
    height: 56,
    borderRadius: 28,
    borderWidth: 1.5,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
