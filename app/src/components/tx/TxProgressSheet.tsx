import { useRouter } from 'expo-router';
import { AlertTriangle, Check, Circle, X } from 'lucide-react-native';
import { ActivityIndicator, Linking, StyleSheet, View } from 'react-native';

import { BottomSheet, Button, ExplorerLink, Text } from '@/components/ui';
import { ApiError } from '@/lib/api';
import type { UnsignedTxOut } from '@/lib/api/types';
import {
  ChainError,
  TERMINAL_PHASES,
  addEthereumChainParams,
  decodeCalldataFunction,
  getChainConfig,
  type TxPhase,
  type TxProgress,
} from '@/lib/chain';
import { userMessage } from '@/lib/errors';
import { colors, radius, spacing } from '@/theme';
import { useSession } from '@/store/session';

/**
 * İşlem ilerleme sheet'i (04 §6.7, §8.2). `useTxExecutor().progress`'i çizer:
 * adım listesi (approve ×n + ana işlem), faz metni, explorer linki, hata yolları.
 * Cüzdan/sunucu meşgulken (imza bekleniyor, approve receipt, submit) kapatma kilitlidir.
 */
export interface TxProgressSheetProps {
  progress: TxProgress;
  onClose: () => void;
  /** Aynı aksiyonu yeniden build edip çalıştırır (failed / rejected / expired). */
  onRetry?: () => void;
  /** `timeout` sonrası `GET /tx/{id}` tek sefer (`useTxExecutor.recheck`). */
  onRecheck?: () => void;
}

/**
 * Calldata'nın istenen `action` ile eşleşmesini doğrular (04 §6.7 "ne imzalıyorum").
 * Eşleşmezse `ChainError('UNKNOWN')` fırlatır — `run(() => verifyUnsignedTx(await build()))`
 * ile yürütücü **devam etmez**. Çözülemeyen selector (ABI dışı) ya da native transfer engellenmez.
 */
export function verifyUnsignedTx(unsigned: UnsignedTxOut): UnsignedTxOut {
  const decoded = decodeCalldataFunction(unsigned.data as `0x${string}`);
  if (decoded && decoded.name.toLowerCase() !== unsigned.action.toLowerCase()) {
    throw new ChainError(
      `Calldata does not match the requested action (${decoded.name} ≠ ${unsigned.action}). Nothing was sent.`,
      'UNKNOWN',
    );
  }
  return unsigned;
}

/** Kapatmanın kilitli olduğu fazlar: cüzdanda ya da sunucuda açık bir istek var. */
const LOCKED_PHASES: ReadonlySet<TxPhase> = new Set([
  'building',
  'checking_wallet',
  'switching_chain',
  'approving',
  'awaiting_signature',
  'submitting',
]);

const ACTION_LABEL: Record<string, string> = {
  open: 'Lock principal in the vault',
  openReserved: 'Open from your listing deposit',
  propose: 'Propose the agreement',
  fund: 'Fund the agreement',
  fundReserved: 'Fund from your listing deposit',
  accept: 'Accept and start',
  cancel: 'Cancel the agreement',
  settle: 'Settle the agreement',
  claim: 'Claim tokens',
  trade: 'Execute the trade',
  reserve: 'Lock listing capital',
  release: 'Release listing capital',
  releaseAll: 'Release all listing capital',
  transfer: 'Transfer',
};

function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

function phaseText(p: TxProgress): string {
  const symbol = p.unsigned?.pre_steps?.[p.step.index - 1]?.symbol ?? 'token';
  switch (p.phase) {
    case 'idle':
      return '';
    case 'building':
      return 'Preparing transaction…';
    case 'checking_wallet':
      return 'Checking wallet…';
    case 'switching_chain':
      return 'Switch to Monad Testnet in your wallet';
    case 'approving':
      return p.preStepHashes.length >= p.step.index
        ? 'Approval sent — waiting for confirmation'
        : `Approve ${symbol} spending in your wallet (${p.step.index}/${p.step.total})`;
    case 'awaiting_signature':
      return 'Confirm the transaction in your wallet';
    case 'submitting':
      return 'Sent — notifying the server';
    case 'submitted':
      return p.status ? 'Sent — waiting for confirmation' : 'Sent — the server will pick it up';
    case 'confirmed':
      return 'Confirmed';
    case 'failed':
      return `Failed: ${p.error ? userMessage(p.error) : 'the transaction did not go through.'}`;
    case 'rejected':
      return 'You rejected the request in your wallet.';
    case 'expired':
      return 'This request expired. Build it again.';
    case 'timeout':
      return 'Still pending. Check the explorer or try again in a moment.';
  }
}

function titleFor(phase: TxPhase): string {
  switch (phase) {
    case 'confirmed':
      return 'Transaction confirmed';
    case 'failed':
      return 'Transaction failed';
    case 'rejected':
      return 'Request rejected';
    case 'expired':
      return 'Request expired';
    case 'timeout':
      return 'Still pending';
    default:
      return 'Signing transaction';
  }
}

