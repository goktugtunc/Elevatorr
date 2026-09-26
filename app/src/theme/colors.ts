/**
 * Renk paleti — Figma "Design System › Renk Paleti" (node 3:2) ile birebir.
 * Yeni renk eklemeden önce Figma'ya ekleyin; buradaki isimler Figma swatch isimleridir.
 */
export const colors = {
  // Marka & aksan
  navy900: '#0B1F3A',
  navy700: '#16305C',
  navy050: '#E9EDF3',
  profit: '#16A34A', // Kâr Yeşili
  loss: '#DC2626', // Zarar Kırmızısı
  amber: '#F59E0B', // Vurgu Amber

  // Durum arka planları
  greenBg: '#DCFCE7',
  redBg: '#FEE2E2',
  amberBg: '#FEF3C7',
  amberInk: '#92400E',

  // Nötr & yüzey
  bg: '#F3F5F9',
  surface: '#FFFFFF',
  surfaceAlt: '#F1F3F8',
  surfaceSunken: '#EAEDF3',
  border: '#E2E6ED',
  borderStrong: '#CDD3DE',
  text: '#0F172A',
  text2: '#5B6472',
  text3: '#94A0AF',
  onNavy: '#FFFFFF',
} as const;

export type ColorToken = keyof typeof colors;

/** K/Z gibi işaretli değerler için renk seçimi. */
export function pnlColor(value: number): string {
  if (value > 0) return colors.profit;
  if (value < 0) return colors.loss;
  return colors.text2;
}
