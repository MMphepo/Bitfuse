from abc import ABC, abstractmethod
from decimal import Decimal


class BaseBlockchainProvider(ABC):
    """Abstract base class/interface for blockchain operations.

    This architecture allows supporting multiple networks (TRON, Ethereum, BSC, etc.)
    by implementing subclasses without breaking the core business logic.
    """

    @abstractmethod
    def validate_address(self, address: str) -> bool:
        """Validate if a destination address is valid for the network."""
        pass

    @abstractmethod
    def get_usdt_balance(self, address: str) -> Decimal:
        """Fetch the USDT balance of a given address on the network."""
        pass

    @abstractmethod
    def build_transfer_transaction(self, to_address: str, amount: Decimal) -> dict:
        """Build and sign/prepare a transfer transaction of USDT to the destination."""
        pass

    @abstractmethod
    def broadcast_transaction(self, tx_data: dict) -> str:
        """Broadcast a prepared/signed transaction to the network.

        Returns the transaction hash.
        """
        pass

    @abstractmethod
    def get_transaction_status(self, tx_hash: str) -> str:
        """Query the transaction status on-chain.

        Returns one of: 'PENDING', 'SUCCESS', 'FAILED'
        """
        pass
