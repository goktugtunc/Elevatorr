#!/usr/bin/env bash
# TraderVault + mock'ları Monad Testnet'e (chainId 10143) deploy eder, deployments/monad-testnet.json'u tamamlar,
# ABI'leri backend/app'e yazar.
#
# Ön koşul (anahtar repoda tutulmaz):
#   source ~/.config/traderkirala/monad-testnet-deployer.env     # MONAD_DEPLOYER_PRIVATE_KEY (+ MONAD_DEPLOYER_ADDRESS)
#   export MINTER_ADDRESS=0x...                                   # opsiyonel: faucet (backend MINTER_KEY) adresi
#   export PLATFORM_FEE_BPS=0 ROUTER_DELAY=600                    # opsiyonel (bkz. .env.example)
#   contracts/scripts/deploy-testnet.sh
#
# Bayraklar: --verify  -> forge script'e Sourcify (blockvision) doğrulaması eklenir
#            --resume  -> yarıda kalmış yayını broadcast/…/run-latest.json'dan sürdürür
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"

CHAIN_ID=10143
NAME=monad-testnet
RPC_ALIAS=monad_testnet
SOURCIFY_URL=https://sourcify-api-monad.blockvision.org/

: "${MONAD_DEPLOYER_PRIVATE_KEY:?MONAD_DEPLOYER_PRIVATE_KEY tanımsız. Önce: source ~/.config/traderkirala/monad-testnet-deployer.env}"
command -v forge >/dev/null || { echo "forge bulunamadı (~/.foundry/bin)"; exit 1; }
command -v jq >/dev/null || { echo "jq gerekli (brew install jq)"; exit 1; }

EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --verify) EXTRA+=(--verify --verifier sourcify --verifier-url "$SOURCIFY_URL") ;;
    --resume) EXTRA+=(--resume) ;;
    *) echo "bilinmeyen bayrak: $arg"; exit 1 ;;
  esac
done

DEPLOYER=$(cast wallet address --private-key "$MONAD_DEPLOYER_PRIVATE_KEY")
BAL=$(cast balance --rpc-url "$RPC_ALIAS" "$DEPLOYER")
echo "deployer : $DEPLOYER"
echo "bakiye   : $(cast from-wei "$BAL") MON"
if [ "$BAL" = "0" ]; then
  echo "Deployer bakiyesi 0 MON. Faucet: https://faucet.monad.xyz (deploy ~1 MON, toplam ~5 MON önerilir)"; exit 1
fi

export DEPLOYMENT_NAME="$NAME"
: "${ROUTER_DELAY:=0}"
: "${PLATFORM_FEE_BPS:=0}"
export ROUTER_DELAY PLATFORM_FEE_BPS
echo "params   : PLATFORM_FEE_BPS=$PLATFORM_FEE_BPS ROUTER_DELAY=$ROUTER_DELAY MINTER_ADDRESS=${MINTER_ADDRESS:-<deployer>}"

forge script script/Deploy.s.sol:Deploy \
  --rpc-url "$RPC_ALIAS" \
  --private-key "$MONAD_DEPLOYER_PRIVATE_KEY" \
  --broadcast --slow -vvvv ${EXTRA[@]+"${EXTRA[@]}"}

# ---- deployments JSON: txHashes + blockNumber + gitCommit (broadcast/…/run-latest.json'dan) ----------------------
DEP="deployments/$NAME.json"
RUN="broadcast/Deploy.s.sol/$CHAIN_ID/run-latest.json"
if [ -f "$RUN" ]; then
  hash_for() { # $1 = kontrat adresi
    jq -r --arg a "$(echo "$1" | tr '[:upper:]' '[:lower:]')" \
      '[.transactions[] | select(.transactionType=="CREATE" and ((.contractAddress // "")|ascii_downcase)==$a) | .hash][0] // ""' "$RUN"
  }
  MIN_BLOCK=""
  for hb in $(jq -r '.receipts[].blockNumber' "$RUN"); do
    n=$((hb))
    if [ -z "$MIN_BLOCK" ] || [ "$n" -lt "$MIN_BLOCK" ]; then MIN_BLOCK=$n; fi
  done
  jq --arg tUSDC "$(hash_for "$(jq -r '.tokens[0].address' "$DEP")")" \
     --arg tWETH "$(hash_for "$(jq -r '.tokens[1].address' "$DEP")")" \
     --arg tWBTC "$(hash_for "$(jq -r '.tokens[2].address' "$DEP")")" \
     --arg router "$(hash_for "$(jq -r '.router' "$DEP")")" \
     --arg impl "$(hash_for "$(jq -r '.vault.implementation' "$DEP")")" \
     --arg proxy "$(hash_for "$(jq -r '.vault.proxy' "$DEP")")" \
     --arg commit "$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
     --argjson block "${MIN_BLOCK:-0}" \
     '.txHashes = {tUSDC:$tUSDC, tWETH:$tWETH, tWBTC:$tWBTC, router:$router, vaultImplementation:$impl, vaultProxy:$proxy}
      | (if $block > 0 then .blockNumber = $block else . end)
      | .gitCommit = $commit' "$DEP" > "$DEP.tmp" && mv "$DEP.tmp" "$DEP"
else
  echo "uyarı: $RUN bulunamadı; txHashes/blockNumber elle doldurulmalı"
fi

echo; echo "== $DEP =="; cat "$DEP"; echo

# ---- ABI dışa aktarma -------------------------------------------------------------------------------------------
./scripts/export-abi.sh

# ---- Doğrulama notu ----------------------------------------------------------------------------------------------
IMPL=$(jq -r '.vault.implementation' "$DEP"); PROXY=$(jq -r '.vault.proxy' "$DEP"); ROUTER=$(jq -r '.router' "$DEP")
cat <<EOF

Explorer : https://testnet.monadvision.com/address/$PROXY   (alternatif: https://testnet.monadscan.com)
Kaynak doğrulama (--verify verilmediyse, spec §9.3):
  V="--chain $CHAIN_ID --verifier sourcify --verifier-url $SOURCIFY_URL"
  forge verify-contract $IMPL src/TraderVault.sol:TraderVault \$V
  forge verify-contract $ROUTER src/mocks/MockRouter.sol:MockRouter \$V --constructor-args \$(cast abi-encode "constructor(address)" $DEPLOYER)
  forge verify-contract $PROXY lib/openzeppelin-contracts/contracts/proxy/ERC1967/ERC1967Proxy.sol:ERC1967Proxy \$V \\
      --constructor-args \$(cast abi-encode "constructor(address,bytes)" $IMPL <INIT_CALLDATA>)   # INIT_CALLDATA: broadcast tx input'unun constructor kısmı
  TestToken'lar: --constructor-args \$(cast abi-encode "constructor(string,string,uint8,address)" "Test USDC" tUSDC 6 $DEPLOYER)  (tWETH 18 / tWBTC 8)
  Monadscan alternatifi: --verifier etherscan --etherscan-api-key \$MONADSCAN_API_KEY --watch

Sonraki adımlar: backend .env (VAULT/ROUTER, DEPLOYMENTS_FILE) + seed_assets; app .env; deployments/$NAME.json ve broadcast/ commit'lenir.
EOF
