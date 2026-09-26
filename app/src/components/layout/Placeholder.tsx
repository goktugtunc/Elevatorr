import { StyleSheet, View } from 'react-native';

import { Card, Text } from '@/components/ui';
import { colors, spacing } from '@/theme';

const FIGMA_FILE = '6ZxzvsYKarDglg0PGYRIgP';

/**
 * Henüz geliştirilmemiş ekranlar için yer tutucu.
 * Figma node id'si ile tasarıma doğrudan link verir — sprint görevi kapandığında silinir.
 */
export function Placeholder({
  screen,
  figmaNode,
  notes,
}: {
  screen: string;
  figmaNode: string;
  notes?: string;
}) {
  const url = `https://www.figma.com/design/${FIGMA_FILE}/TraderKirala?node-id=${figmaNode.replace(':', '-')}`;
  return (
    <View style={styles.wrap}>
      <Card>
        <Text variant="overline" color="text3">
          Screen in progress
        </Text>
        <Text variant="h2" style={{ marginTop: spacing.xs }}>
          {screen}
        </Text>
        <Text variant="caption" color="text2" style={{ marginTop: spacing.sm }} selectable>
          Figma: {url}
        </Text>
        {notes ? (
          <Text variant="body" color="text2" style={{ marginTop: spacing.md }}>
            {notes}
          </Text>
        ) : null}
      </Card>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingVertical: spacing.lg, backgroundColor: colors.bg },
});
