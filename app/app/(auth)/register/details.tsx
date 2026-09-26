import { useQuery } from '@tanstack/react-query';
import { Redirect, useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { Screen, TopBar } from '@/components/layout';
import { Button, Card, Chip, Field, Pill, Progress, Text } from '@/components/ui';
import { ConnectWalletPanel } from '@/components/wallet';
import { metaApi } from '@/lib/api';
import type { MarketCategory, RegisterIn, RiskLevel, RiskProfile } from '@/lib/api/types';
import { applyServerConfig, defaultBaseAsset, parseAmountInput, shortAddress } from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { parseNumberInput } from '@/lib/format';
import { useSession } from '@/store/session';
import { colors, radius, spacing } from '@/theme';

/**
 * Figma 1f/1g · Kayıt · Bilgiler — `POST /api/v1/users/register` (RegisterIn).
 * Sunucu piyasaları üç kategoriye indirger (crypto | stable_fx | defi, en fazla 3),
 * oranları bps ister ve tutarları ondalık string alır (`parseAmountInput`, taban varlığın
 * `decimals` değeri `/config.default_base_asset_id`'den).
 *
 * Cüzdan yoksa `ConnectWalletPanel` (compact) gösterilir; SIWE imzası yalnız gönderimde
 * (`status !== 'signed_in'`) istenir. Kayıt yanıtı token taşır → `register` saklar → `/`.
 */
/** `/config` yüklenmeden önce (ör. derin bağlantı) kullanılan ondalık yedeği. */
const FALLBACK_DECIMALS = 18;
const MARKETS: { value: MarketCategory; label: string }[] = [
  { value: 'crypto', label: 'Crypto' },
  { value: 'stable_fx', label: 'Stable / FX' },
  { value: 'defi', label: 'DeFi' },
];

const RISK_PROFILES: { value: RiskProfile; label: string }[] = [
  { value: 'conservative', label: 'Conservative' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'aggressive', label: 'Aggressive' },
];

const RISK_LEVELS: { value: RiskLevel; label: string }[] = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

type Errors = Partial<Record<string, string>>;

export default function RegisterDetails() {
  const router = useRouter();
  const { role } = useLocalSearchParams<{ role?: string }>();
  const isTrader = role === 'trader';

  const address = useSession((s) => s.address);
  const walletName = useSession((s) => s.walletName);
  const status = useSession((s) => s.status);
  const signIn = useSession((s) => s.signIn);
  const register = useSession((s) => s.register);

  // Taban varlığın ondalığı için /config (giriş ekranı yüklediyse önbellekten gelir).
  const config = useQuery({
    queryKey: ['meta', 'config'],
    queryFn: async () => {
      const cfg = await metaApi.config();
      applyServerConfig(cfg);
      return cfg;
    },
    staleTime: 5 * 60_000,
    retry: 0,
  });
  const baseAsset = config.data ? defaultBaseAsset() : null;
  const decimals = baseAsset?.decimals ?? FALLBACK_DECIMALS;
  const amountError = `Enter a valid amount (up to ${decimals} decimals).`;

  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [budget, setBudget] = useState('');
  const [riskProfile, setRiskProfile] = useState<RiskProfile | null>(null);
  const [riskLevel, setRiskLevel] = useState<RiskLevel | null>(null);
  const [markets, setMarkets] = useState<MarketCategory[]>([]);
  const [strategy, setStrategy] = useState('');
  const [commission, setCommission] = useState('');
  const [minCapital, setMinCapital] = useState('');

  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (role !== 'customer' && role !== 'trader') {
    return <Redirect href="/(auth)/register/role" />;
  }

  const toggleMarket = (m: MarketCategory) =>
    setMarkets((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : prev.length >= 3 ? prev : [...prev, m],
    );

  const buildPayload = (): { payload?: RegisterIn; errors: Errors } => {
    const next: Errors = {};
    const name = username.trim();
    if (name.length < 3) next.username = 'Use at least 3 characters.';
    else if (name.length > 24) next.username = 'Use at most 24 characters.';
    else if (!/^[a-zA-Z0-9._]+$/.test(name))
      next.username = 'Letters, numbers, dots and underscores only.';

    const display = displayName.trim() || name;
    if (markets.length === 0) next.markets = 'Pick at least one market.';

    if (!isTrader) {
      const budgetValue = parseAmountInput(budget, decimals);
      if (!budgetValue || budgetValue.raw <= 0n) next.budget = amountError;
      if (!riskProfile) next.risk = 'Choose your risk preference.';
      if (Object.keys(next).length > 0) return { errors: next };
      return {
        errors: next,
        payload: {
          role: 'customer',
          username: name,
          display_name: display,
          customer: {
            budget_amount: budgetValue?.human ?? '',
            risk_profile: riskProfile as RiskProfile,
            markets,
          },
        },
      };
    }

    const summary = strategy.trim();
    if (summary.length < 20) next.strategy = 'Describe your strategy in at least 20 characters.';
    else if (summary.length > 280) next.strategy = 'Use at most 280 characters.';
    const commissionPct = parseNumberInput(commission);
    if (commissionPct === null || commissionPct <= 0 || commissionPct > 50)
      next.commission = 'Enter a rate between 0% and 50%.';
    const minCapitalValue = parseAmountInput(minCapital, decimals);
    if (!minCapitalValue || minCapitalValue.raw <= 0n) next.minCapital = amountError;
    if (!riskLevel) next.risk = 'Choose your risk level.';
    if (Object.keys(next).length > 0) return { errors: next };
    return {
      errors: next,
      payload: {
        role: 'trader',
        username: name,
        display_name: display,
        trader: {
          markets,
          strategy_summary: summary,
          commission_bps: Math.round((commissionPct as number) * 100),
          min_capital: minCapitalValue?.human ?? '',
          risk_level: riskLevel as RiskLevel,
        },
      },
    };
  };

  const onSubmit = async () => {
    const { payload, errors: found } = buildPayload();
    setErrors(found);
    if (!payload) return;

    setBusy(true);
    setFormError(null);
    try {
      // Kayıt korumalı uç: JWT yoksa önce SIWE girişi (cüzdan bağlı, imza istenir).
      if (status !== 'signed_in') await signIn();
      await register(payload);
      router.replace('/');
    } catch (err) {
      setFormError(userMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen padded={false}>
      <TopBar title="Sign up" />
      <View style={styles.body}>
        <Progress
          value={1}
          label={`Step 2 of 2 · ${isTrader ? 'Trader details' : 'Customer details'}`}
        />

        <View style={{ gap: spacing.xs }}>
          <Text variant="h1">Tell us about you</Text>
          <Text variant="body" color="text2">
            {isTrader
              ? 'Investors see this on your service listing and trader profile.'
              : 'We use this to suggest traders that fit you and to prefill your capital listing.'}
          </Text>
        </View>

        <Card style={styles.walletCard}>
          <View style={styles.walletRow}>
            <View style={{ flex: 1, gap: 2 }}>
              <Text variant="captionStrong" color="text2">
                Wallet address
              </Text>
              <Text variant="numericSm">
                {address ? shortAddress(address, 6, 6) : 'Not connected'}
              </Text>
            </View>
            {address ? <Pill label={walletName ?? 'Connected'} tone="navy" /> : null}
          </View>
          {!address ? (
            <ConnectWalletPanel
              compact
              onConnected={() => {
                setFormError(null);
              }}
            />
          ) : null}
        </Card>

        <Field
          label="Username"
          value={username}
          onChangeText={setUsername}
          placeholder={isTrader ? 'e.g. kaandemir' : 'e.g. elifyilmaz'}
          autoCapitalize="none"
          autoCorrect={false}
          maxLength={24}
          error={errors.username}
          hint="Shown as @username on your profile."
        />

        <Field
          label="Display name"
          value={displayName}
          onChangeText={setDisplayName}
          placeholder="Optional — defaults to your username"
          maxLength={48}
        />

        <ChipGroup
          label={isTrader ? 'Markets you specialise in' : 'Markets you care about'}
          error={errors.markets}
          hint="Pick up to three."
          options={MARKETS.map((m) => ({
            key: m.value,
            label: m.label,
            active: markets.includes(m.value),
          }))}
          onToggle={(key) => toggleMarket(key as MarketCategory)}
        />

        {isTrader ? (
          <>
            <ChipGroup
              label="Risk level"
              error={errors.risk}
              options={RISK_LEVELS.map((r) => ({
                key: r.value,
                label: r.label,
                active: riskLevel === r.value,
              }))}
              onToggle={(key) => setRiskLevel(key as RiskLevel)}
            />
            <Field
              label="Strategy summary"
              value={strategy}
              onChangeText={setStrategy}
              placeholder="Which markets, what time horizon, how do you manage risk?"
              multiline
              maxLength={280}
              error={errors.strategy}
              hint={`${strategy.trim().length}/280 · Investors read this on your profile.`}
            />
            <Field
              label="Commission rate"
              value={commission}
              onChangeText={setCommission}
              placeholder="20"
              keyboardType="decimal-pad"
              suffix="%"
              error={errors.commission}
              hint="Your share of the profit — written into the agreement."
            />
            <Field
              label="Min. capital"
              value={minCapital}
              onChangeText={setMinCapital}
              placeholder="1000"
              keyboardType="decimal-pad"
              error={errors.minCapital}
              hint={`In ${baseAsset?.symbol ?? 'the base asset'}. Offers below this amount are hidden from you.`}
            />
          </>
        ) : (
          <>
            <Field
              label="Investment budget"
              value={budget}
              onChangeText={setBudget}
              placeholder="1000"
              keyboardType="decimal-pad"
              error={errors.budget}
              hint={`In ${baseAsset?.symbol ?? 'the base asset'}. Used for matching only — capital moves to the on-chain vault when you sign a contract.`}
            />
            <ChipGroup
              label="Risk preference"
              error={errors.risk}
              options={RISK_PROFILES.map((r) => ({
                key: r.value,
                label: r.label,
                active: riskProfile === r.value,
              }))}
              onToggle={(key) => setRiskProfile(key as RiskProfile)}
            />
          </>
        )}

        {formError ? (
          <View style={styles.errorBox}>
            <Text variant="captionStrong" color="loss">
              Sign-up failed
            </Text>
            <Text variant="caption" color="text2">
              {formError}
            </Text>
          </View>
        ) : null}

        <Button title="Create account" fullWidth loading={busy} onPress={onSubmit} />
        <Text variant="caption" color="text3" align="center">
          Finishing sign-up links your role to this wallet address. If you are not signed in
          yet, your wallet will ask for one free signature (Sign-In with Ethereum).
        </Text>
      </View>
    </Screen>
  );
}

function ChipGroup({
  label,
  options,
  onToggle,
  error,
  hint,
}: {
  label: string;
  options: { key: string; label: string; active: boolean }[];
  onToggle: (key: string) => void;
  error?: string;
  hint?: string;
}) {
  return (
    <View style={styles.group}>
      <Text variant="captionStrong" color="text2">
        {label}
      </Text>
      <View style={styles.chips}>
        {options.map((o) => (
          <Chip key={o.key} label={o.label} active={o.active} onPress={() => onToggle(o.key)} />
        ))}
      </View>
      {error ? (
        <Text variant="caption" color="loss">
          {error}
        </Text>
      ) : hint ? (
        <Text variant="caption" color="text3">
          {hint}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: spacing.lg, paddingBottom: spacing['2xl'], gap: spacing.lg },
  walletCard: {
    gap: spacing.md,
    padding: spacing.md,
    backgroundColor: colors.surfaceAlt,
  },
  walletRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  group: { gap: spacing.sm },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  errorBox: {
    gap: 2,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.redBg,
  },
});
