import sqlite3
from urllib.parse import urlparse

from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
from utils import _ensure_user_timezone_column, _valid_timezone_name

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings/timezone", methods=["POST"])
def update_timezone():
    if "user_id" not in session:
        return {"success": False}, 401

    payload = request.get_json(silent=True) or {}
    timezone_name = _valid_timezone_name(payload.get("timezone"))
    if timezone_name is None:
        return {"success": False, "message": "Invalid timezone."}, 400

    changed = session.get("timezone") != timezone_name
    if not changed:
        return {"success": True, "changed": False}

    connection = get_db()
    _ensure_user_timezone_column(connection)
    connection.execute(
        "UPDATE users SET timezone = ? WHERE id = ?",
        (timezone_name, session["user_id"])
    )
    connection.commit()

    session["timezone"] = timezone_name
    return {"success": True, "changed": changed}



def planet_appearance():
    if "user_id" not in session:
        return {}

    connection = get_db()
    try:
        preferences = connection.execute(
            "SELECT theme, compact_dashboard FROM user_settings WHERE user_id = ?",
            (session["user_id"],)
        ).fetchone()
    except (sqlite3.OperationalError, IndexError):
        preferences = None

    return {
        "planet_theme": preferences["theme"] if preferences else "editorial",
        "planet_compact": bool(preferences["compact_dashboard"]) if preferences else False
    }



