import { Placeholder, Screen, ScreenHeader } from '@/components/layout';

/** Figma 3b–3d Hareketler (node 30:252) — sprint görevi için bkz. SPRINT-1.md */
export default function CustomerActivity() {
  return (
    <Screen riskStrip={false} padded={false}>
      <ScreenHeader title="Activity" />
      <Placeholder
        screen="3b–3d Activity"
        figmaNode="30:252"
        notes="Trader filter chips, live feed rows (ListRow), combined / per-trader view, Edit view bottom sheet (30:536)."
      />
    </Screen>
  );
}
