import { useEffect, useState } from 'react';
import { Animated, Pressable, StyleSheet, View } from 'react-native';

import { Text } from './Text';
import { colors, radius, spacing } from '@/theme';

/**
 * Figma "Switch" (FE-07) — ör. "Yatırımcılarıma bildirim gönder" (Yeni İşlem sheet).
 * Etiket verilirse satır hâlinde çizilir ve etikete dokunmak da değeri değiştirir.
 */
export interface SwitchProps {
  value: boolean;
  onValueChange: (value: boolean) => void;
  label?: string;
  hint?: string;
  disabled?: boolean;
}

const TRACK_W = 44;
const TRACK_H = 26;
const THUMB = 20;

export function Switch({ value, onValueChange, label, hint, disabled }: SwitchProps) {
  const [progress] = useState(() => new Animated.Value(value ? 1 : 0));

  useEffect(() => {
    Animated.timing(progress, {
      toValue: value ? 1 : 0,
      duration: 140,
      useNativeDriver: false,
    }).start();
  }, [value, progress]);

  const translateX = progress.interpolate({
    inputRange: [0, 1],
    outputRange: [3, TRACK_W - THUMB - 3],
  });
  const backgroundColor = progress.interpolate({
    inputRange: [0, 1],
    outputRange: [colors.borderStrong, colors.navy900],
  });

  const toggle = () => {
    if (!disabled) onValueChange(!value);
  };

  const track = (
    <Animated.View style={[styles.track, { backgroundColor }]}>
      <Animated.View style={[styles.thumb, { transform: [{ translateX }] }]} />
    </Animated.View>
  );

  if (!label) {
    return (
      <Pressable
        accessibilityRole="switch"
        accessibilityState={{ checked: value, disabled }}
        onPress={toggle}
        disabled={disabled}
        style={disabled && styles.disabled}
      >
        {track}
      </Pressable>
    );
  }

  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityState={{ checked: value, disabled }}
      accessibilityLabel={label}
      onPress={toggle}
      disabled={disabled}
      style={[styles.row, disabled && styles.disabled]}
    >
      <View style={styles.rowText}>
        <Text variant="bodyStrong">{label}</Text>
        {hint ? (
          <Text variant="caption" color="text2">
            {hint}
          </Text>
        ) : null}
      </View>
      {track}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  rowText: { flex: 1, gap: 2 },
  track: {
    width: TRACK_W,
    height: TRACK_H,
    borderRadius: radius.full,
    justifyContent: 'center',
  },
  thumb: {
    width: THUMB,
    height: THUMB,
    borderRadius: THUMB / 2,
    backgroundColor: colors.surface,
  },
  disabled: { opacity: 0.45 },
});
