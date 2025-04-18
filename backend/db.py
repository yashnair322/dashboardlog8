
import psycopg2
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get the database URL from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

# Establish database connection
try:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    print("✅ Database connected successfully!")
except Exception as e:
    print(f"❌ Error connecting to the database: {e}")

# Function to create users table if it doesn't exist
def create_users_table():
    try:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(100) NOT NULL
        );
        """)
        conn.commit()
        print("✅ Users table is ready!")
    except Exception as e:
        print(f"❌ Error creating table: {e}")
        conn.rollback()

# Call the function to ensure the table exists
create_users_table()
