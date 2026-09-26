import { Linking } from 'react-native';

import { ChainError } from '@/lib/chain/errors';
import { debugError, debugLog } from '@/lib/log';

/**
 * WalletConnect eşleşme URI'sini cihazdaki EVM cüzdan uygulamasında açmak (FE-33).
 *
 * Ham `wc:` URI'si çoğu cüzdanda kayıtlı değildir; her cüzdan kendi şemasını
 * kaydeder. Şemalar WalletConnect kayıt defterinden alındı
 * (`explorer-api.walletconnect.com/v3/wallets`):
 *
 *   MetaMask → metamask:// + https://metamask.app.link
 *   Rainbow  → rainbow://  + https://rnbwapp.com
 *   Trust    → trust://    + https://link.trustwallet.com
 *   Phantom  → phantom://  + https://phantom.app/ul   (EVM şeması kayıt defterinden DOĞRULANMADI;
 *              `verified: false`, en sonda denenir — Gün 0'da
 *              `curl "https://explorer-api.walletconnect.com/v3/wallets?projectId=$WC_PROJECT_ID&search=phantom"`)
 *
 * Android 11+ `canOpenURL` yalnızca manifest'te tanımlı şemalar için doğru sonuç
 * verir; Expo Go'nun manifest'i bizim şemalarımızı bilmez. Bu yüzden sorgulamak
 * yerine sırayla açmayı deniyoruz ve ilk tutanı kullanıyoruz.
 */
export interface WalletLinkTarget {
  id: string;
  label: string;
  native?: string;
  universal?: string;
  /** Şema WalletConnect kayıt defterinden mi geldi? */
  verified: boolean;
}

export const WC_WALLETS: WalletLinkTarget[] = [
  {
    id: 'metamask',
    label: 'MetaMask',
    native: 'metamask://',
    universal: 'https://metamask.app.link',
    verified: true,
  },
  {
    id: 'rainbow',
    label: 'Rainbow',
    native: 'rainbow://',
    universal: 'https://rnbwapp.com',
    verified: true,
  },
  {
    id: 'trust',
    label: 'Trust Wallet',
    native: 'trust://',
    universal: 'https://link.trustwallet.com',
    verified: true,
  },
  {
    id: 'phantom',
    label: 'Phantom',
    native: 'phantom://',
    universal: 'https://phantom.app/ul',
    verified: false,
  },
];

function withSlash(value: string): string {
  return value.endsWith('/') ? value : `${value}/`;
}

/** Aynı cüzdan için denenecek bağlantı biçimleri — yaygın olandan sıra dışına. */
export function pairingLinks(target: WalletLinkTarget, wcUri: string): string[] {
  const encoded = encodeURIComponent(wcUri);
  const links: string[] = [];

  if (target.universal) {
    links.push(`${withSlash(target.universal)}wc?uri=${encoded}`);
    links.push(`${target.universal}?uri=${encoded}`);
  }

  if (target.native) {
    // Web3Modal/AppKit'in ürettiği biçim: <scheme>[/path]/wc?uri=…
    links.push(`${withSlash(target.native)}wc?uri=${encoded}`);
    links.push(`${target.native}?uri=${encoded}`);
    // Kök şema
    const root = `${target.native.split('://')[0]}://`;
    links.push(`${root}wc?uri=${encoded}`);
  }

  // Son çare: ham wc: URI'si (bazı cihazlarda uygulama seçici açılır)
  links.push(wcUri);
  return links;
}

/**
 * Cüzdanı açmayı sırayla dener. Açılan bağlantıyı döner; hiçbiri tutmazsa
 * kullanıcıya gösterilebilecek bir hata fırlatır.
 */
export async function openPairing(target: WalletLinkTarget, wcUri: string): Promise<string> {
  const links = pairingLinks(target, wcUri);
  debugLog('wallet:deeplink', `${target.label} için ${links.length} biçim denenecek`, {
    scheme: target.native ?? target.universal,
    verified: target.verified,
  });
  for (const [i, link] of links.entries()) {
    try {
      await Linking.openURL(link);
      debugLog('wallet:deeplink', `${target.label} AÇILDI (biçim ${i + 1}/${links.length})`, {
        link: link.split('?')[0],
      });
      return link;
    } catch (err) {
      debugError(
        'wallet:deeplink',
        `biçim ${i + 1}/${links.length} reddedildi: ${link.split('?')[0]}`,
        err,
      );
    }
  }
  debugError('wallet:deeplink', `${target.label} hiçbir biçimle açılamadı`, new Error('no handler'));
  throw new ChainError(
    `Could not open ${target.label}. Is it installed? If not, scan the QR code with a wallet on another device.`,
    'NOT_AVAILABLE',
  );
}
