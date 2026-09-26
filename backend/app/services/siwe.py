"""Sign-In with Ethereum (EIP-4361) message helpers (03-backend-tasarim §3.2).

The server builds the message, so ``parse_message`` only has to understand the subset this module produces (plus the
optional ``Not Before`` / ``Request ID`` / ``Resources`` fields a strict wallet might echo back). Signature recovery is
plain EIP-191 (``personal_sign``) through ``eth_account``; EIP-1271 smart accounts are out of scope for this sprint.
No ``siwe`` PyPI dependency on purpose (see §3.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from eth_account import Account
from eth_account.messages import encode_defunct

from app.services.chain.addresses import checksum, is_evm_address

MESSAGE_TEMPLATE = (
    "{domain} wants you to sign in with your Ethereum account:\n{address}\n\n{statement}\n\n"
    "URI: {uri}\nVersion: 1\nChain ID: {chain_id}\nNonce: {nonce}\nIssued At: {issued_at}\n"
    "Expiration Time: {expiration_time}"
)

HEADER_RE = re.compile(r"^(?P<domain>\S+) wants you to sign in with your Ethereum account:$")
NONCE_RE = re.compile(r"^[A-Za-z0-9]{8,}$")
SIGNATURE_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")

_REQUIRED_FIELDS = ("URI", "Version", "Chain ID", "Nonce", "Issued At")
_KNOWN_FIELDS = _REQUIRED_FIELDS + ("Expiration Time", "Not Before", "Request ID", "Resources")


class SiweError(ValueError):
    """Base class for message-level failures."""


class SiweParseError(SiweError):
    """Text is not a well-formed EIP-4361 message (mapped to ``siwe_invalid``)."""


class SignatureInvalid(SiweError):
    """Signature is malformed or does not recover to any address (mapped to ``signature_invalid``)."""


@dataclass(frozen=True)
class SiweFields:
    domain: str
    address: str  # as written in the message (checksum when we produced it)
    statement: str | None
    uri: str
    version: str
    chain_id: int
    nonce: str
    issued_at: datetime
    expiration_time: datetime | None
    not_before: datetime | None
    request_id: str | None = None
    resources: tuple[str, ...] = ()


# --- time ------------------------------------------------------------------------------------------------------


def format_timestamp(dt: datetime) -> str:
    """RFC 3339 in UTC with millisecond precision: ``2026-09-26T12:00:00.000Z``."""
    dt = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_timestamp(value: str) -> datetime:
    """RFC 3339 → aware UTC datetime; raises ``SiweParseError``."""
    v = value.strip()
    if v.endswith(("Z", "z")):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError as e:
        raise SiweParseError(f"invalid timestamp: {value!r}") from e
    if dt.tzinfo is None:
        raise SiweParseError(f"timestamp without timezone: {value!r}")
    return dt.astimezone(UTC)


# --- build / parse -----------------------------------------------------------------------------------------------


def build_message(
    *,
    domain: str,
    address: str,
    statement: str,
    uri: str,
    chain_id: int,
    nonce: str,
    issued_at: datetime,
    expiration_time: datetime,
) -> str:
    """The exact text the wallet signs. Address is written in EIP-55 checksum form as EIP-4361 requires."""
    return MESSAGE_TEMPLATE.format(
        domain=domain,
        address=checksum(address),
        statement=statement,
        uri=uri,
        chain_id=int(chain_id),
        nonce=nonce,
        issued_at=format_timestamp(issued_at),
        expiration_time=format_timestamp(expiration_time),
    )


def parse_message(text: str) -> SiweFields:
    """Line-based EIP-4361 parser for the grammar subset we emit. Raises ``SiweParseError``."""
    if not isinstance(text, str) or not text:
        raise SiweParseError("empty message")
    lines = text.split("\n")
    if len(lines) < 4:
        raise SiweParseError("message too short")

    m = HEADER_RE.match(lines[0])
    if m is None:
        raise SiweParseError("bad header line")
    domain = m.group("domain")

    address = lines[1].strip()
    if not is_evm_address(address):
        raise SiweParseError("bad address line")

    if lines[2] != "":
        raise SiweParseError("expected blank line after address")

    # Optional statement: either "<statement>\n\n" or a single blank line when absent.
    statement: str | None
    idx = 3
    if lines[3] == "":
        statement = None
        idx = 4
    elif lines[3].startswith("URI: "):
        # Some libraries emit no statement and only one blank line; be lenient.
        statement = None
        idx = 3
    else:
        statement = lines[3]
        if len(lines) <= 4 or lines[4] != "":
            raise SiweParseError("expected blank line after statement")
        idx = 5

    fields: dict[str, str] = {}
    resources: list[str] = []
    in_resources = False
    for raw in lines[idx:]:
        if in_resources:
            if raw.startswith("- "):
                resources.append(raw[2:])
                continue
            raise SiweParseError("unexpected line after resources")
        key, sep, value = raw.partition(": ")
        if not sep and raw == "Resources:":
            in_resources = True
            continue
        if not sep or key not in _KNOWN_FIELDS or key in fields:
            raise SiweParseError(f"bad field line: {raw!r}")
        fields[key] = value

    missing = [k for k in _REQUIRED_FIELDS if k not in fields]
    if missing:
        raise SiweParseError(f"missing fields: {', '.join(missing)}")
    if fields["Version"] != "1":
        raise SiweParseError("unsupported version")
    try:
        chain_id = int(fields["Chain ID"])
    except ValueError as e:
        raise SiweParseError("bad chain id") from e
    nonce = fields["Nonce"]
    if not NONCE_RE.match(nonce):
        raise SiweParseError("bad nonce")

    return SiweFields(
        domain=domain,
        address=address,
        statement=statement,
        uri=fields["URI"],
        version=fields["Version"],
        chain_id=chain_id,
        nonce=nonce,
        issued_at=parse_timestamp(fields["Issued At"]),
        expiration_time=parse_timestamp(fields["Expiration Time"]) if "Expiration Time" in fields else None,
        not_before=parse_timestamp(fields["Not Before"]) if "Not Before" in fields else None,
        request_id=fields.get("Request ID"),
        resources=tuple(resources),
    )


# --- signatures --------------------------------------------------------------------------------------------------


def recover_address(message: str, signature: str) -> str:
    """EIP-191 recovery: ``0x`` + 130 hex signature over ``message`` → signer address (lower-case).

    Raises ``SignatureInvalid`` for a malformed or unrecoverable signature.
    """
    if not isinstance(signature, str) or not SIGNATURE_RE.match(signature.strip()):
        raise SignatureInvalid("signature must be 0x + 130 hex characters")
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature.strip())
    except Exception as e:  # eth_keys raises assorted BadSignature / ValueError types
        raise SignatureInvalid("signature could not be recovered") from e
    return str(recovered).lower()


__all__ = [
    "MESSAGE_TEMPLATE",
    "SIGNATURE_RE",
    "SignatureInvalid",
    "SiweError",
    "SiweFields",
    "SiweParseError",
    "build_message",
    "format_timestamp",
    "parse_message",
    "parse_timestamp",
    "recover_address",
]
