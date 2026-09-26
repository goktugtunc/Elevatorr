import { ExternalLink } from 'lucide-react-native';
import { Linking, Pressable, StyleSheet, type ViewStyle } from 'react-native';

import { Text } from './Text';
import { explorerAddressUrl, explorerTxUrl, formatTxHash, shortAddress } from '@/lib/chain';
import { colors, spacing } from '@/theme';

/**
 * Monad explorer bağlantısı (04 §6.7). Öncelik: `url` (sunucunun hazır `explorer_url`'ü) →
 * `hash` (`explorerTxUrl`) → `address` (`explorerAddressUrl`). Hiçbiri yoksa çizilmez.
 */
export interface ExplorerLinkProps {
  hash?: string | null;
  address?: string | null;
  url?: string | null;
  label?: string;
  style?: ViewStyle;
}

export function ExplorerLink({ hash, address, url, label, style }: ExplorerLinkProps) {
  const href = url || (hash ? explorerTxUrl(hash) : address ? explorerAddressUrl(address) : null);
  if (!href) return null;
  const text =
    label ?? (hash ? formatTxHash(hash) : address ? shortAddress(address) : 'View on explorer');
  return (
    <Pressable
      accessibilityRole="link"
      accessibilityLabel={`${text} — opens the Monad explorer`}
      onPress={() => Linking.openURL(href).catch(() => undefined)}
      hitSlop={6}
      style={({ pressed }) => [styles.row, pressed && styles.pressed, style]}
    >
      <Text variant="captionStrong" color="navy700" numberOfLines={1}>
        {text}
      </Text>
      <ExternalLink size={13} color={colors.navy700} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, alignSelf: 'flex-start' },
  pressed: { opacity: 0.7 },
});
