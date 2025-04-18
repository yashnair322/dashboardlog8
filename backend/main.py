import logging
from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from jose import JWTError, jwt
import psycopg2
import os
import secrets
import random
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
import razorpay

# Importing modules from backend
from backend import main2
from backend.main2 import router
from backend.auth import get_current_user, login_user, create_access_token, get_password_hash, verify_password

# Load environment variables
load_dotenv()

# FastAPI App
app = FastAPI()
app.include_router(router)

# Razorpay Initialization (replace with your actual key and secret)
razorpay_client = razorpay.Client(auth=("your_key_id", "your_key_secret"))


# Store verification codes temporarily
verification_codes = {}

# Security and JWT setup
SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# Database Connection
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Ensure required tables exist
# Add subscription_plan and trade_count columns if they don't exist
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        first_name VARCHAR(50) NOT NULL,
        last_name VARCHAR(50) NOT NULL,
        email VARCHAR(100) UNIQUE NOT NULL,
        password VARCHAR(200) NOT NULL,
        is_verified BOOLEAN DEFAULT FALSE,
        subscription_id INTEGER REFERENCES subscriptions(id),
        subscription_plan VARCHAR(20) DEFAULT 'free',
        trade_count INTEGER DEFAULT 0
    );
    """)

# Add columns if they don't exist
cursor.execute("""
    DO $$ 
    BEGIN 
        IF NOT EXISTS (SELECT FROM information_schema.columns 
                      WHERE table_name = 'users' AND column_name = 'subscription_plan') THEN
            ALTER TABLE users ADD COLUMN subscription_plan VARCHAR(20) DEFAULT 'free';
        END IF;
        
        IF NOT EXISTS (SELECT FROM information_schema.columns 
                      WHERE table_name = 'users' AND column_name = 'trade_count') THEN
            ALTER TABLE users ADD COLUMN trade_count INTEGER DEFAULT 0;
        END IF;
    END $$;
    """)
conn.commit()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS bots (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) UNIQUE NOT NULL,
        exchange VARCHAR(50) NOT NULL,
        symbol VARCHAR(50) NOT NULL,
        quantity FLOAT NOT NULL,
        email_address VARCHAR(100) NOT NULL,
        email_password VARCHAR(200) NOT NULL,
        imap_server VARCHAR(100) NOT NULL,
        email_subject VARCHAR(200) NOT NULL,
        api_key VARCHAR(200),
        api_secret VARCHAR(200),
        account_id VARCHAR(100),
        user_email VARCHAR(100) NOT NULL,
        paused BOOLEAN DEFAULT FALSE,      
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
conn.commit()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        price INTEGER,
        bot_limit INTEGER
    );
""")
conn.commit()

# Insert initial subscriptions
cursor.execute("INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)", ("Free", 0, 1))
cursor.execute("INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)", ("Basic", 99, 5))
cursor.execute("INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)", ("Premium", 199, 10))
conn.commit()


# Utility Functions
def send_reset_email(email: str, reset_link: str):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    msg = EmailMessage()
    msg.set_content(
        f"Click the link to reset your password: {reset_link}\n\nThis link will expire in 15 minutes."
    )
    msg["Subject"] = "Password Reset Request"
    msg["From"] = smtp_user
    msg["To"] = email

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)


# Pydantic Models
class User(BaseModel):
    first_name: str
    last_name: str
    email: str
    password: str = Field(..., min_length=8, pattern=r"^[A-Za-z\d@$!%*?&]+$")
    subscription_id: int = 1 # default to free subscription


class VerifyCode(BaseModel):
    email: str
    code: str


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: str | None = None

class Subscription(BaseModel):
    id: int
    name: str
    price: int
    bot_limit: int

class PaymentResponse(BaseModel):
    payment_id: str
    order_id: str
    signature: str

# Email Verification
def send_verification_email(email: str, code: str):
    try:
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")

        msg = EmailMessage()
        msg.set_content(
            f"Your verification code is: {code}\n\nThis code will expire in 10 minutes."
        )
        msg["Subject"] = "Email Verification Code"
        msg["From"] = smtp_user
        msg["To"] = email

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)

    except Exception as e:
        raise HTTPException(status_code=500,
                            detail="Failed to send verification email.")


