import { Redirect } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';

import { TabBar } from '@/components/layout';
import { useSession } from '@/store/session';

/** Figma "Tab Bar/Müşteri": Dashboard · Activity · Discover · Listings · Profile */
export default function CustomerLayout() {
  const { status, role } = useSession();
  if (status !== 'signed_in') return <Redirect href="/(auth)/login" />;
  if (role !== 'customer') return <Redirect href="/" />;

  return (
    <Tabs tabBar={(props) => <TabBar {...props} />} screenOptions={{ headerShown: false }}>
      <Tabs.Screen name="dashboard" />
      <Tabs.Screen name="activity" />
      <Tabs.Screen name="discover" />
      <Tabs.Screen name="listings" />
      <Tabs.Screen name="profile" />
    </Tabs>
  );
}
