"""Chain layer: lazy singleton for the Monad gateway. Only ``app.services.chain.*`` imports web3.

Routers take the gateway through ``app.api_deps.ChainDep``; tests swap in ``FakeChainGateway`` with
``set_chain``. The concrete class lives in ``app.services.chain.monad.MonadGateway`` and is imported
lazily so importing this package never touches the network or web3.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.chain.gateway import ChainGateway

_chain: Any | None = None


def get_chain() -> ChainGateway:
    """Configured Monad gateway (contract reads, calldata building, receipts, logs)."""
    global _chain
    if _chain is None:
        from app.core.config import get_settings
        from app.services.chain.monad import MonadGateway

        _chain = MonadGateway(get_settings())
    return _chain


def set_chain(gw: Any | None) -> None:
    """Test hook: inject a FakeChainGateway (None resets to the lazy default)."""
    global _chain
    _chain = gw


__all__ = ["get_chain", "set_chain"]
