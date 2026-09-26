/* eslint-disable react-hooks/immutability -- Reanimated shared value'ları (x.value = …)
   React Compiler kuralının dışındadır (SwipeDeck ile aynı). */
import { ChevronsRight } from 'lucide-react-native';
import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, View, type LayoutChangeEvent } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, {
  interpolate,
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import { Text } from './Text';
import { colors, radius, spacing } from '@/theme';

/**
 * "Slide to confirm" (04 §6.7) — zincire giden aksiyonların onayı. Pan + reanimated;
 * eşik %85. Erişilebilirlik için topuza 600 ms basılı tutmak da onaylar (web'de fare ile çalışır).
 * `loading` bittiğinde topuz başa döner; `disabled` iken hareket etmez.
 */
export interface SlideToConfirmProps {
  label: string;
  onConfirm: () => void;
  disabled?: boolean;
  loading?: boolean;
}

const KNOB = 52;
const PAD = 4;
const THRESHOLD = 0.85;
const LONG_PRESS_MS = 600;

export function SlideToConfirm({ label, onConfirm, disabled, loading }: SlideToConfirmProps) {
  const [trackWidth, setTrackWidth] = useState(0);
  const maxX = Math.max(trackWidth - KNOB - PAD * 2, 1);
  const x = useSharedValue(0);
  const locked = !!disabled || !!loading;

  const onLayout = (e: LayoutChangeEvent) => setTrackWidth(e.nativeEvent.layout.width);

  const confirm = useCallback(() => {
    if (!locked) onConfirm();
  }, [locked, onConfirm]);

  useEffect(() => {
    if (!loading) x.value = withSpring(0, { damping: 18 });
  }, [loading, x]);

  const pan = Gesture.Pan()
    .enabled(!locked)
    .activeOffsetX([-8, 8])
    .onUpdate((e) => {
      x.value = Math.min(Math.max(e.translationX, 0), maxX);
    })
    .onEnd(() => {
      if (x.value >= maxX * THRESHOLD) {
        x.value = withTiming(maxX, { duration: 120 }, (finished) => {
          if (finished) runOnJS(confirm)();
        });
        return;
      }
      x.value = withSpring(0, { damping: 18 });
    });

  const longPress = Gesture.LongPress()
    .enabled(!locked)
    .minDuration(LONG_PRESS_MS)
    .onStart(() => {
      x.value = withTiming(maxX, { duration: 160 }, (finished) => {
        if (finished) runOnJS(confirm)();
      });
    });

  const gesture = Gesture.Race(pan, longPress);

  const knobStyle = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));
  const labelStyle = useAnimatedStyle(() => ({
    opacity: interpolate(x.value, [0, maxX * 0.6], [1, 0], 'clamp'),
  }));
  const fillStyle = useAnimatedStyle(() => ({ width: x.value + KNOB + PAD }));

  return (
    <View
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint="Slide right, or press and hold, to confirm"
      accessibilityState={{ disabled: locked, busy: !!loading }}
      onLayout={onLayout}
      style={[styles.track, locked && !loading && styles.disabled]}
    >
      <Animated.View style={[styles.fill, fillStyle]} />
      <Animated.View style={[styles.labelWrap, labelStyle]} pointerEvents="none">
        <Text variant="bodyStrong" color="onNavy" numberOfLines={1}>
          {label}
        </Text>
      </Animated.View>
      <GestureDetector gesture={gesture}>
        <Animated.View style={[styles.knob, knobStyle]}>
          {loading ? (
            <ActivityIndicator color={colors.navy900} />
          ) : (
            <ChevronsRight size={22} color={colors.navy900} />
          )}
        </Animated.View>
      </GestureDetector>
    </View>
  );
}

const styles = StyleSheet.create({
  track: {
    height: KNOB + PAD * 2,
    borderRadius: radius.full,
    backgroundColor: colors.navy900,
    padding: PAD,
    justifyContent: 'center',
    overflow: 'hidden',
  },
  disabled: { opacity: 0.45 },
  fill: {
    position: 'absolute',
    left: 0,
    top: 0,
    bottom: 0,
    backgroundColor: colors.navy700,
    borderRadius: radius.full,
  },
  labelWrap: {
    position: 'absolute',
    left: KNOB + PAD * 2,
    right: spacing.lg,
    alignItems: 'center',
  },
  knob: {
    width: KNOB,
    height: KNOB,
    borderRadius: KNOB / 2,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
