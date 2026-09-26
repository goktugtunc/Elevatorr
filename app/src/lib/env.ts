/**
 * Ortam değişkenleri — tek giriş noktası. Şablon: .env.example
 * Expo, EXPO_PUBLIC_* değişkenlerini derleme zamanında satır içine yazar;
 * bu yüzden process.env erişimleri statik (dinamik anahtar yok) olmalı.
 *
 * Kontrat adresleri env'de YOKTUR: `/config.contracts`'tan gelir (K3, 02 §4).
 * RPC ve explorer yalnızca `/config` alınamadığında kullanılan yedeklerdir.
 */
function optional(value: string | undefined, fallback = ''): string {
  return value && value.length > 0 ? value : fallback;
}

export const env = {
  apiBaseUrl: optional(process.env.EXPO_PUBLIC_API_BASE_URL, 'http://localhost:8013'),
  /** 10143 dışı bir değer uygulamayı açılışta durdurur (lib/chain/config.ts). */
  chainId: Number(optional(process.env.EXPO_PUBLIC_CHAIN_ID, '10143')),
  rpcUrl: optional(process.env.EXPO_PUBLIC_RPC_URL, 'https://testnet-rpc.monad.xyz'),
  explorerUrl: optional(process.env.EXPO_PUBLIC_EXPLORER_URL, 'https://testnet.monadvision.com'),
  walletConnectProjectId: optional(process.env.EXPO_PUBLIC_WALLETCONNECT_PROJECT_ID),
} as const;
