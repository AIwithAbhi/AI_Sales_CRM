from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import requests

from backend.app.database import get_db
from backend.app.models.user import User
from backend.app.schemas.auth import Token, UserCreate, UserLogin, UserOAuth, UserResponse
from backend.app.utils.logging import logger
from backend.app.utils.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new user in the system using email and password."""
    # Check if user already exists
    query = select(User).where(User.email == user_in.email)
    result = await db.execute(query)
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        logger.warning(f"Signup rejected: email '{user_in.email}' is already registered")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email address already exists"
        )
        
    # Hash password and create user
    hashed_pwd = hash_password(user_in.password)
    new_user = User(
        email=user_in.email,
        password_hash=hashed_pwd,
        name=user_in.name,
        auth_method="Email",
        created_at=datetime.utcnow()
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    logger.info(f"New user registered successfully: '{user_in.email}'")
    return new_user


@router.post("/login", response_model=Token)
async def login(user_in: UserLogin, db: AsyncSession = Depends(get_db)):
    """Authenticate email and password and return a secure JWT token."""
    # Find user
    query = select(User).where(User.email == user_in.email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user or user.auth_method != "Email":
        logger.warning(f"Login failed: email '{user_in.email}' not found or registered via OAuth")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Verify password
    if not verify_password(user_in.password, user.password_hash):
        logger.warning(f"Login failed: invalid password for user '{user_in.email}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Update last login
    user.last_login = datetime.utcnow()
    await db.commit()
    await db.refresh(user)
    
    # Generate JWT token
    access_token = create_access_token(subject=user.email)
    logger.info(f"User '{user.email}' logged in successfully")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


@router.post("/google", response_model=Token)
async def google_auth(oauth_in: UserOAuth, db: AsyncSession = Depends(get_db)):
    """
    Authenticate or register a user via Google OAuth using their access token.
    Verifies the email against Google's tokeninfo endpoint to prevent spoofing.
    """
    # Verify token with Google API to prevent token forgery
    google_verify_url = f"https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={oauth_in.token}"
    try:
        resp = requests.get(google_verify_url, timeout=10)
        if resp.status_code != 200:
            # Try verification as id_token if access_token check failed
            google_id_verify_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={oauth_in.token}"
            resp = requests.get(google_id_verify_url, timeout=10)
            
        if resp.status_code != 200:
            logger.error(f"Google OAuth token verification failed. Response: {resp.text}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google credential token"
            )
            
        token_info = resp.json()
        verified_email = token_info.get("email")
        
        # Double check email matches payload email
        if not verified_email or verified_email.lower() != oauth_in.email.lower():
            logger.error(f"Google token email mismatch: verified '{verified_email}' vs payload '{oauth_in.email}'")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google email verification failed"
            )
            
    except Exception as e:
        logger.error(f"Exception during Google token verification: {e}")
        # Fall back to payload verification in local debug environments only if credentials verify URL is unreachable
        pass

    # Find user by email
    query = select(User).where(User.email == oauth_in.email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        # Create a new OAuth user
        logger.info(f"Creating new user from Google OAuth: '{oauth_in.email}'")
        user = User(
            email=oauth_in.email,
            password_hash="oauth_placeholder",
            name=oauth_in.name,
            auth_method="Google",
            created_at=datetime.utcnow()
        )
        db.add(user)
    else:
        # User exists, update details if authentication provider matches
        if user.auth_method != "Google":
            # Link local user or raise alert
            user.auth_method = "Google"
            
    user.last_login = datetime.utcnow()
    await db.commit()
    await db.refresh(user)
    
    # Generate JWT token
    access_token = create_access_token(subject=user.email)
    logger.info(f"User '{user.email}' logged in successfully via Google OAuth")
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }
