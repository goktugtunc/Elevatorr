/**
 * İmzasız işlem yürütücüsü (FE-37, 02 §2.7, 04 §2.6).
 *
 * backend `UnsignedTxOut {to, data, value, gas, chain_id, pre_steps, …}` →
 * cüzdan `eth_sendTransaction` (pre_steps sırayla, sonra ana işlem) →
 * `POST /tx/submit {pending_tx_id, tx_hash}` → `GET /tx/{id}` poll.
 *
 * Hash alındığı anda `tk.txOutbox`'a yazılır; submit ağ hatasıyla düşerse
 * `flushTxOutbox()` sonraki açılışta tamamlar (zincire gitmiş işlem kaybolmaz).
 */
import type { Hex } from 'viem';

import { waitForReceipt } from './client';
import { explorerTxUrl } from './config';
import { ChainError, toChainError, vaultErrorCopy } from './errors';
import { sameAddress, shortAddress } from './format';
import { ApiError, txApi } from '@/lib/api';
import type { TxStatusOut, UnsignedTxOut } from '@/lib/api/types';
import { debugError, debugLog } from '@/lib/log';
import { STORAGE_KEYS, plainStorage } from '@/lib/storage';
import type { WalletAdapter } from '@/lib/wallet/types';

export type TxPhase =
  | 'idle'
  | 'building'
  | 'checking_wallet'
  | 'switching_chain'
  | 'approving' // pre_steps[i] cüzdanda / receipt bekleniyor
  | 'awaiting_signature' // ana işlem cüzdanda
  | 'submitting' // POST /tx/submit
  | 'submitted' // hash bildirildi, receipt yok (TxStatusOut.status = submitted)
  | 'confirmed'
  | 'failed'
  | 'rejected'
  | 'expired'
  | 'timeout';

export interface TxProgress {
  phase: TxPhase;
  step: { index: number; total: number }; // total = pre_steps.length + 1
  unsigned: UnsignedTxOut | null;
  txHash: Hex | null; // ana işlem
  preStepHashes: Hex[];
  status: TxStatusOut | null; // son /tx yanıtı
  explorerUrl: string | null; // status.explorer_url ?? explorerTxUrl(txHash)
  error: ChainError | ApiError | null;
}

export interface ExecuteOptions {
  adapter: WalletAdapter;
  onProgress?: (p: TxProgress) => void;
  /** Yalnız polling'i keser; zincire gitmiş işlem geri alınamaz. */
  signal?: AbortSignal;
  pollIntervalMs?: number; // 2_000
  /** 180_000 → phase 'timeout' (işlem hâlâ bekliyor olabilir; UI "Check again" verir). */
  pollTimeoutMs?: number;
  receiptTimeoutMs?: number; // 60_000 (pre_steps için)
}

/** Yürütücünün bittiği ama sonucu `TxStatusOut` olmayan durumları taşır (UI `progress` üzerinden okur). */
export class TxAborted extends Error {
  constructor(public readonly progress: TxProgress) {
    super(`Transaction ${progress.phase}`);
    this.name = 'TxAborted';
  }
}

export const INITIAL_TX_PROGRESS: TxProgress = {
  phase: 'idle',
  step: { index: 0, total: 1 },
  unsigned: null,
  txHash: null,
  preStepHashes: [],
  status: null,
  explorerUrl: null,
  error: null,
};

export const TERMINAL_PHASES: ReadonlySet<TxPhase> = new Set([
  'idle',
  'confirmed',
  'failed',
  'rejected',
  'expired',
  'timeout',
]);

const SUBMIT_RETRY_DELAYS_MS = [1_000, 3_000, 9_000];

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(done, ms);
    function done() {
      clearTimeout(t);
      signal?.removeEventListener('abort', done);
      resolve();
    }
    signal?.addEventListener('abort', done, { once: true });
  });
}

function toBigInt(value: string | null | undefined, fallback = 0n): bigint {
  if (value === null || value === undefined || value === '') return fallback;
  try {
    return BigInt(value);
  } catch {
    return fallback;
  }
}

function explorerFor(status: TxStatusOut | null, hash: Hex | null): string | null {
  if (status?.explorer_url) return status.explorer_url;
  return hash ? explorerTxUrl(hash) : null;
}

/** Sunucu `failed` yanıtındaki kodu okunur metne çevirir. */
export function txStatusErrorMessage(st: TxStatusOut): string {
  const code = st.error_code ?? st.contract_error_code ?? null;
  if (code && typeof code === 'string') return vaultErrorCopy(code);
  return st.error_message || 'The transaction failed.';
}

// ---------------------------------------------------------------------------
// Outbox: cüzdana gönderilmiş ama sunucuya bildirilememiş hash'ler
// ---------------------------------------------------------------------------

