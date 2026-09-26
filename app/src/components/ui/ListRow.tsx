import { ChevronRight } from 'lucide-react-native';
import { Pressable, StyleSheet, View } from 'react-native';

import { Avatar } from './Avatar';
import { Text } from './Text';
import { colors, pnlColor, spacing } from '@/theme';

/**
 * Figma "List Row" — avatar/ikon + başlık + alt başlık + sağda değer/rozet.
 * Panel'deki takip listesi, Hareketler akışı, Cüzdan hareketleri, Profil menüsü bu satırı kullanır.
 */
export interface ListRowProps {
  title: string;
  subtitle?: string;
  initials?: string;
  leading?: React.ReactNode;
  /** Sağdaki değer; `signed` verilirse renklendirilir. */
  value?: string;
  signed?: number;
  meta?: string; // sağ altta küçük metin (ör. "2 dk", "Yatırımda")
  trailing?: React.ReactNode;
  chevron?: boolean;
  onPress?: () => void;
}

export function ListRow({
  title,
  subtitle,
  initials,
  leading,
  value,
  signed,
  meta,
  trailing,
  chevron,
  onPress,
}: ListRowProps) {
  const content = (
    <View style={styles.row}>
      {leading ?? (initials ? <Avatar initials={initials} size="md" /> : null)}
      <View style={styles.body}>
        <Text variant="bodyStrong" numberOfLines={1}>
          {title}
        </Text>
        {subtitle ? (
          <Text variant="caption" color="text2" numberOfLines={1}>
            {subtitle}
          </Text>
        ) : null}
      </View>
      {trailing ??
        (value || meta ? (
          <View style={styles.right}>
            {value ? (
              <Text
                variant="numericSm"
                color={signed === undefined ? colors.text : pnlColor(signed)}
              >
                {value}
              </Text>
            ) : null}
            {meta ? (
              <Text variant="caption" color="text3">
                {meta}
              </Text>
            ) : null}
          </View>
        ) : null)}
      {chevron ? <ChevronRight size={18} color={colors.text3} /> : null}
    </View>
  );
  if (!onPress) return content;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => pressed && { opacity: 0.7 }}
    >
      {content}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.md,
  },
  body: { flex: 1, gap: 2 },
  right: { alignItems: 'flex-end', gap: 2 },
});