# Serve Static Files and Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/signup", response_class=HTMLResponse)
def signup(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/create-bot", response_class=HTMLResponse)
def create_bot(request: Request):
    return templates.TemplateResponse("create_bot.html", {"request": request})


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password(request: Request):
    return templates.TemplateResponse("reset_password.html",
                                      {"request": request})

@app.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions(request: Request):
    cursor.execute("SELECT * FROM subscriptions")
    subscriptions = cursor.fetchall()
    return templates.TemplateResponse("subscriptions.html", {"request": request, "subscriptions": subscriptions})

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)


# Routes
@app.post("/signup")
def signup(user: User):
    cursor.execute("SELECT id FROM users WHERE email = %s", (user.email, ))
    existing_user = cursor.fetchone()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already exists.")

    verification_code = str(random.randint(100000, 999999))
    verification_codes[user.email] = {
        "code": verification_code,
        "user_data": user.dict()
    }
    send_verification_email(user.email, verification_code)
    return {"message": "Verification code sent to your email."}


@app.post("/verify-email")
def verify_email(data: VerifyCode):
    if data.email in verification_codes:
        stored_data = verification_codes[data.email]
        if stored_data["code"] == data.code:
            user_data = stored_data["user_data"]
            hashed_password = get_password_hash(user_data['password'])

            cursor.execute(
                "INSERT INTO users (first_name, last_name, email, password, is_verified, subscription_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_data['first_name'], user_data['last_name'],
                 user_data['email'], hashed_password, True, user_data['subscription_id']))
            conn.commit()
            del verification_codes[data.email]
            return {"message": "Email verified successfully."}
        else:
            raise HTTPException(status_code=400,
                                detail="Invalid verification code.")
    raise HTTPException(status_code=400,
                        detail="Verification code not found or expired.")


@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    return login_user(form_data)


@app.post("/forgot-password")
def forgot_password(request: Request, data: VerifyCode):
    cursor.execute("SELECT id, email FROM users WHERE email = %s",
                   (data.email, ))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Email not registered")

    reset_token = create_access_token(data={"sub": data.email},
                                      expires_delta=timedelta(minutes=15))
    reset_link = f"{request.url.scheme}://{request.headers.get('host')}/reset-password?token={reset_token}"
    send_reset_email(data.email, reset_link)
    return {"message": "Password reset link sent."}


@app.post("/reset-password")
def reset_password(data: VerifyCode):
    try:
        payload = jwt.decode(data.code, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=400, detail="Invalid reset token")

        hashed_password = get_password_hash(data.code)
        cursor.execute("UPDATE users SET password = %s WHERE email = %s",
                       (hashed_password, email))
        conn.commit()
        return {"message": "Password reset successful."}

    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

@app.post("/initiate_payment", response_model=PaymentResponse)
async def initiate_payment(subscription_id: int):
    cursor.execute("SELECT price FROM subscriptions WHERE id = %s", (subscription_id,))
    price = cursor.fetchone()[0]
    if price is None:
      raise HTTPException(status_code=404, detail="Subscription not found")

    order_response = razorpay_client.order.create({
        "amount": price * 100,  # Amount in paise
        "currency": "INR",
        "receipt": str(secrets.token_hex(8))
    })

    return PaymentResponse(payment_id="", order_id=order_response["id"], signature="")


@app.post("/complete_payment")
async def complete_payment(payment_data: PaymentResponse):
    # Verify payment with Razorpay
    try:
      # Add your Razorpay payment verification logic here.
      # This would involve verifying the signature, order ID, and payment ID.
      # Once verified, update the user's subscription status in the database.
      print("Payment verification successful!")
      return {"message": "Payment successful"}

    except Exception as e:
      return JSONResponse(status_code=500, content={"message": f"Payment verification failed: {str(e)}"})


# Start the FastAPI application
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn")

@app.on_event("startup")
async def startup_event():
    logger.info("Starting application...")
    try:
        # Test database connection
        cursor.execute("SELECT 1")
        logger.info("Database connection successful")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
    logger.info("Application startup complete")

# Start the FastAPI application
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
