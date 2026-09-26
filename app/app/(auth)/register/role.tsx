import { useRouter } from 'expo-router';
import { Briefcase, Check, Wallet } from 'lucide-react-native';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { Screen, TopBar } from '@/components/layout';
import { Button, Progress, Text } from '@/components/ui';
import { colors, radius, spacing } from '@/theme';
import type { Role } from '@/types';

/** Figma 1e · Kayıt · Rol Seçimi (node 19:181) — Adım 1/2 */
const ROLES: {
  role: Role;
  title: string;
  summary: string;
  bullets: string[];
  Icon: typeof Wallet;
}[] = [
  {
    role: 'customer',
    title: 'Customer',
    summary: 'Hand your capital to a trader you choose.',
    bullets: [
      'Swipe through trader listings',
      'Follow your trader’s moves live',
      'Publish a capital listing',
    ],
    Icon: Wallet,
  },
  {
    role: 'trader',
    title: 'Trader',
    summary: 'Trade with investor capital and earn commission.',
    bullets: [
      'Swipe through customer listings',
      'Share your trades with investors',
      'Publish a service listing',
    ],
    Icon: Briefcase,
  },
];

export default function RegisterRole() {
  const router = useRouter();
  const [selected, setSelected] = useState<Role | null>(null);

  return (
    <Screen padded={false}>
      <TopBar title="Sign up" />
      <View style={styles.body}>
        <Progress value={0.5} label="Step 1 of 2 · Choose your role" />

        <Text variant="h1">How will you use TraderKirala?</Text>
        <Text variant="body" color="text2">
          Pick your role now — it shapes your navigation and the screens you see.
        </Text>

        {ROLES.map(({ role, title, summary, bullets, Icon }) => {
          const active = selected === role;
          return (
            <Pressable
              key={role}
              accessibilityRole="radio"
              accessibilityState={{ selected: active }}
              onPress={() => setSelected(role)}
              style={[styles.option, active && styles.optionActive]}
            >
              <View style={styles.optionHead}>
                <View style={[styles.iconTile, active && { backgroundColor: colors.navy900 }]}>
                  <Icon size={20} color={active ? colors.onNavy : colors.navy900} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text variant="h2">{title}</Text>
                  <Text variant="caption" color="text2">
                    {summary}
                  </Text>
                </View>
                {active ? <Check size={20} color={colors.navy900} /> : null}
              </View>
              {bullets.map((b) => (
                <View key={b} style={styles.bullet}>
                  <View style={styles.bulletDot} />
                  <Text variant="caption" color="text2">
                    {b}
                  </Text>
                </View>
              ))}
            </Pressable>
          );
        })}

        <Button
          title="Continue"
          disabled={!selected}
          fullWidth
          onPress={() =>
            selected &&
            router.push({ pathname: '/(auth)/register/details', params: { role: selected } })
          }
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: spacing.lg, paddingBottom: spacing['2xl'], gap: spacing.lg },
  option: {
    backgroundColor: colors.surface,
    borderWidth: 1.5,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  optionActive: { borderColor: colors.navy900 },
  optionHead: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    marginBottom: spacing.xs,
  },
  iconTile: {
    width: 40,
    height: 40,
    borderRadius: radius.md,
    backgroundColor: colors.navy050,
    alignItems: 'center',
    justifyContent: 'center',
  },
  bullet: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingLeft: 4 },
  bulletDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: colors.text3 },
});
