"""SQLite database module for user authentication."""

import hashlib
import os
import sqlite3
from datetime import datetime
from typing import Optional, Tuple


# Database file path
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "users.db")


def init_db():
    """Initialize the SQLite database with users table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                auth_method TEXT DEFAULT 'Email',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        print("Database initialized successfully")
        
    except sqlite3.Error as e:
        print(f"Database initialization error: {e}")


def get_db_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    """
    Hash a password using SHA-256.
    
    Args:
        password: Plain text password.
        
    Returns:
        Hashed password string.
    """
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(email: str, password: str, name: str, auth_method: str = "Email") -> bool:
    """
    Create a new user in the database.
    
    Args:
        email: User email address.
        password: Plain text password (will be hashed).
        name: User display name.
        auth_method: Authentication method (Email, Google, Apple).
        
    Returns:
        True if user created successfully, False otherwise.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Hash the password
        password_hash = hash_password(password)
        
        # Insert user
        cursor.execute(
            """
            INSERT INTO users (email, password_hash, name, auth_method)
            VALUES (?, ?, ?, ?)
            """,
            (email, password_hash, name, auth_method)
        )
        
        conn.commit()
        conn.close()
        print(f"User created successfully: {email}")
        return True
        
    except sqlite3.IntegrityError:
        print(f"User already exists: {email}")
        return False
    except sqlite3.Error as e:
        print(f"Error creating user: {e}")
        return False


def verify_user(email: str, password: str) -> Optional[Tuple[str, str, str]]:
    """
    Verify user credentials.
    
    Args:
        email: User email address.
        password: Plain text password to verify.
        
    Returns:
        Tuple of (email, name, auth_method) if credentials valid, None otherwise.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get user by email
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        conn.close()
        
        if not user:
            print(f"User not found: {email}")
            return None
        
        # Verify password
        password_hash = hash_password(password)
        if user["password_hash"] != password_hash:
            print(f"Invalid password for: {email}")
            return None
        
        # Update last login
        update_last_login(email)
        
        return (user["email"], user["name"], user["auth_method"])
        
    except sqlite3.Error as e:
        print(f"Error verifying user: {e}")
        return None


def get_user_by_email(email: str) -> Optional[dict]:
    """
    Get user information by email.
    
    Args:
        email: User email address.
        
    Returns:
        Dictionary with user info or None if not found.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        conn.close()
        
        if user:
            return dict(user)
        return None
        
    except sqlite3.Error as e:
        print(f"Error getting user: {e}")
        return None


def update_last_login(email: str):
    """Update the last login timestamp for a user."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE users SET last_login = ? WHERE email = ?",
            (datetime.utcnow().isoformat(), email)
        )
        
        conn.commit()
        conn.close()
        
    except sqlite3.Error as e:
        print(f"Error updating last login: {e}")


def user_exists(email: str) -> bool:
    """
    Check if a user already exists.
    
    Args:
        email: User email address.
        
    Returns:
        True if user exists, False otherwise.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE email = ?", (email,))
        result = cursor.fetchone()
        
        conn.close()
        
        return result["count"] > 0
        
    except sqlite3.Error as e:
        print(f"Error checking user existence: {e}")
        return False


# Initialize database on module import
init_db()
