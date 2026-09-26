// https://docs.expo.dev/guides/using-eslint/
const { defineConfig } = require('eslint/config');
const expoConfig = require('eslint-config-expo/flat');

module.exports = defineConfig([
  expoConfig,
  {
    ignores: [
      'dist/*',
      '.expo/*',
      'node_modules/*',
      // Üretilen dosyalar: openapi-typescript ve contracts/scripts/export-abi.sh çıktıları
      'src/lib/api/schema.d.ts',
      'src/lib/chain/abi/traderVault.ts',
      'src/lib/chain/abi/mockRouter.ts',
    ],
  },
]);
