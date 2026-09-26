import { TriangleAlert } from 'lucide-react-native';
import { useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { Button, Text } from '@/components/ui';
import { CHAIN_ID, CHAIN_NAME } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { useSession } from '@/store/session';
import { colors, radius, spacing } from '@/theme';

/**
 * Yanlış ağ şeridi (04 §6.1). Cüzdan bağlı ama `chainId !== CHAIN_ID` iken görünür;
 * `Screen` içine değil, giriş / cüzdan / sözleşme / ilan ekranlarına yerleştirilir.
 * Props yok — `useSession` okur; `switchToAppChain` wallet_switchEthereumChain →
 * gerekirse wallet_addEthereumChain dener.
 */
export function ChainBanner() {
  const walletConnected = useSession((s) => s.walletConnected);
  const chainId = useSession((s) => s.chainId);
  const switchToAppChain = useSession((s) => s.switchToAppChain);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!walletConnected || chainId === null || chainId === CHAIN_ID) return null;

  const onSwitch = async () => {
    setBusy(true);
    setError(null);
    try {
      await switchToAppChain();
    } catch (err) {
      setError(userMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.banner} accessibilityRole="alert">
      <View style={styles.row}>
        <TriangleAlert size={18} color={colors.amberInk} />
        <View style={styles.text}>
          <Text variant="captionStrong" color={colors.amberInk}>
            Your wallet is on another network.
          </Text>
          <Text variant="caption" color={colors.amberInk}>
            Connected to chain {chainId}; this app runs on {CHAIN_NAME} ({CHAIN_ID}).
          </Text>
        </View>
      </View>
      <Button
        title={`Switch to ${CHAIN_NAME}`}
        size="sm"
        variant="secondary"
        loading={busy}
        onPress={onSwitch}
        fullWidth
      />
      {error ? (
        <Text variant="caption" color="loss">
          {error}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    alignSelf: 'stretch',
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.amberBg,
    borderWidth: 1,
    borderColor: colors.amber,
  },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  text: { flex: 1, gap: 2 },
});
