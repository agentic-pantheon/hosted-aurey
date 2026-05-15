### Discovery
- Find the best USDC Earn vaults on Base. Show APY, 30d APY if available, TVL, protocol, KYC, timelock, caps, and whether Composer deposits are supported.
- List LiFi Earn protocols and tell me whether Morpho is supported for Composer deposits.
Show me the top 5 Composer-supported USDC vaults across all LiFi Earn chains, sorted by APY.

### Protocol-Specific Deposit
- I want to deposit 10 USDC into a Morpho vault on Base. Find supported vaults first, explain the best option, then prepare the deposit transaction for my confirmation.
- Deposit 5 USDC into the highest-TVL Aave vault available through LiFi Earn. Check whether Composer supports it before preparing anything.

### Cross-Chain Composer
- Use my ETH on Ethereum to deposit into a USDC vault on Base through LiFi Composer. Find a good Base USDC vault first and prepare the route, but ask me before executing.
- I have USDC on Arbitrum and want yield on Base. Find the best Composer-supported Base USDC vault and prepare a cross-chain deposit quote.

### Approval / Execution Flow
- Prepare a deposit of 1 USDC from Base into the best Base USDC Earn vault. If approval is needed, explain the approval spender and amount before asking me to confirm.
- After I approve the token allowance, re-quote the Earn deposit and prepare the fresh Composer transaction before execution.

### Portfolio / Verification
- Show my current LiFi Earn portfolio positions and summarize balances by protocol and chain.
- After the deposit transaction confirms, check LiFi status if it was cross-chain and then verify my Earn portfolio positions.

### Good End-to-End Test
- Find the best Composer-supported USDC vault on Base with at least $100k TVL. Explain why you chose it, prepare a 1 USDC deposit from Base USDC, ask for confirmation before any tx_execute, and tell me if approval is required first.