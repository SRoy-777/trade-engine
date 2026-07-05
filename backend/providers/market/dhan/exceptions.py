class DhanException(Exception):
    """Base exception for all Dhan provider operations."""
    pass

class DhanAuthException(DhanException):
    """Raised when authentication credentials or token validations fail."""
    pass

class DhanNetworkException(DhanException):
    """Raised when network disconnects, timeouts, or socket failures occur."""
    pass

class DhanSubscriptionException(DhanException):
    """Raised when instrument subscriptions are rejected or invalid."""
    pass

class DhanParserException(DhanException):
    """Raised when binary packet parsing or decoding fails."""
    pass

class DhanHeartbeatException(DhanException):
    """Raised when heartbeat ticks fail to receive responses within limits."""
    pass
