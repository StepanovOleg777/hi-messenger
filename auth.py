from passlib.context import CryptContext
from datetime import datetime, timedelta
import secrets
import jwt
import os

# Настройки для хэширования паролей
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Секретный ключ для JWT
SECRET_KEY = os.getenv("JWT_SECRET", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(user_id: int, username: str):
    expires_delta = timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    expire = datetime.utcnow() + expires_delta

    to_encode = {
        "sub": str(user_id),
        "username": username,
        "exp": expire
    }

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


def generate_session_token():
    return secrets.token_urlsafe(32)