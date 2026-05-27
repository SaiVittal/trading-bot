import os
import sys
import unittest
import uuid
import httpx
from sqlalchemy import delete

# Add backend app directory to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../apps/backend")))

from app.main import app
from app.models.models import User
from app.core.database import async_session, init_db

class TestAuthenticationEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Set up unique test credentials and initialize database tables."""
        await init_db()
        self.suffix = uuid.uuid4().hex[:8]
        self.username = f"quant_test_{self.suffix}"
        self.email = f"test_{self.suffix}@quantplatform.com"
        self.password = "SuperSecurePass123!"

    async def asyncTearDown(self):
        """Clean up the generated test user from the database to maintain hygiene."""
        async with async_session() as session:
            try:
                stmt = delete(User).where(User.username == self.username)
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                print(f"[TEST WARNING] Failed to clean up test user {self.username}: {e}")
                await session.rollback()

    async def test_complete_authentication_flow(self):
        """Integration test verifying user registration, duplicate checks, login, and profile fetching."""
        
        # We will use httpx.AsyncClient with app to test ASGI endpoints directly
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            
            # --- 1. TEST REGISTRATION ---
            print(f"\n[TEST] 1. Registering unique user: {self.username}")
            reg_payload = {
                "username": self.username,
                "email": self.email,
                "password": self.password
            }
            
            response = await client.post("/api/v1/auth/register", json=reg_payload)
            self.assertEqual(response.status_code, 201, f"Registration failed: {response.text}")
            
            data = response.json()
            self.assertEqual(data["username"], self.username)
            self.assertEqual(data["email"], self.email)
            self.assertEqual(data["role"], "trader")
            self.assertTrue(data["is_active"])
            self.assertIn("id", data)
            print("[TEST SUCCESS] Registration succeeded.")

            # --- 2. TEST DUPLICATE USERNAME VALIDATION ---
            print(f"[TEST] 2. Verifying duplicate username validation")
            dup_username_payload = {
                "username": self.username,
                "email": f"different_email_{self.suffix}@quantplatform.com",
                "password": self.password
            }
            response = await client.post("/api/v1/auth/register", json=dup_username_payload)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "Username already registered")
            print("[TEST SUCCESS] Duplicate username check passed.")

            # --- 3. TEST DUPLICATE EMAIL VALIDATION ---
            print(f"[TEST] 3. Verifying duplicate email validation")
            dup_email_payload = {
                "username": f"different_user_{self.suffix}",
                "email": self.email,
                "password": self.password
            }
            response = await client.post("/api/v1/auth/register", json=dup_email_payload)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "Email already registered")
            print("[TEST SUCCESS] Duplicate email check passed.")

            # --- 4. TEST LOGIN (SUCCESSFUL) ---
            print(f"[TEST] 4. Logging in with registered credentials")
            login_data = {
                "username": self.username,
                "password": self.password
            }
            response = await client.post(
                "/api/v1/auth/login",
                data=login_data,  # form data
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            self.assertEqual(response.status_code, 200, f"Login failed: {response.text}")
            
            auth_data = response.json()
            self.assertIn("access_token", auth_data)
            self.assertEqual(auth_data["token_type"], "bearer")
            token = auth_data["access_token"]
            print("[TEST SUCCESS] Login succeeded. JWT token issued.")

            # --- 5. TEST LOGIN (FAILED CREDENTIALS) ---
            print(f"[TEST] 5. Verifying login failure with invalid password")
            invalid_login_data = {
                "username": self.username,
                "password": "WrongPassword123!"
            }
            response = await client.post(
                "/api/v1/auth/login",
                data=invalid_login_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "Incorrect username or password")
            print("[TEST SUCCESS] Login failure validation passed.")

            # --- 6. TEST SECURE PROFILE ENDPOINT (/me) ---
            print(f"[TEST] 6. Querying /me secure endpoint with valid token")
            response = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            self.assertEqual(response.status_code, 200, f"/me query failed: {response.text}")
            
            profile_data = response.json()
            self.assertEqual(profile_data["username"], self.username)
            self.assertEqual(profile_data["email"], self.email)
            print("[TEST SUCCESS] Secure /me endpoint validated.")

            # --- 7. TEST SECURE PROFILE ENDPOINT (UNAUTHORIZED) ---
            print(f"[TEST] 7. Verifying /me secure endpoint fails without authorization")
            response = await client.get("/api/v1/auth/me")
            self.assertEqual(response.status_code, 401)
            print("[TEST SUCCESS] Secure /me endpoint unauthorized validation passed.")

if __name__ == "__main__":
    unittest.main()
