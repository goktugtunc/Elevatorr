// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {BaseTest} from "./Base.t.sol";
import {ITraderVault} from "../src/interfaces/ITraderVault.sol";

contract TraderVaultReservationTest is BaseTest {
    // Rust 38: reserve_locks_capital_and_release_gives_it_back
    function test_reserve_locksCapitalAndReleaseGivesItBack() public {
        uint256 before = usdc.balanceOf(customer);
        uint256 vaultBefore = usdc.balanceOf(address(vault));
        bytes32 ref = listingRef();

        vm.expectEmit(address(vault));
        emit ITraderVault.Reserved(1, customer, address(usdc), PRINCIPAL, ref);
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, ref);
        assertEq(rid, 1);
        assertEq(usdc.balanceOf(customer), before - PRINCIPAL);
        assertEq(usdc.balanceOf(address(vault)), vaultBefore + PRINCIPAL);

        ITraderVault.Reservation memory r = vault.getReservation(rid);
        assertEq(r.id, rid);
        assertEq(r.customer, customer);
        assertEq(r.token, address(usdc));
        assertEq(r.amount, PRINCIPAL);
        assertEq(r.original, PRINCIPAL);
        assertEq(uint8(r.status), uint8(ITraderVault.ReservationStatus.Open));
        assertEq(r.createdAt, START_TS);
        assertEq(r.listingRef, ref);

        // Kısmi release kalanı kilitli bırakır
        uint256 quarter = PRINCIPAL / 4;
        vm.expectEmit(address(vault));
        emit ITraderVault.Released(rid, customer, address(usdc), quarter, PRINCIPAL - quarter);
        vm.prank(customer);
        assertEq(vault.release(rid, quarter), quarter);
        r = vault.getReservation(rid);
        assertEq(r.amount, PRINCIPAL - quarter);
        assertEq(r.original, PRINCIPAL);
        assertEq(uint8(r.status), uint8(ITraderVault.ReservationStatus.Open));

        // release(0) artık tuzak değil: ZeroAmount
        vm.prank(customer);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.release(rid, 0);
        // fazlası: ReservationInsufficient
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationInsufficient.selector);
        vault.release(rid, PRINCIPAL - quarter + 1);

        // releaseAll kalanı verir
        vm.expectEmit(address(vault));
        emit ITraderVault.Released(rid, customer, address(usdc), PRINCIPAL - quarter, 0);
        vm.prank(customer);
        assertEq(vault.releaseAll(rid), PRINCIPAL - quarter);
        assertEq(usdc.balanceOf(customer), before);
        assertEq(uint8(vault.getReservation(rid).status), uint8(ITraderVault.ReservationStatus.Released));
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationClosed.selector);
        vault.release(rid, 1);
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationClosed.selector);
        vault.releaseAll(rid);
        // tam release tek adımda
        vm.prank(customer);
        uint256 rid2 = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(customer);
        assertEq(vault.release(rid2, PRINCIPAL), PRINCIPAL);
        assertEq(uint8(vault.getReservation(rid2).status), uint8(ITraderVault.ReservationStatus.Released));
    }

    // Rust 39: reserve_validation
    function test_reserve_validation() public {
        bytes32 r = listingRef();
        // allow-list'te ama base değil
        vm.prank(customer);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.reserve(address(eurc), PRINCIPAL, r);
        // bilinmeyen
        vm.prank(customer);
        expectVaultError(ITraderVault.TokenNotAllowed.selector);
        vault.reserve(stranger, PRINCIPAL, r);
        vm.prank(customer);
        expectVaultError(ITraderVault.ZeroAmount.selector);
        vault.reserve(address(usdc), 0, r);
        expectVaultError(ITraderVault.ReservationNotFound.selector);
        vault.getReservation(7);
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationNotFound.selector);
        vault.release(7, 1);
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationNotFound.selector);
        vault.releaseAll(7);

        vm.prank(admin);
        vault.setPaused(true);
        vm.prank(customer);
        expectVaultError(ITraderVault.Paused.selector);
        vault.reserve(address(usdc), U6, r);
    }

    // Rust 40: release_works_while_paused
    function test_release_worksWhilePaused() public {
        uint256 before = usdc.balanceOf(customer);
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(admin);
        vault.setPaused(true);
        vm.prank(customer);
        assertEq(vault.releaseAll(rid), PRINCIPAL);
        assertEq(usdc.balanceOf(customer), before);
    }

    // Rust 41: open_reserved_draws_the_principal_without_touching_the_wallet
    function test_openReserved_drawsPrincipalWithoutWallet() public {
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        uint256 afterReserve = usdc.balanceOf(customer);

        // event sırası: ReservationDrawn, sonra Opened
        vm.expectEmit(address(vault));
        emit ITraderVault.ReservationDrawn(rid, 1, PRINCIPAL, 0);
        vm.expectEmit(address(vault));
        emit ITraderVault.Opened(1, trader, customer, PRINCIPAL, address(usdc));
        vm.prank(customer);
        uint256 id = vault.openReserved(terms(), rid);
        // sermaye zaten vault'taydı; ikinci transfer yok
        assertEq(usdc.balanceOf(customer), afterReserve);
        assertEq(vaultBalance(id, address(usdc)), PRINCIPAL);
        assertStatus(id, ITraderVault.AgreementStatus.Funded);
        assertEq(ag(id).proposer, customer);

        ITraderVault.Reservation memory r = vault.getReservation(rid);
        assertEq(r.amount, 0);
        assertEq(uint8(r.status), uint8(ITraderVault.ReservationStatus.Consumed));
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationClosed.selector);
        vault.releaseAll(rid);

        vm.prank(trader);
        vault.accept(id);
        assertStatus(id, ITraderVault.AgreementStatus.Active);
    }

    // Rust 42: fund_reserved_activates_a_proposed_agreement
    function test_fundReserved_activatesProposed() public {
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        uint256 afterReserve = usdc.balanceOf(customer);

        vm.prank(trader);
        uint256 id = vault.propose(terms());
        vm.expectEmit(address(vault));
        emit ITraderVault.ReservationDrawn(rid, id, PRINCIPAL, 0);
        vm.expectEmit(address(vault));
        emit ITraderVault.Activated(id, START_TS, START_TS + DURATION);
        vm.prank(customer);
        vault.fundReserved(id, rid);

        assertEq(usdc.balanceOf(customer), afterReserve);
        assertStatus(id, ITraderVault.AgreementStatus.Active);
        assertEq(vaultBalance(id, address(usdc)), PRINCIPAL);
        assertEq(uint8(vault.getReservation(rid).status), uint8(ITraderVault.ReservationStatus.Consumed));
        // Rezervasyon harcandı; ikinci anlaşma tekrar kullanamaz
        vm.prank(trader);
        uint256 id2 = vault.propose(terms());
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationClosed.selector);
        vault.fundReserved(id2, rid);
        // released rezervasyon da kapalı
        usdc.mint(customer, PRINCIPAL);
        vm.prank(customer);
        uint256 rid2 = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(customer);
        vault.releaseAll(rid2);
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationClosed.selector);
        vault.fundReserved(id2, rid2);
    }

    // Rust 43: reserved_funding_rejects_a_reservation_that_does_not_fit
    function test_reserved_rejectsReservationThatDoesNotFit() public {
        usdc.mint(customer, PRINCIPAL);
        alt.mint(customer, PRINCIPAL * U18 / U6);
        vm.prank(customer);
        alt.approve(address(vault), type(uint256).max);
        vm.prank(trader);
        uint256 id = vault.propose(terms()); // base USDC

        vm.prank(customer);
        uint256 wrongToken = vault.reserve(address(alt), PRINCIPAL * U18 / U6, listingRef());
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationMismatch.selector);
        vault.fundReserved(id, wrongToken);

        usdc.mint(stranger, PRINCIPAL);
        vm.prank(stranger);
        usdc.approve(address(vault), type(uint256).max);
        vm.prank(stranger);
        uint256 someoneElse = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationMismatch.selector);
        vault.fundReserved(id, someoneElse);
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationMismatch.selector);
        vault.openReserved(terms(), someoneElse);

        vm.prank(customer);
        uint256 tooSmall = vault.reserve(address(usdc), PRINCIPAL / 2, listingRef());
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationInsufficient.selector);
        vault.fundReserved(id, tooSmall);
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationInsufficient.selector);
        vault.openReserved(terms(), tooSmall);

        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationNotFound.selector);
        vault.fundReserved(id, 9_999);
        vm.prank(customer);
        expectVaultError(ITraderVault.ReservationNotFound.selector);
        vault.openReserved(terms(), 9_999);
        // başarısız oluşturma id tüketmez
        assertEq(vault.nextId(), 2);
        assertStatus(id, ITraderVault.AgreementStatus.Proposed);
    }

    // Rust 44: a_reservation_bigger_than_the_principal_keeps_the_rest_locked
    function test_reserve_biggerThanPrincipalKeepsRest() public {
        usdc.mint(customer, PRINCIPAL); // 2 x PRINCIPAL elde
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), 2 * PRINCIPAL, listingRef());

        vm.prank(trader);
        uint256 id = vault.propose(terms());
        vm.expectEmit(address(vault));
        emit ITraderVault.ReservationDrawn(rid, id, PRINCIPAL, PRINCIPAL);
        vm.prank(customer);
        vault.fundReserved(id, rid);

        ITraderVault.Reservation memory r = vault.getReservation(rid);
        assertEq(r.amount, PRINCIPAL);
        assertEq(r.original, 2 * PRINCIPAL);
        assertEq(uint8(r.status), uint8(ITraderVault.ReservationStatus.Open));

        // Kalan hâlâ müşterinin
        vm.prank(customer);
        assertEq(vault.releaseAll(rid), PRINCIPAL);
        assertEq(uint8(vault.getReservation(rid).status), uint8(ITraderVault.ReservationStatus.Released));
        assertEq(usdc.balanceOf(customer), PRINCIPAL);
    }

    // Rust 45: reserved_agreement_settles_and_pays_the_customer
    function test_reserved_settlesAndPaysCustomer() public {
        uint256 before = usdc.balanceOf(customer);
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(customer);
        uint256 id = vault.openReserved(terms(), rid);
        vm.prank(trader);
        vault.accept(id);

        vm.warp(START_TS + DURATION + 1);
        vm.prank(customer);
        vault.settle(id, none());
        assertStatus(id, ITraderVault.AgreementStatus.Settled);
        assertEq(usdc.balanceOf(customer), before);
        assertEq(usdc.balanceOf(address(vault)), 0);
    }

    // Rust 46: cancelling_a_reserved_agreement_refunds_the_wallet
    function test_cancel_reservedRefundsWallet() public {
        uint256 before = usdc.balanceOf(customer);
        vm.prank(customer);
        uint256 rid = vault.reserve(address(usdc), PRINCIPAL, listingRef());
        vm.prank(customer);
        uint256 id = vault.openReserved(terms(), rid);

        vm.prank(customer);
        vault.cancel(id);
        // cüzdana iade; rezervasyon Consumed kalır
        assertEq(usdc.balanceOf(customer), before);
        ITraderVault.Reservation memory r = vault.getReservation(rid);
        assertEq(r.amount, 0);
        assertEq(uint8(r.status), uint8(ITraderVault.ReservationStatus.Consumed));
    }

    // Rust 47: reservation_ids_are_their_own_sequence
    function test_reservationIds_ownSequence() public {
        usdc.mint(customer, PRINCIPAL);
        assertEq(vault.nextReservationId(), 1);
        vm.prank(customer);
        uint256 a = vault.reserve(address(usdc), U6, listingRef());
        vm.prank(trader);
        vault.propose(terms());
        vm.prank(customer);
        uint256 b = vault.reserve(address(usdc), U6, listingRef());
        assertEq(a, 1);
        assertEq(b, 2);
        assertEq(vault.nextReservationId(), 3);
        assertEq(vault.nextId(), 2);
    }

    function test_reserve_altBaseToken() public {
        alt.mint(customer, 500 * U18);
        vm.prank(customer);
        alt.approve(address(vault), type(uint256).max);
        vm.prank(customer);
        uint256 rid = vault.reserve(address(alt), 500 * U18, listingRef());
        assertEq(alt.balanceOf(address(vault)), 500 * U18);
        ITraderVault.Terms memory t = terms();
        t.baseToken = address(alt);
        t.principal = 400 * U18;
        vm.prank(customer);
        uint256 id = vault.openReserved(t, rid);
        assertEq(vaultBalance(id, address(alt)), 400 * U18);
        assertEq(vault.getReservation(rid).amount, 100 * U18);
        vm.prank(customer);
        vault.release(rid, 100 * U18);
        assertEq(alt.balanceOf(customer), 100 * U18);
    }
}
