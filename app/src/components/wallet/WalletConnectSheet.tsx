import * as Clipboard from 'expo-clipboard';
import { Copy, ExternalLink } from 'lucide-react-native';
import { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import QRCode from 'react-native-qrcode-svg';

import { BottomSheet, Button, Text } from '@/components/ui';
import { userMessage } from '@/lib/errors';
import { WC_WALLETS, openPairing, type WalletLinkTarget } from '@/lib/wallet';
import { colors, radius, spacing } from '@/theme';

/**
 * Mobil cüzdan bağlantısı (FE-33) — WalletConnect v2 eşleşme ekranı (`eip155:10143`).
 * EVM cüzdan şemaları WalletConnect kayıt defterinden alındı (bkz. lib/wallet/deeplinks.ts);
 * aynı cihazdaki cüzdan için deep link, başka cihaz/masaüstü için QR. MetaMask birincil;
 * şeması doğrulanmamış cüzdanlar (`verified: false`) ghost görünür.
 */
export function WalletConnectSheet({ uri, onClose }: { uri: string | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);

  const open = async (target: WalletLinkTarget) => {
    if (!uri) return;
    setError(null);
    setOpening(target.id);
    try {
      await openPairing(target, uri);
    } catch (err) {
      setError(userMessage(err));
    } finally {
      setOpening(null);
    }
  };

  const copyUri = async () => {
    if (!uri) return;
    await Clipboard.setStringAsync(uri);
    setCopied(true);
  };

  return (
    <BottomSheet
      visible={uri !== null}
      onClose={onClose}
      title="Connect your wallet"
      subtitle="Approve the connection request in your wallet"
      footer={<Button title="Cancel" variant="ghost" fullWidth onPress={onClose} />}
    >
      {uri ? (
        <>
          <View style={styles.qrWrap}>
            <QRCode value={uri} size={200} backgroundColor={colors.surface} color={colors.text} />
          </View>

          <View style={styles.buttons}>
            {WC_WALLETS.map((w) => {
              const variant = w.id === 'metamask' ? 'primary' : w.verified ? 'secondary' : 'ghost';
              return (
                <Button
                  key={w.id}
                  title={`Open in ${w.label}`}
                  variant={variant}
                  fullWidth
                  loading={opening === w.id}
                  leftIcon={
                    <ExternalLink
                      size={16}
                      color={variant === 'primary' ? colors.onNavy : colors.navy900}
                    />
                  }
                  onPress={() => open(w)}
                />
              );
            })}
            <Button
              title={copied ? 'Link copied' : 'Copy connection link'}
              variant="ghost"
              fullWidth
              leftIcon={<Copy size={16} color={colors.navy900} />}
              onPress={copyUri}
            />
          </View>

          {error ? (
            <View style={styles.error}>
              <Text variant="caption" color="loss">
                {error}
              </Text>
            </View>
          ) : null}

          <Text variant="caption" color="text3">
            Tap your wallet above to open it on this phone, or scan the code with a wallet on
            another device. Connecting is free — you only approve a session, no funds move.
          </Text>
        </>
      ) : null}
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  qrWrap: {
    alignSelf: 'center',
    padding: spacing.lg,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  buttons: { gap: spacing.sm },
  error: { padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.redBg },
});
