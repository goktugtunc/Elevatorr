"""ABI plumbing (03-backend-tasarim §1.7): JSON loading, calldata encoding, custom-error selector table,
revert decoding and event (log) decoding for ``TraderVault`` / ``MockRouter`` / ``TestToken`` / ``IERC20``.

Everything here is pure (no network); ``MonadGateway`` and ``FakeChainGateway`` both go through it so the
production decode path is exercised by the fake's tests. ``VaultError`` codes follow 01-kontrat-spec §5 and are
never renumbered.
"""
from __future__ import annotations

import functools
import json
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import event_abi_to_log_topic, function_abi_to_4byte_selector, keccak
from web3 import Web3

from app.services.chain.addresses import checksum, normalize
from app.services.chain.types import (
    AgreementView,
    ConfigView,
    EventRecord,
    ReservationView,
    RevertInfo,
    SettlePreview,
    Terms,
    TokenInfoView,
)

ABI_DIR = Path(__file__).resolve().parent / "abi"

# --- contract constants (01-kontrat-spec §3.1) ------------------------------------------------------
MAX_TOKENS = 6
MIN_DURATION = 86_400
MAX_DURATION = 94_608_000
MAX_COMMISSION_BPS = 5_000
MAX_PLATFORM_FEE_BPS = 1_000
MIN_DRAWDOWN_BPS = 100
MAX_SETTLE_SLIPPAGE_BPS = 5_000
KEEPER_GRACE = 604_800
BPS_DENOM = 10_000
MAX_ROUTER_DELAY = 2_592_000
CONFIG_KEYS = frozenset({"router", "fees", "paused", "slippage"})

ERROR_STRING_SELECTOR = "0x08c379a0"  # Error(string)
PANIC_SELECTOR = "0x4e487b71"  # Panic(uint256)
PANIC_OVERFLOW = 0x11
GENERIC_REVERT_MESSAGE = "işlem geri alındı"


class VaultError(IntEnum):
    """01-kontrat-spec §5 codes (1–30). Names are the backend names (OZ errors map onto 1/2/23/28/29/30)."""

    NotInitialized = 1
    Unauthorized = 2
    Paused = 3
    InvalidTerms = 4
    TokenNotAllowed = 5
    NotFound = 6
    WrongStatus = 7
    Expired = 8
    NotExpired = 9
    InsufficientBalance = 10
    TooManyTokens = 11
    DrawdownBreached = 12
    SlippageExceeded = 13
    Overflow = 14
    InvalidAmount = 15
    RouterError = 16
    NotParty = 17
    ReservationNotFound = 18
    ReservationClosed = 19
    ReservationInsufficient = 20
    ReservationMismatch = 21
    ZeroAmount = 22
    ZeroAddress = 23
    InvalidRouter = 24
    RouterChangeNotReady = 25
    NoPendingRouterChange = 26
    InvalidToken = 27
    Reentrancy = 28
    TransferFailed = 29
    UpgradeError = 30


