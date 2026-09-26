"""app.services.chain.abi — selector table vs ABI JSON, revert decoding, event decoding, calldata (03 §8.3)."""
from __future__ import annotations

from hexbytes import HexBytes

from app.services.chain import abi
from app.services.chain.abi import ERROR_MESSAGES, ERROR_SELECTORS, EVENT_TOPICS, VaultError
from app.services.chain.errors import ContractRevertError
from app.services.chain.types import EventRecord, Terms

VAULT = "0x00000000000000000000000000000000000000f1"
A = "0x" + "11" * 20
B = "0x" + "22" * 20
TOKEN = "0x" + "c1" * 20
TOKEN2 = "0x" + "c2" * 20
TX = "0x" + "cd" * 32
BLOCK_HASH = "0x" + "ab" * 32

FIXED_SELECTORS = {
    "Unauthorized()": "0x82b42900",
    "Paused()": "0x9e87fac8",
    "NotFound()": "0xc5723b51",
    "ZeroAmount()": "0x1f2a2005",
    "Error(string)": "0x08c379a0",
    "Panic(uint256)": "0x4e487b71",
}


# --- ABI JSON shape ------------------------------------------------------------------------------------


def test_trader_vault_abi_counts_match_sc1_export():
    entries = abi.load_abi("TraderVault")
    kinds = {k: sum(1 for e in entries if e.get("type") == k) for k in ("function", "event", "error")}
    assert kinds == {"function": 50, "event": 19, "error": 39}
    for name in ("MockRouter", "TestToken", "IERC20"):
        assert abi.load_abi(name)
    assert abi.has_function("TraderVault", "previewSettle")
    assert not abi.has_function("TraderVault", "setRouter")  # replaced by the two-step router change


def test_fixed_selectors_and_keccak_agree_with_table():
    for signature, selector in FIXED_SELECTORS.items():
        assert abi.selector_of(signature) == selector
        assert selector in ERROR_SELECTORS, signature
    # every ABI error is in the table under its keccak selector
    for entry in abi.load_abi("TraderVault"):
        if entry.get("type") != "error":
            continue
        sig = f"{entry['name']}({','.join(i['type'] for i in entry['inputs'])})"
        name, _code, types = ERROR_SELECTORS[abi.selector_of(sig)]
        assert name == entry["name"]
        assert types == [i["type"] for i in entry["inputs"]]


def test_vault_error_codes_follow_spec_table():
    # 1–27: the vault's own errors share their Solidity name
    for err in VaultError:
        if err <= 27:
            sel = abi.selector_of(f"{err.name}({'bytes' if err is VaultError.RouterError else ''}"
                                  f"{'uint64' if err is VaultError.RouterChangeNotReady else ''})")
            assert ERROR_SELECTORS[sel][1] == int(err), err
    # OZ errors fold into 1 / 2 / 23 / 28 / 29 / 30
    folded = {
        "InvalidInitialization()": 1, "NotInitializing()": 1,
        "OwnableUnauthorizedAccount(address)": 2, "OwnableInvalidOwner(address)": 23,
        "ReentrancyGuardReentrantCall()": 28, "SafeERC20FailedOperation(address)": 29,
        "UUPSUnauthorizedCallContext()": 30, "UUPSUnsupportedProxiableUUID(bytes32)": 30,
        "ERC1967InvalidImplementation(address)": 30, "ERC1967NonPayable()": 30,
        "AddressEmptyCode(address)": 30, "FailedCall()": 30,
    }
    for sig, code in folded.items():
        assert ERROR_SELECTORS[abi.selector_of(sig)][1] == code, sig
    # ERC-20 and MockRouter errors carry no vault code
    for sig in ("ERC20InsufficientAllowance(address,uint256,uint256)", "ERC20InsufficientBalance(address,uint256,uint256)",
                "ERC20InvalidReceiver(address)", "ERC20InvalidSender(address)", "NoPrice(address,address)",
                "InsufficientOutput(uint256,uint256)", "DeadlineExpired()", "InvalidPath()"):
        assert ERROR_SELECTORS[abi.selector_of(sig)][1] is None, sig
    assert len(VaultError) == 30 and set(ERROR_MESSAGES) == set(VaultError)
    assert all(msg.strip() for msg in ERROR_MESSAGES.values())


