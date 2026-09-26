import { useMutation, useQuery } from '@tanstack/react-query';
import { Bookmark, Check, RotateCcw, X } from 'lucide-react-native';
import { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';

import { ListingCard, OfferSheet, SwipeDeck, type SwipeDeckHandle } from '@/components/discover';
import { Screen, ScreenHeader } from '@/components/layout';
import { Button, Card, Text } from '@/components/ui';
import { discoverApi, offersApi } from '@/lib/api';
import type { DiscoverCardOut, InteractionAction, OfferCreateIn } from '@/lib/api/types';
import { userMessage } from '@/lib/errors';
import { colors, layout, radius, shadow, spacing } from '@/theme';

/**
 * Keşfet · Trader — `GET /api/v1/discover` (sunucu role göre sermaye ilanlarını döner).
 * Sağa kaydır = teklif sheet'i · Sola kaydır = pass · Yer imi = save.
 * Teklif `POST /api/v1/offers` ile gönderilir. Sheet gönderilmeden kapatılırsa kart
 * desteye geri döner (`deckRef.restore()`, inceleme hata 6).
 */
export default function TraderDiscover() {
  const deckRef = useRef<SwipeDeckHandle>(null);
  const [top, setTop] = useState<DiscoverCardOut | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [deckKey, setDeckKey] = useState(0);
  const [offerTarget, setOfferTarget] = useState<DiscoverCardOut | null>(null);

  const feed = useQuery({
    queryKey: ['discover', 'trader'],
    queryFn: () => discoverApi.feed({ limit: 20 }),
  });

  const act = useMutation({
    mutationFn: ({ card, action }: { card: DiscoverCardOut; action: InteractionAction }) =>
      discoverApi.action(card.target_type, card.target_id, action),
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });

  const save = useMutation({
    mutationFn: (card: DiscoverCardOut) =>
      discoverApi.action(card.target_type, card.target_id, 'save'),
    onSuccess: () => setNotice({ tone: 'ok', text: 'Listing saved.' }),
    onError: (err) => setNotice({ tone: 'error', text: userMessage(err) }),
  });

  const createOffer = useMutation({
    mutationFn: (draft: OfferCreateIn) => offersApi.create(draft),
    onSuccess: () => {
      setOfferTarget(null);
      setNotice({ tone: 'ok', text: 'Your offer was sent to the customer.' });
    },
  });

  const onSwipe = useCallback(
    (card: DiscoverCardOut, direction: 'left' | 'right') => {
      setNotice(null);
      if (direction === 'right') {
        setOfferTarget(card);
        return;
      }
      const action: InteractionAction = 'pass';
      act.mutate({ card, action });
    },
    [act],
  );

  const items = feed.data?.items ?? [];

  return (
    <Screen riskStrip={false} padded={false} scroll={false}>
      <ScreenHeader title="Discover" subtitle="Browse capital listings and make offers" />

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
              Capital listings from customers show up here. Publish a service listing and customers
              can reach out to you too.
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
            rightLabel="OFFER"
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
              label="Save"
              onPress={() => top && save.mutate(top)}
              disabled={!top || save.isPending}
              tint={colors.navy900}
            >
              <Bookmark size={20} color={colors.navy900} />
            </ActionButton>
            <ActionButton
              label="Make offer"
              onPress={() => deckRef.current?.swipe('right')}
              disabled={!top}
              tint={colors.profit}
            >
              <Check size={22} color={colors.profit} />
            </ActionButton>
          </View>
        ) : null}
      </View>

      <OfferSheet
        key={offerTarget?.listing.id ?? 'offer-sheet'}
        listing={offerTarget?.listing ?? null}
        visible={offerTarget !== null}
        onClose={() => {
          setOfferTarget(null);
          createOffer.reset();
          deckRef.current?.restore();
        }}
        submitting={createOffer.isPending}
        error={createOffer.isError ? userMessage(createOffer.error) : null}
        onSubmit={(draft) => createOffer.mutate(draft)}
      />
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
