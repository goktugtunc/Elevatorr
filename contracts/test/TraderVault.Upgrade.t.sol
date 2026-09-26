// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {IERC1967} from "@openzeppelin/contracts/interfaces/IERC1967.sol";
import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {OwnableUpgradeable} from "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";

import {TraderVault} from "../src/TraderVault.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";
import {TraderVaultV2} from "./mocks/TraderVaultV2.sol";

contract TraderVaultUpgradeTest is BaseTest {
    // ERC-1967 implementation slot
    bytes32 internal constant IMPL_SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    function _impl() internal view returns (address) {
        return address(uint160(uint256(vm.load(address(vault), IMPL_SLOT))));
    }

    // Rust 7 + yeni: gerçek UUPS upgrade, state korunur, V2 fonksiyonu çalışır
    function test_upgrade_ownerCanUpgradeToV2() public {
        // aktif anlaşma + trade + rezervasyon varken
        usdc.mint(customer, PRINCIPAL);
        uint256 id = openActive();
        vm.prank(trader);
        vault.trade(id, address(usdc), address(alt), 200 * U6, 800 * U18, dl());
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(trader);
        uint256 proposed = vault.propose(terms());

        bytes32 agBefore = keccak256(abi.encode(vault.getAgreement(id)));
        bytes32 resBefore = keccak256(abi.encode(vault.getReservation(rid)));
        bytes32 cfgBefore = keccak256(abi.encode(vault.getConfig()));
        (address[] memory t0, uint256[] memory a0) = vault.getBalances(id);
        assertEq(_impl(), address(impl));

        TraderVaultV2 v2 = new TraderVaultV2();
        vm.expectEmit(address(vault));
        emit IERC1967.Upgraded(address(v2));
        vm.prank(admin);
        vault.upgradeToAndCall(address(v2), abi.encodeCall(TraderVaultV2.initializeV2, (42)));

        assertEq(_impl(), address(v2));
        TraderVaultV2 up = TraderVaultV2(address(vault));
        assertEq(up.version(), 2);
        assertEq(up.newField(), 42);
        // state aynı
        assertEq(keccak256(abi.encode(vault.getAgreement(id))), agBefore);
        assertEq(keccak256(abi.encode(vault.getReservation(rid))), resBefore);
        assertEq(keccak256(abi.encode(vault.getConfig())), cfgBefore);
        (address[] memory t1, uint256[] memory a1) = vault.getBalances(id);
        assertEq(t1, t0);
        assertEq(a1, a0);
        assertEq(vault.nextId(), 3);
        assertEq(vault.nextReservationId(), 2);
        assertEq(vault.owner(), admin);
        assertTrue(vault.isTokenAllowed(address(usdc)).isBase);
        assertStatus(proposed, ITraderVault.AgreementStatus.Proposed);
        // reinitializer(2) ikinci kez çalışmaz
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        up.initializeV2(7);
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        vault.initialize(admin, address(router), feeRecipient, 1, 1, 1);
        // işlevsellik: settle, release, fund çalışır
        vm.prank(customer);
        vault.settle(id, one(200 * U6));
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
        vm.prank(customer);
        vault.fundReserved(proposed, rid);
        assertStatus(proposed, ITraderVault.AgreementStatus.Active);
        // yeni anlaşma da çalışır
        usdc.mint(customer, PRINCIPAL);
        uint256 id3 = openActive();
        assertEq(id3, 3);
    }

    function test_upgrade_revertsForNonOwner() public {
        TraderVaultV2 v2 = new TraderVaultV2();
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, stranger));
        vault.upgradeToAndCall(address(v2), "");
        vm.prank(customer);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, customer));
        vault.upgradeToAndCall(address(v2), "");
        assertEq(_impl(), address(impl));
        // bekleyen owner da yapamaz
        address newOwner = makeAddr("newOwner");
        vm.prank(admin);
        vault.transferOwnership(newOwner);
        vm.prank(newOwner);
        vm.expectRevert(abi.encodeWithSelector(OwnableUpgradeable.OwnableUnauthorizedAccount.selector, newOwner));
        vault.upgradeToAndCall(address(v2), "");
        vm.prank(newOwner);
        vault.acceptOwnership();
        vm.prank(newOwner);
        vault.upgradeToAndCall(address(v2), "");
        assertEq(_impl(), address(v2));
    }

    function test_upgrade_rejectsNonUUPSImplementation() public {
        // proxiableUUID döndürmeyen adres (token) -> ERC1967InvalidImplementation
        vm.prank(admin);
        vm.expectRevert(
            abi.encodeWithSelector(bytes4(keccak256("ERC1967InvalidImplementation(address)")), address(usdc))
        );
        vault.upgradeToAndCall(address(usdc), "");
        assertEq(_impl(), address(impl));
    }

    function test_upgrade_implementationCannotBeInitialized() public {
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        impl.initialize(admin, address(router), feeRecipient, 100, 100, ROUTER_DELAY);
        TraderVaultV2 v2 = new TraderVaultV2();
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        v2.initialize(admin, address(router), feeRecipient, 100, 100, ROUTER_DELAY);
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        v2.initializeV2(1);
        // implementasyon üzerinden doğrudan upgrade çağrısı: onlyProxy
        vm.prank(admin);
        vm.expectRevert(UUPSUpgradeable.UUPSUnauthorizedCallContext.selector);
        impl.upgradeToAndCall(address(v2), "");
        assertEq(impl.proxiableUUID(), IMPL_SLOT);
        assertEq(vault.UPGRADE_INTERFACE_VERSION(), "5.0.0");
        // proxy üzerinden proxiableUUID çağrısı reddedilir (notDelegated)
        vm.expectRevert(UUPSUpgradeable.UUPSUnauthorizedCallContext.selector);
        vault.proxiableUUID();
    }

    function test_initialize_cannotRunTwice() public {
        vm.prank(admin);
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        vault.initialize(admin, address(router), feeRecipient, 100, 100, ROUTER_DELAY);
        vm.prank(stranger);
        vm.expectRevert(Initializable.InvalidInitialization.selector);
        vault.initialize(stranger, address(router), stranger, 0, 0, 0);
        assertEq(vault.owner(), admin);
    }

    /// @dev Storage düzeni (§3.2, `forge inspect TraderVault storage-layout`): slot0..3 Config, 4 _nextId,
    ///      5 _nextReservationId, 6..9 mapping'ler, 10..59 __gap (50), V2 newField 60. CI'da inspect diff'i ile tamamlanır.
    function test_upgrade_storageLayoutUnchanged() public {
        usdc.mint(customer, 1);
        vm.prank(customer);
        vault.open(terms());
        vm.prank(customer);
        vault.reserve(address(usdc), 1, listingRef());
        // Config: slot0 router; slot1 feeRecipient|platformFeeBps|settleSlippageBps|paused; slot2 pendingRouter|activation; slot3 routerDelay
        assertEq(address(uint160(uint256(vm.load(address(vault), bytes32(uint256(0)))))), address(router));
        uint256 slot1 = uint256(vm.load(address(vault), bytes32(uint256(1))));
        assertEq(address(uint160(slot1)), feeRecipient);
        assertEq(uint16(slot1 >> 160), PLATFORM_FEE_BPS);
        assertEq(uint16(slot1 >> 176), SETTLE_SLIPPAGE_BPS);
        assertEq(uint8(slot1 >> 192), 0); // paused=false
        assertEq(uint256(vm.load(address(vault), bytes32(uint256(3)))), ROUTER_DELAY);
        assertEq(uint256(vm.load(address(vault), bytes32(uint256(4)))), 2); // _nextId
        assertEq(uint256(vm.load(address(vault), bytes32(uint256(5)))), 2); // _nextReservationId
        // gap boş
        for (uint256 i = 10; i < 60; ++i) {
            assertEq(vm.load(address(vault), bytes32(i)), bytes32(0), "gap slot not empty");
        }
        TraderVaultV2 v2 = new TraderVaultV2();
        vm.prank(admin);
        vault.upgradeToAndCall(address(v2), abi.encodeCall(TraderVaultV2.initializeV2, (0xBEEF)));
        assertEq(uint256(vm.load(address(vault), bytes32(uint256(60)))), 0xBEEF); // newField gap'ten sonra
        assertEq(uint256(vm.load(address(vault), bytes32(uint256(4)))), 2);
        assertEq(uint256(vm.load(address(vault), bytes32(uint256(5)))), 2);
        for (uint256 i = 10; i < 60; ++i) {
            assertEq(vm.load(address(vault), bytes32(i)), bytes32(0), "gap touched by upgrade");
        }
        // Config paused yazımı slot1'de doğru yere düşer
        vm.prank(admin);
        vault.setPaused(true);
        slot1 = uint256(vm.load(address(vault), bytes32(uint256(1))));
        assertEq(uint8(slot1 >> 192), 1);
    }
}
