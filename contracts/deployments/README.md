# deployments/

`script/Deploy.s.sol` çıktıları. Backend (`seed_assets`, indexer başlangıç bloğu) ve frontend bu dosyaları
**doğruluk kaynağı** olarak okur; adresler elle düzenlenmez, yeniden deploy edilirse dosya yeniden üretilir.

| Dosya | Üreten | Commit? |
|---|---|---|
| `monad-testnet.json` | `scripts/deploy-testnet.sh` (chainId 10143, Monad Testnet) | evet |
| `anvil-local.json` | `scripts/anvil-local.sh` (chainId 10143, yerel anvil) | hayır (her çalıştırmada değişir) |

## Şema (spec `docs/monad/01-kontrat-spec.md` §9.4)

```jsonc
{
  "chainId": 10143,
  "vault": { "proxy": "0x…", "implementation": "0x…" },   // proxy = kullanıcıların çağırdığı adres (UUPS)
  "router": "0x…",                                         // MockRouter (IUniswapV2Router02Like)
  "tokens": [
    { "symbol": "tUSDC", "address": "0x…", "decimals": 6,  "isBase": true  },
    { "symbol": "tWETH", "address": "0x…", "decimals": 18, "isBase": false },
    { "symbol": "tWBTC", "address": "0x…", "decimals": 8,  "isBase": false }
  ],
  "deployer": "0x…",                                       // vault owner, TestToken DEFAULT_ADMIN_ROLE, router owner
  "blockNumber": 0,                                        // indexer başlangıç bloğu (ilk deploy tx'inin bloğu)
  "txHashes": {
    "tUSDC": "0x…", "tWETH": "0x…", "tWBTC": "0x…", "router": "0x…",
    "vaultImplementation": "0x…", "vaultProxy": "0x…"
  },

  // opsiyonel alanlar
  "feeRecipient": "0x…",
  "platformFeeBps": 0,
  "settleSlippageBps": 100,
  "routerDelay": 0,
  "minter": "0x…",                                         // MINTER_ROLE verilen faucet adresi (yoksa deployer)
  "deployedAt": 1790000000,                                // block.timestamp (saniye)
  "gitCommit": "…"
}
```

Kurallar:

- Adresler EIP-55 checksum'lu yazılır (`vm.serializeAddress`); backend küçük harfe normalize eder (K11).
- Zorunlu alanlar `chainId`, `vault`, `router`, `tokens`, `deployer`, `blockNumber`, `txHashes`.
- `Deploy.s.sol` `txHashes` dışındaki her şeyi yazar ve `blockNumber`'a deploy öncesi bloğu koyar. Shell betikleri
  (`deploy-testnet.sh`, `anvil-local.sh`) ardından `broadcast/Deploy.s.sol/10143/run-latest.json`'dan
  `txHashes`, kesin `blockNumber` (en küçük receipt bloğu) ve `gitCommit`'i doldurur.
- Tokens sırası sabittir: `[tUSDC, tWETH, tWBTC]` (`script/Configure.s.sol` bu sıraya dayanır).
- Yeni deploy = yeni dosya içeriği + yeni `blockNumber`; backend indexer state'i buna göre sıfırlanır (BE-09).
