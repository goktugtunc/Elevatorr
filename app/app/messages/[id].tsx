import { Placeholder, Screen, TopBar } from '@/components/layout';

/** Figma 9c Sohbet (node 28:266) — sprint görevi için bkz. SPRINT-1.md */
export default function MessageThread() {
  return (
    <Screen padded={false}>
      <TopBar title="Chat" />
      <Placeholder
        screen="9c Chat"
        figmaNode="28:266"
        notes="Bubble message stream with a Write a message field. Contract proposal goes to /contract/[id]."
      />
    </Screen>
  );
}