ERROR_MESSAGES: dict[VaultError, str] = {
    VaultError.NotInitialized: "Kasa kontratı henüz başlatılmamış.",
    VaultError.Unauthorized: "Bu işlemi yalnızca ilgili taraf yapabilir.",
    VaultError.Paused: "Kasa geçici olarak durdurulmuş; işlem şu an yapılamaz.",
    VaultError.InvalidTerms: "Anlaşma koşulları geçersiz.",
    VaultError.TokenNotAllowed: "Bu token kasada işlem için izinli değil.",
    VaultError.NotFound: "Anlaşma zincirde bulunamadı.",
    VaultError.WrongStatus: "Anlaşma bu işlem için uygun durumda değil.",
    VaultError.Expired: "Anlaşma süresi dolmuş; işlem yapılamaz.",
    VaultError.NotExpired: "Anlaşma süresi henüz dolmadı.",
    VaultError.InsufficientBalance: "Kasadaki bakiye bu işlem için yetersiz.",
    VaultError.TooManyTokens: "Anlaşma en fazla altı farklı token tutabilir.",
    VaultError.DrawdownBreached: "İşlem izin verilen azami zarar sınırını aşıyor.",
    VaultError.SlippageExceeded: "Fiyat kayması tolerans sınırını aştı.",
    VaultError.Overflow: "Aritmetik taşma oluştu.",
    VaultError.InvalidAmount: "Tutar listesi anlaşmadaki token sayısıyla uyuşmuyor.",
    VaultError.RouterError: "Takas yönlendiricisi işlemi reddetti.",
    VaultError.NotParty: "Bu anlaşmanın tarafı değilsiniz.",
    VaultError.ReservationNotFound: "Rezervasyon bulunamadı.",
    VaultError.ReservationClosed: "Rezervasyon kapatılmış.",
    VaultError.ReservationInsufficient: "Rezervasyondaki tutar yetersiz.",
    VaultError.ReservationMismatch: "Rezervasyon bu anlaşmayla eşleşmiyor.",
    VaultError.ZeroAmount: "Tutar sıfır olamaz.",
    VaultError.ZeroAddress: "Adres boş olamaz.",
    VaultError.InvalidRouter: "Geçersiz yönlendirici adresi.",
    VaultError.RouterChangeNotReady: "Yönlendirici değişikliği için bekleme süresi dolmadı.",
    VaultError.NoPendingRouterChange: "Bekleyen yönlendirici değişikliği yok.",
    VaultError.InvalidToken: "Geçersiz token adresi.",
    VaultError.Reentrancy: "Yeniden giriş engellendi.",
    VaultError.TransferFailed: "Token transferi başarısız oldu (onay veya bakiye eksik).",
    VaultError.UpgradeError: "Kontrat yükseltme hatası.",
}

# Solidity error name -> VaultError code (vault's own errors share their name; OZ errors are folded in).
_OZ_ERROR_CODES: dict[str, VaultError] = {
    "OwnableUnauthorizedAccount": VaultError.Unauthorized,
    "OwnableInvalidOwner": VaultError.ZeroAddress,
    "InvalidInitialization": VaultError.NotInitialized,
    "NotInitializing": VaultError.NotInitialized,
    "ReentrancyGuardReentrantCall": VaultError.Reentrancy,
    "SafeERC20FailedOperation": VaultError.TransferFailed,
    "UUPSUnauthorizedCallContext": VaultError.UpgradeError,
    "UUPSUnsupportedProxiableUUID": VaultError.UpgradeError,
    "ERC1967InvalidImplementation": VaultError.UpgradeError,
    "ERC1967NonPayable": VaultError.UpgradeError,
    "AddressEmptyCode": VaultError.UpgradeError,
    "FailedCall": VaultError.UpgradeError,
}
_ROUTER_ERROR_NAMES = frozenset({"NoPrice", "InvalidPath", "InsufficientOutput", "DeadlineExpired"})


# --- ABI loading -------------------------------------------------------------------------------------


@functools.cache
def load_abi(name: str) -> list[dict]:
    """``abi/<name>.json`` (either a bare ABI list or a forge artifact with an ``abi`` key)."""
    path = ABI_DIR / f"{name}.json"
    with path.open("rb") as fh:
        raw = json.load(fh)
    abi = raw.get("abi", []) if isinstance(raw, dict) else raw
    if not isinstance(abi, list):
        raise ValueError(f"{path} does not contain an ABI list")
    return abi


@functools.cache
def _codec_w3() -> Web3:
    """Provider-less Web3 used only for its ABI codec (encode/decode); never talks to a network."""
    return Web3()


@functools.cache
def _factory(abi_name: str):
    return _codec_w3().eth.contract(abi=load_abi(abi_name))


@functools.cache
def _decoder(abi_name: str):
    return _codec_w3().eth.contract(address=checksum("0x" + "00" * 20), abi=load_abi(abi_name))


def vault_contract(w3: Any, address: str):
    return w3.eth.contract(address=checksum(address), abi=load_abi("TraderVault"))


def erc20_contract(w3: Any, address: str):
    return w3.eth.contract(address=checksum(address), abi=load_abi("IERC20"))


def router_contract(w3: Any, address: str):
    return w3.eth.contract(address=checksum(address), abi=load_abi("MockRouter"))


