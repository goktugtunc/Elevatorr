# contracts — TraderVault (Monad Testnet, Foundry)

TraderKirala escrow/vault kontratı (Solidity, UUPS). Bağlayıcı spesifikasyon: `docs/monad/01-kontrat-spec.md`.
Ağ bilgileri: `docs/monad/06-monad-testnet.md` (chainId `10143`, RPC `https://testnet-rpc.monad.xyz`).

## İçerik

| Yol | Ne |
|---|---|
| `src/TraderVault.sol` | UUPS kasa (Ownable2Step, ReentrancyGuard, SafeERC20) |
| `src/interfaces/ITraderVault.sol` | enum/struct/error/event + dış imzalar (ABI'nin kaynağı) |
| `src/interfaces/IUniswapV2Router02Like.sol` | `getAmountsOut` + `swapExactTokensForTokens` |
| `src/libraries/SettleMath.sol` | bps / drawdown / slippage / settle bölüşümü |
| `src/mocks/MockRouter.sol` | sabit fiyatlı V2 benzeri router (testnet'e deploy edilir) |
| `src/mocks/TestToken.sol` | tUSDC(6) / tWETH(18) / tWBTC(8), mint yalnız `MINTER_ROLE` |
| `scripts/export-abi.sh` | `out/` → backend + app ABI dosyaları |

## Kurulum

Foundry gerekir (`~/.foundry/bin`, sürüm ≥ 1.8). Bağımlılıklar `lib/` altına git submodule **kullanılmadan** kurulur
(`lib/` `.gitignore`'dadır; klonda yeniden kurulur):

```sh
export PATH="$HOME/.foundry/bin:$PATH"
cd contracts
make setup        # forge-std, openzeppelin-contracts@v5.4.0, openzeppelin-contracts-upgradeable@v5.4.0 (--no-git)
```

## Derleme ve test

```sh
make build        # forge build
make sizes        # forge build --sizes (24 KB altı hedefi)
make test         # forge test -vvv
make test-ci      # FOUNDRY_PROFILE=ci forge test (daha yüksek fuzz/invariant sayıları)
make fmt-check
```

## ABI dışa aktarma

```sh
make abi          # scripts/export-abi.sh (jq gerekir)
```

Yazılan dosyalar:

- `backend/app/services/chain/abi/{TraderVault,MockRouter,TestToken,IERC20}.json`
- `app/src/lib/chain/abi/{traderVault,erc20}.ts` (`export const … as const`, viem uyumlu)

`IERC20.json` / `erc20Abi` içeriği OZ `IERC20Metadata` ABI'sidir (IERC20 + `name/symbol/decimals`).
Kontrat ABI'si değiştiğinde script yeniden çalıştırılır; hedef dosyalar elle düzenlenmez.

## Deploy

Betikler: `script/Deploy.s.sol` (spec §9.1 akışı), `script/Configure.s.sol` (opsiyonel yönetim), `scripts/deploy-testnet.sh`,
`scripts/anvil-local.sh`. Çıktı şeması: `deployments/README.md` (spec §9.4). Env değişkenleri: `.env.example`.

`Deploy.s.sol` sırasıyla: `tUSDC(6)/tWETH(18)/tWBTC(8)` TestToken'ları (admin + `MINTER_ROLE` = deployer; `MINTER_ADDRESS`
verilirse ona da) → `MockRouter` + iki yönlü fiyatlar (1 WETH = 3000 USDC, 1 WBTC = 60000 USDC, 1 WBTC = 20 WETH) + router
likiditesi (10M tUSDC / 5k tWETH / 250 tWBTC) + deployer'a 100k tUSDC → `TraderVault` impl + `ERC1967Proxy` +
`initialize(owner=deployer, router, feeRecipient=FEE_RECIPIENT|deployer, PLATFORM_FEE_BPS=0, SETTLE_SLIPPAGE_BPS=100, ROUTER_DELAY=0)`
→ allow-list (`tUSDC` taban, `tWETH/tWBTC` işlem varlığı) → `deployments/<DEPLOYMENT_NAME>.json`. Deployer, `--private-key`
ile verilen hesaptır; script anahtar okumaz. `txHashes`/`blockNumber`/`gitCommit` shell betiği tarafından
`broadcast/Deploy.s.sol/10143/run-latest.json`'dan tamamlanır.

### Yerel prova (anvil, chainId 10143)

```sh
scripts/anvil-local.sh          # anvil'i arka planda başlatır, anvil hesap #0 ile deploy eder → deployments/anvil-local.json
scripts/anvil-local.sh stop     # anvil'i durdurur (restart: durdur + yeniden deploy)
```

anvil çalışır bırakılır (backend `pytest -m chain` vb.). `anvil-local.json` ve anvil'in ürettiği `broadcast/…/10143/`
commit'lenmez (testnet ile aynı chainId; testnet yayını öncesi `broadcast/` temizlenir).

### Monad Testnet

```sh
source ~/.config/traderkirala/monad-testnet-deployer.env   # MONAD_DEPLOYER_PRIVATE_KEY (repo dışı, 600)
export MINTER_ADDRESS=0x...                                 # opsiyonel: backend MINTER_KEY adresi (faucet)
export PLATFORM_FEE_BPS=0 ROUTER_DELAY=600                  # opsiyonel (varsayılan 0 / 0)
scripts/deploy-testnet.sh [--verify] [--resume]
```

Betik: bakiye kontrolü (0 MON ise durur; faucet `https://faucet.monad.xyz`, deploy ≈ 1 MON) →
`forge script script/Deploy.s.sol:Deploy --rpc-url monad_testnet --private-key $MONAD_DEPLOYER_PRIVATE_KEY --broadcast --slow -vvvv`
→ `deployments/monad-testnet.json` tamamlanır → `scripts/export-abi.sh` → explorer linki ve `forge verify-contract` komutları
(Sourcify: `--chain 10143 --verifier sourcify --verifier-url https://sourcify-api-monad.blockvision.org/`; Monadscan alternatifi
`--verifier etherscan --etherscan-api-key $MONADSCAN_API_KEY`). `--verify` bayrağı doğrulamayı forge script içinde yapar.
`deployments/monad-testnet.json` ve `broadcast/` **commit'lenir**; sonra backend `.env` (VAULT/ROUTER, `DEPLOYMENTS_FILE`) +
`seed_assets`, app `.env` güncellenir.

### Deploy sonrası yönetim (`script/Configure.s.sol`)

Vault/router adresleri `deployments/$DEPLOYMENT_NAME.json`'dan okunur (varsayılan `monad-testnet`); gönderen owner/minter olmalıdır.

```sh
F="--rpc-url monad_testnet --private-key $MONAD_DEPLOYER_PRIVATE_KEY --broadcast"
forge script script/Configure.s.sol --sig "setToken(address,bool,bool)" 0xTOKEN true false $F
forge script script/Configure.s.sol --sig "setPair(address,address,uint256,uint256)" 0xWETH 0xUSDC 3200000000 1000000000000000000 $F   # 1 WETH = 3200 USDC, iki yön
forge script script/Configure.s.sol --sig "setPrice(address,address,uint256,uint256)" 0xIN 0xOUT NUM DEN $F                            # tek yön
forge script script/Configure.s.sol --sig "mint(address,address,uint256)" 0xTOKEN 0xTO 1000000000 $F
forge script script/Configure.s.sol --sig "grantMinter(address)" 0xMINTER $F
forge script script/Configure.s.sol --sig "setPaused(bool)" true $F
```
