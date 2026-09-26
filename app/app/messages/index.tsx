import { Placeholder, Screen, TopBar } from '@/components/layout';

/** Figma 9b Mesajlar (node 28:171) — sprint görevi için bkz. SPRINT-1.md */
export default function MessagesIndex() {
  return (
    <Screen padded={false}>
      <TopBar title="Messages" />
      <Placeholder
        screen="9b Messages"
        figmaNode="28:171"
        notes="Thread list (avatar, last message, time, unread count). messagesApi.threads()."
      />
    </Screen>
  );
}