def test_token_contract(w3: Any, address: str):
    return w3.eth.contract(address=checksum(address), abi=load_abi("TestToken"))


def has_function(abi_name: str, fn: str) -> bool:
    return any(e.get("type") == "function" and e.get("name") == fn for e in load_abi(abi_name))


# --- calldata ----------------------------------------------------------------------------------------


def _abi_arg(value: Any) -> Any:
    """Checksum lower-case address strings (web3 refuses non-checksum addresses); recurse into containers."""
    if isinstance(value, Terms):
        return value.as_abi_tuple()
    if isinstance(value, str) and len(value) == 42 and value.startswith("0x"):
        try:
            return checksum(value)
        except Exception:  # noqa: BLE001 - not an address after all (e.g. a 40-hex-char string)
            return value
    if isinstance(value, tuple):
        return tuple(_abi_arg(v) for v in value)
    if isinstance(value, list):
        return [_abi_arg(v) for v in value]
    return value


def encode_call(abi_name: str, fn: str, args: list | tuple | None = None) -> str:
    """Calldata hex for ``fn(*args)`` from ``abi/<abi_name>.json``. Addresses may be lower-case."""
    prepared = [_abi_arg(a) for a in (args or [])]
    return _factory(abi_name).encode_abi(fn, args=prepared)


def decode_call(abi_name: str, data: str | bytes) -> tuple[str, dict[str, Any]]:
    """Inverse of ``encode_call``: ``(fn_name, {arg_name: value})``; struct args come back as dicts,
    addresses lower-cased. Raises ``ValueError`` for an unknown selector."""
    hexdata = _hexstr(data)
    fn, args = _decoder(abi_name).decode_function_input(hexdata)
    return fn.fn_name, {k: _lower_addresses(v) for k, v in args.items()}


def selector_of(signature: str) -> str:
    """``keccak256(signature)[:4]`` as ``0x`` hex, e.g. ``selector_of("NotFound()") == "0xc5723b51"``."""
    return "0x" + keccak(text=signature)[:4].hex()


def _signature(entry: dict) -> str:
    return f"{entry['name']}({','.join(_type_of(i) for i in entry.get('inputs', []))})"


def _type_of(inp: dict) -> str:
    t = inp["type"]
    if t.startswith("tuple"):
        inner = ",".join(_type_of(c) for c in inp["components"])
        return f"({inner}){t[5:]}"
    return t


@functools.cache
def function_selectors(abi_name: str) -> dict[str, str]:
    """selector -> function name for every function in the ABI (fake dispatch, tests)."""
    return {
        "0x" + function_abi_to_4byte_selector(e).hex(): e["name"]
        for e in load_abi(abi_name)
        if e.get("type") == "function"
    }


# --- custom errors -----------------------------------------------------------------------------------


def _build_error_table() -> tuple[dict[str, tuple[str, int | None, list[str]]], dict[str, list[str]]]:
    selectors: dict[str, tuple[str, int | None, list[str]]] = {}
    arg_names: dict[str, list[str]] = {}
    vault_names = {e.name for e in VaultError}
    for abi_name in ("TraderVault", "MockRouter", "TestToken"):
        for entry in load_abi(abi_name):
            if entry.get("type") != "error":
                continue
            sel = selector_of(_signature(entry))
            if sel in selectors and abi_name != "TraderVault":
                continue  # vault definition wins (shared OZ errors, InvalidAmount)
            name = entry["name"]
            code: int | None
            if name in _OZ_ERROR_CODES:
                code = int(_OZ_ERROR_CODES[name])
            elif name in vault_names and abi_name == "TraderVault":
                code = int(VaultError[name])
            else:
                code = None
            selectors[sel] = (name, code, [_type_of(i) for i in entry.get("inputs", [])])
            arg_names[sel] = [i.get("name") or f"arg{n}" for n, i in enumerate(entry.get("inputs", []))]
    selectors[ERROR_STRING_SELECTOR] = ("Error", None, ["string"])
    arg_names[ERROR_STRING_SELECTOR] = ["reason"]
    selectors[PANIC_SELECTOR] = ("Panic", None, ["uint256"])
    arg_names[PANIC_SELECTOR] = ["code"]
    return selectors, arg_names


