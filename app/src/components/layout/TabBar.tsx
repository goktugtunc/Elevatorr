import type { BottomTabBarProps } from 'expo-router/js-tabs';
import {
  ArrowLeftRight,
  Briefcase,
  Compass,
  LayoutGrid,
  TrendingUp,
  User,
  type LucideIcon,
} from 'lucide-react-native';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { RiskStrip } from './RiskStrip';
import { Text } from '@/components/ui';
import { colors, layout, shadow, spacing } from '@/theme';

/**
 * Figma "Tab Bar/Müşteri" ve "Tab Bar/Trader" (arayüz metinleri İngilizce).
 * Ortadaki Keşfet sekmesi yükseltilmiş yuvarlak buton olarak çizilir.
 * Rol farkı yalnızca 2. sekmede: Müşteri → Hareketler, Trader → İşlemler.
 */
const ICONS: Record<string, LucideIcon> = {
  dashboard: LayoutGrid,
  activity: TrendingUp,
  trades: ArrowLeftRight,
  discover: Compass,
  listings: Briefcase,
  profile: User,
};

const LABELS: Record<string, string> = {
  dashboard: 'Dashboard',
  activity: 'Activity',
  trades: 'Trades',
  discover: 'Discover',
  listings: 'Listings',
  profile: 'Profile',
};

export function TabBar({ state, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  return (
    <View style={styles.wrap}>
      <View style={[styles.bar, { paddingBottom: Math.max(insets.bottom, spacing.sm) }]}>
        {state.routes.map((route, index) => {
          const focused = state.index === index;
          const Icon = ICONS[route.name] ?? LayoutGrid;
          const label = LABELS[route.name] ?? route.name;
          const isCenter = route.name === 'discover';
          const onPress = () => {
            const event = navigation.emit({
              type: 'tabPress',
              target: route.key,
              canPreventDefault: true,
            });
            if (!focused && !event.defaultPrevented) navigation.navigate(route.name);
          };
          const tint = focused ? colors.navy900 : colors.text3;
          return (
            <Pressable
              key={route.key}
              accessibilityRole="tab"
              accessibilityState={{ selected: focused }}
              accessibilityLabel={label}
              onPress={onPress}
              style={styles.tab}
            >
              {isCenter ? (
                <View style={[styles.centerButton, shadow.raised]}>
                  <Icon size={24} color={colors.onNavy} strokeWidth={2.2} />
                </View>
              ) : (
                <Icon size={21} color={tint} strokeWidth={focused ? 2.2 : 1.8} />
              )}
              <Text variant="tab" color={tint} style={isCenter && styles.centerLabel}>
                {label}
              </Text>
            </Pressable>
          );
        })}
      </View>
      <RiskStrip />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    width: '100%',
    maxWidth: layout.maxContentWidth,
    alignSelf: 'center',
    backgroundColor: colors.surface,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  bar: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    paddingTop: spacing.sm - 2,
    paddingHorizontal: spacing.xs,
  },
  tab: { flex: 1, alignItems: 'center', gap: spacing.xs, paddingBottom: 2 },
  centerButton: {
    width: 46,
    height: 46,
    borderRadius: 23,
    backgroundColor: colors.navy900,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: -22,
  },
  centerLabel: { color: colors.navy900 },
});