type StepState = 'done' | 'active' | 'pending' | 'failed';

function stepStates(p: TxProgress): StepState[] {
  const total = p.step.total;
  const failed = p.phase === 'failed' || p.phase === 'rejected' || p.phase === 'expired';
  const states: StepState[] = [];
  for (let i = 1; i <= total; i++) {
    const isMain = i === total;
    if (isMain) {
      if (p.phase === 'confirmed') states.push('done');
      else if (
        p.phase === 'awaiting_signature' ||
        p.phase === 'submitting' ||
        p.phase === 'submitted' ||
        p.phase === 'timeout'
      )
        states.push(failed ? 'failed' : 'active');
      else if (failed && p.preStepHashes.length >= total - 1) states.push('failed');
      else states.push('pending');
      continue;
    }
    if (p.preStepHashes.length >= i && (p.phase !== 'approving' || p.step.index > i))
      states.push('done');
    else if (p.phase === 'approving' && p.step.index === i)
      states.push(failed ? 'failed' : 'active');
    else if (failed && p.preStepHashes.length === i - 1 && p.phase !== 'building')
      states.push('failed');
    else states.push('pending');
  }
  return states;
}

function StepIcon({ state }: { state: StepState }) {
  switch (state) {
    case 'done':
      return (
        <View style={[styles.stepIcon, { backgroundColor: colors.greenBg }]}>
          <Check size={14} color={colors.profit} />
        </View>
      );
    case 'active':
      return (
        <View style={[styles.stepIcon, { backgroundColor: colors.navy050 }]}>
          <ActivityIndicator size="small" color={colors.navy900} />
        </View>
      );
    case 'failed':
      return (
        <View style={[styles.stepIcon, { backgroundColor: colors.redBg }]}>
          <X size={14} color={colors.loss} />
        </View>
      );
    default:
      return (
        <View style={[styles.stepIcon, { backgroundColor: colors.surfaceSunken }]}>
          <Circle size={10} color={colors.text3} />
        </View>
      );
  }
}

/** Hata koduna göre yardımcı aksiyon (faucet / ağ değiştir / cüzdan). */
function errorHelp(err: TxProgress['error']): 'faucet' | 'switch_network' | 'wallet' | null {
  if (!err) return null;
  if (err instanceof ChainError) {
    if (err.code === 'INSUFFICIENT_FUNDS') return 'faucet';
    if (err.code === 'CHAIN_NOT_ADDED' || err.code === 'WRONG_NETWORK') return 'switch_network';
    if (err.details?.errorName && /ERC20InsufficientBalance/.test(err.details.errorName))
      return 'wallet';
    return null;
  }
  if (err instanceof ApiError) {
    const code =
      typeof err.details.error_code === 'string' ? err.details.error_code : (err.code ?? '');
    if (/ERC20InsufficientBalance/.test(code)) return 'wallet';
  }
  return null;
}

