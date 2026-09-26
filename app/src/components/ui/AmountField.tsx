import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View, type TextInputProps } from 'react-native';

import { Text } from './Text';
import { parseAmountInput } from '@/lib/chain';
import { colors, fontFamily, radius, spacing } from '@/theme';

/**
 * Token tutarı alanı (04 §6.7): `Field` görünümü + `decimals`/`symbol`. Doğrulama
 * `parseAmountInput` ile yapılır (sunucudaki `too_many_decimals` kuralıyla aynı); `max`
 * verilirse sağda `Max` düğmesi görünür. Tutar **string** olarak taşınır (K12).
 */
export interface AmountFieldProps extends Omit<
  TextInputProps,
  'style' | 'value' | 'onChangeText' | 'keyboardType'
> {
  label: string;
  value: string;
  onChangeText: (value: string) => void;
  decimals: number;
  symbol?: string;
  /** İnsan okunur üst sınır (ör. bakiye). Verilirse `Max` düğmesi çıkar. */
  max?: string | null;
  /** Dış hata; verilmezse alan kendi biçim hatasını gösterir. */
  error?: string;
  hint?: string;
}

/** Boş değilse ve geçersizse hata metni; aksi hâlde undefined. */
export function amountInputError(value: string, decimals: number): string | undefined {
  if (!value.trim()) return undefined;
  const parsed = parseAmountInput(value, decimals);
  if (!parsed) return `Enter a valid amount (up to ${decimals} decimals).`;
  if (parsed.raw <= 0n) return 'Enter an amount greater than zero.';
  return undefined;
}

export function AmountField({
  label,
  value,
  onChangeText,
  decimals,
  symbol,
  max,
  error,
  hint,
  editable = true,
  ...input
}: AmountFieldProps) {
  const [focused, setFocused] = useState(false);
  const shownError = error ?? amountInputError(value, decimals);
  const borderColor = shownError ? colors.loss : focused ? colors.navy700 : colors.border;

  return (
    <View style={styles.wrap}>
      <View style={styles.labelRow}>
        <Text variant="captionStrong" color="text2" style={styles.label}>
          {label}
        </Text>
        {max ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Use maximum ${max}${symbol ? ` ${symbol}` : ''}`}
            onPress={() => editable && onChangeText(max)}
            hitSlop={6}
            disabled={!editable}
          >
            <Text variant="captionStrong" color="navy700">
              Max
            </Text>
          </Pressable>
        ) : null}
      </View>
      <View style={[styles.inputWrap, { borderColor }, !editable && styles.readonly]}>
        <TextInput
          {...input}
          value={value}
          onChangeText={onChangeText}
          editable={editable}
          keyboardType="decimal-pad"
          inputMode="decimal"
          autoCapitalize="none"
          autoCorrect={false}
          placeholderTextColor={colors.text3}
          onFocus={(e) => {
            setFocused(true);
            input.onFocus?.(e);
          }}
          onBlur={(e) => {
            setFocused(false);
            input.onBlur?.(e);
          }}
          style={styles.input}
        />
        {symbol ? (
          <Text variant="body" color="text3">
            {symbol}
          </Text>
        ) : null}
      </View>
      {shownError ? (
        <Text variant="caption" color="loss">
          {shownError}
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
  labelRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  label: { flex: 1 },
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
  readonly: { backgroundColor: colors.surfaceAlt },
  input: {
    flex: 1,
    fontFamily: fontFamily.regular,
    fontSize: 15,
    color: colors.text,
    paddingVertical: 0,
  },
});
