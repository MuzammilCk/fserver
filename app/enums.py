from enum import Enum

class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    USER = "user"

    # This ensures string comparison works (e.g. UserRole.ADMIN == "admin")
    def __str__(self):
        return self.value
