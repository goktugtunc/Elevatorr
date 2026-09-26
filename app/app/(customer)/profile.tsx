import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { Alert, Linking, Platform, StyleSheet, View } from 'react-native';

import { Placeholder, Screen, ScreenHeader } from '@/components/layout';
import { Avatar, Button, Card, ListRow, Pill, RiskBadge, Text, initialsOf } from '@/components/ui';
import { usersApi } from '@/lib/api';
import { CHAIN_NAME, explorerAddressUrl, formatAmount, shortAddress } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { getLocalWallet } from '@/lib/wallet';
import { useSession } from '@/store/session';
import { layout, spacing } from '@/theme';

/**
 * Profil · Müşteri (04 §6.5) — kullanıcı kartı, cüzdan adresi (kısa + explorer), Wallet / Sign out
 * satırları; yerel cüzdanda Back up / Forget (onaylı). Kalan menü Placeholder (Sprint 3).
 */
export default function CustomerProfile() {
  const router = useRouter();
  const { address, walletKind, walletName, profile, signOut, forgetLocalWallet } = useSession();
  const me = useQuery({ queryKey: ['users', 'me'], queryFn: usersApi.me, enabled: !profile });
  const user = profile ?? me.data ?? null;
  const isLocal = Platform.OS !== 'web' && walletKind === 'local' && !!getLocalWallet();

  const onSignOut = async () => {
    await signOut();
    router.replace('/(auth)/login');
  };

  const onBackup = () => {
    Alert.alert(
      'Back up private key',
      'The key gives full control of this wallet. Reveal it only in a private place.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Reveal',
          onPress: async () => {
            try {
              const pk = await getLocalWallet()?.exportPrivateKey();
              if (!pk)
                return Alert.alert(
                  'No key found',
                  'No in-app wallet key was found on this device.',
                );
              Alert.alert('Private key', pk, [{ text: 'Done' }]);
            } catch (err) {
              Alert.alert('Could not read the key', userMessage(err));
            }
          },
        },
      ],
    );
  };

  const onForget = () => {
    Alert.alert(
      'Forget in-app wallet?',
      'Your MON and test tokens stay on this address. Without the private key you cannot access them again. Back up first?',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Back up', onPress: onBackup },
        {
          text: 'Forget',
          style: 'destructive',
          onPress: () =>
            Alert.alert('Are you sure?', 'This removes the key from this device.', [
              { text: 'Cancel', style: 'cancel' },
              {
                text: 'Forget wallet',
                style: 'destructive',
                onPress: async () => {
                  try {
                    await forgetLocalWallet();
                    router.replace('/(auth)/login');
                  } catch (err) {
                    Alert.alert('Could not forget the wallet', userMessage(err));
                  }
                },
              },
            ]),
        },
      ],
    );
  };

  return (
    <Screen riskStrip={false} padded={false}>
      <ScreenHeader title="Profile" />
      <View style={styles.body}>
        <Card style={styles.card}>
          {user ? (
            <View style={styles.head}>
              <Avatar initials={initialsOf(user.display_name || user.username)} size="lg" />
              <View style={styles.headText}>
                <Text variant="h2" numberOfLines={1}>
                  {user.display_name || user.username}
                </Text>
                <Text variant="caption" color="text2">
                  @{user.username}
                </Text>
                <View style={styles.pills}>
                  <Pill label="Customer" tone="navy" />
                  {user.risk_profile ? <RiskBadge level={user.risk_profile} /> : null}
                </View>
              </View>
            </View>
          ) : me.isError ? (
            <Text variant="caption" color="loss">
              {userMessage(me.error)}
            </Text>
          ) : (
            <Text variant="caption" color="text2">
              Loading profile…
            </Text>
          )}
          {user?.budget_amount ? (
            <Text variant="caption" color="text2">
              Budget {formatAmount(user.budget_amount)} ·{' '}
              {user.markets.join(', ') || 'no markets yet'}
            </Text>
          ) : null}
        </Card>

        <Card style={styles.list}>
          <ListRow
            title="Wallet"
            subtitle={
              address
                ? `${shortAddress(address)} · ${walletName ?? 'Wallet'} · ${CHAIN_NAME}`
                : 'No wallet connected'
            }
            chevron
            onPress={() => router.push('/wallet')}
          />
          {address ? (
            <ListRow
              title="View on explorer"
              subtitle="Opens the Monad explorer"
              chevron
              onPress={() => Linking.openURL(explorerAddressUrl(address))}
            />
          ) : null}
          {isLocal ? (
            <>
              <ListRow title="Back up private key" chevron onPress={onBackup} />
              <ListRow title="Forget in-app wallet" chevron onPress={onForget} />
            </>
          ) : null}
        </Card>

        <Button title="Sign out" variant="secondary" onPress={() => void onSignOut()} />
      </View>

      <Placeholder
        screen="8a Profile · Customer"
        figmaNode="27:436"
        notes="Notification settings, Risk profile, Security, Help & support (Sprint 3)."
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: layout.screenPaddingH, gap: spacing.md },
  card: { gap: spacing.md },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  headText: { flex: 1, gap: 2 },
  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  list: { paddingVertical: 0 },
});