@settings_bp.route("/settings", methods=["GET", "POST"])
def settings():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    user_id = session["user_id"]
    connection = get_db()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            reschedule_missed INTEGER NOT NULL DEFAULT 1,
            grade_priority INTEGER NOT NULL DEFAULT 1,
            allow_weekends INTEGER NOT NULL DEFAULT 1,
            theme TEXT NOT NULL DEFAULT 'editorial',
            compact_dashboard INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    # CREATE TABLE IF NOT EXISTS does not update an older table. Add any
    # Settings columns that are missing from an existing Planet database.
    user_settings_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(user_settings)").fetchall()
    }
    user_settings_migrations = {
        "reschedule_missed": "INTEGER NOT NULL DEFAULT 1",
        "grade_priority": "INTEGER NOT NULL DEFAULT 1",
        "allow_weekends": "INTEGER NOT NULL DEFAULT 1",
        "theme": "TEXT NOT NULL DEFAULT 'editorial'",
        "compact_dashboard": "INTEGER NOT NULL DEFAULT 0"
    }
    for column_name, column_definition in user_settings_migrations.items():
        if column_name not in user_settings_columns:
            connection.execute(
                f"ALTER TABLE user_settings ADD COLUMN {column_name} {column_definition}"
            )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'other',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS study_plan_settings (
            user_id INTEGER PRIMARY KEY,
            earliest_time TEXT NOT NULL DEFAULT '09:00',
            latest_time TEXT NOT NULL DEFAULT '21:00',
            weekly_target_hours REAL NOT NULL DEFAULT 8,
            preferred_session_minutes INTEGER NOT NULL DEFAULT 45,
            break_minutes INTEGER NOT NULL DEFAULT 10,
            include_weekends INTEGER NOT NULL DEFAULT 1,
            available_days TEXT NOT NULL DEFAULT 'monday,tuesday,wednesday,thursday,friday',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    study_settings_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(study_plan_settings)").fetchall()
    }
    study_settings_migrations = {
        "earliest_time": "TEXT NOT NULL DEFAULT '09:00'",
        "latest_time": "TEXT NOT NULL DEFAULT '21:00'",
        "weekly_target_hours": "REAL NOT NULL DEFAULT 8",
        "preferred_session_minutes": "INTEGER NOT NULL DEFAULT 45",
        "break_minutes": "INTEGER NOT NULL DEFAULT 10",
        "include_weekends": "INTEGER NOT NULL DEFAULT 1",
        "available_days": (
            "TEXT NOT NULL DEFAULT "
            "'monday,tuesday,wednesday,thursday,friday'"
        )
    }
    for column_name, column_definition in study_settings_migrations.items():
        if column_name not in study_settings_columns:
            connection.execute(
                f"ALTER TABLE study_plan_settings ADD COLUMN {column_name} {column_definition}"
            )
    connection.commit()

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        earliest_time = request.form.get("earliest_time", "09:00")
        latest_time = request.form.get("latest_time", "21:00")
        weekly_target = request.form.get("weekly_target_hours", type=float)
        session_minutes = request.form.get("preferred_session_minutes", type=int)
        break_minutes = request.form.get("break_minutes", type=int)
        theme = request.form.get("theme", "editorial")
        available_days = request.form.getlist("available_days")

        if theme not in {"editorial", "rose", "sage", "espresso", "midnight"}:
            theme = "editorial"

        if (
            not first_name or not last_name or not email
            or not earliest_time or not latest_time
            or earliest_time >= latest_time
            or weekly_target is None or weekly_target <= 0
            or session_minutes is None or session_minutes <= 0
            or break_minutes is None or break_minutes < 0
            or not available_days
        ):
            return redirect(url_for("settings.settings", error="Please complete every required setting."))

        duplicate_email = connection.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?",
            (email, user_id)
        ).fetchone()
        if duplicate_email:
            return redirect(url_for("settings.settings", error="That email is already connected to another account."))

        allow_weekends = 1 if request.form.get("allow_weekends") else 0
        connection.execute(
            "UPDATE users SET first_name = ?, last_name = ?, email = ? WHERE id = ?",
            (first_name, last_name, email, user_id)
        )
        connection.execute(
            """
            INSERT INTO user_settings (
                user_id, reschedule_missed, grade_priority,
                allow_weekends, theme, compact_dashboard
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                reschedule_missed = excluded.reschedule_missed,
                grade_priority = excluded.grade_priority,
                allow_weekends = excluded.allow_weekends,
                theme = excluded.theme,
                compact_dashboard = excluded.compact_dashboard
            """,
            (
                user_id,
                1 if request.form.get("reschedule_missed") else 0,
                1 if request.form.get("grade_priority") else 0,
                allow_weekends,
                theme,
                1 if request.form.get("compact_dashboard") else 0
            )
        )
        connection.execute(
            """
            INSERT INTO study_plan_settings (
                user_id, earliest_time, latest_time, weekly_target_hours,
                preferred_session_minutes, break_minutes,
                include_weekends, available_days
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                earliest_time = excluded.earliest_time,
                latest_time = excluded.latest_time,
                weekly_target_hours = excluded.weekly_target_hours,
                preferred_session_minutes = excluded.preferred_session_minutes,
                break_minutes = excluded.break_minutes,
                include_weekends = excluded.include_weekends,
                available_days = excluded.available_days
            """,
            (
                user_id, earliest_time, latest_time, weekly_target,
                session_minutes, break_minutes, allow_weekends,
                ",".join(available_days)
            )
        )
        connection.commit()
        session["first_name"] = first_name
        return redirect(url_for("settings.settings", saved="Settings saved."))

    user = connection.execute(
        "SELECT first_name, last_name, email FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    preferences = connection.execute(
        "SELECT * FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    study_preferences = connection.execute(
        "SELECT * FROM study_plan_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    quick_links = connection.execute(
        """
        SELECT * FROM quick_links
        WHERE user_id = ?
        ORDER BY name COLLATE NOCASE
        """,
        (user_id,)
    ).fetchall()

    if preferences is None:
        preferences = {
            "reschedule_missed": 1,
            "grade_priority": 1,
            "allow_weekends": 1,
            "theme": "editorial",
            "compact_dashboard": 0
        }
    if study_preferences is None:
        study_preferences = {
            "earliest_time": "09:00",
            "latest_time": "21:00",
            "weekly_target_hours": 8,
            "preferred_session_minutes": 45,
            "break_minutes": 10,
            "available_days": "monday,tuesday,wednesday,thursday,friday"
        }

    return render_template(
        "settings.html",
        name=session["first_name"],
        user=user,
        preferences=preferences,
        study_preferences=study_preferences,
        selected_days=study_preferences["available_days"].split(","),
        quick_links=quick_links,
        saved=request.args.get("saved"),
        error=request.args.get("error"),
        security_saved=request.args.get("security_saved"),
        security_error=request.args.get("security_error")
    )



