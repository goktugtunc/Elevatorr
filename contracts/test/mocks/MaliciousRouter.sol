// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IUniswapV2Router02Like} from "../../src/interfaces/IUniswapV2Router02Like.sol";

/// @notice Vault'un savunmalarını test eden kötü niyetli router (§8.3). Sahipsiz; yalnız test.
///         Fiyat modeli MockRouter ile aynı (out = in * num / den), likiditeyi kendi tutar.
contract MaliciousRouter is IUniswapV2Router02Like {
    using SafeERC20 for IERC20;

    enum Mode {
        NORMAL, // strict Uniswap davranışı
        REPORT_MORE, // bildirdiğinin yarısını gönderir
        SEND_MORE, // bildirdiğinin iki katını gönderir
        IGNORE_MIN, // amountOutMin'i yok sayar
        NO_PULL, // girdiyi çekmez, çıktıyı gönderir
        REENTER, // swap içinde `reenterTarget.call(reenterData)`
        GAS_BURN // getAmountsOut revert eder (quote yok)
    }

    struct Price {
        uint256 num;
        uint256 den;
    }

    Mode public mode;
    mapping(address => mapping(address => Price)) public prices;

    address public reenterTarget;
    bytes public reenterData;
    bool public reenterBubble = true;
    bool public reenterOk;
    bytes public reenterReturn;

    error NoPrice(address tokenIn, address tokenOut);
    error InsufficientOutput(uint256 amountOut, uint256 amountOutMin);
    error QuoteFailed();

    function setMode(Mode m) external {
        mode = m;
    }

    function setPrice(address tokenIn, address tokenOut, uint256 num, uint256 den) external {
        prices[tokenIn][tokenOut] = Price(num, den);
    }

    function setReenter(address target, bytes calldata data, bool bubble) external {
        reenterTarget = target;
        reenterData = data;
        reenterBubble = bubble;
    }

    function getAmountsOut(uint256 amountIn, address[] calldata path)
        external
        view
        override
        returns (uint256[] memory amounts)
    {
        if (mode == Mode.GAS_BURN) revert QuoteFailed();
        return _amountsOut(amountIn, path);
    }

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 /* deadline */
    ) external override returns (uint256[] memory amounts) {
        amounts = _amountsOut(amountIn, path);
        uint256 out = amounts[amounts.length - 1];
        Mode m = mode;
        if (m != Mode.IGNORE_MIN && out < amountOutMin) revert InsufficientOutput(out, amountOutMin);

        if (m != Mode.NO_PULL) IERC20(path[0]).safeTransferFrom(msg.sender, address(this), amountIn);

        uint256 send = out;
        if (m == Mode.REPORT_MORE) send = out / 2;
        if (m == Mode.SEND_MORE) send = out * 2;
        IERC20(path[path.length - 1]).safeTransfer(to, send);

        if (m == Mode.REENTER) {
            (bool ok, bytes memory ret) = reenterTarget.call(reenterData);
            reenterOk = ok;
            reenterReturn = ret;
            if (!ok && reenterBubble) {
                assembly ("memory-safe") {
                    revert(add(ret, 0x20), mload(ret))
                }
            }
        }
    }

    function _amountsOut(uint256 amountIn, address[] calldata path) internal view returns (uint256[] memory amounts) {
        amounts = new uint256[](path.length);
        amounts[0] = amountIn;
        uint256 current = amountIn;
        for (uint256 i = 0; i + 1 < path.length; ++i) {
            Price memory p = prices[path[i]][path[i + 1]];
            if (p.den == 0) revert NoPrice(path[i], path[i + 1]);
            current = current * p.num / p.den;
            amounts[i + 1] = current;
        }
    }
}
