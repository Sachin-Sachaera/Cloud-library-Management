import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("LIBRARY_DB", "library-test.db")

from app import app, init_db, get_db, refresh_overdue_status


class BorrowRequestFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="library-test-", dir=".")
        self.db_path = os.path.join(self.temp_dir, "library.db")
        os.environ["LIBRARY_DB"] = self.db_path
        app.config.update(TESTING=True)
        app.secret_key = "test-secret"
        with app.app_context():
            init_db()
        self.client = app.test_client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("LIBRARY_DB", None)

    def test_overdue_fine_is_10_rs_per_day(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO transactions (book_id, member_id, issue_date, due_date, status) VALUES (?, ?, ?, ?, 'issued')",
                (1, 1, "2024-01-01", (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")),
            )
            db.commit()
            refresh_overdue_status(db)
            txn = db.execute("SELECT status, fine FROM transactions WHERE id = 1").fetchone()
            self.assertEqual(txn["status"], "overdue")
            self.assertEqual(txn["fine"], 10)

    def test_member_signup_creates_account_and_profile(self):
        signup_response = self.client.post(
            "/signup",
            data={
                "name": "New Member",
                "email": "newmember@example.com",
                "phone": "9999999999",
                "username": "newmember",
                "password": "newpass123",
            },
            follow_redirects=False,
        )
        self.assertEqual(signup_response.status_code, 302)

        with app.app_context():
            db = get_db()
            member = db.execute("SELECT id FROM members WHERE email = ?", ("newmember@example.com",)).fetchone()
            user = db.execute("SELECT id, role FROM users WHERE username = ?", ("newmember",)).fetchone()
            self.assertIsNotNone(member)
            self.assertIsNotNone(user)
            self.assertEqual(user["role"], "member")

    def test_member_request_and_admin_approval_flow(self):
        login = self.client.post(
            "/login",
            data={"username": "mem001", "password": "member123"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        request_response = self.client.post(
            "/books/1/request",
            follow_redirects=False,
        )
        self.assertEqual(request_response.status_code, 302)

        with app.app_context():
            db = get_db()
            request_row = db.execute(
                "SELECT status FROM borrow_requests WHERE book_id = 1 AND member_id = 1"
            ).fetchone()
            self.assertIsNotNone(request_row)
            self.assertEqual(request_row["status"], "pending")

        logout = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(logout.status_code, 302)

        admin_login = self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=False,
        )
        self.assertEqual(admin_login.status_code, 302)

        approval_response = self.client.post(
            "/borrow-requests/1/approve",
            data={"due_date": "2030-12-31"},
            follow_redirects=False,
        )
        self.assertEqual(approval_response.status_code, 302)

        with app.app_context():
            db = get_db()
            request_row = db.execute(
                "SELECT status, due_date FROM borrow_requests WHERE id = 1"
            ).fetchone()
            transaction_row = db.execute(
                "SELECT status, due_date FROM transactions WHERE book_id = 1 AND member_id = 1"
            ).fetchone()
            self.assertEqual(request_row["status"], "approved")
            self.assertEqual(transaction_row["status"], "issued")
            self.assertEqual(transaction_row["due_date"], "2030-12-31")


if __name__ == "__main__":
    unittest.main()
