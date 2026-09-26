import { Placeholder, Screen, TopBar } from '@/components/layout';

/** Figma 7a/7b/7c Bildirimler (node 27:96) — sprint görevi için bkz. SPRINT-1.md */
export default function Notifications() {
  return (
    <Screen padded={false}>
      <TopBar title="Notifications" />
      <Placeholder
        screen="7a/7b/7c Notifications"
        figmaNode="27:96"
        notes="Mark all read action, filter chips, Today / Yesterday groups; empty state (27:371). Same for both roles, data from notificationsApi.list()."
      />
    </Screen>
  );
}
