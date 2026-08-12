from .base import BaseBlockchainProvider
from .tron import TronProvider


def get_blockchain_provider(network: str) -> BaseBlockchainProvider:
    """Factory function to fetch the appropriate blockchain provider for a network.

    This ensures that adding new networks (e.g. Ethereum, BSC) only requires
    registering a new provider class here, completely keeping the withdrawal
    and business layer decoupling intact.
    """
    normalized_network = network.strip().upper()

    if normalized_network == "TRON":
        return TronProvider()
    else:
        raise ValueError(f"Blockchain network '{network}' is not supported yet.")
