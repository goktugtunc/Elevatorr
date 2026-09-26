/**
 * React kancası: build → executeUnsignedTx → react-query invalidation (04 §2.7).
 * `adapter` `@/lib/wallet` `wallet` singleton'ıdır. UI `components/tx/TxProgressSheet` `progress`'i çizer.
 */
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import { ChainError, toChainError } from './errors';
import { sameAddress, shortAddress } from './format';
import {
  INITIAL_TX_PROGRESS,
  TERMINAL_PHASES,
  TxAborted,
  executeUnsignedTx,
  type TxProgress,
} from './tx';
import { ApiError, txApi } from '@/lib/api';
import type { TxStatusOut, UnsignedTxOut } from '@/lib/api/types';
import { debugError } from '@/lib/log';
import { wallet } from '@/lib/wallet';
import { useSession } from '@/store/session';

export interface UseTxExecutorOptions {
  invalidate?: QueryKey[];
  onConfirmed?: (st: TxStatusOut) => void;
}

export interface RunOptions {
  /** build `409 use_reserved_action` verirse **bir kez** `details.action` ile yeniden build. */
  onUseReserved?: (action: string) => Promise<UnsignedTxOut>;
}

function apiErrorDetails(err: ApiError): Record<string, unknown> | undefined {
  const direct = (err as ApiError & { details?: unknown }).details;
  if (direct && typeof direct === 'object') return direct as Record<string, unknown>;
  const body = err.body as { details?: unknown } | undefined;
  if (body?.details && typeof body.details === 'object') return body.details as Record<string, unknown>;
  return undefined;
}

export function useTxExecutor(opts: UseTxExecutorOptions = {}) {
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<TxProgress>(INITIAL_TX_PROGRESS);
  const progressRef = useRef<TxProgress>(INITIAL_TX_PROGRESS);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const optsRef = useRef(opts);

  useEffect(() => {
    optsRef.current = opts;
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const update = useCallback((next: TxProgress) => {
    progressRef.current = next;
    if (mountedRef.current) setProgress(next);
  }, []);

  const invalidateAfter = useCallback(
    async (st: TxStatusOut, unsigned: UnsignedTxOut | null) => {
      const keys: QueryKey[] = [['agreements'], ['listings'], ['wallet'], ['dashboard']];
      const agreementId = st.agreement_id ?? unsigned?.agreement_id ?? null;
      const listingId = unsigned?.listing_id ?? null;
      if (agreementId) keys.push(['agreement', agreementId]);
      if (listingId) keys.push(['listing', listingId]);
      keys.push(['tx', st.pending_tx_id]);
      for (const k of optsRef.current.invalidate ?? []) keys.push(k);
      await Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
    },
    [queryClient],
  );

  const run = useCallback(
    async (
      build: () => Promise<UnsignedTxOut>,
      runOpts: RunOptions = {},
    ): Promise<TxStatusOut | null> => {
      if (!TERMINAL_PHASES.has(progressRef.current.phase)) return null; // aynı pending için ikinci kez çağrılmaz
      const controller = new AbortController();
      abortRef.current = controller;
      update({ ...INITIAL_TX_PROGRESS, phase: 'building' });

      let unsigned: UnsignedTxOut;
      try {
        try {
          unsigned = await build();
        } catch (err) {
          if (
            err instanceof ApiError &&
            err.status === 409 &&
            err.code === 'use_reserved_action' &&
            runOpts.onUseReserved
          ) {
            const action = apiErrorDetails(err)?.action;
            if (typeof action !== 'string') throw err;
            unsigned = await runOpts.onUseReserved(action);
          } else {
            throw err;
          }
        }

        const sessionAddress = useSession.getState().address;
        if (sessionAddress && !sameAddress(sessionAddress, unsigned.from_address)) {
          throw new ChainError(
            `Switch your wallet to ${shortAddress(unsigned.from_address)}.`,
            'WRONG_ACCOUNT',
          );
        }
      } catch (err) {
        const error = err instanceof ApiError ? err : toChainError(err);
        update({ ...INITIAL_TX_PROGRESS, phase: 'failed', error });
        return null;
      }

      try {
        const st = await executeUnsignedTx(unsigned, {
          adapter: wallet,
          signal: controller.signal,
          onProgress: update,
        });
        if (st.status === 'confirmed') {
          await invalidateAfter(st, unsigned).catch((e) =>
            debugError('chain:tx', 'invalidate başarısız', e),
          );
          optsRef.current.onConfirmed?.(st);
        }
        return st;
      } catch (err) {
        // executeUnsignedTx progress'i zaten son hâline getirdi (rejected/failed/expired/submitted).
        if (!(err instanceof TxAborted)) debugError('chain:tx', 'işlem başarısız', err);
        return null;
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [update, invalidateAfter],
  );

  /** timeout sonrası `GET /tx/{id}` tek sefer. */
  const recheck = useCallback(async () => {
    const current = progressRef.current;
    const id = current.unsigned?.pending_tx_id ?? current.status?.pending_tx_id;
    if (!id) return;
    try {
      const st = await txApi.status(id);
      const phase: TxProgress['phase'] =
        st.status === 'confirmed'
          ? 'confirmed'
          : st.status === 'failed'
            ? 'failed'
            : st.status === 'expired'
              ? 'expired'
              : current.phase === 'timeout'
                ? 'timeout'
                : 'submitted';
      const next: TxProgress = {
        ...current,
        phase,
        status: st,
        txHash: (st.tx_hash as TxProgress['txHash']) ?? current.txHash,
        explorerUrl: st.explorer_url ?? current.explorerUrl,
        error:
          st.status === 'failed'
            ? new ChainError(st.error_message || 'The transaction failed.', 'CONTRACT_REVERT', {
                errorName: st.error_code ?? undefined,
              })
            : null,
      };
      update(next);
      if (st.status === 'confirmed') {
        await invalidateAfter(st, current.unsigned).catch(() => undefined);
        optsRef.current.onConfirmed?.(st);
      }
    } catch (err) {
      update({ ...current, error: err instanceof ApiError ? err : toChainError(err) });
    }
  }, [update, invalidateAfter]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    update(INITIAL_TX_PROGRESS);
  }, [update]);

  const isBusy = !TERMINAL_PHASES.has(progress.phase);

  return { progress, run, recheck, reset, isBusy };
}