# --- decode_revert ---------------------------------------------------------------------------------------


def test_decode_revert_vault_error():
    info = abi.decode_revert(abi.encode_revert("NotFound"))
    assert (info.selector, info.name, info.code) == ("0xc5723b51", "NotFound", 6)
    assert info.message == ERROR_MESSAGES[VaultError.NotFound]
    assert abi.error_code_of(info) == "vault:NotFound"
    ready = abi.decode_revert(abi.encode_revert("RouterChangeNotReady", [1_800_000_600]))
    assert ready.code == 25 and ready.args == {"activationTime": 1_800_000_600}


def test_decode_revert_error_string_and_empty():
    info = abi.decode_revert(abi.encode_revert("Error", ["boom"]))
    assert info.name == "Error" and info.code is None and info.args == {"reason": "boom"} and info.message == "boom"
    assert abi.error_code_of(info) == "reverted"
    for empty in (None, "", "0x", b""):
        e = abi.decode_revert(empty)
        assert e.name is None and e.code is None and e.message == abi.GENERIC_REVERT_MESSAGE
        assert abi.error_code_of(e) == "reverted"
    unknown = abi.decode_revert("0xdeadbeef" + "00" * 32)
    assert unknown.selector == "0xdeadbeef" and unknown.name is None and abi.error_code_of(unknown) == "reverted"
    assert abi.decode_revert("no data").name is None  # web3's placeholder for missing revert data


def test_decode_revert_panic():
    overflow = abi.decode_revert(abi.encode_revert("Panic", [0x11]))
    assert (overflow.name, overflow.code) == ("Overflow", 14)
    assert abi.error_code_of(overflow) == "vault:Overflow"
    other = abi.decode_revert(abi.encode_revert("Panic", [0x32]))
    assert (other.name, other.code) == ("Panic", None)
    assert abi.error_code_of(other) == "panic:0x32"


def test_decode_revert_router_error_decodes_inner_reason():
    inner = abi.encode_revert("NoPrice", [TOKEN, TOKEN2])
    info = abi.decode_revert(abi.encode_revert("RouterError", [inner]))
    assert (info.name, info.code) == ("RouterError", 16)
    assert info.args["inner"] == "router:NoPrice"
    assert info.args["reason"] == "0x" + inner.hex()
    assert info.message == "RouterError: router:NoPrice"
    assert abi.error_code_of(info) == "vault:RouterError"
    ascii_reason = abi.decode_revert(abi.encode_revert("RouterError", [b"input-mismatch"]))
    assert ascii_reason.args["inner"] == "input-mismatch"
    empty_reason = abi.decode_revert(abi.encode_revert("RouterError", [b""]))
    assert empty_reason.args["inner"] == "empty"
    nested_erc20 = abi.decode_revert(abi.encode_revert("RouterError", [abi.encode_revert("ERC20InsufficientBalance", [A, 1, 2])]))
    assert nested_erc20.args["inner"] == "erc20:ERC20InsufficientBalance"


def test_decode_revert_oz_and_erc20_errors():
    safe = abi.decode_revert(abi.encode_revert("SafeERC20FailedOperation", [TOKEN]))
    assert (safe.name, safe.code) == ("TransferFailed", 29)
    assert safe.args == {"token": TOKEN}
    assert abi.error_code_of(safe) == "vault:TransferFailed"
    owner = abi.decode_revert(abi.encode_revert("OwnableUnauthorizedAccount", [A]))
    assert (owner.name, owner.code) == ("Unauthorized", 2) and owner.args == {"account": A}
    erc = abi.decode_revert(abi.encode_revert("ERC20InsufficientAllowance", [VAULT, 5, 10]))
    assert erc.name == "erc20:ERC20InsufficientAllowance" and erc.code is None
    assert erc.args == {"spender": VAULT, "allowance": 5, "needed": 10}
    assert abi.error_code_of(erc) == "erc20:ERC20InsufficientAllowance"
    # HexBytes / dict payloads (web3 shapes) decode the same way
    assert abi.decode_revert(HexBytes(abi.encode_revert("Paused"))).code == 3
    assert abi.decode_revert({"data": "0x" + abi.encode_revert("Paused").hex()}).code == 3


