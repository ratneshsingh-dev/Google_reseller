"""
Rate limiting configuration for the API using slowapi.
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address

# We use the IP address (get_remote_address) as the default identifier.
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
