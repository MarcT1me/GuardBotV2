import json
import os
import time
import secrets

from fastapi import FastAPI, Depends, HTTPException, Response, Request
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.exc import OperationalError
import aiohttp
from pydantic import BaseModel
from loguru import logger
import dotenv
from authlib.integrations.starlette_client import OAuth
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

dotenv.load_dotenv(dotenv_path="../.env")
BOT_API_URL = os.getenv("BOT_API_URL")
GUARD_AUTH_CLIENT_ID = os.getenv("GUARD_AUTH_CLIENT_ID")
GUARD_AUTH_CLIENT_SECRET = os.getenv("GUARD_AUTH_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

oauth = OAuth()
oauth.register(
    name='discord',
    client_id=GUARD_AUTH_CLIENT_ID,
    client_secret=GUARD_AUTH_CLIENT_SECRET,
    authorize_url='https://discord.com/api/oauth2/authorize',
    access_token_url='https://discord.com/api/oauth2/token',
    client_kwargs={
        'scope': 'identify guilds',
        'response_type': 'code'
    },
)

sessions = {}


class Database:
    POSTGRES_DB = os.getenv("POSTGRES_DB")
    POSTGRES_USER = os.getenv("POSTGRES_USER")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")

    DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@db:5432/{POSTGRES_DB}"

    Base = declarative_base()

    def __init__(self):
        self.init_engine = self.new_engine
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.init_engine)
        logger.success("Database initialized")

    @property
    def new_engine(self):
        return create_engine(Database.DATABASE_URL)

    def wait_for_db(self, max_retries=5, retry_delay=5):
        for attempt in range(max_retries):
            try:
                eng = self.new_engine
                with eng.connect():
                    logger.success("Database connection successful!")
                    return
            except OperationalError:
                logger.error(f"Database connection failed, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(retry_delay)

        raise Exception("Database connection failed after retries")

    def create_all_models(self):
        Database.Base.metadata.create_all(self.init_engine)


class Server(Database.Base):
    __tablename__ = "servers"
    id = Column(Integer, primary_key=True)
    discord_id = Column(BigInteger, unique=True)
    name = Column(String)


class User(Database.Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    discord_id = Column(BigInteger, unique=True)
    username = Column(String)


class Message(Database.Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.discord_id"))
    server_id = Column(BigInteger, ForeignKey("servers.discord_id"))
    content = Column(Text, default='Default message')


database = Database()
database.wait_for_db()
database.create_all_models()

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET"),
    session_cookie="session",
    https_only=False,
    same_site="lax",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/test/health")
async def health_check():
    return Response(
        status_code=200,
        content=json.dumps({
            "status": "ok"
        }),
    )


@app.get("/test/test_bot")
async def test_bot():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BOT_API_URL}/health") as response:
                content = await response.text()
                status = response.status
                content = content
    except Exception as e:
        logger.error(f"error in bot health testing:\n{e}")
        status = 500
        content = json.dumps({"status": "error", "error": str(e)})

    return Response(
        status_code=status,
        content=content,
    )


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/auth/login")
async def login_via_discord(request: Request):
    state = secrets.token_urlsafe(16)

    request.session["oauth_state"] = state
    logger.info(f"Saved state in session: {state}")

    redirect_uri = str(request.url_for("auth_callback"))
    logger.info(f"Redirect URI: {redirect_uri}")

    return await oauth.discord.authorize_redirect(request, redirect_uri, state=state)


@app.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        state = request.query_params.get("state")
        code = request.query_params.get("code")
        logger.info(f"Received callback with state: {state}, code: {code}")

        if not state:
            raise HTTPException(status_code=400, detail="Missing state parameter")

        oauth_state = request.session.get("oauth_state")
        logger.info(f"Session oauth_state: {oauth_state}")

        if not oauth_state or oauth_state != state:
            logger.error(f"State mismatch: oauth_state={oauth_state}, state={state}")
            raise HTTPException(status_code=400, detail="Invalid state")

        token = await oauth.discord.authorize_access_token(request)
        logger.info("Successfully obtained access token")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                    "https://discord.com/api/users/@me",
                    headers={"Authorization": f"Bearer {token['access_token']}"}
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail="Failed to fetch user data")
                user_data = await resp.json()

        user_id = int(user_data['id'])

        user = db.query(User).filter_by(discord_id=user_id).first()
        if not user:
            user = User(discord_id=user_id, username=user_data['username'])
            db.add(user)
            db.commit()

        # Сохраняем серверы пользователя
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    "https://discord.com/api/users/@me/guilds",
                    headers={"Authorization": f"Bearer {token['access_token']}"}
            ) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail="Failed to fetch guilds")
                guilds = await resp.json()

        for guild in guilds:
            server = db.query(Server).filter_by(discord_id=guild['id']).first()
            if not server:
                server = Server(discord_id=guild['id'], name=guild['name'])
                db.add(server)

        db.commit()

        sessions[state] = {
            "host": request.client.host,
            "user_id": user_id,
            "guilds": guilds,
        }

        logger.info("RedirectResponse")
        return RedirectResponse(url=f"http://localhost:3000/auth-success?state={state}")

    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=400, detail="Authentication failed")


