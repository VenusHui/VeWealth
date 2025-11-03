"""
认证路由
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.core.security import (
    create_access_token,
    verify_master_key,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    RegisterRequest,
    RegisterResponse,
    LoginRequest,
    TokenResponse,
    UserInfo,
    UpdateUserRequest,
)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """
    用户注册

    需要提供管理员的主密钥（注册令牌）才能注册
    """
    # 验证主密钥
    if not verify_master_key(request.master_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="主密钥错误，无权注册"
        )

    # 检查用户名是否已存在
    existing_user = db.query(User).filter(User.username == request.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在"
        )

    # 创建用户（哈希密码）
    new_user = User(
        username=request.username,
        hashed_password=hash_password(request.password),
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # 生成访问令牌
    access_token = create_access_token(data={"sub": new_user.id})

    return RegisterResponse(
        success=True,
        message="注册成功",
        access_token=access_token,
        user_id=new_user.id,
        username=new_user.username,
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """
    用户登录

    使用用户名和密码登录，返回JWT令牌
    """
    # 查找用户
    user = db.query(User).filter(User.username == request.username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 验证密码
    if not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查用户是否激活
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="用户已被禁用"
        )

    # 生成访问令牌
    access_token = create_access_token(data={"sub": user.id})

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user_id=user.id,
        username=user.username,
    )


@router.get("/me", response_model=UserInfo)
async def get_current_user_info(current_user: User = Depends(get_current_active_user)):
    """
    获取当前用户信息
    """
    return current_user


@router.put("/me", response_model=UserInfo)
async def update_current_user(
    request: UpdateUserRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    更新当前用户信息
    """
    if request.alert_threshold is not None:
        current_user.alert_threshold = request.alert_threshold

    if request.wechat_openid is not None:
        # 检查OpenID是否已被其他用户使用
        existing = (
            db.query(User)
            .filter(
                User.wechat_openid == request.wechat_openid, User.id != current_user.id
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="该微信已绑定其他账号"
            )
        current_user.wechat_openid = request.wechat_openid

    db.commit()
    db.refresh(current_user)

    return current_user