def test_contract_revert_error_carries_error_code_and_details():
    info = abi.revert_info_for("WrongStatus")
    err = ContractRevertError(info.message, revert=info, error=VaultError(info.code), error_code=abi.error_code_of(info))
    assert err.status_code == 400 and err.code == "contract_error"
    assert err.error_code == "vault:WrongStatus"
    assert err.details == {"error_code": "vault:WrongStatus", "contract_error_code": 7}


# --- events -----------------------------------------------------------------------------------------------


def _log(name: str, args: dict, *, address: str = VAULT, log_index: int = 0) -> dict:
    return abi.encode_log(name, args, address=address, block_number=1234, block_hash=BLOCK_HASH,
                          log_index=log_index, tx_hash=TX, tx_index=3)


def test_event_table_covers_vault_oz_and_erc20_events():
    names = {e["name"] for e in EVENT_TOPICS.values()}
    assert {"Proposed", "Opened", "Activated", "Cancelled", "Reserved", "Released", "ReservationDrawn", "Traded",
            "Settled", "Unliquidated", "Claimed", "ConfigChanged", "TokenSet", "RouterChangeProposed",
            "RouterChangeCancelled", "Upgraded", "OwnershipTransferStarted", "OwnershipTransferred", "Initialized",
            "Transfer", "Approval"} <= names
    assert abi.event_topic("Traded") in EVENT_TOPICS


def test_decode_log_opened_and_traded():
    rec = abi.decode_log(_log("Opened", {"id": 7, "trader": B, "customer": A, "principal": 10**9, "baseToken": TOKEN}))
    assert isinstance(rec, EventRecord)
    assert rec.name == "Opened" and rec.address == VAULT and rec.block_number == 1234
    assert rec.block_hash == BLOCK_HASH and rec.tx_hash == TX and rec.tx_index == 3 and rec.log_index == 0
    assert rec.topic0 == abi.event_topic("Opened")
    assert rec.args == {"id": 7, "trader": B, "customer": A, "principal": 10**9, "baseToken": TOKEN}
    traded = abi.decode_log(_log("Traded", {
        "id": 7, "trader": B, "tokenIn": TOKEN, "tokenOut": TOKEN2, "amountIn": 5, "amountOut": 6, "valueAfter": 99,
    }, log_index=2))
    assert traded.args["tokenOut"] == TOKEN2 and traded.args["valueAfter"] == 99 and traded.log_index == 2


def test_decode_log_reserved_bytes32_and_config_changed_key():
    ref = b"\x0a" * 32
    reserved = abi.decode_log(_log("Reserved", {"id": 1, "customer": A, "token": TOKEN, "amount": 5, "listingRef": ref}))
    assert reserved.args["listingRef"] == ref
    cfg = abi.decode_log(_log("ConfigChanged", {
        "key": abi.config_key_bytes("slippage"), "router": B, "platformFeeBps": 100, "feeRecipient": A,
        "paused": True, "settleSlippageBps": 250,
    }))
    assert abi.config_key_of(cfg.args["key"]) == "slippage"
    assert cfg.args["paused"] is True and cfg.args["settleSlippageBps"] == 250 and cfg.args["router"] == B
    assert abi.config_key_of(b"\xff" * 32).startswith("0x")


