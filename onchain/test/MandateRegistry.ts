import { expect } from "chai";
import { ethers } from "hardhat";
import { loadFixture, time } from "@nomicfoundation/hardhat-network-helpers";

/**
 * MandateRegistry behaviour.
 *
 * Grouped by the question each block answers rather than by function name, because the point of
 * this contract is which spends it refuses. The `spend` block is the demo: every rejection here
 * is a reverted transaction a judge can open on Snowtrace.
 */
describe("MandateRegistry", () => {
  // XSGD reports 6 decimals. Verify against mainnet before mirroring it anywhere else.
  const DECIMALS = 6n;
  const ONE_XSGD = 10n ** DECIMALS;

  const MANDATE_ID = ethers.id("mandate-1");
  const BASKET_HASH = ethers.id("basket-1");
  const CAP = 5n * ONE_XSGD;

  async function deploy() {
    const [admin, registrar, settler, principal, agent, merchant, outsider] =
      await ethers.getSigners();

    const token = await ethers.deployContract("MockXSGD", [DECIMALS]);
    const registry = await ethers.deployContract("MandateRegistry", [
      await token.getAddress(),
      admin.address,
    ]);

    await registry.grantRole(await registry.REGISTRAR_ROLE(), registrar.address);
    await registry.grantRole(await registry.SETTLER_ROLE(), settler.address);

    // The principal funds itself and approves the registry. Funds are never custodied by the
    // registry; `spend` pulls straight through to the merchant.
    await token.mint(principal.address, 100n * ONE_XSGD);
    await token.connect(principal).approve(await registry.getAddress(), 100n * ONE_XSGD);

    const expiresAt = BigInt(await time.latest()) + 3600n;

    return {
      token,
      registry,
      admin,
      registrar,
      settler,
      principal,
      agent,
      merchant,
      outsider,
      expiresAt,
    };
  }

  /** Registers MANDATE_ID with the merchant already bound, which is the stricter default. */
  async function deployWithMandate() {
    const ctx = await loadFixture(deploy);
    await ctx.registry
      .connect(ctx.registrar)
      .registerMandate(
        MANDATE_ID,
        ctx.principal.address,
        ctx.agent.address,
        ctx.merchant.address,
        CAP,
        ctx.expiresAt,
        ethers.id("mandate-hash-1"),
      );
    return ctx;
  }

  describe("deployment", () => {
    it("pins the settlement token and grants admin", async () => {
      const { registry, token, admin } = await loadFixture(deploy);
      expect(await registry.token()).to.equal(await token.getAddress());
      expect(await registry.hasRole(await registry.DEFAULT_ADMIN_ROLE(), admin.address)).to.be
        .true;
    });

    it("refuses a zero token or zero admin", async () => {
      const [admin] = await ethers.getSigners();
      const factory = await ethers.getContractFactory("MandateRegistry");

      await expect(
        factory.deploy(ethers.ZeroAddress, admin.address),
      ).to.be.revertedWithCustomError(factory, "InvalidAddress");

      const token = await ethers.deployContract("MockXSGD", [DECIMALS]);
      await expect(
        factory.deploy(await token.getAddress(), ethers.ZeroAddress),
      ).to.be.revertedWithCustomError(factory, "InvalidAddress");
    });
  });

  describe("registerMandate", () => {
    it("stores the mandate and emits", async () => {
      const { registry, registrar, principal, agent, merchant, expiresAt } =
        await loadFixture(deploy);
      const mandateHash = ethers.id("mandate-hash-1");

      await expect(
        registry
          .connect(registrar)
          .registerMandate(
            MANDATE_ID,
            principal.address,
            agent.address,
            merchant.address,
            CAP,
            expiresAt,
            mandateHash,
          ),
      )
        .to.emit(registry, "MandateRegistered")
        .withArgs(
          MANDATE_ID,
          principal.address,
          agent.address,
          merchant.address,
          CAP,
          expiresAt,
          mandateHash,
        );

      const stored = await registry.getMandate(MANDATE_ID);
      expect(stored.principal).to.equal(principal.address);
      expect(stored.cap).to.equal(CAP);
      expect(stored.revoked).to.be.false;
      expect(stored.consumed).to.be.false;
    });

    it("accepts an unbound merchant, because the mandate is minted before a product is chosen", async () => {
      const { registry, registrar, principal, agent, expiresAt } = await loadFixture(deploy);

      await registry
        .connect(registrar)
        .registerMandate(
          MANDATE_ID,
          principal.address,
          agent.address,
          ethers.ZeroAddress,
          CAP,
          expiresAt,
          ethers.ZeroHash,
        );

      expect((await registry.getMandate(MANDATE_ID)).merchant).to.equal(ethers.ZeroAddress);
    });

    it("rejects a caller without REGISTRAR_ROLE", async () => {
      const { registry, outsider, principal, agent, merchant, expiresAt } =
        await loadFixture(deploy);

      await expect(
        registry
          .connect(outsider)
          .registerMandate(
            MANDATE_ID,
            principal.address,
            agent.address,
            merchant.address,
            CAP,
            expiresAt,
            ethers.ZeroHash,
          ),
      ).to.be.revertedWithCustomError(registry, "AccessControlUnauthorizedAccount");
    });

    it("rejects duplicates, a zero principal, a zero cap, and a past expiry", async () => {
      const ctx = await loadFixture(deployWithMandate);
      const { registry, registrar, principal, agent, merchant, expiresAt } = ctx;

      await expect(
        registry
          .connect(registrar)
          .registerMandate(
            MANDATE_ID,
            principal.address,
            agent.address,
            merchant.address,
            CAP,
            expiresAt,
            ethers.ZeroHash,
          ),
      ).to.be.revertedWithCustomError(registry, "MandateAlreadyExists");

      await expect(
        registry
          .connect(registrar)
          .registerMandate(
            ethers.id("mandate-2"),
            ethers.ZeroAddress,
            agent.address,
            merchant.address,
            CAP,
            expiresAt,
            ethers.ZeroHash,
          ),
      ).to.be.revertedWithCustomError(registry, "InvalidAddress");

      await expect(
        registry
          .connect(registrar)
          .registerMandate(
            ethers.id("mandate-3"),
            principal.address,
            agent.address,
            merchant.address,
            0n,
            expiresAt,
            ethers.ZeroHash,
          ),
      ).to.be.revertedWithCustomError(registry, "InvalidAmount");

      await expect(
        registry
          .connect(registrar)
          .registerMandate(
            ethers.id("mandate-4"),
            principal.address,
            agent.address,
            merchant.address,
            CAP,
            BigInt(await time.latest()) - 1n,
            ethers.ZeroHash,
          ),
      ).to.be.revertedWithCustomError(registry, "InvalidExpiry");
    });
  });

  describe("revoke", () => {
    it("marks revoked and emits", async () => {
      const { registry, registrar } = await loadFixture(deployWithMandate);

      await expect(registry.connect(registrar).revoke(MANDATE_ID))
        .to.emit(registry, "MandateRevoked")
        .withArgs(MANDATE_ID);

      expect((await registry.getMandate(MANDATE_ID)).revoked).to.be.true;
      expect(await registry.isSpendable(MANDATE_ID)).to.be.false;
    });

    it("rejects a non-registrar, an unknown mandate, and a second revoke", async () => {
      const { registry, registrar, outsider } = await loadFixture(deployWithMandate);

      await expect(
        registry.connect(outsider).revoke(MANDATE_ID),
      ).to.be.revertedWithCustomError(registry, "AccessControlUnauthorizedAccount");

      await expect(
        registry.connect(registrar).revoke(ethers.id("nope")),
      ).to.be.revertedWithCustomError(registry, "MandateNotFound");

      await registry.connect(registrar).revoke(MANDATE_ID);
      await expect(
        registry.connect(registrar).revoke(MANDATE_ID),
      ).to.be.revertedWithCustomError(registry, "MandateIsRevoked");
    });
  });

  describe("spend", () => {
    it("moves funds principal -> merchant and consumes the mandate", async () => {
      const { registry, token, settler, principal, merchant } =
        await loadFixture(deployWithMandate);
      const amount = 4n * ONE_XSGD;

      const principalBefore = await token.balanceOf(principal.address);
      const tx = await registry
        .connect(settler)
        .spend(MANDATE_ID, merchant.address, amount, BASKET_HASH);

      await expect(tx)
        .to.emit(registry, "Spent")
        .withArgs(MANDATE_ID, merchant.address, amount, BASKET_HASH);

      expect(await token.balanceOf(merchant.address)).to.equal(amount);
      expect(await token.balanceOf(principal.address)).to.equal(principalBefore - amount);
      expect((await registry.getMandate(MANDATE_ID)).consumed).to.be.true;
      expect(await registry.isSpendable(MANDATE_ID)).to.be.false;
    });

    it("binds the merchant on first spend when it was left unbound", async () => {
      const { registry, registrar, settler, principal, agent, merchant, expiresAt } =
        await loadFixture(deploy);
      const id = ethers.id("unbound");

      await registry
        .connect(registrar)
        .registerMandate(
          id,
          principal.address,
          agent.address,
          ethers.ZeroAddress,
          CAP,
          expiresAt,
          ethers.ZeroHash,
        );

      await registry.connect(settler).spend(id, merchant.address, ONE_XSGD, BASKET_HASH);

      expect((await registry.getMandate(id)).merchant).to.equal(merchant.address);
    });

    it("refuses a spend above the cap", async () => {
      const { registry, settler, merchant } = await loadFixture(deployWithMandate);

      await expect(
        registry.connect(settler).spend(MANDATE_ID, merchant.address, CAP + 1n, BASKET_HASH),
      )
        .to.be.revertedWithCustomError(registry, "AmountExceedsCap")
        .withArgs(MANDATE_ID, CAP, CAP + 1n);
    });

    it("refuses a different merchant than the one bound", async () => {
      const { registry, settler, outsider, merchant } = await loadFixture(deployWithMandate);

      await expect(
        registry.connect(settler).spend(MANDATE_ID, outsider.address, ONE_XSGD, BASKET_HASH),
      )
        .to.be.revertedWithCustomError(registry, "MerchantMismatch")
        .withArgs(MANDATE_ID, merchant.address, outsider.address);
    });

    it("refuses a revoked mandate", async () => {
      const { registry, registrar, settler, merchant } = await loadFixture(deployWithMandate);
      await registry.connect(registrar).revoke(MANDATE_ID);

      await expect(
        registry.connect(settler).spend(MANDATE_ID, merchant.address, ONE_XSGD, BASKET_HASH),
      ).to.be.revertedWithCustomError(registry, "MandateIsRevoked");
    });

    it("refuses an expired mandate", async () => {
      const { registry, settler, merchant, expiresAt } = await loadFixture(deployWithMandate);
      await time.increaseTo(expiresAt);

      await expect(
        registry.connect(settler).spend(MANDATE_ID, merchant.address, ONE_XSGD, BASKET_HASH),
      ).to.be.revertedWithCustomError(registry, "MandateExpired");
    });

    it("refuses a second spend on the same mandate", async () => {
      const { registry, settler, merchant } = await loadFixture(deployWithMandate);
      await registry.connect(settler).spend(MANDATE_ID, merchant.address, ONE_XSGD, BASKET_HASH);

      await expect(
        registry.connect(settler).spend(MANDATE_ID, merchant.address, ONE_XSGD, BASKET_HASH),
      ).to.be.revertedWithCustomError(registry, "MandateAlreadyConsumed");
    });

    it("refuses an unknown mandate, a zero merchant, and a zero amount", async () => {
      const { registry, settler, merchant } = await loadFixture(deployWithMandate);

      await expect(
        registry.connect(settler).spend(ethers.id("nope"), merchant.address, ONE_XSGD, BASKET_HASH),
      ).to.be.revertedWithCustomError(registry, "MandateNotFound");

      await expect(
        registry.connect(settler).spend(MANDATE_ID, ethers.ZeroAddress, ONE_XSGD, BASKET_HASH),
      ).to.be.revertedWithCustomError(registry, "InvalidAddress");

      await expect(
        registry.connect(settler).spend(MANDATE_ID, merchant.address, 0n, BASKET_HASH),
      ).to.be.revertedWithCustomError(registry, "InvalidAmount");
    });

    it("refuses a caller without SETTLER_ROLE", async () => {
      const { registry, outsider, merchant } = await loadFixture(deployWithMandate);

      await expect(
        registry.connect(outsider).spend(MANDATE_ID, merchant.address, ONE_XSGD, BASKET_HASH),
      ).to.be.revertedWithCustomError(registry, "AccessControlUnauthorizedAccount");
    });

    it("surfaces an insufficient allowance rather than silently succeeding", async () => {
      const { registry, token, settler, principal, merchant } =
        await loadFixture(deployWithMandate);
      await token.connect(principal).approve(await registry.getAddress(), 0n);

      await expect(
        registry.connect(settler).spend(MANDATE_ID, merchant.address, ONE_XSGD, BASKET_HASH),
      ).to.be.revertedWithCustomError(token, "ERC20InsufficientAllowance");
    });

    it("leaves the mandate spendable when the transfer reverts", async () => {
      const { registry, token, settler, principal, merchant } =
        await loadFixture(deployWithMandate);
      await token.connect(principal).approve(await registry.getAddress(), 0n);

      await expect(
        registry.connect(settler).spend(MANDATE_ID, merchant.address, ONE_XSGD, BASKET_HASH),
      ).to.be.reverted;

      // The whole transaction reverted, so `consumed` was rolled back with it.
      expect(await registry.isSpendable(MANDATE_ID)).to.be.true;
    });
  });

  describe("views", () => {
    it("getMandate reverts for an unknown id", async () => {
      const { registry } = await loadFixture(deploy);
      await expect(registry.getMandate(ethers.id("nope"))).to.be.revertedWithCustomError(
        registry,
        "MandateNotFound",
      );
    });

    it("isSpendable is false for an unknown id rather than reverting", async () => {
      const { registry } = await loadFixture(deploy);
      expect(await registry.isSpendable(ethers.id("nope"))).to.be.false;
    });
  });
});