ERROR_SELECTORS, _ERROR_ARG_NAMES = _build_error_table()
_ERROR_SELECTOR_BY_NAME: dict[str, str] = {v[0]: k for k, v in ERROR_SELECTORS.items()}


def _backend_error_name(solidity_name: str, code: int | None) -> str:
    if code is not None:
        return VaultError(code).name
    if solidity_name.startswith("ERC20"):
        return f"erc20:{solidity_name}"
    if solidity_name in _ROUTER_ERROR_NAMES:
        return f"router:{solidity_name}"
    return solidity_name


def _ascii_or_hex(raw: bytes) -> str:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return "0x" + raw.hex()
    return text if text.isprintable() else "0x" + raw.hex()


def _inner_label(reason: bytes) -> str:
    if not reason:
        return "empty"
    if len(reason) >= 4:
        inner = decode_revert(reason)
        if inner.name:
            return inner.name
    return _ascii_or_hex(reason)


def decode_revert(data: bytes | str | None) -> RevertInfo:
    """Revert payload -> ``RevertInfo`` (03 §1.7). Empty -> generic; ``RouterError(bytes)`` decodes its reason."""
    raw = _bytes(data)
    if not raw:
        return RevertInfo(selector=None, name=None, code=None, args={}, message=GENERIC_REVERT_MESSAGE)
    if len(raw) < 4:
        return RevertInfo(selector=None, name=None, code=None, args={"data": "0x" + raw.hex()},
                          message=f"{GENERIC_REVERT_MESSAGE} (bozuk veri)")
    selector = "0x" + raw[:4].hex()
    entry = ERROR_SELECTORS.get(selector)
    if entry is None:
        return RevertInfo(selector=selector, name=None, code=None, args={"data": "0x" + raw.hex()},
                          message=f"{GENERIC_REVERT_MESSAGE} (bilinmeyen hata {selector})")
    solidity_name, code, types = entry
    args: dict[str, Any] = {}
    if types:
        try:
            values = abi_decode(types, raw[4:])
        except Exception:  # noqa: BLE001 - malformed payload; keep the selector, drop the args
            values = ()
        args = {n: _lower_addresses(v) for n, v in zip(_ERROR_ARG_NAMES[selector], values, strict=False)}

    if selector == PANIC_SELECTOR:
        panic_code = int(args.get("code", 0))
        if panic_code == PANIC_OVERFLOW:
            return RevertInfo(selector=selector, name=VaultError.Overflow.name, code=int(VaultError.Overflow),
                              args=args, message=ERROR_MESSAGES[VaultError.Overflow])
        return RevertInfo(selector=selector, name="Panic", code=None, args=args,
                          message=f"{GENERIC_REVERT_MESSAGE} (panic 0x{panic_code:02x})")
    if selector == ERROR_STRING_SELECTOR:
        reason = str(args.get("reason", "") or "")
        return RevertInfo(selector=selector, name="Error", code=None, args=args,
                          message=reason or GENERIC_REVERT_MESSAGE)
    if code == VaultError.RouterError:
        reason = args.get("reason", b"")
        reason_bytes = reason if isinstance(reason, bytes | bytearray) else b""
        label = _inner_label(bytes(reason_bytes))
        return RevertInfo(selector=selector, name=VaultError.RouterError.name, code=int(code),
                          args={"reason": "0x" + bytes(reason_bytes).hex(), "inner": label},
                          message=f"RouterError: {label}")

    name = _backend_error_name(solidity_name, code)
    if code is not None:
        message = ERROR_MESSAGES[VaultError(code)]
        if solidity_name != name:
            message = f"{message} ({solidity_name})"
    else:
        message = f"{solidity_name}({', '.join(f'{k}={v}' for k, v in args.items())})"
    return RevertInfo(selector=selector, name=name, code=code, args=args, message=message)


