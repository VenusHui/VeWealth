"""
认证相关Schema
"""

from typing import Optional
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """用户注册请求"""

    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    master_key: str = Field(..., description="主密钥")


class RegisterResponse(BaseModel):
    """用户注册响应"""

    success: bool
    message: str
    access_token: Optional[str] = None
    token_type: str = "bearer"
    user_id: Optional[int] = None
    username: Optional[str] = None


class TokenResponse(BaseModel):
    """令牌响应"""

    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


class UserInfo(BaseModel):
    """用户信息"""

    id: int
    username: str
    wechat_openid: Optional[str] = None
    is_active: bool
    alert_threshold: float

    class Config:
        from_attributes = True


class UpdateUserRequest(BaseModel):
    """更新用户信息请求"""

    alert_threshold: Optional[float] = Field(
        None, ge=0, le=1, description="预警阈值（0-1）"
    )
    wechat_openid: Optional[str] = Field(None, description="微信OpenID")
