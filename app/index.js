/**
 * Uygulamanın gerçek giriş noktası (package.json → "main").
 *
 * Neden var: `@noble/hashes/crypto.js` gibi kütüphaneler (viem/ox ve WalletConnect'in
 * bağımlılığı) `globalThis.crypto`'yu **import anında** yakalıyor
 * (`'crypto' in globalThis ? globalThis.crypto : undefined`).
 * Polyfill'ler `app/_layout.tsx` içinde kalırsa expo-router rota dosyalarını daha
 * önce değerlendirebiliyor ve o kütüphaneler `undefined` yakalayıp
 * "crypto.getRandomValues must be defined" hatası veriyor.
 *
 * Bu dosya polyfill'leri her şeyden önce çalıştırır, sonra expo-router'ı devreye alır.
 * Sıra kritiktir: aşağıdaki iki satırın yeri değiştirilmemeli.
 */
import './src/polyfills';

import 'expo-router/entry';
