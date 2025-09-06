from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import asyncio
import json
import datetime
from typing import Dict, Any, List, Optional
import os
from cryptography.fernet import Fernet
import base64
import hashlib


# Генерация ключа шифрования (в production используй переменные окружения)
def generate_encryption_key():
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        # Генерируем ключ на основе секрета + соль
        secret = os.getenv("JWT_SECRET", "default-secret-change-in-production")
        salt = b"hi-messenger-salt-2024"
        key = hashlib.pbkdf2_hmac('sha256', secret.encode(), salt, 100000, 32)
        return base64.urlsafe_b64encode(key)
    return key.encode()


# Инициализация шифрования
fernet = Fernet(generate_encryption_key())


# Шифрование данных
def encrypt_data(data: str) -> str:
    encrypted_data = fernet.encrypt(data.encode())
    return encrypted_data.decode()


# Дешифрование данных
def decrypt_data(encrypted_data: str) -> str:
    decrypted_data = fernet.decrypt(encrypted_data.encode())
    return decrypted_data.decode()


# Импорты для базы данных
try:
    from database import engine, get_db, AsyncSessionLocal
    from models import Base
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.future import select
    from sqlalchemy.orm import selectinload

    DB_AVAILABLE = True
except ImportError as e:
    print(f"Database import error: {e}")
    DB_AVAILABLE = False
except Exception as e:
    print(f"Database initialization error: {e}")
    DB_AVAILABLE = False


# Асинхронная функция для создания таблиц при старте
async def create_tables():
    if not DB_AVAILABLE:
        print("Database not available - skipping table creation")
        return

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("Таблицы в БД созданы/проверены")
    except Exception as e:
        print(f"Error creating tables: {e}")


# Lifespan-события FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запускается при старте приложения
    await create_tables()
    yield
    # Запускается при остановке приложения
    if DB_AVAILABLE:
        await engine.dispose()


app = FastAPI(lifespan=lifespan)

# Включи CORS для работы с фронтендом
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем все origins для разработки
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Менеджер для управления активными WebSocket-подключениями
class ConnectionManager:
    def __init__(self):
        # Словарь для хранения подключений: {user_id: websocket}
        self.active_connections: Dict[int, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        print(f"User #{user_id} connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        print(f"User #{user_id} disconnected. Active connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, user_id: int):
        if user_id in self.active_connections:
            await self.active_connections[user_id].send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections.values():
            await connection.send_text(message)


# Создаем экземпляр менеджера
manager = ConnectionManager()

# JWT Authentication
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext

# Настройки безопасности
SECRET_KEY = os.getenv("JWT_SECRET", "your-super-secret-jwt-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
security = HTTPBearer()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
        "exp": expire,
        "type": "access"
    }

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = verify_token(token)

    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload.get("sub"))
    username = payload.get("username")

    return {"id": user_id, "username": username}


# Эндпоинт регистрации
@app.post("/api/auth/register")
async def register_user(request: Request):
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        email = data.get("email", "").strip().lower()

        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username and password are required"
            )

        if len(username) < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username must be at least 3 characters long"
            )

        if len(password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 6 characters long"
            )

        from database import AsyncSessionLocal
        from models import User

        async with AsyncSessionLocal() as db:
            # Проверяем существование пользователя
            existing_user = await db.execute(
                select(User).filter((User.username == username) | (User.email == email))
            )
            if existing_user.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="User already exists"
                )

            # Шифруем email перед сохранением
            encrypted_email = encrypt_data(email) if email else None

            # Создаем нового пользователя
            hashed_password = get_password_hash(password)
            new_user = User(
                username=username,
                password_hash=hashed_password,
                email=encrypted_email,
                created_at=datetime.datetime.utcnow(),
                last_seen=datetime.datetime.utcnow()
            )

            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)

            # Создаем JWT токен
            access_token = create_access_token(new_user.id, new_user.username)

            return JSONResponse(
                status_code=status.HTTP_201_CREATED,
                content={
                    "message": "User created successfully",
                    "user_id": new_user.id,
                    "username": new_user.username,
                    "access_token": access_token,
                    "token_type": "bearer"
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


# Эндпоинт логина
@app.post("/api/auth/login")
async def login_user(request: Request):
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username and password are required"
            )

        from database import AsyncSessionLocal
        from models import User

        async with AsyncSessionLocal() as db:
            # Ищем пользователя
            result = await db.execute(select(User).filter(User.username == username))
            user = result.scalar_one_or_none()

            if not user or not verify_password(password, user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Обновляем last_seen
            user.last_seen = datetime.datetime.utcnow()
            await db.commit()

            # Создаем JWT токен
            access_token = create_access_token(user.id, user.username)

            return {
                "message": "Login successful",
                "user_id": user.id,
                "username": user.username,
                "access_token": access_token,
                "token_type": "bearer"
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


# Эндпоинт проверки токена
@app.get("/api/auth/me")
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    return current_user


# Защищенные WebSocket с авторизацией
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    # Проверяем токен
    payload = verify_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = int(payload.get("sub"))
    username = payload.get("username")

    await manager.connect(websocket, user_id)

    try:
        while True:
            # Ждем данные от клиента
            data = await websocket.receive_text()
            message_data = json.loads(data)

            # Шифруем текст сообщения перед сохранением
            encrypted_text = encrypt_data(message_data.get("text", ""))

            # Эмуляция работы с БД если БД недоступна
            if not DB_AVAILABLE:
                broadcast_message = json.dumps({
                    "type": "message",
                    "from": user_id,
                    "from_username": username,
                    "text": message_data.get("text", ""),
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                    "message_id": datetime.datetime.now().timestamp()
                })
                await manager.broadcast(broadcast_message)
                continue

            # Реальная работа с БД
            try:
                from database import AsyncSessionLocal
                from models import User, Message

                async with AsyncSessionLocal() as db:
                    # Проверяем существование пользователя
                    user_result = await db.execute(select(User).filter(User.id == user_id))
                    user = user_result.scalar_one_or_none()

                    if not user:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "User not found"
                        }))
                        continue

                    # Создаем и сохраняем зашифрованное сообщение
                    new_message = Message(
                        text=encrypted_text,  # Сохраняем зашифрованный текст
                        sender_id=user_id,
                        timestamp=datetime.datetime.utcnow()
                    )
                    db.add(new_message)
                    await db.commit()
                    await db.refresh(new_message)

                    # Отправляем расшифрованное сообщение всем
                    broadcast_message = json.dumps({
                        "type": "message",
                        "from": user_id,
                        "from_username": user.username,
                        "text": message_data.get("text", ""),  # Отправляем оригинальный текст
                        "timestamp": new_message.timestamp.isoformat(),
                        "message_id": new_message.id
                    })

                    await manager.broadcast(broadcast_message)

            except Exception as e:
                print(f"Database error: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Error saving message"
                }))

    except WebSocketDisconnect:
        manager.disconnect(user_id)
        disconnect_message = json.dumps({
            "type": "user_disconnected",
            "user_id": user_id,
            "username": username
        })
        await manager.broadcast(disconnect_message)


