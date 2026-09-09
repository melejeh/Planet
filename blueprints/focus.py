import sqlite3
from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db
from utils import planet_now

focus_bp = Blueprint("focus", __name__)


@focus_bp.route("/focus", methods=["GET", "POST"])
def focus():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

    # Create the table for a new database.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS focus_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER,
            title TEXT NOT NULL DEFAULT 'Focus session',
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_minutes INTEGER NOT NULL DEFAULT 0,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'timer',
            notes TEXT,
            technique TEXT NOT NULL DEFAULT 'stopwatch',
            planned_minutes INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        )
        """
    )

    # Add newer columns to databases that already
    # had the focus_sessions table.
    focus_columns = {
        column["name"]
        for column in connection.execute(
            "PRAGMA table_info(focus_sessions)"
        ).fetchall()
    }

    if "technique" not in focus_columns:
        connection.execute(
            """
            ALTER TABLE focus_sessions
            ADD COLUMN technique TEXT
            NOT NULL DEFAULT 'stopwatch'
            """
        )

    if "planned_minutes" not in focus_columns:
        connection.execute(
            """
            ALTER TABLE focus_sessions
            ADD COLUMN planned_minutes INTEGER
            """
        )

    if "duration_seconds" not in focus_columns:
        connection.execute(
            """
            ALTER TABLE focus_sessions
            ADD COLUMN duration_seconds INTEGER
            NOT NULL DEFAULT 0
            """
        )

    connection.commit()

    # Save a manually logged study session.
    if request.method == "POST":
        title = request.form.get(
            "title",
            ""
        ).strip()

        course_id = (
            request.form.get("course_id")
            or None
        )

        session_date = request.form.get(
            "session_date",
            ""
        )

        start_time = (
            request.form.get("start_time")
            or "12:00"
        )

        notes = request.form.get(
            "notes",
            ""
        ).strip()

        try:
            hours = int(
                request.form.get("hours")
                or 0
            )

            minutes = int(
                request.form.get("minutes")
                or 0
            )

        except ValueError:
            hours = 0
            minutes = 0

        hours = max(0, hours)
        minutes = max(
            0,
            min(minutes, 59)
        )

        duration_minutes = (
            hours * 60
            + minutes
        )

        # Confirm that the selected course belongs
        # to the currently signed-in user.
        if course_id:
            valid_course = connection.execute(
                """
                SELECT courses.id
                FROM courses
                JOIN semesters
                    ON semesters.id = courses.semester_id
                WHERE courses.id = ?
                AND semesters.user_id = ?
                """,
                (
                    course_id,
                    session["user_id"]
                )
            ).fetchone()

            if valid_course is None:
                course_id = None

        if session_date and duration_minutes > 0:
            try:
                started_at = datetime.strptime(
                    f"{session_date} {start_time}",
                    "%Y-%m-%d %H:%M"
                )

            except ValueError:
                return redirect(url_for("focus.focus"))

            ended_at = started_at + timedelta(
                minutes=duration_minutes
            )

            connection.execute(
                """
                INSERT INTO focus_sessions (
                    user_id,
                    course_id,
                    title,
                    started_at,
                    ended_at,
                    duration_minutes,
                    duration_seconds,
                    source,
                    notes,
                    technique,
                    planned_minutes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    course_id,
                    title or "Focus session",
                    started_at.isoformat(
                        timespec="minutes"
                    ),
                    ended_at.isoformat(
                        timespec="minutes"
                    ),
                    duration_minutes,
                    duration_minutes * 60,
                    "manual",
                    notes,
                    "manual",
                    duration_minutes
                )
            )

            connection.commit()


        return redirect(url_for("focus.focus"))

    # Retrieve courses belonging to this user.
    courses = connection.execute(
        """
        SELECT
            courses.id,
            courses.code,
            courses.name
        FROM courses
        JOIN semesters
            ON semesters.id = courses.semester_id
        WHERE semesters.user_id = ?
        ORDER BY courses.name
        """,
        (session["user_id"],)
    ).fetchall()

    # Retrieve this user's study sessions.
    focus_session_rows = connection.execute(
        """
        SELECT
            focus_sessions.*,
            courses.code AS course_code,
            courses.name AS course_name
        FROM focus_sessions
        LEFT JOIN courses
            ON courses.id = focus_sessions.course_id
        WHERE focus_sessions.user_id = ?
        ORDER BY focus_sessions.started_at DESC
        """,
        (session["user_id"],)
    ).fetchall()


    technique_labels = {
        "stopwatch": "Stopwatch",
        "pomodoro": "Pomodoro",
        "long_pomodoro": "Long Pomodoro",
        "deep_work": "Deep Work",
        "custom": "Custom timer",
        "manual": "Manually logged"
    }

    focus_sessions = []

    for row in focus_session_rows:
        focus_session = dict(row)

        started_at = datetime.fromisoformat(
            row["started_at"]
        )

        ended_at = None

        if row["ended_at"]:
            ended_at = datetime.fromisoformat(
                row["ended_at"]
            )

        duration_minutes = int(
            row["duration_minutes"]
            or 0
        )

        duration_seconds = int(
            row["duration_seconds"]
            or 0
        )

        # Older rows predate duration_seconds, so derive
        # their exact duration from the stored minutes.
        if duration_seconds <= 0 and duration_minutes > 0:
            duration_seconds = duration_minutes * 60

        duration_hours = (
            duration_minutes // 60
        )

        remaining_minutes = (
            duration_minutes % 60
        )

        if duration_seconds < 60:
            duration_label = f"{duration_seconds}s"

        elif duration_hours and remaining_minutes:
            duration_label = (
                f"{duration_hours}h "
                f"{remaining_minutes}m"
            )

        elif duration_hours:
            duration_label = (
                f"{duration_hours}h"
            )

        else:
            duration_label = (
                f"{remaining_minutes}m"
            )

        technique = (
            row["technique"]
            or "stopwatch"
        )

        focus_session["technique_label"] = (
            technique_labels.get(
                technique,
                "Focus session"
            )
        )

        focus_session["date_label"] = (
            started_at.strftime(
                "%B %-d, %Y"
            )
        )

        focus_session["start_label"] = (
            started_at.strftime(
                "%-I:%M %p"
            )
        )

        focus_session["end_label"] = (
            ended_at.strftime("%-I:%M %p")
            if ended_at
            else "In progress"
        )

        focus_session["duration_label"] = (
            duration_label
        )

        focus_sessions.append(
            focus_session
        )

    now = planet_now()

    start_of_week = (
        now
        - timedelta(days=now.weekday())
    ).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    weekly_seconds = 0

    for focus_session in focus_sessions:
        session_started_at = (
            datetime.fromisoformat(
                focus_session["started_at"]
            )
        )

        if session_started_at >= start_of_week:
            stored_seconds = int(
                focus_session.get("duration_seconds")
                or 0
            )

            if stored_seconds <= 0:
                stored_seconds = int(
                    focus_session["duration_minutes"]
                    or 0
                ) * 60

            weekly_seconds += stored_seconds

    weekly_minutes = round(
        weekly_seconds / 60,
        1
    )

    weekly_hours = round(
        weekly_seconds / 3600,
        1
    )

    return render_template(
        "focus.html",
        name=session["first_name"],
        courses=courses,
        focus_sessions=focus_sessions,
        weekly_minutes=weekly_minutes,
        weekly_hours=weekly_hours,
        today_date=now.strftime("%Y-%m-%d"),
        current_time=now.strftime("%H:%M")
    )



