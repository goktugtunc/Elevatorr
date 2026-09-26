import { Text as RNText, type TextProps as RNTextProps, StyleSheet } from 'react-native';

import { colors, typography, type ColorToken, type TypographyToken } from '@/theme';

export interface TextProps extends RNTextProps {
  variant?: TypographyToken;
  color?: ColorToken | (string & {});
  align?: 'left' | 'center' | 'right';
}

function resolveColor(color: TextProps['color']): string {
  if (!color) return colors.text;
  return color in colors ? colors[color as ColorToken] : color;
}

/** Tema tipografisiyle bağlı Text. Varsayılan: body / colors.text. */
export function Text({ variant = 'body', color, align, style, ...rest }: TextProps) {
  return (
    <RNText
      {...rest}
      style={[
        typography[variant],
        { color: resolveColor(color) },
        align ? { textAlign: align } : null,
        styles.base,
        style,
      ]}
    />
  );
}

const styles = StyleSheet.create({
  base: { includeFontPadding: false },
});
