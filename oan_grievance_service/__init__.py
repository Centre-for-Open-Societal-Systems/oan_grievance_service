__version__ = "0.0.1"

# Register JWT API namespace with oan_auth_service on module import
from oan_grievance_service.api import middleware