@settings_bp.route("/settings/change-password", methods=["POST"])
def change_password():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    def security_redirect(message, is_error=False):
        key = "security_error" if is_error else "security_saved"
        return redirect(url_for("settings.settings", **{key: message}) + "#security")

    if not current_password or not new_password or not confirm_password:
        return security_redirect("Complete all three password fields.", True)

    if new_password != confirm_password:
        return security_redirect("The new passwords do not match.", True)

    if (
        len(new_password) < 8
        or len(new_password) > 128
        or not any(character.isalpha() for character in new_password)
        or not any(character.isdigit() for character in new_password)
    ):
        return security_redirect(
            "Use 8–128 characters with at least one letter and one number.",
            True
        )

    connection = get_db()
    user = connection.execute(
        "SELECT password_hash FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    if user is None or not check_password_hash(
        user["password_hash"], current_password
    ):
        return security_redirect("Your current password is incorrect.", True)

    if check_password_hash(user["password_hash"], new_password):
        return security_redirect(
            "Choose a new password that is different from your current one.",
            True
        )

    new_password_hash = generate_password_hash(
        new_password,
        method="pbkdf2:sha256"
    )
    connection.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (new_password_hash, session["user_id"])
    )
    connection.commit()

    return security_redirect("Password changed successfully.")



@settings_bp.route("/settings/quick-links/add", methods=["POST"])
def add_quick_link():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    name = request.form.get("name", "").strip()
    link_url = request.form.get("url", "").strip()
    category = request.form.get("category", "other").strip()
    allowed_categories = {"academics", "learning", "email", "library", "other"}

    if category not in allowed_categories:
        category = "other"

    parsed_url = urlparse(link_url)
    if not name or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return redirect(url_for(
            "settings.settings",
            error="Add a link name and a complete website address beginning with https://."
        ))

    connection = get_db()
    connection.execute(
        """
        INSERT INTO quick_links (user_id, name, url, category)
        VALUES (?, ?, ?, ?)
        """,
        (session["user_id"], name, link_url, category)
    )
    connection.commit()
    return redirect(url_for("settings.settings", saved="Quick link added.") + "#quick-links")



@settings_bp.route("/settings/quick-links/<int:link_id>/delete", methods=["POST"])
def delete_quick_link(link_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    connection.execute(
        "DELETE FROM quick_links WHERE id = ? AND user_id = ?",
        (link_id, session["user_id"])
    )
    connection.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"success": True}

    return redirect(url_for("settings.settings", saved="Quick link removed.") + "#quick-links")


@settings_bp.route("/settings/delete-account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    user_id = session["user_id"]
    password = request.form.get("password", "")

    connection = get_db()
    user = connection.execute(
        "SELECT password_hash FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        return redirect(url_for(
            "settings.settings",
            error="Incorrect password. Your account was not deleted."
        ) + "#danger-zone")

    # Delete children before parents so nothing is left orphaned.
    connection.execute(
        """
        DELETE FROM assessments
        WHERE course_id IN (
            SELECT courses.id FROM courses
            JOIN semesters ON courses.semester_id = semesters.id
            WHERE semesters.user_id = ?
        )
        """,
        (user_id,)
    )
    connection.execute(
        """
        DELETE FROM courses
        WHERE semester_id IN (
            SELECT id FROM semesters WHERE user_id = ?
        )
        """,
        (user_id,)
    )
    connection.execute("DELETE FROM semesters WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM goal_progress_logs WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM goals WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM gratitude_entries WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM quick_links WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM study_blocks WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM study_plan_settings WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM focus_sessions WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    connection.commit()

    session.clear()
    return render_template(
        "index.html",
        success="Your Planet account and all its data have been deleted."
    )