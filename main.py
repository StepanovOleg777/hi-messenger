from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import asyncio
import json
import datetime
from typing import Dict, Any, List
import os

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


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await manager.connect(websocket, user_id)

    try:
        while True:
            # Ждем данные от клиента
            data = await websocket.receive_text()
            message_data = json.loads(data)

            # Эмуляция работы с БД если БД недоступна
            if not DB_AVAILABLE:
                broadcast_message = json.dumps({
                    "type": "message",
                    "from": user_id,
                    "from_username": f"User_{user_id}",
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
                        # Если пользователь не найден, создаем временного
                        user = User(
                            id=user_id,
                            username=f"User_{user_id}",
                            password_hash="temp",
                            last_seen=datetime.datetime.utcnow()
                        )
                        db.add(user)
                        await db.commit()
                    else:
                        # Обновляем время последней активности
                        user.last_seen = datetime.datetime.utcnow()
                        await db.commit()

                    # Создаем и сохраняем сообщение
                    new_message = Message(
                        text=message_data.get("text", ""),
                        sender_id=user_id,
                        timestamp=datetime.datetime.utcnow()
                    )
                    db.add(new_message)
                    await db.commit()
                    await db.refresh(new_message)

                    # Отправляем сообщение всем
                    broadcast_message = json.dumps({
                        "type": "message",
                        "from": user_id,
                        "from_username": user.username,
                        "text": new_message.text,
                        "timestamp": new_message.timestamp.isoformat(),
                        "message_id": new_message.id
                    })

                    await manager.broadcast(broadcast_message)

            except Exception as e:
                print(f"Database error: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Ошибка сохранения сообщения"
                }))

    except WebSocketDisconnect:
        manager.disconnect(user_id)
        disconnect_message = json.dumps({
            "type": "user_disconnected",
            "user_id": user_id
        })
        await manager.broadcast(disconnect_message)


# API для получения истории сообщений
@app.get("/api/messages")
async def get_messages(limit: int = 100):
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
                messages.append({
                    "id": message.id,
                    "text": message.text,
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
async def get_user_info(user_id: int):
    if not DB_AVAILABLE:
        return {"id": user_id, "username": f"User_{user_id}", "status": "offline"}

    try:
        from database import AsyncSessionLocal
        from models import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).filter(User.id == user_id))
            user = result.scalar_one_or_none()

            if user:
                return {
                    "id": user.id,
                    "username": user.username,
                    "created_at": user.created_at.isoformat(),
                    "last_seen": user.last_seen.isoformat()
                }
            else:
                return JSONResponse(status_code=404, content={"message": "User not found"})

    except Exception as e:
        print(f"Error getting user info: {e}")
        return JSONResponse(status_code=500, content={"message": "Internal server error"})


# API для обновления username
@app.post("/api/user/{user_id}/username")
async def update_username(user_id: int, new_username: str):
    if not DB_AVAILABLE:
        return {"message": "Database not available"}

    try:
        from database import AsyncSessionLocal
        from models import User

        async with AsyncSessionLocal() as db:
            # Проверяем, не занят ли username
            existing_user = await db.execute(
                select(User).filter(User.username == new_username, User.id != user_id)
            )
            if existing_user.scalar_one_or_none():
                return JSONResponse(status_code=400, content={"message": "Username already taken"})

            # Находим пользователя
            result = await db.execute(select(User).filter(User.id == user_id))
            user = result.scalar_one_or_none()

            if user:
                user.username = new_username
                user.last_seen = datetime.datetime.utcnow()
                await db.commit()
                return {"message": "Username updated successfully"}
            else:
                return JSONResponse(status_code=404, content={"message": "User not found"})

    except Exception as e:
        print(f"Error updating username: {e}")
        return JSONResponse(status_code=500, content={"message": "Internal server error"})


# Простейший эндпоинт для проверки
@app.get("/")
async def root():
    return {
        "message": "Hello World",
        "python_version": "3.11.0",
        "database_available": DB_AVAILABLE,
        "endpoints": {
            "chat": "/hi",
            "api_messages": "/api/messages",
            "websocket": "/ws/{user_id}"
        }
    }


# Эндпоинт для проверки активных подключений
@app.get("/connections")
async def get_connections():
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

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)