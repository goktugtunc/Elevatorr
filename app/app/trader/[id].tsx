import { Placeholder, Screen, TopBar } from '@/components/layout';

/** Figma 3e Trader Profili (node 22:171) — sprint görevi için bkz. SPRINT-1.md */
export default function TraderPublicProfile() {
  return (
    <Screen padded={false}>
      <TopBar title="Trader Profile" />
      <Placeholder
        screen="3e Trader Profile"
        figmaNode="22:171"
        notes="Header + pills, KPI grid, performance (1M/3M/6M/1Y segmented + sparkline), live activity, open positions, recent trades, strategy, reviews. Follow / Request offer actions."
      />
    </Screen>
  );
}