@focus_bp.route(
    "/focus/<int:focus_session_id>/edit",
    methods=["GET", "POST"]
)
def edit_focus_session(focus_session_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

    focus_session = connection.execute(
        """
        SELECT *
        FROM focus_sessions
        WHERE id = ?
        AND user_id = ?
        """,
        (
            focus_session_id,
            session["user_id"]
        )
    ).fetchone()

    if focus_session is None:
        return redirect(url_for("focus.focus"))

    if request.method == "POST":
        title = request.form.get(
            "title",
            ""
        ).strip()

        course_id = (
            request.form.get("course_id")
            or None
        )

        session_date = request.form.get(
            "session_date",
            ""
        )

        start_time = request.form.get(
            "start_time",
            ""
        )

        notes = request.form.get(
            "notes",
            ""
        ).strip()

        try:
            hours = int(
                request.form.get("hours")
                or 0
            )

            minutes = int(
                request.form.get("minutes")
                or 0
            )

        except ValueError:
            hours = 0
            minutes = 0

        hours = max(0, hours)
        minutes = max(0, min(minutes, 59))

        duration_minutes = (
            hours * 60
            + minutes
        )

        if session_date and start_time and duration_minutes > 0:
            started_at = datetime.strptime(
                f"{session_date} {start_time}",
                "%Y-%m-%d %H:%M"
            )

            ended_at = started_at + timedelta(
                minutes=duration_minutes
            )

            connection.execute(
                """
                UPDATE focus_sessions
                SET
                    course_id = ?,
                    title = ?,
                    started_at = ?,
                    ended_at = ?,
                    duration_minutes = ?,
                    notes = ?
                WHERE id = ?
                AND user_id = ?
                """,
                (
                    course_id,
                    title or "Focus session",
                    started_at.isoformat(
                        timespec="minutes"
                    ),
                    ended_at.isoformat(
                        timespec="minutes"
                    ),
                    duration_minutes,
                    notes,
                    focus_session_id,
                    session["user_id"]
                )
            )

            connection.commit()


        return redirect(url_for("focus.focus"))

    courses = connection.execute(
        """
        SELECT
            courses.id,
            courses.code,
            courses.name
        FROM courses
        JOIN semesters
            ON semesters.id = courses.semester_id
        WHERE semesters.user_id = ?
        ORDER BY courses.name
        """,
        (session["user_id"],)
    ).fetchall()


    started_at = datetime.fromisoformat(
        focus_session["started_at"]
    )

    focus_session_data = dict(focus_session)

    focus_session_data["session_date"] = (
        started_at.strftime("%Y-%m-%d")
    )

    focus_session_data["start_time"] = (
        started_at.strftime("%H:%M")
    )

    focus_session_data["hours"] = (
        focus_session["duration_minutes"] // 60
    )

    focus_session_data["minutes"] = (
        focus_session["duration_minutes"] % 60
    )

    return render_template(
        "edit_focus_session.html",
        name=session["first_name"],
        focus_session=focus_session_data,
        courses=courses
    )



@focus_bp.route(
    "/focus/<int:focus_session_id>/delete",
    methods=["POST"]
)
def delete_focus_session(focus_session_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

    connection.execute(
        """
        DELETE FROM focus_sessions
        WHERE id = ?
        AND user_id = ?
        """,
        (
            focus_session_id,
            session["user_id"]
        )
    )

    connection.commit()

    return redirect(url_for("focus.focus"))



@focus_bp.route(
    "/focus/timer/save",
    methods=["POST"]
)
def save_focus_timer():
    if "user_id" not in session:
        return {
            "success": False,
            "message": "You are not signed in."
        }, 401

    timer_data = request.get_json(
        silent=True
    ) or {}
    technique = str(
        timer_data.get(
            "technique",
            "stopwatch"
        )
    ).strip().lower()

    allowed_techniques = {
        "stopwatch",
        "pomodoro",
        "long_pomodoro",
        "deep_work",
        "custom"
    }

    if technique not in allowed_techniques:
        technique = "stopwatch"

    try:
        planned_minutes = int(
            timer_data.get("planned_minutes")
            or 0
        )

    except (TypeError, ValueError):
        planned_minutes = 0

    planned_minutes = max(
        0,
        planned_minutes
    )

    title = str(
        timer_data.get("title", "")
    ).strip()

    course_id = (
        timer_data.get("course_id")
        or None
    )

    try:
        elapsed_seconds = int(
            timer_data.get("elapsed_seconds")
            or 0
        )

    except (TypeError, ValueError):
        elapsed_seconds = 0

    elapsed_seconds = max(
        0,
        elapsed_seconds
    )

    if elapsed_seconds < 1:
        return {
            "success": False,
            "message": "Start the timer before saving."
        }, 400

    # Record completed minutes without inflating a few
    # seconds into a full minute of study time.
    duration_minutes = elapsed_seconds // 60

    ended_at = planet_now()

    started_at = ended_at - timedelta(
        seconds=elapsed_seconds
    )

    connection = get_db()

    # Make sure the selected course belongs to
    # the currently signed-in user.
    if course_id:
        valid_course = connection.execute(
            """
            SELECT courses.id
            FROM courses
            JOIN semesters
                ON semesters.id = courses.semester_id
            WHERE courses.id = ?
            AND semesters.user_id = ?
            """,
            (
                course_id,
                session["user_id"]
            )
        ).fetchone()

        if valid_course is None:
            course_id = None

    # This insert must run whether a course was selected
    # or the session was saved as personal study time.
    try:
        connection.execute(
            """
            INSERT INTO focus_sessions (
                user_id,
                course_id,
                title,
                started_at,
                ended_at,
                duration_minutes,
                duration_seconds,
                source,
                notes,
                technique,
                planned_minutes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                course_id,
                title or "Focus session",
                started_at.isoformat(
                    timespec="seconds"
                ),
                ended_at.isoformat(
                    timespec="seconds"
                ),
                duration_minutes,
                elapsed_seconds,
                "timer",
                "",
                technique,
                planned_minutes or None
            )
        )

        connection.commit()

    except sqlite3.Error:
        connection.rollback()

        return {
            "success": False,
            "message": "Planet could not save this focus session."
        }, 500


    return {
        "success": True,
        "message": "Focus session saved.",
        "duration_minutes": duration_minutes,
        "duration_seconds": elapsed_seconds
    }