# API для получения истории сообщений (с расшифровкой)
@app.get("/api/messages")
async def get_messages(limit: int = 100, current_user: dict = Depends(get_current_user)):
    if not DB_AVAILABLE:
        return {"messages": []}

    try:
        from database import AsyncSessionLocal
        from models import Message, User

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Message, User.username)
                .join(User, Message.sender_id == User.id)
                .order_by(Message.timestamp.desc())
                .limit(limit)
            )

            messages = []
            for message, username in result:
                try:
                    # Расшифровываем текст сообщения
                    decrypted_text = decrypt_data(message.text)
                except:
                    decrypted_text = "Не удалось расшифровать сообщение"

                messages.append({
                    "id": message.id,
                    "text": decrypted_text,
                    "from": message.sender_id,
                    "from_username": username,
                    "timestamp": message.timestamp.isoformat()
                })

            return {"messages": list(reversed(messages))}

    except Exception as e:
        print(f"Error getting messages: {e}")
        return {"messages": []}


# API для получения информации о пользователе
@app.get("/api/user/{user_id}")
async def get_user_info(user_id: int, current_user: dict = Depends(get_current_user)):
    if not DB_AVAILABLE:
        return {"id": user_id, "username": f"User_{user_id}", "status": "offline"}

    try:
        from database import AsyncSessionLocal
        from models import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).filter(User.id == user_id))
            user = result.scalar_one_or_none()

            if user:
                # Расшифровываем email если он есть
                decrypted_email = None
                if user.email:
                    try:
                        decrypted_email = decrypt_data(user.email)
                    except:
                        decrypted_email = "Не удалось расшифровать"

                return {
                    "id": user.id,
                    "username": user.username,
                    "email": decrypted_email,
                    "created_at": user.created_at.isoformat(),
                    "last_seen": user.last_seen.isoformat()
                }
            else:
                raise HTTPException(status_code=404, detail="User not found")

    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# API для обновления username
@app.post("/api/user/profile")
async def update_user_profile(request: Request, current_user: dict = Depends(get_current_user)):
    try:
        data = await request.json()
        new_username = data.get("username", "").strip()
        email = data.get("email", "").strip().lower()

        if not new_username:
            raise HTTPException(status_code=400, detail="Username is required")

        if len(new_username) < 3:
            raise HTTPException(status_code=400, detail="Username must be at least 3 characters long")

        from database import AsyncSessionLocal
        from models import User

        async with AsyncSessionLocal() as db:
            # Проверяем, не занят ли username
            existing_user = await db.execute(
                select(User).filter(User.username == new_username, User.id != current_user["id"])
            )
            if existing_user.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Username already taken")

            # Находим пользователя
            result = await db.execute(select(User).filter(User.id == current_user["id"]))
            user = result.scalar_one_or_none()

            if user:
                user.username = new_username
                if email:
                    user.email = encrypt_data(email)
                user.last_seen = datetime.datetime.utcnow()
                await db.commit()

                # Создаем новый токен с обновленным username
                access_token = create_access_token(user.id, user.username)

                return {
                    "message": "Profile updated successfully",
                    "username": user.username,
                    "access_token": access_token
                }
            else:
                raise HTTPException(status_code=404, detail="User not found")

    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


# Простейший эндпоинт для проверки
@app.get("/")
async def root():
    return {
        "message": "Hello World",
        "python_version": "3.11.0",
        "database_available": DB_AVAILABLE,
        "encryption_enabled": True,
        "endpoints": {
            "chat": "/hi",
            "api_messages": "/api/messages",
            "register": "/api/auth/register",
            "login": "/api/auth/login",
            "profile": "/api/auth/me"
        }
    }


# Эндпоинт для проверки активных подключений
@app.get("/connections")
async def get_connections(current_user: dict = Depends(get_current_user)):
    return {"active_connections": len(manager.active_connections)}


# Главная страница чата Hi!
@app.get("/hi")
async def hi_chat():
    return FileResponse("static/index.html")


# Обслуживание статических файлов
app.mount("/static", StaticFiles(directory="static"), name="static")


# Fallback для SPA - все остальные пути возвращают index.html
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path.startswith("static/") or full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000)