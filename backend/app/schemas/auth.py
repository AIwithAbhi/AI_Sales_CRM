from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


# User Authentication Schemas
class UserBase(BaseModel):
    email: EmailStr
    name: str


class UserCreate(UserBase):
    password: str = Field(..., min_length=6, description="Password must be at least 6 characters")


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOAuth(BaseModel):
    email: EmailStr
    name: str
    token: str  # OAuth access token from client
    provider: str = "Google"


class UserResponse(UserBase):
    id: int
    auth_method: str
    created_at: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


# Auth Token Schemas
class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


class TokenPayload(BaseModel):
    sub: Optional[str] = None