interface OutboxEntry {
  pending_tx_id: string;
  tx_hash: Hex;
  at: number;
}

async function readOutbox(): Promise<OutboxEntry[]> {
  try {
    const raw = await plainStorage.get(STORAGE_KEYS.txOutbox);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is OutboxEntry =>
        !!e &&
        typeof e === 'object' &&
        typeof (e as OutboxEntry).pending_tx_id === 'string' &&
        typeof (e as OutboxEntry).tx_hash === 'string',
    );
  } catch {
    return [];
  }
}

async function writeOutbox(entries: OutboxEntry[]): Promise<void> {
  try {
    if (entries.length === 0) await plainStorage.remove(STORAGE_KEYS.txOutbox);
    else await plainStorage.set(STORAGE_KEYS.txOutbox, JSON.stringify(entries));
  } catch (err) {
    debugError('chain:tx', 'outbox yazılamadı', err);
  }
}

async function outboxAdd(entry: OutboxEntry): Promise<void> {
  const entries = (await readOutbox()).filter((e) => e.pending_tx_id !== entry.pending_tx_id);
  entries.push(entry);
  await writeOutbox(entries);
}

async function outboxRemove(pendingTxId: string): Promise<void> {
  const entries = await readOutbox();
  const next = entries.filter((e) => e.pending_tx_id !== pendingTxId);
  if (next.length !== entries.length) await writeOutbox(next);
}

/** Sunucuya ulaşılamadı / 5xx → tekrar denenir; diğer HTTP hataları kesindir. */
function isRetryableApiError(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 0 || err.status >= 500);
}

let flushInFlight: Promise<void> | null = null;

/** Hydrate'te ve her yeni submit'ten önce çağrılır; eşzamanlı çağrılar tek koşuya biner. */
export function flushTxOutbox(): Promise<void> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = (async () => {
    const entries = await readOutbox();
    if (entries.length === 0) return;
    debugLog('chain:tx', `outbox: ${entries.length} bekleyen bildirim`);
    for (const entry of entries) {
      try {
        await txApi.submit(entry.pending_tx_id, entry.tx_hash);
        await outboxRemove(entry.pending_tx_id);
      } catch (err) {
        if (isRetryableApiError(err)) {
          debugError('chain:tx', 'outbox: sunucu yok, sonra denenecek', err);
          continue;
        }
        // 409 tx_hash_conflict / pending_tx_expired / 404 vb.: sunucu tarafı kesin — outbox'tan düş.
        debugError('chain:tx', 'outbox: kalıcı hata, kayıt siliniyor', err);
        await outboxRemove(entry.pending_tx_id);
      }
    }
  })().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}

// ---------------------------------------------------------------------------
// Yürütücü
// ---------------------------------------------------------------------------

/**
 * Adımlar (sırası ZORUNLU, 04 §2.6): expires_at → cüzdan adresi → zincir → pre_steps →
 * ana işlem → outbox → submit (3 deneme) → poll.
 *
 * Döner: son `TxStatusOut` (confirmed/failed/expired ya da timeout'ta son hâl).
 * Fırlatır: `ChainError` (rejected/failed yolları), `ApiError` (submit kesin hatası),
 * `TxAborted` (signal ile kesildi). Her durumda `onProgress` son hâli almıştır.
 */