def test_decode_log_accepts_web3_shapes_and_ignores_unknown_topics():
    raw = _log("Activated", {"id": 3, "startTime": 10, "endTime": 20})
    web3_like = {
        "address": raw["address"],
        "topics": [HexBytes(t) for t in raw["topics"]],
        "data": HexBytes(raw["data"]),
        "blockNumber": raw["blockNumber"],
        "blockHash": HexBytes(raw["blockHash"]),
        "logIndex": raw["logIndex"],
        "transactionHash": HexBytes(raw["transactionHash"]),
        "transactionIndex": raw["transactionIndex"],
        "removed": False,
    }
    rec = abi.decode_log(web3_like)
    assert rec.args == {"id": 3, "startTime": 10, "endTime": 20}
    assert abi.decode_log({**raw, "topics": ["0x" + "77" * 32]}) is None
    assert abi.decode_log({**raw, "topics": []}) is None


def test_decode_receipt_logs_keeps_only_vault_logs():
    logs = [
        _log("Transfer", {"from": A, "to": VAULT, "value": 5}, address=TOKEN, log_index=0),
        _log("Opened", {"id": 1, "trader": B, "customer": A, "principal": 5, "baseToken": TOKEN}, log_index=1),
        {**_log("Opened", {"id": 2, "trader": B, "customer": A, "principal": 5, "baseToken": TOKEN}, log_index=2),
         "topics": ["0x" + "77" * 32]},
    ]
    recs = abi.decode_receipt_logs(logs, VAULT.upper().replace("0X", "0x"))
    assert [r.name for r in recs] == ["Opened"] and recs[0].args["id"] == 1


# --- calldata ---------------------------------------------------------------------------------------------


def test_encode_call_open_selector_and_roundtrip():
    terms = Terms(A, B, TOKEN, 10**9, 86_400, 1_000, 2_000, b"\x01" * 32)
    data = abi.encode_call("TraderVault", "open", [terms])
    assert data.startswith(abi.selector_of("open((address,address,address,uint256,uint64,uint16,uint16,bytes32))"))
    assert abi.function_selectors("TraderVault")[data[:10]] == "open"
    fn, args = abi.decode_call("TraderVault", data)
    assert fn == "open"
    assert args["terms"]["customer"] == A and args["terms"]["baseToken"] == TOKEN  # lower-cased
    assert args["terms"]["principal"] == 10**9 and args["terms"]["listingRef"] == b"\x01" * 32


def test_encode_call_accepts_lower_case_addresses_and_lists():
    data = abi.encode_call("TraderVault", "settle", [4, [1, 2, 3]])
    assert abi.decode_call("TraderVault", data) == ("settle", {"id": 4, "minOuts": [1, 2, 3]})
    approve = abi.encode_call("IERC20", "approve", [VAULT, 2**256 - 1])
    assert abi.decode_call("TestToken", approve) == ("approve", {"spender": VAULT, "value": 2**256 - 1})
    assert abi.encode_call("TraderVault", "nextId") == "0x61b8ce8c"


def test_struct_mapping_is_positional():
    raw_terms = (A, B, TOKEN, 10**9, 86_400, 1_000, 2_000, b"\x01" * 32)
    raw = (5, raw_terms, 3, A, 100, 101, 102, 0, 100, [TOKEN, TOKEN2], 0, 0, 0, 0, 10**9)
    ag = abi.agreement_from_raw(raw)
    assert ag.id == 5 and ag.status == 3 and ag.tokens == [TOKEN, TOKEN2] and ag.terms.trader == B
    cfg = abi.config_from_raw((B, A, 100, 100, False, "0x" + "00" * 20, 0, 600), owner=A)
    assert cfg.pending_router is None and cfg.owner == A and cfg.router == B
    res = abi.reservation_from_raw((1, A, TOKEN, 5, 10, 1, 7, b"\x02" * 32))
    assert res.status == 1 and res.original == 10
    assert abi.token_info_from_raw((True, False)).allowed is True
    prev = abi.settle_preview_from_raw(([TOKEN2], [1], [2], 3, 4, 5))
    assert prev.keeper_floor == 5 and prev.tokens == [TOKEN2]
