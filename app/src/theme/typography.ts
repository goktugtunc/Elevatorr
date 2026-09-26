import type { TextStyle } from 'react-native';

/**
 * Tipografi — Figma "Tipografi — Inter, Tabular Numerik" (node 3:105).
 * Font aileleri @expo-google-fonts/inter ile yüklenir (bkz. app/_layout.tsx).
 */
export const fontFamily = {
  regular: 'Inter_400Regular',
  medium: 'Inter_500Medium',
  semiBold: 'Inter_600SemiBold',
  bold: 'Inter_700Bold',
  extraBold: 'Inter_800ExtraBold',
} as const;

const tabular: TextStyle = { fontVariant: ['tabular-nums'] };

export const typography = {
  /** Display / Extra Bold 28 */
  display: { fontFamily: fontFamily.extraBold, fontSize: 28, lineHeight: 34 } as TextStyle,
  /** Heading H1 / Bold 22 */
  h1: { fontFamily: fontFamily.bold, fontSize: 22, lineHeight: 27 } as TextStyle,
  /** Heading H2 / Semi Bold 18 */
  h2: { fontFamily: fontFamily.semiBold, fontSize: 18, lineHeight: 22 } as TextStyle,
  /** Body / Regular 15 */
  body: { fontFamily: fontFamily.regular, fontSize: 15, lineHeight: 20 } as TextStyle,
  /** Body vurgulu (Figma'da satır başlıkları) */
  bodyStrong: { fontFamily: fontFamily.semiBold, fontSize: 15, lineHeight: 20 } as TextStyle,
  /** Numeric / Bold 24 — para ve yüzdeler, tabular */
  numeric: { fontFamily: fontFamily.bold, fontSize: 24, lineHeight: 29, ...tabular } as TextStyle,
  /** Küçük numerik (kart istatistikleri) */
  numericSm: { fontFamily: fontFamily.bold, fontSize: 15, lineHeight: 20, ...tabular } as TextStyle,
  /** Caption / Regular 12 */
  caption: { fontFamily: fontFamily.regular, fontSize: 12, lineHeight: 15 } as TextStyle,
  /** Caption vurgulu (etiketler, chip metni) */
  captionStrong: { fontFamily: fontFamily.semiBold, fontSize: 12, lineHeight: 15 } as TextStyle,
  /** Section etiketi (ör. "RENK PALETİ") */
  overline: {
    fontFamily: fontFamily.semiBold,
    fontSize: 11,
    lineHeight: 14,
    letterSpacing: 0.6,
    textTransform: 'uppercase',
  } as TextStyle,
  /** Tab bar etiketi */
  tab: { fontFamily: fontFamily.medium, fontSize: 11, lineHeight: 13 } as TextStyle,
} as const;

export type TypographyToken = keyof typeof typography;
