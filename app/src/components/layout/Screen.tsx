import { StatusBar } from 'expo-status-bar';
import { ScrollView, StyleSheet, View, type ViewStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { RiskStrip } from './RiskStrip';
import { colors, layout } from '@/theme';

/**
 * Tüm ekranların kök sarmalayıcısı.
 * - Safe area (üst) + arka plan rengi
 * - Expo Web'de içeriği 480px mobil çerçeveye ortalar
 * - Altta Risk Strip (Figma'da her ekranda var)
 * Tab ekranlarında tab bar Risk Strip'in üstünde yer alır (bkz. TabBar).
 */
export interface ScreenProps {
  children: React.ReactNode;
  /** true → ScrollView; false → düz View (kaydırmalı deck gibi ekranlar için). */
  scroll?: boolean;
  padded?: boolean;
  /** Tab ekranlarında Risk Strip'i TabBar çiziyor; burada kapatın. */
  riskStrip?: boolean;
  contentStyle?: ViewStyle;
}

export function Screen({
  children,
  scroll = true,
  padded = true,
  riskStrip = true,
  contentStyle,
}: ScreenProps) {
  const insets = useSafeAreaInsets();
  const body = scroll ? (
    <ScrollView
      style={styles.flex}
      contentContainerStyle={[padded && styles.padded, contentStyle]}
      keyboardShouldPersistTaps="handled"
    >
      {children}
    </ScrollView>
  ) : (
    <View style={[styles.flex, padded && styles.padded, contentStyle]}>{children}</View>
  );

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      <StatusBar style="dark" />
      <View style={styles.frame}>
        {body}
        {riskStrip ? <RiskStrip /> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  frame: {
    flex: 1,
    width: '100%',
    maxWidth: layout.maxContentWidth,
    alignSelf: 'center',
  },
  flex: { flex: 1 },
  padded: { paddingHorizontal: layout.screenPaddingH, paddingBottom: 24 },
});
