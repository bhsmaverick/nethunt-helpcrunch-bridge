import requests
import pyotp
import sys

BASE_URL = "http://127.0.0.1:8088"

def run_test():
    session = requests.Session()
    
    print("1. Checking Auth Status...")
    res = session.get(f"{BASE_URL}/api/auth/status")
    print(res.json())
    status_data = res.json()
    
    username = "admin"
    password = "SuperSecurePassword123"
    
    if status_data.get("status") == "unregistered":
        print("\n2. Registering first Administrator user...")
        payload = {
            "username": username,
            "password": password
        }
        res = session.post(f"{BASE_URL}/api/auth/register", json=payload)
        if res.status_code != 200:
            print(f"Registration failed: {res.text}")
            return
        
        reg_data = res.json()
        print(f"Registered! Secret Key: {reg_data.get('twofa_secret')}")
        secret = reg_data.get("twofa_secret")
        
        # Calculate TOTP token locally using pyotp
        totp = pyotp.TOTP(secret)
        token = totp.now()
        print(f"Generated TOTP Verification Token: {token}")
        
        print("\n3. Verifying 2FA Setup...")
        payload = {
            "username": username,
            "token": token
        }
        res = session.post(f"{BASE_URL}/api/auth/verify-2fa", json=payload)
        print(res.status_code, res.json())
        if res.status_code != 200:
            print("2FA verification failed")
            return
            
    else:
        print("\n2. User already registered. Performing standard Login...")
        payload = {
            "username": username,
            "password": password
        }
        res = session.post(f"{BASE_URL}/api/auth/login", json=payload)
        print(res.status_code, res.json())
        login_data = res.json()
        
        if login_data.get("status") == "require_2fa":
            # We need the secret from SQLite for testing login, but since this is verification,
            # let's fetch settings. Wait, settings are protected. 
            # In a real setup we scan the QR code. For this automated test, let's register a new test user if we can,
            # but since we are single-tenant, let's verify if we can reset DB or just test session.
            # Wait, let's query the DB directly using sqlite3 to get the 2fa secret for admin!
            import sqlite3
            import os
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "bridge.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            secret = cursor.execute("SELECT twofa_secret FROM users WHERE username = ?", (username,)).fetchone()[0]
            conn.close()
            
            totp = pyotp.TOTP(secret)
            token = totp.now()
            print(f"Retrieved Secret from DB. Generated TOTP login token: {token}")
            
            payload = {
                "username": username,
                "token": token
            }
            res = session.post(f"{BASE_URL}/api/auth/login-2fa", json=payload)
            print(res.status_code, res.json())
            if res.status_code != 200:
                print("2FA login verification failed")
                return
    
    print("\n4. Querying protected logs endpoint...")
    res = session.get(f"{BASE_URL}/api/logs")
    print(f"Status Code: {res.status_code}")
    if res.status_code == 200:
        logs = res.json()
        print(f"Retrieved {len(logs)} logs from database successfully!")
        if len(logs) > 0:
            print("Latest Log details:")
            latest = logs[0]
            print(f"Customer: {latest.get('customer_name')}")
            print(f"Status: {latest.get('status')}")
            print(f"Trace details:\n{latest.get('details')}")
    else:
        print(f"Failed to query logs: {res.text}")

if __name__ == "__main__":
    run_test()
