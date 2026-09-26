import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';

import { Screen } from '@/components/layout';
import { Button, Card, Pill, Text } from '@/components/ui';
import { ConnectWalletPanel } from '@/components/wallet';
import { metaApi } from '@/lib/api';
import { CHAIN_ID, ChainError, applyServerConfig } from '@/lib/chain';
import { networkLabel, userMessage } from '@/lib/errors';
import { useSession } from '@/store/session';
import { colors, radius, spacing } from '@/theme';

/**
 * Figma 1d · Giriş · Cüzdan ile (node 19:109) — Monad Testnet (04 §6.1, §8.1).
 *
 * Web'de tarayıcı cüzdanı (MetaMask, Rabby…) ya da WalletConnect; mobilde WalletConnect
 * ya da uygulama içi cüzdan. Bağlanınca SIWE (EIP-4361) imzasıyla giriş; `registered`
 * false ise `app/index.tsx` kayıt akışına yönlendirir.
 *
 * Sunucu kontrolü: `/config` alınır ve `applyServerConfig` ile uygulanır; sunucu zinciri
 * 10143 değilse giriş düğmeleri kapanır (SERVER_CHAIN_MISMATCH).
 */
export default function Login() {
  const router = useRouter();
  const signIn = useSession((s) => s.signIn);
  const sessionError = useSession((s) => s.error);
  const clearError = useSession((s) => s.clearError);
  const [error, setError] = useState<string | null>(null);

  const backend = useQuery({
    queryKey: ['meta', 'config'],
    queryFn: async () => {
      const cfg = await metaApi.config();
      applyServerConfig(cfg); // zincir uyuşmazlığında ChainError fırlatır → backend.error
      return cfg;
    },
    staleTime: 5 * 60_000,
    retry: 0,
  });
  const chainMismatch =
    backend.error instanceof ChainError && backend.error.code === 'SERVER_CHAIN_MISMATCH'
      ? backend.error
      : null;

  /** Cüzdan bağlandı → SIWE ile giriş → yönlendirme (kayıt kararı index.tsx'te). */
  const onConnected = async () => {
    setError(null);
    clearError();
    try {
      await signIn();
      router.replace('/');
    } catch (err) {
      setError(userMessage(err));
    }
  };

  const notice = error ?? sessionError;

  return (
    <Screen contentStyle={styles.content}>
      <View style={styles.logo}>
        <Text variant="h2" color={colors.onNavy}>
          TK
        </Text>
      </View>
      <Text variant="display">Welcome</Text>
      <Text variant="body" color="text2">
        Connect an EVM wallet on {networkLabel()} to continue
      </Text>
      <View style={styles.status}>
        <Pill label={`Network: ${networkLabel()}`} tone="navy" />
        {backend.isPending ? (
          <View style={styles.statusRow}>
            <ActivityIndicator size="small" color={colors.text3} />
            <Text variant="caption" color="text3">
              Checking the server…
            </Text>
          </View>
        ) : chainMismatch ? (
          <Text variant="caption" color="loss">
            Server is on chain {chainMismatch.details?.chainId ?? '?'}; this app is built for{' '}
            {networkLabel()} ({CHAIN_ID}).
          </Text>
        ) : backend.isError ? (
          <Text variant="caption" color="loss">
            Server unreachable — signing in may not work right now.
          </Text>
        ) : (
          <Text variant="caption" color={colors.profit}>
            Server connected · {backend.data?.auth?.siwe_domain}
          </Text>
        )}
      </View>

      <Card style={styles.card}>
        <ConnectWalletPanel onConnected={onConnected} disabled={chainMismatch !== null} />
      </Card>

      <Text variant="caption" color="text2" align="center">
        Signing in asks your wallet to sign a message (Sign-In with Ethereum). It is free and
        moves no funds.
      </Text>

      {notice ? (
        <View style={styles.notice}>
          <Text variant="caption" color="loss" align="center">
            {notice}
          </Text>
        </View>
      ) : null}

      <View style={styles.footer}>
        <Text variant="body" color="text2">
          No account yet?
        </Text>
        <Button
          title="Sign up"
          variant="ghost"
          size="sm"
          onPress={() => router.push('/(auth)/register/role')}
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    flexGrow: 1,
    justifyContent: 'center',
    alignItems: 'flex-start',
    gap: spacing.md,
    paddingVertical: 40,
  },
  logo: {
    width: 64,
    height: 64,
    borderRadius: radius.lg,
    backgroundColor: colors.navy900,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  status: { gap: spacing.xs },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  card: { gap: spacing.md, marginTop: spacing.lg, alignSelf: 'stretch' },
  notice: {
    alignSelf: 'stretch',
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.redBg,
  },
  footer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'stretch',
    gap: spacing.xs,
    marginTop: spacing.lg,
  },
});
