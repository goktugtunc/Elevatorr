/** Boşluk ölçeği — Figma "Boşluk & Radius" (node 3:143). */
export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  '2xl': 24,
  '3xl': 32,
  '4xl': 40,
} as const;

/** Köşe yarıçapları — sm 8, md 12, lg 16, full 999. */
export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  full: 999,
} as const;

/** Ekran sabitleri (Figma frame: 390 × 844). */
export const layout = {
  screenPaddingH: spacing.lg,
  maxContentWidth: 480, // Expo Web'de mobil çerçeve genişliği
  tabBarHeight: 62,
  riskStripHeight: 28,
  topBarHeight: 52,
} as const;

export const shadow = {
  card: {
    shadowColor: '#0B1F3A',
    shadowOpacity: 0.06,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
  raised: {
    shadowColor: '#0B1F3A',
    shadowOpacity: 0.18,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 6,
  },
} as const;
