import { Placeholder, Screen, ScreenHeader } from '@/components/layout';

/** Figma 5a/5b İşlemler · Trader (node 23:45) — sprint görevi için bkz. SPRINT-1.md */
export default function TraderTrades() {
  return (
    <Screen riskStrip={false} padded={false}>
      <ScreenHeader title="Trades" />
      <Placeholder
        screen="5a/5b Trades · Trader"
        figmaNode="23:45"
        notes="Today P&amp;L header, Open positions / History segmented, position rows; New trade bottom sheet (23:181): symbol, side, size, price, note, notify-investors switch."
      />
    </Screen>
  );
}
