/**
 * ABI yüzeyi. Frontend calldata ÜRETMEZ (backend üretir, K3); ABI yalnız
 *  (a) revert verisini ada çevirmek (`decodeVaultError`),
 *  (b) `TxProgressSheet`'te `decodeFunctionData` ile "ne imzalıyorum" satırını doğrulamak için.
 * `traderVault.ts` ve `erc20.ts` `contracts/scripts/export-abi.sh` çıktısıdır — elle düzenlenmez.
 * Kontrat adresleri `/config.contracts`'tan gelir; deploy JSON'u frontend'e kopyalanmaz.
 */
import { decodeErrorResult, decodeFunctionData, type Hex } from 'viem';

import { erc20Abi } from './erc20';
import { traderVaultAbi } from './traderVault';

export { traderVaultAbi, erc20Abi };

/** OZ v5 `IERC20Errors` — export edilen ERC-20 ABI'de yer almaz, revert decode için gerekir (02 §2.6 `erc20:*`). */
export const erc20ErrorsAbi = [
  {
    type: 'error',
    name: 'ERC20InsufficientBalance',
    inputs: [
      { name: 'sender', type: 'address' },
      { name: 'balance', type: 'uint256' },
      { name: 'needed', type: 'uint256' },
    ],
  },
  { type: 'error', name: 'ERC20InvalidSender', inputs: [{ name: 'sender', type: 'address' }] },
  { type: 'error', name: 'ERC20InvalidReceiver', inputs: [{ name: 'receiver', type: 'address' }] },
  {
    type: 'error',
    name: 'ERC20InsufficientAllowance',
    inputs: [
      { name: 'spender', type: 'address' },
      { name: 'allowance', type: 'uint256' },
      { name: 'needed', type: 'uint256' },
    ],
  },
  { type: 'error', name: 'ERC20InvalidApprover', inputs: [{ name: 'approver', type: 'address' }] },
  { type: 'error', name: 'ERC20InvalidSpender', inputs: [{ name: 'spender', type: 'address' }] },
] as const;

/** Vault + ERC-20 birleşik ABI — yalnız decode için. */
export const vaultAndErc20Abi = [...traderVaultAbi, ...erc20Abi, ...erc20ErrorsAbi] as const;

/**
 * Revert verisini (`0x` + 4 byte selector + args) kontrat/ERC-20 hata adına çevirir.
 * Tanınmayan selector için `null`; `Error(string)` → `{ name: 'Error', args: [reason] }`,
 * `Panic(uint256)` → `{ name: 'Panic', args: [code] }` (viem yerleşik).
 */
export function decodeVaultError(data: Hex): { name: string; args: unknown[] } | null {
  if (!data || data.length < 10) return null;
  try {
    const decoded = decodeErrorResult({ abi: vaultAndErc20Abi, data });
    return { name: decoded.errorName, args: [...(decoded.args ?? [])] as unknown[] };
  } catch {
    return null;
  }
}

/**
 * Calldata'daki fonksiyon adını döner (vault ya da ERC-20); çözülemezse `null`.
 * `UnsignedTxOut.action` ile karşılaştırılır (04 §6.7). Native transfer (`data === '0x'`) → `null`.
 */
export function decodeCalldataFunction(data: Hex): { name: string; args: unknown[] } | null {
  if (!data || data.length < 10) return null;
  try {
    const decoded = decodeFunctionData({ abi: vaultAndErc20Abi, data });
    return { name: decoded.functionName, args: [...(decoded.args ?? [])] as unknown[] };
  } catch {
    return null;
  }
}
