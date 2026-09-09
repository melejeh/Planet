from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db
from utils import planet_now

goals_bp = Blueprint("goals", __name__)


def create_goals_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            goal_type TEXT NOT NULL DEFAULT 'manual',
            target_value REAL NOT NULL,
            current_value REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'units',
            period TEXT NOT NULL DEFAULT 'custom',
            deadline TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            course_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        )
        """
    )

    goal_columns = {
        column["name"]
        for column in connection.execute(
            "PRAGMA table_info(goals)"
        ).fetchall()
    }

    if "course_id" not in goal_columns:
        connection.execute(
            "ALTER TABLE goals ADD COLUMN course_id INTEGER"
        )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_progress_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            period_start TEXT NOT NULL,
            current_value REAL NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(goal_id, period_start),
            FOREIGN KEY (goal_id) REFERENCES goals(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    task_tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    if "tasks" in task_tables:
        task_columns = {
            column["name"]
            for column in connection.execute(
                "PRAGMA table_info(tasks)"
            ).fetchall()
        }

        if "completed_at" not in task_columns:
            connection.execute(
                "ALTER TABLE tasks ADD COLUMN completed_at TEXT"
            )
            connection.execute(
                """
                UPDATE tasks
                SET completed_at = CURRENT_TIMESTAMP
                WHERE completed = 1 AND completed_at IS NULL
                """
            )

    connection.commit()



def goal_period_bounds(connection, goal, user_id, now=None):
    now = now or planet_now()
    period = goal["period"] or "custom"

    if period == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )
        return start, start + timedelta(days=7)

    if period == "semester":
        semester = None

        if goal["course_id"]:
            semester = connection.execute(
                """
                SELECT semesters.start_date, semesters.end_date
                FROM courses
                JOIN semesters ON semesters.id = courses.semester_id
                WHERE courses.id = ? AND semesters.user_id = ?
                """,
                (goal["course_id"], user_id)
            ).fetchone()

        if semester is None and session.get("active_semester_id"):
            semester = connection.execute(
                """
                SELECT start_date, end_date
                FROM semesters
                WHERE id = ? AND user_id = ?
                """,
                (session["active_semester_id"], user_id)
            ).fetchone()

        if semester:
            start = datetime.strptime(
                semester["start_date"], "%Y-%m-%d"
            )
            end = datetime.strptime(
                semester["end_date"], "%Y-%m-%d"
            ) + timedelta(days=1)
            return start, end

    try:
        start = datetime.fromisoformat(goal["created_at"])
    except (TypeError, ValueError):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    end = None
    if goal["deadline"]:
        try:
            end = datetime.strptime(
                goal["deadline"], "%Y-%m-%d"
            ) + timedelta(days=1)
        except ValueError:
            end = None

    return start, end



def build_goal_list(connection, user_id):
    create_goals_table(connection)
    now = planet_now()

    goal_rows = connection.execute(
        """
        SELECT goals.*, courses.code AS course_code,
               courses.name AS course_name
        FROM goals
        LEFT JOIN courses ON courses.id = goals.course_id
        WHERE goals.user_id = ?
        ORDER BY goals.created_at DESC
        """,
        (user_id,)
    ).fetchall()

    table_names = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    goal_list = []

    for row in goal_rows:
        goal = dict(row)
        start, end = goal_period_bounds(
            connection, row, user_id, now
        )
        period_start = start.date().isoformat()

        progress_log = connection.execute(
            """
            SELECT current_value, completed
            FROM goal_progress_logs
            WHERE goal_id = ? AND user_id = ? AND period_start = ?
            """,
            (goal["id"], user_id, period_start)
        ).fetchone()

        if progress_log is None and (
            float(goal["current_value"] or 0) > 0
            or bool(goal["completed"])
        ):
            connection.execute(
                """
                INSERT INTO goal_progress_logs (
                    goal_id, user_id, period_start,
                    current_value, completed
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    goal["id"],
                    user_id,
                    period_start,
                    float(goal["current_value"] or 0),
                    int(bool(goal["completed"]))
                )
            )
            progress_log = {
                "current_value": float(goal["current_value"] or 0),
                "completed": int(bool(goal["completed"]))
            }
            if goal["period"] in {"weekly", "semester"}:
                connection.execute(
                    """
                    UPDATE goals
                    SET current_value = 0, completed = 0
                    WHERE id = ? AND user_id = ?
                    """,
                    (goal["id"], user_id)
                )

        manual_complete = bool(
            progress_log["completed"] if progress_log else 0
        )

        if goal["goal_type"] == "study_hours":
            parameters = [
                user_id,
                start.isoformat(timespec="minutes")
            ]
            end_filter = ""
            course_filter = ""

            if end is not None:
                end_filter = " AND started_at < ?"
                parameters.append(end.isoformat(timespec="minutes"))

            if goal["course_id"]:
                course_filter = " AND course_id = ?"
                parameters.append(goal["course_id"])

            current_value = 0
            if "focus_sessions" in table_names:
                current_value = connection.execute(
                    f"""
                    SELECT COALESCE(SUM(duration_minutes), 0)
                    FROM focus_sessions
                    WHERE user_id = ? AND started_at >= ?
                    {end_filter}{course_filter}
                    """,
                    tuple(parameters)
                ).fetchone()[0] / 60

            if goal["course_name"]:
                goal["source_label"] = (
                    f"Updated from {goal['course_name']} Focus sessions"
                )
            else:
                goal["source_label"] = "Updated from all Focus sessions"

        elif goal["goal_type"] == "completed_tasks":
            parameters = [user_id, start.isoformat(timespec="minutes")]
            end_filter = ""
            if end is not None:
                end_filter = " AND completed_at < ?"
                parameters.append(end.isoformat(timespec="minutes"))

            current_value = 0
            if "tasks" in table_names:
                current_value = connection.execute(
                    f"""
                    SELECT COUNT(*) FROM tasks
                    WHERE user_id = ? AND completed = 1
                      AND completed_at >= ?{end_filter}
                    """,
                    tuple(parameters)
                ).fetchone()[0]
            goal["source_label"] = "Updated from completed to-dos"

        else:
            current_value = float(
                progress_log["current_value"] if progress_log else 0
            )
            goal["source_label"] = "Progress updated by you"

        target_value = float(goal["target_value"] or 0)
        progress = (
            current_value / target_value * 100
            if target_value > 0 else 0
        )

        goal["current_display"] = round(current_value, 1)
        goal["target_display"] = round(target_value, 1)
        goal["progress_percent"] = min(100, round(progress, 1))
        goal["is_complete"] = bool(
            manual_complete or progress >= 100
        )
        goal["period_start"] = period_start
        goal["period_label"] = {
            "weekly": "This week",
            "semester": "This semester",
            "custom": "Overall"
        }.get(goal["period"], goal["period"].title())

        if goal["deadline"]:
            try:
                goal["deadline_label"] = datetime.strptime(
                    goal["deadline"], "%Y-%m-%d"
                ).strftime("%B %-d, %Y")
            except ValueError:
                goal["deadline_label"] = goal["deadline"]
        else:
            goal["deadline_label"] = None

        goal_list.append(goal)

    connection.commit()
    return goal_list