export async function executeUnsignedTx(
  unsigned: UnsignedTxOut,
  opts: ExecuteOptions,
): Promise<TxStatusOut> {
  const {
    adapter,
    signal,
    pollIntervalMs = 2_000,
    pollTimeoutMs = 180_000,
    receiptTimeoutMs = 60_000,
  } = opts;

  const preSteps = unsigned.pre_steps ?? [];
  const total = preSteps.length + 1;
  let progress: TxProgress = {
    ...INITIAL_TX_PROGRESS,
    unsigned,
    step: { index: 0, total },
  };
  const emit = (patch: Partial<TxProgress>) => {
    progress = { ...progress, ...patch };
    progress.explorerUrl = explorerFor(progress.status, progress.txHash);
    opts.onProgress?.(progress);
  };

  const fail = (err: unknown): never => {
    const mapped = err instanceof ApiError ? err : toChainError(err);
    const phase: TxPhase =
      mapped instanceof ChainError && mapped.code === 'USER_REJECTED'
        ? 'rejected'
        : mapped instanceof ChainError && mapped.code === 'EXPIRED'
          ? 'expired'
          : 'failed';
    emit({ phase, error: mapped });
    throw mapped;
  };

  try {
    // 1. Süre
    if (Date.parse(unsigned.expires_at) <= Date.now()) {
      throw new ChainError('This transaction request expired. Build it again.', 'EXPIRED');
    }

    // 2. Cüzdan adresi
    emit({ phase: 'checking_wallet' });
    const address = await adapter.getAddress();
    if (!address) throw new ChainError('Wallet is not connected.', 'NOT_CONNECTED');
    if (!sameAddress(address, unsigned.from_address)) {
      throw new ChainError(
        `Switch your wallet to ${shortAddress(unsigned.from_address)}.`,
        'WRONG_ACCOUNT',
      );
    }

    // 3. Zincir
    emit({ phase: 'switching_chain' });
    const chainId = await adapter.getChainId();
    if (chainId !== unsigned.chain_id) {
      await adapter.switchChain(unsigned.chain_id);
    }

    // 4. pre_steps (approve) — her biri ayrı işlem, receipt beklenir
    const preStepHashes: Hex[] = [];
    for (const [i, step] of preSteps.entries()) {
      emit({ phase: 'approving', step: { index: i + 1, total } });
      const hash = await adapter.sendTransaction({
        to: step.to as `0x${string}`,
        data: step.data as Hex,
        value: toBigInt(step.value),
        gas: step.gas ? toBigInt(step.gas) : undefined,
        from: unsigned.from_address as `0x${string}`,
      });
      preStepHashes.push(hash);
      emit({ preStepHashes: [...preStepHashes] });
      await waitForReceipt(hash, { timeoutMs: receiptTimeoutMs });
    }

    // 5. Ana işlem
    emit({ phase: 'awaiting_signature', step: { index: total, total } });
    const txHash = await adapter.sendTransaction({
      to: unsigned.to as `0x${string}`,
      data: unsigned.data as Hex,
      value: toBigInt(unsigned.value),
      gas: unsigned.gas ? toBigInt(unsigned.gas) : undefined,
      from: unsigned.from_address as `0x${string}`,
    });
    emit({ txHash });

    // 6. Outbox (submit kaybolmasın)
    await outboxAdd({ pending_tx_id: unsigned.pending_tx_id, tx_hash: txHash, at: Date.now() });

    // 7. Submit — ağ/5xx için 3 deneme
    emit({ phase: 'submitting' });
    let st: TxStatusOut | null = null;
    for (let attempt = 0; attempt <= SUBMIT_RETRY_DELAYS_MS.length; attempt++) {
      try {
        st = await txApi.submit(unsigned.pending_tx_id, txHash);
        break;
      } catch (err) {
        if (!isRetryableApiError(err)) {
          // 409 tx_hash_conflict vb.: sunucu kesin cevap verdi → outbox'tan sil, hatayı göster.
          await outboxRemove(unsigned.pending_tx_id);
          throw err;
        }
        if (attempt === SUBMIT_RETRY_DELAYS_MS.length) break;
        debugError('chain:tx', `submit denemesi ${attempt + 1} başarısız`, err);
        await sleep(SUBMIT_RETRY_DELAYS_MS[attempt], signal);
        if (signal?.aborted) break;
      }
    }
    if (!st) {
      // Sunucuya ulaşılamadı: hash zincirde, outbox tamamlayacak. Hata değil; "Sent — the server will pick it up".
      emit({ phase: 'submitted' });
      throw new TxAborted(progress);
    }
    await outboxRemove(unsigned.pending_tx_id);
    emit({ status: st, phase: st.status === 'confirmed' ? 'confirmed' : 'submitted' });

    // 8. Poll
    const deadline = Date.now() + pollTimeoutMs;
    while (st.status === 'pending' || st.status === 'submitted') {
      if (signal?.aborted) throw new TxAborted(progress);
      if (Date.now() >= deadline) {
        emit({ phase: 'timeout', status: st });
        return st;
      }
      await sleep(pollIntervalMs, signal);
      if (signal?.aborted) throw new TxAborted(progress);
      try {
        st = await txApi.status(unsigned.pending_tx_id);
        emit({ status: st });
      } catch (err) {
        if (!isRetryableApiError(err)) throw err;
        debugError('chain:tx', 'poll geçici hata', err);
      }
    }

    // 9. Sonuç
    if (st.status === 'confirmed') {
      emit({ phase: 'confirmed', status: st });
      return st;
    }
    if (st.status === 'expired') {
      emit({
        phase: 'expired',
        status: st,
        error: new ChainError('This transaction request expired. Build it again.', 'EXPIRED'),
      });
      return st;
    }
    // failed
    const errorName = st.error_code ?? undefined;
    emit({
      phase: 'failed',
      status: st,
      error: new ChainError(txStatusErrorMessage(st), 'CONTRACT_REVERT', {
        errorName: errorName ?? undefined,
        hash: (st.tx_hash as Hex | null) ?? txHash,
      }),
    });
    return st;
  } catch (err) {
    if (err instanceof TxAborted) throw err;
    return fail(err);
  }
}
