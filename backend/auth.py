
from fastapi import Depends, HTTPException, Security
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import os
import psycopg2
from dotenv import load_dotenv
from fastapi import Request

# Load environment variables
load_dotenv()

# Security and JWT setup
SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Database Connection
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Pydantic Models
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: str | None = None

# Password Hashing Functions
def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# JWT Token Functions
def create_access_token(data: dict, expires_delta: timedelta) -> str:
    """Generate a JWT access token."""
    expire = datetime.utcnow() + expires_delta
    data.update({"exp": expire})
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request):
    """Manually extract and decode JWT token."""
    auth_header = request.headers.get("Authorization")
    print(f"\n🔍 Raw Authorization Header: {auth_header}")  # Print the raw header

    if not auth_header or not auth_header.startswith("Bearer "):
        print("❌ No Bearer token found in header!")
        raise HTTPException(status_code=401, detail="Token missing or incorrect format")

    token = auth_header.split("Bearer ")[1]
    print(f"✅ Extracted Token: {token}")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"✅ Decoded JWT Payload: {payload}")  # Print payload for debugging

        email = payload.get("sub")
        if not email:
            print("❌ 'sub' (email) missing from token!")
            raise HTTPException(status_code=401, detail="Invalid token (No email)")

        return {"email": email}  # ✅ Return dict for easy use

    except JWTError as e:
        print(f"❌ JWT Decode Error: {e}")
        raise HTTPException(status_code=401, detail="Could not validate credentials")

# Authentication Route: Login
def login_user(form_data: OAuth2PasswordRequestForm) -> dict:
    """Authenticate user and return JWT token."""
    cursor.execute("SELECT email, password, is_verified FROM users WHERE email = %s", (form_data.username,))
    db_user = cursor.fetchone()

    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")  # Email not found

    email, hashed_password, is_verified = db_user

    if not verify_password(form_data.password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")  # Wrong password

    if not is_verified:
        raise HTTPException(status_code=401, detail="Email not verified. Please check your email.")

    access_token = create_access_token(data={"sub": email}, expires_delta=timedelta(minutes=30))

    return {"access_token": access_token, "token_type": "bearer"}
