import logging
from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, Header
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
from typing import Optional

# Importing modules from backend
from backend import main2
from backend.main2 import router
from backend.auth import get_current_user, login_user, create_access_token, get_password_hash, verify_password

# Load environment variables
load_dotenv()

# FastAPI App
app = FastAPI()
app.include_router(router)

# Razorpay Initialization - Replace with your actual keys
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "your_key_id")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "your_key_secret")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

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
    CREATE TABLE IF NOT EXISTS subscriptions (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        price INTEGER,
        bot_limit INTEGER
    );
""")
conn.commit()

# Then, create users table with reference to subscriptions
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

# Create bots table
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

# Create orders table
cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY,
        order_id VARCHAR(100) UNIQUE NOT NULL,
        user_email VARCHAR(100) NOT NULL,
        plan VARCHAR(20) NOT NULL,
        amount INTEGER NOT NULL,
        status VARCHAR(20) DEFAULT 'created',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
""")
conn.commit()

# Insert initial subscriptions if they don't exist
cursor.execute("SELECT COUNT(*) FROM subscriptions")
subscription_count = cursor.fetchone()[0]
if subscription_count == 0:
    cursor.execute(
        "INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)",
        ("Free", 0, 1))
    cursor.execute(
        "INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)",
        ("Pro", 999, 5))
    cursor.execute(
        "INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)",
        ("Enterprise", 2499, 10))
    conn.commit()

# Insert initial subscriptions if they don't exist
cursor.execute("SELECT COUNT(*) FROM subscriptions")
subscription_count = cursor.fetchone()[0]
if subscription_count == 0:
    cursor.execute("INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)", ("Free", 0, 1))
    cursor.execute("INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)", ("Pro", 999, 5))
    cursor.execute("INSERT INTO subscriptions (name, price, bot_limit) VALUES (%s, %s, %s)", ("Enterprise", 2499, 10))
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

class PlanRequest(BaseModel):
    plan: str

class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    plan: str

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


# Authentication helper function
async def get_current_user_from_token(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    
    cursor.execute("SELECT id, email, subscription_plan FROM users WHERE email = %s", (token_data.email,))
    user = cursor.fetchone()
    if user is None:
        raise credentials_exception
    return {"id": user[0], "email": user[1], "subscription_plan": user[2]}


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
    return templates.TemplateResponse("subscriptions.html", {"request": request})

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

# New routes for Razorpay integration
@app.post("/create-order")
async def create_order(plan_request: PlanRequest, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    token = authorization.replace("Bearer ", "")
    
    try:
        user = await get_current_user_from_token(token)
        plan = plan_request.plan
        
        # Get plan amount based on the selected plan
        amount = 0
        if plan == "pro":
            amount = 999 * 100  # ₹999 in paise
        elif plan == "enterprise":
            amount = 2499 * 100  # ₹2499 in paise
        else:
            raise HTTPException(status_code=400, detail="Invalid plan selected")
        
        # Create Razorpay order
        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": f"receipt_{secrets.token_hex(8)}"
        }
        
        order = razorpay_client.order.create(data=order_data)
        
        # Store order details in database
        cursor.execute(
            "INSERT INTO orders (order_id, user_email, plan, amount, status) VALUES (%s, %s, %s, %s, %s)",
            (order["id"], user["email"], plan, amount, "created")
        )
        conn.commit()
        
        # Return necessary details for frontend
        return {
            "key_id": RAZORPAY_KEY_ID,
            "amount": amount,
            "currency": "INR",
            "order_id": order["id"]
        }
    
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        logging.error(f"Order creation error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create order: {str(e)}")

@app.post("/verify-payment")
async def verify_payment(payment_data: VerifyPaymentRequest, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    token = authorization.replace("Bearer ", "")
    
    try:
        user = await get_current_user_from_token(token)
        
        # Verify payment signature
        params_dict = {
            'razorpay_order_id': payment_data.razorpay_order_id,
            'razorpay_payment_id': payment_data.razorpay_payment_id,
            'razorpay_signature': payment_data.razorpay_signature
        }
        
        # Verify signature
        razorpay_client.utility.verify_payment_signature(params_dict)
        
        # Update order status in database
        cursor.execute(
            "UPDATE orders SET status = %s WHERE order_id = %s",
            ("completed", payment_data.razorpay_order_id)
        )
        
        # Update user subscription plan
        cursor.execute(
            "UPDATE users SET subscription_plan = %s WHERE email = %s",
            (payment_data.plan, user["email"])
        )
        conn.commit()
        
        return {"status": "success", "message": "Payment verified successfully"}
        
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    except Exception as e:
        logging.error(f"Payment verification error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to verify payment: {str(e)}")


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