def error_code_of(info: RevertInfo) -> str:
    """API ``error_code``: ``vault:<Name>`` (coded), ``erc20:<Name>``, ``router:<Name>``, ``panic:<hex>``, ``reverted``."""
    if info.code is not None and info.name:
        return f"vault:{info.name}"
    if info.name and (info.name.startswith("erc20:") or info.name.startswith("router:")):
        return info.name
    if info.name == "Panic":
        return f"panic:0x{int(info.args.get('code', 0)):02x}"
    if info.name == "OutOfGas":
        return "out_of_gas"
    return "reverted"


def encode_revert(name: str, args: list | tuple | None = None) -> bytes:
    """ABI-encode a custom error by Solidity name (fake gateway, tests). ``Error``/``Panic`` accepted too."""
    if name not in _ERROR_SELECTOR_BY_NAME:
        raise KeyError(f"unknown error {name}")
    selector = _ERROR_SELECTOR_BY_NAME[name]
    _, _, types = ERROR_SELECTORS[selector]
    prepared = [_abi_arg(a) for a in (args or [])]
    return bytes.fromhex(selector[2:]) + (abi_encode(types, prepared) if types else b"")


def revert_info_for(name: str, args: list | tuple | None = None) -> RevertInfo:
    """``decode_revert(encode_revert(name, args))`` — what a real node would hand back for that revert."""
    return decode_revert(encode_revert(name, args))


# --- events ------------------------------------------------------------------------------------------


def _build_event_table() -> dict[str, dict]:
    topics: dict[str, dict] = {}
    for abi_name in ("TraderVault", "IERC20"):
        for entry in load_abi(abi_name):
            if entry.get("type") != "event":
                continue
            topics.setdefault("0x" + event_abi_to_log_topic(entry).hex(), entry)
    return topics


EVENT_TOPICS: dict[str, dict] = _build_event_table()
_EVENT_BY_NAME: dict[str, dict] = {e["name"]: e for e in EVENT_TOPICS.values()}
_STATIC_INDEXED = ("address", "bool", "uint", "int", "bytes32")


def event_topic(name: str) -> str:
    """topic0 of an event by name (``get_logs`` filters, tests)."""
    return "0x" + event_abi_to_log_topic(_EVENT_BY_NAME[name]).hex()


def decode_log(log: Any) -> EventRecord | None:
    """One raw ``eth_getLogs`` / receipt log -> ``EventRecord`` (None when topic0 is not a known event).

    Accepts web3 ``AttributeDict``/``LogReceipt`` or a plain dict with hex strings; values may be ``HexBytes``.
    ``args`` hold ABI names: addresses lower-case, ``uint``/``int`` -> int, ``bytes32`` -> bytes, ``bool``.
    """
    get = _getter(log)
    raw_topics = get("topics") or []
    if not raw_topics:
        return None
    topic0 = _hexstr(raw_topics[0]).lower()
    event = EVENT_TOPICS.get(topic0)
    if event is None:
        return None
    indexed = [i for i in event["inputs"] if i.get("indexed")]
    unindexed = [i for i in event["inputs"] if not i.get("indexed")]
    if len(raw_topics) != len(indexed) + 1:
        return None
    args: dict[str, Any] = {}
    for inp, topic in zip(indexed, raw_topics[1:], strict=True):
        tb = _bytes(topic)
        if inp["type"].startswith(_STATIC_INDEXED) and not inp["type"].endswith("]"):
            args[inp["name"]] = _lower_addresses(abi_decode([inp["type"]], tb)[0])
        else:  # dynamic indexed values are keccak hashes; keep the topic
            args[inp["name"]] = "0x" + tb.hex()
    data = _bytes(get("data"))
    if unindexed:
        values = abi_decode([_type_of(i) for i in unindexed], data)
        for inp, value in zip(unindexed, values, strict=True):
            args[inp["name"]] = _lower_addresses(value)
    ts = get("blockTimestamp")
    return EventRecord(
        block_number=_int(get("blockNumber")),
        block_hash=_hexstr(get("blockHash")).lower(),
        log_index=_int(get("logIndex")),
        tx_hash=_hexstr(get("transactionHash")).lower(),
        tx_index=_int(get("transactionIndex") or 0),
        address=normalize(_hexstr(get("address"))),
        name=event["name"],
        args=args,
        topic0=topic0,
        block_timestamp=ts if isinstance(ts, datetime) else None,
        removed=bool(get("removed") or False),
    )


