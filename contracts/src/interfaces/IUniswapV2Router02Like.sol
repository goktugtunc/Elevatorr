// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @title IUniswapV2Router02Like
/// @notice Uniswap V2 Router02'nin vault tarafından kullanılan alt kümesi.
///         `MockRouter` bu arayüzü uygular; gerçek bir V2 router çıkarsa vault değişmeden çalışır.
interface IUniswapV2Router02Like {
    function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts);

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}
