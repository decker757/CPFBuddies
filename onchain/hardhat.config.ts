import { HardhatUserConfig } from "hardhat/config";
import "@nomicfoundation/hardhat-toolbox";
import * as dotenv from "dotenv";

dotenv.config();

/**
 * Network config is entirely environment-driven so that promoting Fuji -> mainnet is a
 * deployment parameter, never a code change. See CLAUDE.md: "Deploy and test on Fuji testnet
 * FIRST. Only touch mainnet once the flow works."
 *
 * DEPLOYER_PRIVATE_KEY is a convenience for testnet only. Production signing goes through KMS
 * in the Python settlement worker, never through a key in this repo.
 */
const deployerKey = process.env.DEPLOYER_PRIVATE_KEY;
const accounts = deployerKey ? [deployerKey] : [];

const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },
  networks: {
    hardhat: {
      chainId: 31337,
    },
    localhost: {
      url: "http://127.0.0.1:8545",
      chainId: 31337,
    },
    fuji: {
      url: process.env.FUJI_RPC_URL ?? "https://api.avax-test.network/ext/bc/C/rpc",
      chainId: 43113,
      accounts,
    },
    avalanche: {
      url: process.env.AVALANCHE_RPC_URL ?? "https://api.avax.network/ext/bc/C/rpc",
      chainId: 43114,
      accounts,
    },
  },
  etherscan: {
    apiKey: {
      avalancheFujiTestnet: process.env.SNOWTRACE_API_KEY ?? "",
      avalanche: process.env.SNOWTRACE_API_KEY ?? "",
    },
  },
};

export default config;
