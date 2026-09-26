import { StyleSheet, View } from 'react-native';

import { Text } from './Text';
import { colors, fontFamily } from '@/theme';

/** Figma "Avatar" — baş harfler, doğrulama işareti yok. Boyutlar: 56 / 44 / 32. */
export type AvatarSize = 'lg' | 'md' | 'sm';

const SIZES: Record<AvatarSize, { box: number; font: number }> = {
  lg: { box: 56, font: 20 },
  md: { box: 44, font: 16 },
  sm: { box: 32, font: 12 },
};

export function Avatar({ initials, size = 'md' }: { initials: string; size?: AvatarSize }) {
  const s = SIZES[size];
  return (
    <View style={[styles.base, { width: s.box, height: s.box, borderRadius: s.box / 2 }]}>
      <Text
        style={{ fontFamily: fontFamily.bold, fontSize: s.font, lineHeight: s.font * 1.2 }}
        color={colors.navy900}
      >
        {initials.slice(0, 2).toUpperCase()}
      </Text>
    </View>
  );
}

/** "Kaan Demir" → "KD", "A. Yalçın" → "AY" */
export function initialsOf(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w.replace('.', '')[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

const styles = StyleSheet.create({
  base: {
    backgroundColor: colors.navy050,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