@goals_bp.route("/goals", methods=["GET", "POST"])
def goals():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    create_goals_table(connection)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        goal_type = request.form.get("goal_type", "manual")
        period = request.form.get("period", "custom")
        deadline = request.form.get("deadline") or None
        course_id = request.form.get("course_id") or None

        if goal_type not in {
            "study_hours",
            "completed_tasks",
            "manual"
        }:
            goal_type = "manual"

        if period not in {"weekly", "semester", "custom"}:
            period = "custom"

        try:
            target_value = float(
                request.form.get("target_value") or 0
            )
            current_value = float(
                request.form.get("current_value") or 0
            )
        except ValueError:
            target_value = 0
            current_value = 0

        target_value = max(0, target_value)
        current_value = max(0, current_value)

        if goal_type == "study_hours":
            unit = "hours"
            current_value = 0
            if course_id:
                owned_course = connection.execute(
                    """
                    SELECT courses.id FROM courses
                    JOIN semesters ON semesters.id = courses.semester_id
                    WHERE courses.id = ? AND semesters.user_id = ?
                    """,
                    (course_id, session["user_id"])
                ).fetchone()
                if owned_course is None:
                    course_id = None
        elif goal_type == "completed_tasks":
            unit = "tasks"
            current_value = 0
            course_id = None
        else:
            course_id = None
            unit = (
                request.form.get("unit", "units").strip()
                or "units"
            )[:30]

        if title and target_value > 0:
            connection.execute(
                """
                INSERT INTO goals (
                    user_id,
                    title,
                    goal_type,
                    target_value,
                    current_value,
                    unit,
                    period,
                    deadline,
                    course_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    title,
                    goal_type,
                    target_value,
                    current_value,
                    unit,
                    period,
                    deadline,
                    course_id
                )
            )
            connection.commit()

        return redirect(url_for("goals.goals"))

    goal_list = build_goal_list(connection, session["user_id"])
    courses = connection.execute(
        """
        SELECT courses.id, courses.code, courses.name
        FROM courses
        JOIN semesters ON semesters.id = courses.semester_id
        WHERE semesters.user_id = ?
        ORDER BY courses.name
        """,
        (session["user_id"],)
    ).fetchall()

    completed_count = sum(
        1 for goal in goal_list if goal["is_complete"]
    )
    automatic_count = sum(
        1 for goal in goal_list
        if goal["goal_type"] != "manual"
    )

    return render_template(
        "goals.html",
        name=session["first_name"],
        goals=goal_list,
        courses=courses,
        completed_count=completed_count,
        automatic_count=automatic_count
    )



@goals_bp.route("/goals/<int:goal_id>/progress", methods=["POST"])
def update_goal_progress(goal_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    try:
        current_value = max(
            0,
            float(request.form.get("current_value") or 0)
        )
    except ValueError:
        current_value = 0

    connection = get_db()
    create_goals_table(connection)
    goal = connection.execute(
        "SELECT * FROM goals WHERE id = ? AND user_id = ?",
        (goal_id, session["user_id"])
    ).fetchone()
    if goal and goal["goal_type"] == "manual":
        period_start = goal_period_bounds(
            connection, goal, session["user_id"]
        )[0].date().isoformat()
        connection.execute(
            """
            INSERT INTO goal_progress_logs (
                goal_id, user_id, period_start, current_value, completed
            ) VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(goal_id, period_start) DO UPDATE SET
                current_value = excluded.current_value,
                completed = 0,
                updated_at = CURRENT_TIMESTAMP
            """,
            (goal_id, session["user_id"], period_start, current_value)
        )
    connection.execute(
        """
        UPDATE goals
        SET current_value = ?, completed = 0
        WHERE id = ?
          AND user_id = ?
          AND goal_type = 'manual'
        """,
        (current_value, goal_id, session["user_id"])
    )
    connection.commit()
    return redirect(url_for("goals.goals", _anchor=f"goal-{goal_id}"))



@goals_bp.route("/goals/<int:goal_id>/edit", methods=["POST"])
def edit_goal(goal_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    title = request.form.get("title", "").strip()
    period = request.form.get("period", "custom")
    if period not in {"weekly", "semester", "custom"}:
        period = "custom"
    deadline = request.form.get("deadline") or None
    course_id = request.form.get("course_id") or None
    unit = (
        request.form.get("unit", "units").strip()
        or "units"
    )[:30]

    try:
        target_value = max(
            0,
            float(request.form.get("target_value") or 0)
        )
    except ValueError:
        target_value = 0

    connection = get_db()
    create_goals_table(connection)
    goal = connection.execute(
        "SELECT * FROM goals WHERE id = ? AND user_id = ?",
        (goal_id, session["user_id"])
    ).fetchone()

    if goal and title and target_value > 0:
        if goal["goal_type"] == "study_hours":
            unit = "hours"
            if course_id:
                owned_course = connection.execute(
                    """
                    SELECT courses.id FROM courses
                    JOIN semesters ON semesters.id = courses.semester_id
                    WHERE courses.id = ? AND semesters.user_id = ?
                    """,
                    (course_id, session["user_id"])
                ).fetchone()
                if owned_course is None:
                    course_id = None
        elif goal["goal_type"] == "completed_tasks":
            unit = "tasks"
            course_id = None
        else:
            course_id = None

        connection.execute(
            """
            UPDATE goals
            SET title = ?, target_value = ?, unit = ?,
                period = ?, deadline = ?, course_id = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                title,
                target_value,
                unit,
                period,
                deadline,
                course_id,
                goal_id,
                session["user_id"]
            )
        )
        connection.commit()

    return redirect(url_for("goals.goals", _anchor=f"goal-{goal_id}"))



