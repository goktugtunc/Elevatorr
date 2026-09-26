/**
 * Arayüz tarafındaki küçük ortak tipler.
 *
 * Alan modelinin tamamı artık sunucu şemasından geliyor: `src/lib/api/types.ts`
 * (OpenAPI ile birebir). Burada yalnızca sunucuda karşılığı olmayan ya da
 * gösterim için etiketlenen birkaç şey kalır.
 */
import type { AgreementStatus, UserRole } from '@/lib/api/types';

export type Role = UserRole;

/** Figma "Status Chip" — sözleşme (agreement) durumlarının arayüz etiketleri. */
export type ContractStatus = AgreementStatus;

export const STATUS_LABEL: Record<ContractStatus, string> = {
  draft: 'Draft',
  proposed: 'Proposed',
  funded: 'Funded',
  active: 'Active',
  settled: 'Settled',
  cancelled: 'Cancelled',
  failed: 'Failed',
};
