import { Redirect } from 'expo-router';

import { useSession } from '@/store/session';

/**
 * Giriş noktası — oturum durumuna göre yönlendirir.
 *   onboarding görülmedi → /(auth)/onboarding
 *   oturum yok           → /(auth)/login
 *   oturum var, kayıtsız → /(auth)/register/role   (SIWE girişi yapıldı, `registered` false)
 *   müşteri              → /(customer)/dashboard
 *   trader               → /(trader)/dashboard
 */
export default function Index() {
  const { status, role, registered, onboardingSeen } = useSession();

  if (!onboardingSeen) return <Redirect href="/(auth)/onboarding" />;
  if (status !== 'signed_in') return <Redirect href="/(auth)/login" />;
  if (!registered) return <Redirect href="/(auth)/register/role" />;
  if (role === 'customer') return <Redirect href="/(customer)/dashboard" />;
  if (role === 'trader') return <Redirect href="/(trader)/dashboard" />;
  return <Redirect href="/(auth)/register/role" />;
}
