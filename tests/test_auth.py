"""Authentication, profile and user-isolation tests."""
from conftest import register, setup_business


def test_registration_creates_user_and_business(client, app):
    from models import Business, User
    resp = register(client)
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="test@example.com").first()
        assert user is not None
        assert user.password_hash != "Password123"
        assert user.check_password("Password123")
        assert Business.query.filter_by(user_id=user.id).count() == 1
        # default accounting period 1 Jan - 31 Mar 2026
        business = Business.query.first()
        period = business.active_period()
        assert period.start_date.isoformat() == "2026-01-01"
        assert period.end_date.isoformat() == "2026-03-31"


def test_registration_rejects_duplicate_email(client, app):
    register(client)
    resp = register(client, name="Second", email="test@example.com")
    assert b"already exists" in resp.data


def test_registration_rejects_weak_password(client):
    resp = client.post("/register", data={"name": "A B", "email": "x@y.com",
                                          "password": "123", "confirm_password": "123"},
                       follow_redirects=True)
    assert b"at least 8 characters" in resp.data


def test_login_and_logout(client, app):
    register(client)
    client.get("/logout")
    resp = client.post("/login", data={"email": "test@example.com",
                                       "password": "wrong"}, follow_redirects=True)
    assert b"Invalid email or password" in resp.data
    resp = client.post("/login", data={"email": "test@example.com",
                                       "password": "Password123"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_dashboard_requires_login(client):
    resp = client.get("/dashboard", follow_redirects=True)
    assert b"Please sign in" in resp.data or b"Welcome back" in resp.data


def test_business_profile_update(client, app):
    from models import Business
    register(client)
    resp = setup_business(client, name="Al-Noor Traders")
    assert b"saved" in resp.data
    with app.app_context():
        business = Business.query.first()
        assert business.name == "Al-Noor Traders"
        assert business.currency == "PKR"
        assert business.fy_start.isoformat() == "2026-01-01"


def test_change_password(client, app):
    register(client)
    resp = client.post("/settings", data={"section": "password",
                                          "current_password": "Password123",
                                          "new_password": "NewPassword456",
                                          "confirm_password": "NewPassword456"},
                       follow_redirects=True)
    assert b"Password changed" in resp.data
    client.get("/logout")
    resp = client.post("/login", data={"email": "test@example.com",
                                       "password": "NewPassword456"},
                       follow_redirects=True)
    assert b"Dashboard" in resp.data


def test_user_data_isolation(client, app):
    """A second user must never see the first user's business or vouchers."""
    from models import Business, JournalEntry, User
    from conftest import load_accounts, post_entry

    # user A
    register(client, name="User A", email="a@example.com")
    setup_business(client, name="Business A")
    accounts = load_accounts(app)
    with app.app_context():
        user_a = User.query.filter_by(email="a@example.com").first()
        biz_a = Business.query.filter_by(user_id=user_a.id).first()
        post_entry(app, biz_a.id, user_a.id, "JV-0001",
                   [(accounts["1000"], "1000", "0", "x"),
                    (accounts["3000"], "0", "1000", "x")])
        entry_a_id = JournalEntry.query.first().id
        biz_a_id = biz_a.id

    client.get("/logout")
    register(client, name="User B", email="b@example.com")
    with app.app_context():
        user_b = User.query.filter_by(email="b@example.com").first()
        biz_b = Business.query.filter_by(user_id=user_b.id).first()
        assert biz_b.id != biz_a_id
        assert biz_b.name == ""
        # B has no vouchers at all
        assert JournalEntry.query.filter_by(business_id=biz_b.id).count() == 0
    resp = client.get(f"/transactions/{entry_a_id}")
    assert resp.status_code == 404
    resp = client.get("/transactions")
    assert b"JV-0001" not in resp.data
    resp = client.get("/journal")
    assert b"JV-0001" not in resp.data


def test_password_reset_flow(client, app):
    from models import User
    register(client)
    client.get("/logout")
    resp = client.post("/forgot-password", data={"email": "test@example.com"},
                       follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.first()
        token = user.reset_token
    assert token
    resp = client.post(f"/reset-password/{token}",
                       data={"password": "ResetPass123", "confirm_password": "ResetPass123"},
                       follow_redirects=True)
    assert b"Password updated" in resp.data
