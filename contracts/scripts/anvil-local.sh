#!/usr/bin/env bash
# Yerel prova: anvil'i (chainId 10143, Monad Testnet ile aynı) arka planda başlatır, Deploy.s.sol'u anvil'in 0 numaralı
# hesabıyla çalıştırır ve deployments/anvil-local.json üretir. anvil çalışır durumda bırakılır (backend `pytest -m chain`
# vb. için); durdurmak için `scripts/anvil-local.sh stop`.
#
# Kullanım:
#   scripts/anvil-local.sh            # başlat + deploy
#   scripts/anvil-local.sh stop       # anvil'i durdur
#   scripts/anvil-local.sh restart    # durdur + başlat + deploy
# Env (opsiyonel): ANVIL_PORT (8545), MINTER_ADDRESS, PLATFORM_FEE_BPS (0), ROUTER_DELAY (0), SETTLE_SLIPPAGE_BPS (100)
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"

CHAIN_ID=10143
NAME=anvil-local
PORT="${ANVIL_PORT:-8545}"
RPC="http://127.0.0.1:$PORT"
PID_FILE="${TMPDIR:-/tmp}/traderkirala-anvil-$PORT.pid"
LOG_FILE="${TMPDIR:-/tmp}/traderkirala-anvil-$PORT.log"
# anvil varsayılan mnemonic'inin 0 numaralı hesabı (0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266)
ANVIL_KEY0=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80

stop_anvil() {
  if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then kill "$pid"; echo "anvil durduruldu (pid $pid)"; fi
    rm -f "$PID_FILE"
  fi
}

case "${1:-start}" in
  stop) stop_anvil; exit 0 ;;
  restart) stop_anvil ;;
  start) ;;
  *) echo "kullanım: $0 [start|stop|restart]"; exit 1 ;;
esac

command -v anvil >/dev/null || { echo "anvil bulunamadı (~/.foundry/bin)"; exit 1; }
command -v jq >/dev/null || { echo "jq gerekli (brew install jq)"; exit 1; }

# ---- anvil ------------------------------------------------------------------------------------------------------
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "anvil zaten çalışıyor (pid $(cat "$PID_FILE"), $RPC)"
else
  if curl -s -o /dev/null "$RPC" 2>/dev/null; then
    echo "Port $PORT dolu ama pid dosyası yok; mevcut RPC kullanılacak ($RPC)"
  else
    anvil --chain-id "$CHAIN_ID" --port "$PORT" --silent > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "anvil başlatıldı (pid $!, chainId $CHAIN_ID, $RPC, log $LOG_FILE)"
  fi
fi

for _ in $(seq 1 50); do
  if cast chain-id --rpc-url "$RPC" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
cast chain-id --rpc-url "$RPC" >/dev/null || { echo "anvil RPC cevap vermiyor: $RPC"; exit 1; }

# ---- deploy -----------------------------------------------------------------------------------------------------
export DEPLOYMENT_NAME="$NAME"
: "${ROUTER_DELAY:=0}"
: "${PLATFORM_FEE_BPS:=0}"
export ROUTER_DELAY PLATFORM_FEE_BPS

forge script script/Deploy.s.sol:Deploy \
  --rpc-url "$RPC" \
  --private-key "$ANVIL_KEY0" \
  --broadcast -vv

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
fi

echo; echo "== $DEP =="; cat "$DEP"; echo
echo "anvil çalışıyor: $RPC (chainId $CHAIN_ID). Durdurmak için: scripts/anvil-local.sh stop"
echo "Not: broadcast/Deploy.s.sol/$CHAIN_ID/ anvil çalıştırmasıyla dolmuştur; testnet yayını öncesi commit'lemeyin."