def decode_receipt_logs(receipt_logs: list[Any], vault: str) -> list[EventRecord]:
    """Only logs emitted by ``vault`` (lower-case compare) that decode to a known event."""
    vault_l = normalize(vault)
    out: list[EventRecord] = []
    for log in receipt_logs:
        try:
            addr = normalize(_hexstr(_getter(log)("address")))
        except Exception:  # noqa: BLE001
            continue
        if addr != vault_l:
            continue
        rec = decode_log(log)
        if rec is not None:
            out.append(rec)
    return out


def encode_log(
    name: str,
    args: dict[str, Any],
    *,
    address: str,
    block_number: int,
    block_hash: str,
    log_index: int,
    tx_hash: str,
    tx_index: int = 0,
) -> dict[str, Any]:
    """ABI-encode an event into a raw log dict shaped like ``eth_getLogs`` output (fake gateway).

    ``args`` are keyed by ABI names; addresses may be lower-case, ``bytes32`` as bytes, ints/bools as is.
    """
    event = _EVENT_BY_NAME[name]
    topics = ["0x" + event_abi_to_log_topic(event).hex()]
    data_types: list[str] = []
    data_values: list[Any] = []
    for inp in event["inputs"]:
        value = _abi_arg(args[inp["name"]])
        if inp.get("indexed"):
            topics.append("0x" + abi_encode([inp["type"]], [value]).hex())
        else:
            data_types.append(_type_of(inp))
            data_values.append(value)
    return {
        "address": checksum(address),
        "topics": topics,
        "data": "0x" + (abi_encode(data_types, data_values).hex() if data_types else ""),
        "blockNumber": int(block_number),
        "blockHash": block_hash,
        "logIndex": int(log_index),
        "transactionHash": tx_hash,
        "transactionIndex": int(tx_index),
        "removed": False,
    }


def config_key_of(b32: bytes | str) -> str:
    """``bytes32("router")`` (right zero-padded ASCII) -> ``"router"``; unknown keys come back as hex."""
    raw = _bytes(b32)
    text = raw.rstrip(b"\x00")
    try:
        key = text.decode("ascii")
    except UnicodeDecodeError:
        return "0x" + raw.hex()
    return key if key in CONFIG_KEYS else ("0x" + raw.hex() if not key.isprintable() else key)


def config_key_bytes(key: str) -> bytes:
    """Inverse of ``config_key_of``: ``"fees"`` -> 32-byte right zero-padded."""
    if key not in CONFIG_KEYS:
        raise ValueError(f"unknown config key {key!r}")
    return key.encode("ascii").ljust(32, b"\x00")


# --- struct -> view mapping (positional; component order = 01-kontrat-spec §2.2) ----------------------


def _seq(v: Any) -> tuple:
    if isinstance(v, dict):
        return tuple(v.values())
    return tuple(v)


def terms_from_raw(raw: Any) -> Terms:
    t = _seq(raw)
    return Terms(
        customer=normalize(t[0]),
        trader=normalize(t[1]),
        base_token=normalize(t[2]),
        principal=int(t[3]),
        duration_seconds=int(t[4]),
        commission_bps=int(t[5]),
        max_drawdown_bps=int(t[6]),
        listing_ref=bytes(t[7]),
    )


def agreement_from_raw(raw: Any) -> AgreementView:
    a = _seq(raw)
    return AgreementView(
        id=int(a[0]),
        terms=terms_from_raw(a[1]),
        status=int(a[2]),
        proposer=normalize(a[3]),
        created_at=int(a[4]),
        start_time=int(a[5]),
        end_time=int(a[6]),
        settled_at=int(a[7]),
        platform_fee_bps=int(a[8]),
        tokens=[normalize(x) for x in a[9]],
        final_value=int(a[10]),
        trader_fee=int(a[11]),
        platform_fee=int(a[12]),
        customer_payout=int(a[13]),
        last_value=int(a[14]),
    )