@app.get("/user/session")
async def get_session(request: Request):
    state = request.query_params.get("state")
    host = request.client.host

    if not state or state not in sessions:
        return unauthorized_response()

    session = sessions[state]

    if session["host"] != host:
        return unauthorized_response()

    async with aiohttp.ClientSession() as s:
        payload = {
            "guilds": session["guilds"],
        }
        async with s.post(f"{BOT_API_URL}/overhaul_guilds", json=payload) as response:
            resp = await response.json()
            logger.info(f"bot send: {response.status}, {type(resp)}, {resp}")
            guilds = json.loads(resp['approved'])

    return Response(
        status_code=200,
        content=json.dumps({
            "status": "success",
            "user_id": session["user_id"],
            "guilds": guilds
        })
    )


def unauthorized_response():
    return Response(
        status_code=401,
        content=json.dumps({
            "status": "Unauthorized"
        })
    )


class UserRequest(BaseModel):
    user_id: int


@app.post("/user/create")
async def user_create(
        request: UserRequest,
        db: Session = Depends(get_db),
):
    try:
        user = db.query(User).filter_by(
            discord_id=request.user_id
        ).first()

        if not user:
            user = User(
                discord_id=request.user_id,
                username="Test"
            )
            db.add(user)

        db.commit()

        return Response(
            status_code=200,
            content=json.dumps({
                "status": "success"
            }),
        )
    except Exception as e:
        logger.error(f"Any error in login:\n{e}")
        return Response(
            status_code=500,
            content=json.dumps({
                "status": "error",
                "error": str(e)
            }),
        )


class GuildRequest(BaseModel):
    server_id: int


@app.post("/guild/create")
async def guild_create(
        request: GuildRequest,
        db: Session = Depends(get_db),
):
    try:
        server = db.query(Server).filter_by(
            discord_id=request.server_id
        ).first()

        if not server:
            user = Server(
                discord_id=request.server_id,
                name="Test"
            )
            db.add(user)

        db.commit()

        return Response(
            status_code=200,
            content=json.dumps({
                "status": "success"
            }),
        )
    except Exception as e:
        logger.error(f"Any error in login:\n{e}")
        return Response(
            status_code=500,
            content=json.dumps({
                "status": "error",
                "error": str(e)
            }),
        )


class SaveMessageRequest(BaseModel):
    user_id: int
    server_id: int
    content: str


@app.post("/message/save")
async def save_message(
        request: SaveMessageRequest,
        db: Session = Depends(get_db),
):
    try:
        message = db.query(Message).filter_by(
            user_id=request.user_id,
            server_id=request.server_id
        ).first()

        if message:
            message.content = request.content
        else:
            message = Message(
                user_id=request.user_id,
                server_id=request.server_id,
                content=request.content
            )
            db.add(message)

        db.commit()

        return Response(
            status_code=200,
            content=json.dumps({
                "status": "save"
            }),
        )
    except Exception as e:
        logger.error(f"Any error in save message:\n{e}")
        return Response(
            status_code=500,
            content=json.dumps({
                "status": "error",
                "error": str(e)
            }),
        )


class ResetMessageRequest(BaseModel):
    user_id: int
    server_id: int


@app.post("/message/reset")
async def reset_message(
        request: ResetMessageRequest,
        db: Session = Depends(get_db)
):
    try:
        message = db.query(Message).filter_by(
            user_id=request.user_id,
            server_id=request.server_id
        ).first()

        if message:
            message.content = "Default message"
            db.commit()
            return Response(
                status_code=200,
                content=json.dumps({
                    "status": "reset"
                }),
            )

        return Response(
            status_code=404,
            content=json.dumps({
                "status": "message not found"
            }),
        )
    except Exception as e:
        logger.error(f"Any error in resset message:\n{e}")
        return Response(
            status_code=500,
            content=json.dumps({
                "status": "error",
                "error": str(e)
            }),
        )


class SendMessageRequest(BaseModel):
    user_id: int
    server_id: int
    channel_id: int


@app.post("/message/send")
async def send_message(
        request: SendMessageRequest,
        db: Session = Depends(get_db),
):
    try:
        message = db.query(Message).filter_by(
            user_id=request.user_id,
            server_id=request.server_id
        ).first()

        if not message or not message.content:
            raise HTTPException(status_code=404, detail="Message not found")

        async with aiohttp.ClientSession() as session:
            payload = {
                "user_id": request.user_id,
                "server_id": request.server_id,
                "channel_id": request.channel_id,
                "content": message.content,
            }
            async with session.post(f"{BOT_API_URL}/send_message", json=payload) as response:
                resp = await response.json()
                logger.info(f"bot send: {response.status}, {type(resp)}, {resp}")
                return Response(
                    status_code=response.status,
                    content=json.dumps({
                        "status": "success" if response.status == 200 else "error",
                        "answer": resp
                    })
                )
    except Exception as e:
        logger.error(f"Any error in sending message:\n{e}")
        return Response(
            status_code=500,
            content=json.dumps({
                "status": "error",
                "error": str(e)
            }),
        )


class GetMessageRequest(BaseModel):
    user_id: int
    server_id: int


@app.get("/message/get")
async def get_message(
        request: GetMessageRequest,
        db: Session = Depends(get_db),
):
    try:
        message = db.query(Message).filter_by(
            user_id=request.user_id,
            server_id=request.server_id
        ).first()

        return Response(
            status_code=200,
            content=json.dumps({
                "status": "success",
                "content": message.content
            }),
        )
    except Exception as e:
        logger.error(f"Any error in get message:\n{e}")
        return Response(
            status_code=500,
            content=json.dumps({
                "status": "error",
                "error": str(e)
            }),
        )
