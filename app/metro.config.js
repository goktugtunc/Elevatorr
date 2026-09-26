// https://docs.expo.dev/guides/customizing-metro/
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

/**
 * `wagmi/connectors` barrel'ı (wallet.web.ts → injected + walletConnect) `porto` bağlayıcısını da
 * çeker. porto'nun iç içe `ox@0.9.x` kopyası Metro'da `.ts` kaynağına çözülür ve
 * `../core/*.js` import'ları web bundle'ını kırar. Uygulama Porto kullanmaz; web hedefinde modül
 * boş bırakılır. Native bundle'da wagmi yer almaz (04 §1), bu yüzden yalnız web'de devreye girer.
 */
const defaultResolveRequest = config.resolver.resolveRequest;
config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (platform === 'web' && (moduleName === 'porto' || moduleName.startsWith('porto/'))) {
    return { type: 'empty' };
  }
  return defaultResolveRequest
    ? defaultResolveRequest(context, moduleName, platform)
    : context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