export function TxProgressSheet({ progress, onClose, onRetry, onRecheck }: TxProgressSheetProps) {
  const router = useRouter();
  const switchToAppChain = useSession((s) => s.switchToAppChain);
  const visible = progress.phase !== 'idle';
  const locked = LOCKED_PHASES.has(progress.phase);
  const terminal = TERMINAL_PHASES.has(progress.phase);
  const unsigned = progress.unsigned;
  const preSteps = unsigned?.pre_steps ?? [];
  const states = stepStates(progress);
  const help = errorHelp(progress.error);
  const chainParams = help === 'switch_network' ? addEthereumChainParams() : null;
  const isCalldataWarning =
    progress.error instanceof ChainError && /Calldata does not match/.test(progress.error.message);

  const close = () => {
    if (!locked) onClose();
  };

  const footer = (
    <>
      {progress.phase === 'timeout' && onRecheck ? (
        <Button title="Check again" fullWidth onPress={onRecheck} />
      ) : null}
      {(progress.phase === 'failed' ||
        progress.phase === 'rejected' ||
        progress.phase === 'expired') &&
      onRetry ? (
        <Button title="Try again" fullWidth onPress={onRetry} />
      ) : null}
      {help === 'faucet' ? (
        <Button
          title="Open faucet"
          variant="secondary"
          fullWidth
          onPress={() => Linking.openURL(getChainConfig().faucetUrl).catch(() => undefined)}
        />
      ) : null}
      {help === 'switch_network' ? (
        <Button
          title="Switch network"
          variant="secondary"
          fullWidth
          onPress={() => switchToAppChain().catch(() => undefined)}
        />
      ) : null}
      {help === 'wallet' ? (
        <Button
          title="Go to wallet"
          variant="secondary"
          fullWidth
          onPress={() => {
            onClose();
            router.push('/wallet');
          }}
        />
      ) : null}
      {!locked ? (
        <Button
          title={progress.phase === 'confirmed' ? 'Done' : 'Close'}
          variant={terminal && progress.phase === 'confirmed' ? 'primary' : 'ghost'}
          fullWidth
          onPress={onClose}
        />
      ) : (
        <Text variant="caption" color="text3" align="center">
          Finish the request in your wallet — this sheet stays open until it is sent.
        </Text>
      )}
    </>
  );

  return (
    <BottomSheet
      visible={visible}
      onClose={close}
      title={titleFor(progress.phase)}
      subtitle={unsigned?.description}
      footer={footer}
    >
      {/* Faz metni */}
      <View
        style={[
          styles.phase,
          progress.phase === 'confirmed' && styles.phaseOk,
          (progress.phase === 'failed' ||
            progress.phase === 'rejected' ||
            progress.phase === 'expired') &&
            styles.phaseErr,
          progress.phase === 'timeout' && styles.phaseWarn,
        ]}
      >
        {locked ? <ActivityIndicator size="small" color={colors.navy900} /> : null}
        <Text
          variant="body"
          color={
            progress.phase === 'confirmed'
              ? 'profit'
              : progress.phase === 'failed' ||
                  progress.phase === 'rejected' ||
                  progress.phase === 'expired'
                ? 'loss'
                : progress.phase === 'timeout'
                  ? 'amberInk'
                  : 'text'
          }
          style={styles.phaseText}
        >
          {phaseText(progress)}
        </Text>
      </View>

      {isCalldataWarning ? (
        <View style={styles.warn}>
          <AlertTriangle size={16} color={colors.amberInk} />
          <Text variant="caption" color="amberInk" style={styles.phaseText}>
            Calldata does not match the requested action. Nothing was sent to your wallet.
          </Text>
        </View>
      ) : null}

      {/* Adımlar */}
      {unsigned ? (
        <View style={styles.steps}>
          {preSteps.map((step, i) => (
            <View key={`${step.to}-${i}`} style={styles.step}>
              <StepIcon state={states[i] ?? 'pending'} />
              <View style={styles.stepBody}>
                <Text variant="bodyStrong">
                  Approve {step.amount} {step.symbol}
                </Text>
                <Text variant="caption" color="text2" numberOfLines={2}>
                  {step.description}
                </Text>
                {progress.preStepHashes[i] ? (
                  <ExplorerLink hash={progress.preStepHashes[i]} />
                ) : null}
              </View>
              <Text variant="caption" color="text3">
                {i + 1}/{progress.step.total}
              </Text>
            </View>
          ))}
          <View style={styles.step}>
            <StepIcon state={states[states.length - 1] ?? 'pending'} />
            <View style={styles.stepBody}>
              <Text variant="bodyStrong">{actionLabel(unsigned.action)}</Text>
              <Text variant="caption" color="text2" numberOfLines={2}>
                {unsigned.description}
              </Text>
              {progress.txHash || progress.explorerUrl ? (
                <ExplorerLink
                  hash={progress.txHash ?? progress.status?.tx_hash ?? undefined}
                  url={progress.explorerUrl}
                  label={progress.txHash ? undefined : 'View on explorer'}
                />
              ) : null}
            </View>
            <Text variant="caption" color="text3">
              {progress.step.total}/{progress.step.total}
            </Text>
          </View>
        </View>
      ) : null}

      {/* Sunucu sonucu */}
      {progress.status?.status === 'confirmed' && progress.status.block_number ? (
        <Text variant="caption" color="text3">
          Block {progress.status.block_number}
          {progress.status.confirmations !== null
            ? ` · ${progress.status.confirmations} confirmations`
            : ''}
          {progress.status.agreement_status
            ? ` · agreement ${progress.status.agreement_status}`
            : ''}
        </Text>
      ) : null}

      {progress.phase === 'submitted' && !progress.status ? (
        <Text variant="caption" color="text2">
          The transaction is on-chain but the server could not be reached. It will be reported
          automatically the next time the app connects.
        </Text>
      ) : null}

      {/* Ağ ekleme parametreleri */}
      {chainParams ? (
        <View style={styles.params}>
          <Text variant="captionStrong" color="text2">
            Add Monad Testnet manually
          </Text>
          <Text variant="caption" color="text2" selectable>
            Network name: {chainParams.chainName}
          </Text>
          <Text variant="caption" color="text2" selectable>
            RPC URL: {chainParams.rpcUrls[0]}
          </Text>
          <Text variant="caption" color="text2" selectable>
            Chain ID: 10143 · Symbol: {chainParams.nativeCurrency.symbol}
          </Text>
          <Text variant="caption" color="text2" selectable>
            Explorer: {chainParams.blockExplorerUrls[0]}
          </Text>
        </View>
      ) : null}
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  phase: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceAlt,
  },
  phaseOk: { backgroundColor: colors.greenBg },
  phaseErr: { backgroundColor: colors.redBg },
  phaseWarn: { backgroundColor: colors.amberBg },
  phaseText: { flex: 1 },
  warn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.amberBg,
  },
  steps: { gap: spacing.md },
  step: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md },
  stepIcon: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 2,
  },
  stepBody: { flex: 1, gap: 2 },
  params: {
    gap: spacing.xs,
    padding: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
});
