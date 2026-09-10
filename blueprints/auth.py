import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
from utils import DEFAULT_TIMEZONE, _ensure_user_timezone_column, _valid_timezone_name

auth_bp = Blueprint("auth", __name__)

PASSWORD_RESET_MAX_AGE = 30 * 60
PASSWORD_RESET_SALT = "planet-password-reset"


def _password_reset_serializer():
    return URLSafeTimedSerializer(
        current_app.secret_key,
        salt=PASSWORD_RESET_SALT
    )


def _password_fingerprint(password_hash):
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()


def _send_password_reset_email(recipient, reset_url):
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    from_email = os.environ.get(
        "RESET_FROM_EMAIL",
        "Planet <noreply@myplanetplanner.app>"
    ).strip()

    if not api_key:
        current_app.logger.warning(
            "Password reset email skipped: RESEND_API_KEY is not configured."
        )
        return False

    email_html = f"""
        <div style="font-family:Arial,sans-serif;color:#24241f;line-height:1.6">
            <h1 style="font-family:Georgia,serif;color:#8f3c5b">
                Reset your Planet password
            </h1>
            <p>Use the button below to choose a new password.</p>
            <p>
                <a href="{reset_url}"
                   style="display:inline-block;padding:12px 18px;border-radius:10px;
                          background:#8f3c5b;color:#fff;text-decoration:none;
                          font-weight:bold">
                    Reset my password
                </a>
            </p>
            <p>This link expires in 30 minutes.</p>
            <p>If you did not request this, you can safely ignore this email.</p>
        </div>
    """

    payload = json.dumps({
        "from": from_email,
        "to": [recipient],
        "subject": "Reset your Planet password",
        "html": email_html
    }).encode("utf-8")

    email_request = Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Planet-App/1.0 (+https://myplanetplanner.app)"
        },
        method="POST"
    )

    try:
        with urlopen(email_request, timeout=10) as response:
            return 200 <= response.status < 300
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        current_app.logger.error(
            "Password reset email failed: HTTP %s - %s",
            error.code,
            error_body
        )
        return False
    except (URLError, TimeoutError) as error:
        current_app.logger.error("Password reset email failed: %s", error)
        return False


@auth_bp.route("/login", methods=["POST"])
def login():
    email = request.form["email"].strip().lower()
    password = request.form["password"]

    connection = get_db()
    _ensure_user_timezone_column(connection)

    user = connection.execute(
        "SELECT * FROM users WHERE lower(email) = ?",
        (email,)
    ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("index.html", error="Invalid email or password.")

    session["user_id"] = user["id"]
    session["first_name"] = user["first_name"]
    session["timezone"] = _valid_timezone_name(user["timezone"]) or DEFAULT_TIMEZONE

    return redirect(url_for("dashboard.dashboard"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if email:
            connection = get_db()
            user = connection.execute(
                "SELECT id, email, password_hash FROM users "
                "WHERE lower(email) = ?",
                (email,)
            ).fetchone()

            if user is not None:
                token = _password_reset_serializer().dumps({
                    "user_id": user["id"],
                    "password_fingerprint": _password_fingerprint(
                        user["password_hash"]
                    )
                })
                configured_base_url = os.environ.get(
                    "RESET_BASE_URL", ""
                ).strip().rstrip("/")
                if configured_base_url:
                    reset_url = (
                        f"{configured_base_url}"
                        f"{url_for('auth.reset_password', token=token)}"
                    )
                else:
                    reset_url = url_for(
                        "auth.reset_password",
                        token=token,
                        _external=True
                    )
                _send_password_reset_email(user["email"], reset_url)

        # Never reveal whether an email address belongs to an account.
        message = (
            "If an account exists for that email, a password reset "
            "link is on its way."
        )

    return render_template("forgot_password.html", message=message)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        token_data = _password_reset_serializer().loads(
            token,
            max_age=PASSWORD_RESET_MAX_AGE
        )
    except SignatureExpired:
        return render_template(
            "reset_password.html",
            token_valid=False,
            error="That reset link has expired. Request a new one."
        )
    except BadSignature:
        return render_template(
            "reset_password.html",
            token_valid=False,
            error="That reset link is invalid. Request a new one."
        )

    connection = get_db()
    user = connection.execute(
        "SELECT id, password_hash FROM users WHERE id = ?",
        (token_data.get("user_id"),)
    ).fetchone()

    token_is_current = (
        user is not None
        and token_data.get("password_fingerprint")
        == _password_fingerprint(user["password_hash"])
    )

    if not token_is_current:
        return render_template(
            "reset_password.html",
            token_valid=False,
            error="That reset link is no longer valid. Request a new one."
        )

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 8:
            return render_template(
                "reset_password.html",
                token_valid=True,
                error="Your password must be at least 8 characters."
            )

        if password != confirm_password:
            return render_template(
                "reset_password.html",
                token_valid=True,
                error="Passwords do not match."
            )

        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (
                generate_password_hash(password, method="pbkdf2:sha256"),
                user["id"]
            )
        )
        connection.commit()
        session.clear()

        return render_template(
            "index.html",
            success="Your password has been reset. You can log in now."
        )

    return render_template("reset_password.html", token_valid=True)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            return render_template("signup.html", error="Passwords do not match.")

        password_hash = generate_password_hash(password, method="pbkdf2:sha256")

        connection = get_db()
        _ensure_user_timezone_column(connection)

        # NOTE: fixed to match login's case-insensitive lookup (lower(email)).
        # The original compared "email" exactly here, which let two accounts
        # be created that only differ by letter case (e.g. Mel@x.com vs
        # mel@x.com) even though login treats them as the same address.
        existing_user = connection.execute(
            "SELECT id FROM users WHERE lower(email) = ?",
            (email.strip().lower(),)
        ).fetchone()

        if existing_user is not None:
            return render_template(
                "signup.html",
                error="An account with this email already exists."
            )

        cursor = connection.execute(
            """
            INSERT INTO users (first_name, last_name, email, password_hash)
            VALUES (?, ?, ?, ?)
            """,
            (first_name, last_name, email, password_hash)
        )

        new_user_id = cursor.lastrowid
        connection.commit()

        session["user_id"] = new_user_id
        session["first_name"] = first_name
        session["timezone"] = DEFAULT_TIMEZONE

        return redirect(url_for("dashboard.dashboard"))

    return render_template("signup.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("core.home"))