def reservation_from_raw(raw: Any) -> ReservationView:
    r = _seq(raw)
    return ReservationView(
        id=int(r[0]),
        customer=normalize(r[1]),
        token=normalize(r[2]),
        amount=int(r[3]),
        original=int(r[4]),
        status=int(r[5]),
        created_at=int(r[6]),
        listing_ref=bytes(r[7]),
    )


def config_from_raw(raw: Any, owner: str) -> ConfigView:
    c = _seq(raw)
    pending = normalize(c[5])
    return ConfigView(
        owner=normalize(owner),
        router=normalize(c[0]),
        fee_recipient=normalize(c[1]),
        platform_fee_bps=int(c[2]),
        settle_slippage_bps=int(c[3]),
        paused=bool(c[4]),
        pending_router=None if int(pending, 16) == 0 else pending,
        router_activation_time=int(c[6]),
        router_delay=int(c[7]),
    )


def token_info_from_raw(raw: Any) -> TokenInfoView:
    t = _seq(raw)
    return TokenInfoView(allowed=bool(t[0]), is_base=bool(t[1]))


def settle_preview_from_raw(raw: Any) -> SettlePreview:
    p = _seq(raw)
    return SettlePreview(
        tokens=[normalize(x) for x in p[0]],
        balances=[int(x) for x in p[1]],
        quotes=[int(x) for x in p[2]],
        estimated_final_value=int(p[3]),
        drawdown_floor=int(p[4]),
        keeper_floor=int(p[5]),
    )


# --- small value helpers -----------------------------------------------------------------------------


def _getter(obj: Any):
    if isinstance(obj, dict) or hasattr(obj, "get"):
        return lambda k: obj.get(k)
    return lambda k: getattr(obj, k, None)


def _hexstr(v: Any) -> str:
    if v is None:
        return "0x"
    if isinstance(v, bytes | bytearray):
        return "0x" + bytes(v).hex()
    s = str(v)
    return s if s.startswith("0x") else "0x" + s


def _bytes(v: Any) -> bytes:
    if v is None:
        return b""
    if isinstance(v, bytes | bytearray):
        return bytes(v)
    if isinstance(v, dict):  # web3 sometimes wraps revert data as {"data": "0x..", ...}
        return _bytes(v.get("data"))
    s = str(v).strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    if not s:
        return b""
    try:
        return bytes.fromhex(s)
    except ValueError:
        return b""


def _int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, bytes | bytearray):
        return int.from_bytes(bytes(v), "big")
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


def _lower_addresses(v: Any) -> Any:
    if isinstance(v, str) and len(v) == 42 and v.startswith("0x"):
        try:
            return normalize(v)
        except Exception:  # noqa: BLE001
            return v
    if isinstance(v, tuple | list):
        return [_lower_addresses(x) for x in v]
    if isinstance(v, dict):
        return {k: _lower_addresses(x) for k, x in v.items()}
    return v


__all__ = [
    "ABI_DIR",
    "BPS_DENOM",
    "CONFIG_KEYS",
    "ERROR_MESSAGES",
    "ERROR_SELECTORS",
    "ERROR_STRING_SELECTOR",
    "EVENT_TOPICS",
    "KEEPER_GRACE",
    "MAX_COMMISSION_BPS",
    "MAX_DURATION",
    "MAX_PLATFORM_FEE_BPS",
    "MAX_ROUTER_DELAY",
    "MAX_SETTLE_SLIPPAGE_BPS",
    "MAX_TOKENS",
    "MIN_DRAWDOWN_BPS",
    "MIN_DURATION",
    "PANIC_SELECTOR",
    "VaultError",
    "agreement_from_raw",
    "config_from_raw",
    "config_key_bytes",
    "config_key_of",
    "decode_call",
    "decode_log",
    "decode_receipt_logs",
    "decode_revert",
    "encode_call",
    "encode_log",
    "encode_revert",
    "erc20_contract",
    "error_code_of",
    "event_topic",
    "function_selectors",
    "has_function",
    "load_abi",
    "reservation_from_raw",
    "revert_info_for",
    "router_contract",
    "selector_of",
    "settle_preview_from_raw",
    "terms_from_raw",
    "test_token_contract",
    "token_info_from_raw",
    "vault_contract",
]
