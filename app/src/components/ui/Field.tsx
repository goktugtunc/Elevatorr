import { useState } from 'react';
import { StyleSheet, TextInput, View, type TextInputProps } from 'react-native';

import { Text } from './Text';
import { colors, fontFamily, radius, spacing } from '@/theme';

/** Figma "Field" + "Input" — etiket, giriş alanı, hata metni. */
export interface FieldProps extends Omit<TextInputProps, 'style'> {
  label: string;
  error?: string;
  hint?: string;
  suffix?: string; // ör. "%" veya "TL"
  multiline?: boolean;
}

export function Field({ label, error, hint, suffix, multiline, ...input }: FieldProps) {
  const [focused, setFocused] = useState(false);
  const borderColor = error ? colors.loss : focused ? colors.navy700 : colors.border;
  return (
    <View style={styles.wrap}>
      <Text variant="captionStrong" color="text2">
        {label}
      </Text>
      <View style={[styles.inputWrap, { borderColor }, multiline && styles.multiline]}>
        <TextInput
          {...input}
          multiline={multiline}
          placeholderTextColor={colors.text3}
          onFocus={(e) => {
            setFocused(true);
            input.onFocus?.(e);
          }}
          onBlur={(e) => {
            setFocused(false);
            input.onBlur?.(e);
          }}
          style={[styles.input, multiline && styles.inputMultiline]}
        />
        {suffix ? (
          <Text variant="body" color="text3">
            {suffix}
          </Text>
        ) : null}
      </View>
      {error ? (
        <Text variant="caption" color="loss">
          {error}
        </Text>
      ) : hint ? (
        <Text variant="caption" color="text3">
          {hint}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.sm - 2 },
  inputWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 48,
    borderWidth: 1,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
    paddingHorizontal: spacing.md,
    gap: spacing.sm,
  },
  multiline: { minHeight: 96, alignItems: 'flex-start', paddingVertical: spacing.md },
  input: {
    flex: 1,
    fontFamily: fontFamily.regular,
    fontSize: 15,
    color: colors.text,
    paddingVertical: 0,
  },
  inputMultiline: { textAlignVertical: 'top', minHeight: 72 },
});
