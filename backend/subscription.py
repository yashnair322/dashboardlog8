
import os
import razorpay
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from backend.auth import get_current_user
import psycopg2
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

# Initialize Razorpay client
client = razorpay.Client(
    auth=(os.getenv('RAZORPAY_KEY_ID'), os.getenv('RAZORPAY_KEY_SECRET')))

PLANS = {
    'free': {'price': 0, 'bots': 1, 'trade_limit': 4},
    'pro': {'price': 99900, 'bots': 5, 'trade_limit': -1},
    'enterprise': {'price': 249900, 'bots': -1, 'trade_limit': -1}
}

def get_db_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

@router.post("/create-order")
async def create_order(plan_data: dict, current_user: dict = Depends(get_current_user)):
    plan = plan_data.get('plan')
    if plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan selected")
    
    if plan == 'free':
        return {"message": "Free plan selected"}

    try:
        amount = PLANS[plan]['price']
        order_data = {
            'amount': amount,
            'currency': 'INR',
            'receipt': f"order_{datetime.now().timestamp()}"
        }
        order = client.order.create(data=order_data)
        
        return {
            "key_id": os.getenv('RAZORPAY_KEY_ID'),
            "amount": amount,
            "currency": "INR",
            "order_id": order['id']
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verify-payment")
async def verify_payment(payment_data: dict, current_user: dict = Depends(get_current_user)):
    try:
        client.utility.verify_payment_signature({
            'razorpay_order_id': payment_data['razorpay_order_id'],
            'razorpay_payment_id': payment_data['razorpay_payment_id'],
            'razorpay_signature': payment_data['razorpay_signature']
        })
        
        # Update user's subscription in database
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE users 
            SET subscription_plan = %s, 
                subscription_date = CURRENT_TIMESTAMP 
            WHERE email = %s""", (payment_data['plan'], current_user['email']))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Payment verification failed")