@goals_bp.route("/goals/<int:goal_id>/toggle", methods=["POST"])
def toggle_goal(goal_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    create_goals_table(connection)
    goal = connection.execute(
        "SELECT * FROM goals WHERE id = ? AND user_id = ?",
        (goal_id, session["user_id"])
    ).fetchone()
    if goal:
        period_start = goal_period_bounds(
            connection, goal, session["user_id"]
        )[0].date().isoformat()
        current = connection.execute(
            """
            SELECT completed FROM goal_progress_logs
            WHERE goal_id = ? AND user_id = ? AND period_start = ?
            """,
            (goal_id, session["user_id"], period_start)
        ).fetchone()
        completed = 0 if current and current["completed"] else 1
        connection.execute(
            """
            INSERT INTO goal_progress_logs (
                goal_id, user_id, period_start, current_value, completed
            ) VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(goal_id, period_start) DO UPDATE SET
                completed = excluded.completed,
                updated_at = CURRENT_TIMESTAMP
            """,
            (goal_id, session["user_id"], period_start, completed)
        )
    connection.commit()
    return redirect(url_for("goals.goals", _anchor=f"goal-{goal_id}"))



@goals_bp.route("/goals/<int:goal_id>/delete", methods=["POST"])
def delete_goal(goal_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    create_goals_table(connection)
    connection.execute(
        "DELETE FROM goal_progress_logs WHERE goal_id = ? AND user_id = ?",
        (goal_id, session["user_id"])
    )
    connection.execute(
        "DELETE FROM goals WHERE id = ? AND user_id = ?",
        (goal_id, session["user_id"])
    )
    connection.commit()
    return redirect(url_for("goals.goals"))
