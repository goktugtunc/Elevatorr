// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IUniswapV2Router02Like} from "../interfaces/IUniswapV2Router02Like.sol";

/// @title MockRouter
/// @notice Sabit (num/den) fiyatlı Uniswap V2 benzeri router (§8.1). Testnet'e deploy edilir.
///         Girdi `msg.sender`'dan çekilir (Uniswap semantiği); çıktı kendi likiditesinden ödenir.
contract MockRouter is IUniswapV2Router02Like, Ownable {
    using SafeERC20 for IERC20;

    struct Price {
        uint256 num;
        uint256 den;
    }

    /// @notice Yönlü fiyat: out = in * num / den; prices[tokenIn][tokenOut]
    mapping(address => mapping(address => Price)) public prices;

    /// @notice false ise router `amountOutMin`'i yok sayar (vault'un kendi re-check'ini test etmek için)
    bool public strict = true;

    error NoPrice(address tokenIn, address tokenOut);
    error InvalidPath();
    error InsufficientOutput(uint256 amountOut, uint256 amountOutMin);
    error DeadlineExpired();
    error InvalidAmount();

    event PriceSet(address indexed tokenIn, address indexed tokenOut, uint256 num, uint256 den);
    event PriceCleared(address indexed tokenIn, address indexed tokenOut);
    event StrictSet(bool strict);

    constructor(address owner_) Ownable(owner_) {}

    // ---- Admin -----------------------------------------------------------

    function setPrice(address tokenIn, address tokenOut, uint256 num, uint256 den) external onlyOwner {
        if (num == 0 || den == 0) revert InvalidAmount();
        prices[tokenIn][tokenOut] = Price(num, den);
        emit PriceSet(tokenIn, tokenOut, num, den);
    }

    function clearPrice(address tokenIn, address tokenOut) external onlyOwner {
        delete prices[tokenIn][tokenOut];
        emit PriceCleared(tokenIn, tokenOut);
    }

    function setStrict(bool strict_) external onlyOwner {
        strict = strict_;
        emit StrictSet(strict_);
    }

    function withdraw(address token, address to, uint256 amount) external onlyOwner {
        if (amount == 0) revert InvalidAmount();
        IERC20(token).safeTransfer(to, amount);
    }

    // ---- Router ----------------------------------------------------------

    function getAmountsOut(uint256 amountIn, address[] calldata path)
        external
        view
        override
        returns (uint256[] memory amounts)
    {
        return _amountsOut(amountIn, path);
    }

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external override returns (uint256[] memory amounts) {
        if (block.timestamp > deadline) revert DeadlineExpired();
        amounts = _amountsOut(amountIn, path);
        uint256 out = amounts[amounts.length - 1];
        if (strict && out < amountOutMin) revert InsufficientOutput(out, amountOutMin);
        IERC20(path[0]).safeTransferFrom(msg.sender, address(this), amountIn);
        IERC20(path[path.length - 1]).safeTransfer(to, out);
    }

    // ---- İç ---------------------------------------------------------------

    function _amountsOut(uint256 amountIn, address[] calldata path) internal view returns (uint256[] memory amounts) {
        if (amountIn == 0) revert InvalidAmount();
        if (path.length < 2) revert InvalidPath();
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
