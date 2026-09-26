import { useMutation, useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { useCallback, useMemo, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { Screen, TopBar } from '@/components/layout';
import { TxProgressSheet, verifyUnsignedTx } from '@/components/tx';
import {
  AmountField,
  Button,
  Card,
  Chip,
  Field,
  Progress,
  RiskBadge,
  SlideToConfirm,
  Text,
  amountInputError,
} from '@/components/ui';
import { listingsApi, metaApi } from '@/lib/api';
import type {
  AssetOut,
  ListingCreateIn,
  ListingOut,
  MarketCategory,
  RiskProfile,
  UnsignedTxOut,
} from '@/lib/api/types';
import { applyServerConfig, formatAmount, parseAmountInput, useTxExecutor } from '@/lib/chain';
import { fieldErrors, userMessage } from '@/lib/errors';
import { formatBps, formatDuration, parseNumberInput } from '@/lib/format';
import { colors, layout, radius, spacing } from '@/theme';
import { useSession } from '@/store/session';

/**
 * İlan oluştur (FE-41, 04 §6.2) — 4 adımlı sihirbaz. Müşteri **sermaye** ilanı (draft doğar,
 * 4. adımda `reserve` ile kasaya kilitlenir → active), trader **hizmet** ilanı (active doğar).
 * Uçlar: `POST /listings`, `POST /listings/{id}/tx/reserve`, `/tx/submit`, `GET /tx/{id}`.
 */
const MARKETS: { value: MarketCategory; label: string }[] = [
  { value: 'crypto', label: 'Crypto' },
  { value: 'stable_fx', label: 'Stable / FX' },
  { value: 'defi', label: 'DeFi' },
];
const RISKS: { value: RiskProfile; label: string; hint: string }[] = [
  { value: 'conservative', label: 'Conservative', hint: 'Low drawdown, steady pace' },
  { value: 'balanced', label: 'Balanced', hint: 'Moderate risk for moderate return' },
  { value: 'aggressive', label: 'Aggressive', hint: 'Higher swings, higher upside' },
];
const TITLE_MIN = 3;
const TITLE_MAX = 120;
const DESC_MAX = 4000;
const DURATION_MIN = 1;
const DURATION_MAX = 1095;

type Step = 1 | 2 | 3 | 4;

export default function CreateListing() {
  const router = useRouter();
  const role = useSession((s) => s.role);
  const profile = useSession((s) => s.profile);
  const isCapital = role === 'customer';
  const totalSteps = isCapital ? 4 : 3;

  const config = useQuery({
    queryKey: ['config'],
    queryFn: async () => {
      const cfg = await metaApi.config();
      try {
        applyServerConfig(cfg);
      } catch {
        // zincir uyuşmazlığı giriş ekranında ele alınır
      }
      return cfg;
    },
    staleTime: 5 * 60_000,
  });
  const baseAssets = useMemo<AssetOut[]>(
    () => (config.data?.assets ?? []).filter((a) => a.is_base_allowed && a.is_active),
    [config.data],
  );

  // --- form ---
  const [step, setStep] = useState<Step>(1);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [markets, setMarkets] = useState<MarketCategory[]>([]);
  const [risk, setRisk] = useState<RiskProfile | null>(null);
  const [assetId, setAssetId] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [duration, setDuration] = useState('30');
  const [maxLoss, setMaxLoss] = useState('');
  const [commission, setCommission] = useState(
    profile?.commission_bps ? String(profile.commission_bps / 100) : '',
  );
  const [minCapital, setMinCapital] = useState('');
  const [retMin, setRetMin] = useState('');
  const [retMax, setRetMax] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});

  const asset =
    baseAssets.find((a) => a.id === assetId) ??
    (config.data?.default_base_asset_id
      ? baseAssets.find((a) => a.id === config.data?.default_base_asset_id)
      : undefined) ??
    baseAssets[0] ??
    null;
  const decimals = asset?.decimals ?? 6;
  const symbol = asset?.symbol ?? '';

  // --- adım doğrulama ---
  const validateBasics = (): Record<string, string> => {
    const e: Record<string, string> = {};
    const t = title.trim();
    if (t.length < TITLE_MIN || t.length > TITLE_MAX)
      e.title = `Use ${TITLE_MIN}–${TITLE_MAX} characters.`;
    if (description.length > DESC_MAX) e.description = `Keep it under ${DESC_MAX} characters.`;
    if (markets.length < 1 || markets.length > 3) e.markets = 'Pick 1 to 3 markets.';
    if (!risk) e.risk = 'Choose a risk profile.';
    return e;
  };

  const validateTerms = (): Record<string, string> => {
    const e: Record<string, string> = {};
    if (isCapital) {
      if (!asset) e.amount = 'No base asset is configured on the server.';
      else {
        const amtErr = amountInputError(amount, decimals);
        if (!amount.trim()) e.amount = 'Enter the capital amount.';
        else if (amtErr) e.amount = amtErr;
      }
      const days = parseNumberInput(duration);
      if (days === null || days < DURATION_MIN || days > DURATION_MAX || !Number.isInteger(days))
        e.duration = `Enter a whole number of days (${DURATION_MIN}–${DURATION_MAX}).`;
      if (maxLoss.trim()) {
        const pct = parseNumberInput(maxLoss);
        if (pct === null || pct <= 0 || pct > 100)
          e.maxLoss = 'Enter a percentage between 0 and 100, or leave empty.';
      }
    } else {
      const pct = parseNumberInput(commission);
      if (pct === null || pct <= 0 || pct > 50) e.commission = 'Enter a rate between 0% and 50%.';
      if (minCapital.trim()) {
        const err = amountInputError(minCapital, decimals);
        if (err) e.minCapital = err;
      }
      const lo = retMin.trim() ? parseNumberInput(retMin) : undefined;
      const hi = retMax.trim() ? parseNumberInput(retMax) : undefined;
      if (lo === null) e.retMin = 'Enter a percentage.';
      if (hi === null) e.retMax = 'Enter a percentage.';
      if (typeof lo === 'number' && typeof hi === 'number' && lo > hi)
        e.retMax = 'The upper bound cannot be below the lower bound.';
    }
    return e;
  };

  const goNext = () => {
    const e = step === 1 ? validateBasics() : step === 2 ? validateTerms() : {};
    setErrors(e);
    if (Object.keys(e).length > 0) return;
    setStep((s) => Math.min(s + 1, totalSteps) as Step);
  };

  const payload = (): ListingCreateIn => {
    const base: ListingCreateIn = {
      kind: isCapital ? 'capital' : 'service',
      title: title.trim(),
      description: description.trim(),
      markets,
      risk_profile: risk,
    };
    if (isCapital) {
      const parsed = parseAmountInput(amount, decimals);
      const pct = maxLoss.trim() ? parseNumberInput(maxLoss) : null;
      return {
        ...base,
        amount: parsed?.human ?? amount,
        base_asset_id: asset?.id ?? null,
        duration_days: parseNumberInput(duration) ?? undefined,
        max_loss_bps: pct === null ? null : Math.round(pct * 100),
      };
    }
    const pct = parseNumberInput(commission) ?? 0;
    const lo = retMin.trim() ? parseNumberInput(retMin) : null;
    const hi = retMax.trim() ? parseNumberInput(retMax) : null;
    const minCap = minCapital.trim() ? parseAmountInput(minCapital, decimals) : null;
    return {
      ...base,
      commission_bps: Math.round(pct * 100),
      min_capital: minCap?.human ?? null,
      base_asset_id: asset?.id ?? null,
      expected_return_min_bps: lo === null ? null : Math.round(lo * 100),
      expected_return_max_bps: hi === null ? null : Math.round(hi * 100),
    };
  };

  // --- create ---
  const [created, setCreated] = useState<ListingOut | null>(null);
  const create = useMutation({
    mutationFn: (body: ListingCreateIn) => listingsApi.create(body),
    onSuccess: (listing) => {
      setCreated(listing);
      if (listing.kind === 'service' || listing.status !== 'draft') {
        router.replace({ pathname: '/listing/[id]', params: { id: listing.id } });
        return;
      }
      setStep(4);
    },
    onError: (err) => {
      const fe = fieldErrors(err);
      if (Object.keys(fe).length > 0) {
        setErrors(mapServerFields(fe));
        // alan hangi adımdaysa oraya dön
        setStep(['title', 'description', 'markets', 'risk_profile'].some((k) => k in fe) ? 1 : 2);
      }
    },
  });

  // --- reserve tx (adım 4) ---
  const [live, setLive] = useState(false);
  const tx = useTxExecutor({ onConfirmed: () => setLive(true) });
  const buildRef = useRef<(() => Promise<UnsignedTxOut>) | null>(null);
  const lock = useCallback(() => {
    if (!created) return;
    const build = async () => verifyUnsignedTx(await listingsApi.reserveTx(created.id));
    buildRef.current = build;
    void tx.run(build);
  }, [created, tx]);
  const retry = useCallback(() => {
    const build = buildRef.current;
    if (!build) return;
    tx.reset();
    void tx.run(build);
  }, [tx]);
  const goToListing = () => {
    if (created) router.replace({ pathname: '/listing/[id]', params: { id: created.id } });
    else router.back();
  };

  const stepLabel = ['Basics', 'Terms', 'Review', 'Lock capital'][step - 1];

  if (!role) {
    return (
      <Screen padded={false}>
        <TopBar title="Create listing" />
        <View style={styles.body}>
          <Card style={styles.state}>
            <Text variant="h2">Finish sign-up first</Text>
            <Text variant="body" color="text2">
              Choose a role to publish listings.
            </Text>
          </Card>
        </View>
      </Screen>
    );
  }

  return (
    <Screen padded={false}>
      <TopBar title={isCapital ? 'New capital listing' : 'New service listing'} />
      <View style={styles.body}>
        <Progress
          value={step / totalSteps}
          label={`Step ${step}/${totalSteps} · ${stepLabel}`}
          trailing={`${Math.round((step / totalSteps) * 100)}%`}
        />

        {/* Adım 1 — Basics */}
        {step === 1 ? (
          <Card style={styles.section}>
            <Field
              label="Title"
              value={title}
              onChangeText={setTitle}
              placeholder={
                isCapital
                  ? 'e.g. 5,000 tUSDC for a 3-month DeFi strategy'
                  : 'e.g. Market-neutral crypto strategy'
              }
              maxLength={TITLE_MAX}
              error={errors.title}
              hint={`${title.trim().length}/${TITLE_MAX}`}
            />
            <Field
              label="Description"
              value={description}
              onChangeText={setDescription}
              placeholder={
                isCapital
                  ? 'What are you looking for in a trader?'
                  : 'Describe your approach, instruments and risk management.'
              }
              multiline
              maxLength={DESC_MAX}
              error={errors.description}
              hint={`${description.length}/${DESC_MAX} · optional`}
            />
            <View style={styles.group}>
              <Text variant="captionStrong" color="text2">
                Markets (1–3)
              </Text>
              <View style={styles.chips}>
                {MARKETS.map((m) => {
                  const active = markets.includes(m.value);
                  return (
                    <Chip
                      key={m.value}
                      label={m.label}
                      active={active}
                      onPress={() =>
                        setMarkets((prev) =>
                          active
                            ? prev.filter((x) => x !== m.value)
                            : prev.length >= 3
                              ? prev
                              : [...prev, m.value],
                        )
                      }
                    />
                  );
                })}
              </View>
              {errors.markets ? (
                <Text variant="caption" color="loss">
                  {errors.markets}
                </Text>
              ) : null}
            </View>
            <View style={styles.group}>
              <Text variant="captionStrong" color="text2">
                Risk profile
              </Text>
              {RISKS.map((r) => (
                <Button
                  key={r.value}
                  title={`${r.label} — ${r.hint}`}
                  variant={risk === r.value ? 'primary' : 'secondary'}
                  fullWidth
                  onPress={() => setRisk(r.value)}
                />
              ))}
              {errors.risk ? (
                <Text variant="caption" color="loss">
                  {errors.risk}
                </Text>
              ) : null}
            </View>
          </Card>
        ) : null}

        {/* Adım 2 — Terms */}
        {step === 2 ? (
          <Card style={styles.section}>
            {config.isPending ? (
              <Text variant="caption" color="text3">
                Loading assets…
              </Text>
            ) : config.isError ? (
              <View style={styles.inlineError}>
                <Text variant="caption" color="loss" style={{ flex: 1 }}>
                  {userMessage(config.error)}
                </Text>
                <Button
                  title="Retry"
                  size="sm"
                  variant="secondary"
                  onPress={() => config.refetch()}
                />
              </View>
            ) : null}

            {baseAssets.length > 1 ? (
              <View style={styles.group}>
                <Text variant="captionStrong" color="text2">
                  Base asset
                </Text>
                <View style={styles.chips}>
                  {baseAssets.map((a) => (
                    <Chip
                      key={a.id}
                      label={a.symbol}
                      active={asset?.id === a.id}
                      onPress={() => setAssetId(a.id)}
                    />
                  ))}
                </View>
              </View>
            ) : null}

            {isCapital ? (
              <>
                <AmountField
                  label="Capital amount"
                  value={amount}
                  onChangeText={setAmount}
                  decimals={decimals}
                  symbol={symbol}
                  error={errors.amount}
                  placeholder="1000"
                  hint="Locked into the TraderKirala vault in the last step."
                />
                <Field
                  label="Duration"
                  value={duration}
                  onChangeText={setDuration}
                  keyboardType="number-pad"
                  suffix="days"
                  error={errors.duration}
                  hint={`${DURATION_MIN}–${DURATION_MAX} days`}
                />
                <Field
                  label="Max loss"
                  value={maxLoss}
                  onChangeText={setMaxLoss}
                  keyboardType="decimal-pad"
                  suffix="%"
                  placeholder="No limit"
                  error={errors.maxLoss}
                  hint="The vault blocks trades that would push value below this floor. Leave empty for no limit."
                />
              </>
            ) : (
              <>
                <Field
                  label="Commission rate"
                  value={commission}
                  onChangeText={setCommission}
                  keyboardType="decimal-pad"
                  suffix="%"
                  placeholder="20"
                  error={errors.commission}
                  hint="Your share of the profit — written into each agreement."
                />
                <AmountField
                  label="Minimum capital"
                  value={minCapital}
                  onChangeText={setMinCapital}
                  decimals={decimals}
                  symbol={symbol}
                  error={errors.minCapital}
                  placeholder="500"
                  hint="Optional"
                />
                <View style={styles.row}>
                  <View style={{ flex: 1 }}>
                    <Field
                      label="Expected return (low)"
                      value={retMin}
                      onChangeText={setRetMin}
                      keyboardType="decimal-pad"
                      suffix="%"
                      placeholder="10"
                      error={errors.retMin}
                    />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Field
                      label="Expected return (high)"
                      value={retMax}
                      onChangeText={setRetMax}
                      keyboardType="decimal-pad"
                      suffix="%"
                      placeholder="25"
                      error={errors.retMax}
                    />
                  </View>
                </View>
              </>
            )}
          </Card>
        ) : null}

        {/* Adım 3 — Review */}
        {step === 3 ? (
          <Card style={styles.section}>
            <Text variant="h2">{title.trim()}</Text>
            {description.trim() ? (
              <Text variant="body" color="text2">
                {description.trim()}
              </Text>
            ) : null}
            <View style={styles.chips}>
              {markets.map((m) => (
                <Chip key={m} label={MARKETS.find((x) => x.value === m)?.label ?? m} active />
              ))}
              <RiskBadge level={risk} />
            </View>
            {isCapital ? (
              <>
                <Row
                  label="Capital"
                  value={formatAmount(parseAmountInput(amount, decimals)?.human ?? amount, symbol)}
                />
                <Row label="Duration" value={formatDuration(parseNumberInput(duration) ?? 0)} />
                <Row
                  label="Max loss"
                  value={
                    maxLoss.trim()
                      ? formatBps(Math.round((parseNumberInput(maxLoss) ?? 0) * 100))
                      : 'No limit'
                  }
                />
              </>
            ) : (
              <>
                <Row
                  label="Commission"
                  value={formatBps(Math.round((parseNumberInput(commission) ?? 0) * 100))}
                />
                <Row
                  label="Minimum capital"
                  value={
                    minCapital.trim()
                      ? formatAmount(
                          parseAmountInput(minCapital, decimals)?.human ?? minCapital,
                          symbol,
                        )
                      : '—'
                  }
                />
                <Row
                  label="Expected return"
                  value={
                    retMin.trim() || retMax.trim()
                      ? `${retMin.trim() || '?'}% – ${retMax.trim() || '?'}%`
                      : '—'
                  }
                />
              </>
            )}
            <Text variant="caption" color="text3">
              {isCapital
                ? 'The listing is created as a draft. It goes live once you lock the capital in the next step.'
                : 'The listing goes live immediately. Customers can request offers from you.'}
            </Text>
            {create.isError && Object.keys(fieldErrors(create.error)).length === 0 ? (
              <View style={styles.error}>
                <Text variant="caption" color="loss">
                  {userMessage(create.error)}
                </Text>
              </View>
            ) : null}
          </Card>
        ) : null}

        {/* Adım 4 — Lock capital */}
        {step === 4 && created ? (
          <Card style={styles.section}>
            <Text variant="h2">{live ? 'Listing is live' : 'Lock capital'}</Text>
            <Text variant="body" color="text2">
              {live
                ? `${formatAmount(created.amount, symbol)} is locked in the vault. Traders can now make offers.`
                : `Your ${formatAmount(created.amount, symbol)} moves into the TraderKirala vault. Trades can only start from this locked amount.`}
            </Text>
            {!live ? (
              <>
                <SlideToConfirm
                  label={`Slide to lock ${formatAmount(created.amount, symbol)}`}
                  loading={tx.isBusy}
                  onConfirm={lock}
                />
                <Text variant="caption" color="text3">
                  Two wallet requests: approve {symbol} spending, then lock. You need a little MON
                  for gas.
                </Text>
              </>
            ) : null}
          </Card>
        ) : null}

        {/* Alt aksiyonlar */}
        <View style={styles.footer}>
          {step > 1 && step < 4 ? (
            <Button
              title="Back"
              variant="ghost"
              onPress={() => setStep((s) => (s - 1) as Step)}
              style={{ flex: 1 }}
            />
          ) : null}
          {step < 3 ? (
            <Button title="Continue" onPress={goNext} style={{ flex: 2 }} />
          ) : step === 3 ? (
            <Button
              title={isCapital ? 'Create draft' : 'Publish listing'}
              loading={create.isPending}
              onPress={() => create.mutate(payload())}
              style={{ flex: 2 }}
            />
          ) : live ? (
            <Button title="View listing" fullWidth onPress={goToListing} style={{ flex: 1 }} />
          ) : (
            <Button
              title="Later"
              variant="ghost"
              disabled={tx.isBusy}
              onPress={goToListing}
              style={{ flex: 1 }}
            />
          )}
        </View>
      </View>

      <TxProgressSheet
        progress={tx.progress}
        onClose={() => {
          const confirmed = tx.progress.phase === 'confirmed';
          tx.reset();
          if (confirmed) goToListing();
        }}
        onRetry={retry}
        onRecheck={() => void tx.recheck()}
      />
    </Screen>
  );
}

/** Sunucu alan adları → form anahtarları. */
function mapServerFields(fe: Record<string, string>): Record<string, string> {
  const map: Record<string, string> = {
    risk_profile: 'risk',
    max_loss_bps: 'maxLoss',
    duration_days: 'duration',
    commission_bps: 'commission',
    min_capital: 'minCapital',
    expected_return_min_bps: 'retMin',
    expected_return_max_bps: 'retMax',
    base_asset_id: 'amount',
  };
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(fe)) out[map[k] ?? k] = v;
  return out;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.kv}>
      <Text variant="caption" color="text2" style={{ flex: 1 }}>
        {label}
      </Text>
      <Text variant="numericSm">{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: {
    paddingHorizontal: layout.screenPaddingH,
    paddingBottom: spacing['3xl'],
    gap: spacing.lg,
  },
  state: { gap: spacing.md, alignItems: 'flex-start' },
  section: { gap: spacing.lg },
  group: { gap: spacing.sm },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  row: { flexDirection: 'row', gap: spacing.md },
  kv: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  footer: { flexDirection: 'row', gap: spacing.sm },
  error: { padding: spacing.md, borderRadius: radius.md, backgroundColor: colors.redBg },
  inlineError: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
});
