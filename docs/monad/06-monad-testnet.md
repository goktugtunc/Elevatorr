# Monad Testnet — doğrulanmış ağ bilgileri (OPS-03, 26 Eyl 2026)

Kaynak: docs.monad.xyz/developer-essentials/testnet + zincir üstü doğrulama (`cast call`, blok 65.849.390).
Testnet sürümü v0.15.2 / MONAD_NINE. **Önceki testnet iterasyonuna ait adresler (Nabla dokümanı, eski botlar) artık kodsuz; kullanılmaz.**

## Ağ

| Alan | Değer |
|---|---|
| Chain ID | `10143` (`0x279f`) |
| Ad / token | Monad Testnet / MON (18 ondalık) |
| RPC (QuickNode) | `https://testnet-rpc.monad.xyz` — 50 rps, `eth_call`/`estimateGas` için 25 rps; WS `wss://testnet-rpc.monad.xyz` |
| RPC (Ankr) | `https://rpc.ankr.com/monad_testnet` — 300 istek/10 sn |
| RPC (Monad Foundation) | `https://rpc-testnet.monadinfra.com` — 20 rps; WS `wss://rpc-testnet.monadinfra.com` |
| Explorer | `https://testnet.monadvision.com` (birincil), `https://testnet.monadscan.com` |
| Faucet | `https://faucet.monad.xyz` |
| Blok gas limiti | 150.000.000 |
| Base fee | 100 gwei (sabit görünüyor); `eth_gasPrice` ≈ 102 gwei |
| Blok süresi | ~0.4 s; `block.timestamp` saniye çözünürlüğünde |

**Gas notu (resmi doküman, doğrulandı):** Monad, işlemi `gas_used` değil **`gas_limit`** üzerinden ücretlendirir ("the gas charged for a transaction is the gas limit set in the transaction"). Bu yüzden frontend/backend gas tahminine büyük pay (×2 vb.) koymamalı; ×1.2 yeterli. Base fee tabanı 100 gwei; EIP-1559 tip-2 işlemler destekleniyor (`maxFeePerGas` / `maxPriorityFeePerGas`); hedef blok doluluğu %80, base fee yavaş artar hızlı düşer.

## Kanonik kontratlar (zincir üstünde doğrulandı ✅ / dokümandan ⬜)

| Kontrat | Adres | Durum |
|---|---|---|
| WMON | `0xFb8bf4c1CC7a94c73D209a149eA2AbEa852BC541` | ✅ symbol=WMON, decimals=18 |
| USDC (Circle testnet) | `0x534b2f3A21130d7a60830c2Df862319e593943A3` | ✅ symbol=USDC, decimals=6 |
| Multicall3 | `0xcA11bde05977b3631167028862bE2a173976CA11` | ✅ kod var |
| Permit2 | `0x000000000022d473030f116ddee9f6b43ac78ba3` | ✅ kod var |
| CreateX | `0xba5Ed099633D3B313e4D5F7bdc1305d3c28ba5Ed` | ✅ kod var |
| EntryPoint v0.7 | `0x0000000071727De22E5E9d8BAf0edAc6f37da032` | ⬜ |
| Safe v1.4.1 / SafeL2 / ProxyFactory | `0x41675C099F32341bf84BFc5382aF534df5C7461a` / `0x29fcB43b46531BcA003ddC8FCB67FFE91900C762` / `0x4e1DCf7AD4e460CfD30791CCC4F9c8a4f820ec67` | ⬜ |
| ERC-6492 UniversalSigValidator | `0xdAcD51A54883eb67D95FAEb2BBfdC4a9a6BD2a3B` | ⬜ |

**Uniswap:** V2/V3 testnet adresleri resmi Monad ve Uniswap deploy tablolarında **listelenmiyor** (mainnet V2: Factory `0x182a927119d56008d921126764bf884221b10f59`, Router02 `0x4b2ab38dbf28d31d467aa8993f6c2585981d6804`). Karar K5 doğrulandı: **kendi `MockRouter` + kendi test tokenlarımız** birincil yol. Circle USDC'yi mint edemeyiz; demo tabanı `tUSDC` (bizim TestToken, 6 ondalık) olur. İleride gerçek USDC için Circle faucet + gerçek DEX gerekir.

## Cüzdanlar

| Rol | Adres | Not |
|---|---|---|
| Deployer / vault owner | `0x67aD0CaE18528017B79E0aF8C4D3dd5937caFF19` | anahtar `~/.config/traderkirala/monad-testnet-deployer.env` (yerel, 600) — bakiye 0 MON, **kullanıcı yükleyecek** |
| Minter (faucet) | _oluşturulacak_ | yalnız TestToken MINTER_ROLE; sunucuda `.env` içinde |
| Demo müşteri / demo trader | _oluşturulacak_ | e2e için |

**Deploy için gereken MON (kaba):** vault impl (~3.5M gas) + ERC1967 proxy (~0.5M) + MockRouter (~1M) + 3 TestToken (~3×1.2M) + yapılandırma tx'leri (~10 × 60k) ≈ **9–10M gas × ~102 gwei ≈ 1 MON**. Faucet ve ilk kullanıcı işlemleri için toplam **~5 MON** rahat eder.

## Cüzdan ekleme (MetaMask)
`wallet_addEthereumChain` parametreleri: chainId `0x279f`, chainName "Monad Testnet", rpcUrls `["https://testnet-rpc.monad.xyz"]`, nativeCurrency `{name:"MON", symbol:"MON", decimals:18}`, blockExplorerUrls `["https://testnet.monadvision.com"]`.
