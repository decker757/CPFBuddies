import { ethers, network } from "hardhat";
import { writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const IERC20_METADATA =
  "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol:IERC20Metadata";

/**
 * Deploys MandateRegistry and, on networks without a real XSGD, a MockXSGD to stand in for it.
 *
 * The settlement token is resolved from configuration, never hardcoded:
 *   - XSGD_ADDRESS set   -> attach to it (mainnet, or any network where XSGD is deployed)
 *   - XSGD_ADDRESS unset -> deploy MockXSGD (local and Fuji, where XSGD does not exist)
 *
 * Decimals are read off the token rather than assumed. Guessing here is a silent 100x error.
 *
 * Writes deployments/<network>.json, which is the single source of truth the Python settlement
 * worker reads for addresses. Do not copy addresses by hand into config anywhere else.
 */
async function main() {
  const [deployer] = await ethers.getSigners();
  const chainId = Number((await ethers.provider.getNetwork()).chainId);

  console.log(`network  ${network.name} (chainId ${chainId})`);
  console.log(`deployer ${deployer.address}`);

  const tokenAddress = await resolveSettlementToken();
  const token = await ethers.getContractAt(IERC20_METADATA, tokenAddress);
  const decimals = Number(await token.decimals());
  const symbol = await token.symbol();
  console.log(`token    ${tokenAddress} (${symbol}, ${decimals} decimals)`);

  const registry = await ethers.deployContract("MandateRegistry", [
    tokenAddress,
    deployer.address,
  ]);
  await registry.waitForDeployment();
  const registryAddress = await registry.getAddress();
  console.log(`registry ${registryAddress}`);

  // Roles default to the deployer so a local run is immediately usable. In a real deployment
  // these are distinct principals: the Mandate Service registers, the Settlement Worker spends.
  const registrar = process.env.REGISTRAR_ADDRESS ?? deployer.address;
  const settler = process.env.SETTLER_ADDRESS ?? deployer.address;

  await (await registry.grantRole(await registry.REGISTRAR_ROLE(), registrar)).wait();
  await (await registry.grantRole(await registry.SETTLER_ROLE(), settler)).wait();
  console.log(`registrar ${registrar}`);
  console.log(`settler   ${settler}`);

  writeDeployment({
    network: network.name,
    chainId,
    settlementToken: tokenAddress,
    settlementTokenSymbol: symbol,
    settlementTokenDecimals: decimals,
    mandateRegistry: registryAddress,
    registrar,
    settler,
    deployedAtBlock: await ethers.provider.getBlockNumber(),
    deployedAt: new Date().toISOString(),
  });
}

async function resolveSettlementToken(): Promise<string> {
  const configured = process.env.XSGD_ADDRESS;
  if (configured) {
    if (!ethers.isAddress(configured)) {
      throw new Error(`XSGD_ADDRESS is not a valid address: ${configured}`);
    }
    return configured;
  }

  // MockXSGD mirrors whatever the real token reports. Verify against mainnet XSGD before
  // changing this default.
  const decimals = Number(process.env.MOCK_XSGD_DECIMALS ?? 6);
  console.log(`XSGD_ADDRESS unset, deploying MockXSGD with ${decimals} decimals`);

  const mock = await ethers.deployContract("MockXSGD", [decimals]);
  await mock.waitForDeployment();
  return mock.getAddress();
}

function writeDeployment(record: Record<string, unknown>) {
  const dir = join(__dirname, "..", "deployments");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${network.name}.json`);
  writeFileSync(path, `${JSON.stringify(record, null, 2)}\n`);
  console.log(`wrote    ${path}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
