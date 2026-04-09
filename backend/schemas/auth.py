from pydantic import BaseModel, EmailStr, field_validator


class SetupRequest(BaseModel):
    username: str
    email: EmailStr
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) < 3 or len(v) > 80:
            raise ValueError("Username must be 3-80 characters")
        if not v.isalnum() and "_" not in v:
            raise ValueError("Username must be alphanumeric or underscore")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    status: str
    message: str


class UserInfo(BaseModel):
    username: str
    email: str
    is_admin: bool
    broker: str | None = None
    broker_authenticated: bool = False
