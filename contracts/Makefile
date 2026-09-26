SHELL := /bin/bash
export PATH := $(HOME)/.foundry/bin:$(PATH)

OZ_TAG ?= v5.4.0

.PHONY: setup build sizes test test-ci fmt fmt-check abi clean

## Bağımlılıkları lib/ altına kurar (git submodule kullanılmaz; lib/ .gitignore'dadır).
setup:
	@[ -d lib/forge-std ] || forge install --no-git foundry-rs/forge-std
	@[ -d lib/openzeppelin-contracts ] || forge install --no-git OpenZeppelin/openzeppelin-contracts@$(OZ_TAG)
	@[ -d lib/openzeppelin-contracts-upgradeable ] || forge install --no-git OpenZeppelin/openzeppelin-contracts-upgradeable@$(OZ_TAG)

build:
	forge build

sizes:
	forge build --sizes

test:
	forge test -vvv

test-ci:
	FOUNDRY_PROFILE=ci forge test

fmt:
	forge fmt

fmt-check:
	forge fmt --check

## ABI'leri backend ve app dizinlerine yazar.
abi:
	./scripts/export-abi.sh

clean:
	forge clean
