from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for

from blueprints.goals import build_goal_list
from db import get_db
from utils import planet_now

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

    today = planet_now().date()
    now = planet_now()

    active_semester = None
    courses = []
    next_assessment = None
    next_assessment_days = None
    upcoming_assessments = []
    upcoming_assessment_count = 0
    projected_average = None
    today_events = []
    dashboard_tasks = []

    # Make sure the calendar table exists.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            event_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'personal',
            notes TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    # Make sure the To-Do table exists.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER,
            title TEXT NOT NULL,
            due_date TEXT,
            due_time TEXT,
            priority TEXT NOT NULL DEFAULT 'normal',
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        )
        """
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

    connection.commit()

    # Retrieve all semesters belonging to the signed-in user.
    semesters = connection.execute(
        """
        SELECT *
        FROM semesters

        WHERE user_id = ?

        ORDER BY start_date DESC
        """,
        (session["user_id"],)
    ).fetchall()

    # Retrieve and verify the selected semester.
    if session.get("active_semester_id") is not None:
        active_semester = connection.execute(
            """
            SELECT *
            FROM semesters

            WHERE id = ?
              AND user_id = ?
            """,
            (
                session["active_semester_id"],
                session["user_id"]
            )
        ).fetchone()

    # Retrieve events happening today.
    event_rows = connection.execute(
        """
        SELECT *
        FROM events

        WHERE user_id = ?
          AND event_date = ?

        ORDER BY start_time
        """,
        (
            session["user_id"],
            today.isoformat()
        )
    ).fetchall()

    for row in event_rows:
        event = dict(row)

        start = datetime.strptime(
            event["start_time"],
            "%H:%M"
        )

        end = datetime.strptime(
            event["end_time"],
            "%H:%M"
        )

        event["display_time"] = (
            start.strftime("%-I:%M %p")
        )

        event["duration_minutes"] = max(
            int(
                (end - start).total_seconds()
                // 60
            ),
            0
        )

        event["category_label"] = (
            event["category"]
            .replace("_", " ")
            .title()
        )

        today_events.append(event)

    # Study Plan sessions live in study_blocks rather than events. Include
    # them in today's dashboard schedule without copying or duplicating them.
    study_blocks_table_exists = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'study_blocks'
        """
    ).fetchone()

    if study_blocks_table_exists:
        today_study_blocks = connection.execute(
            """
            SELECT
                study_blocks.id,
                study_blocks.title,
                study_blocks.notes,
                study_blocks.start_time,
                study_blocks.end_time,
                study_blocks.status,
                courses.code AS course_code
            FROM study_blocks
            LEFT JOIN courses
              ON courses.id = study_blocks.course_id
            WHERE study_blocks.user_id = ?
              AND study_blocks.scheduled_date = ?
              AND study_blocks.status IN ('planned', 'completed')
            ORDER BY study_blocks.start_time
            """,
            (session["user_id"], today.isoformat())
        ).fetchall()

        for row in today_study_blocks:
            block = dict(row)
            start = datetime.strptime(block["start_time"], "%H:%M")
            end = datetime.strptime(block["end_time"], "%H:%M")
            block.update({
                "category": "study",
                "category_label": (
                    f"Study Plan · {block['course_code']}"
                    if block["course_code"]
                    else "Study Plan"
                ),
                "display_time": start.strftime("%-I:%M %p"),
                "duration_minutes": max(
                    int((end - start).total_seconds() // 60),
                    0
                ),
                "source_type": "study_plan"
            })
            today_events.append(block)

    today_events.sort(key=lambda item: item["start_time"])

    # Retrieve course and assessment information.
    if active_semester is not None:
        courses = connection.execute(
            """
            SELECT
                courses.*,

                ROUND(
                    SUM(
                        CASE
                            WHEN assessments.score IS NOT NULL
                            THEN assessments.score
                                 * assessments.weight
                            ELSE 0
                        END
                    )
                    /
                    NULLIF(
                        SUM(
                            CASE
                                WHEN assessments.score IS NOT NULL
                                THEN assessments.weight
                                ELSE 0
                            END
                        ),
                        0
                    ),
                    1
                ) AS current_grade,

                MIN(
                    CASE
                        WHEN assessments.due_date >= ?
                         AND assessments.score IS NULL
                        THEN assessments.due_date
                    END
                ) AS next_due

            FROM courses

            LEFT JOIN assessments
                ON assessments.course_id = courses.id

            WHERE courses.semester_id = ?

            GROUP BY courses.id

            ORDER BY courses.name
            """,
            (
                today.isoformat(),
                active_semester["id"]
            )
        ).fetchall()

        graded_course_values = [
            course["current_grade"]
            for course in courses
            if course["current_grade"] is not None
        ]

        if graded_course_values:
            projected_average = round(
                sum(graded_course_values)
                / len(graded_course_values),
                1
            )

        assessment_rows = connection.execute(
            """
            SELECT
                assessments.*,
                courses.name AS course_name,
                courses.id AS course_id

            FROM assessments

            JOIN courses
                ON courses.id = assessments.course_id

            WHERE courses.semester_id = ?
              AND assessments.due_date >= ?
              AND assessments.score IS NULL

            ORDER BY assessments.due_date
            """,
            (
                active_semester["id"],
                today.isoformat()
            )
        ).fetchall()

        for row in assessment_rows:
            assessment = dict(row)

            due_date = datetime.strptime(
                assessment["due_date"],
                "%Y-%m-%d"
            ).date()

            assessment["pretty_due"] = (
                due_date.strftime("%B %-d, %Y")
            )

            assessment["month"] = (
                due_date.strftime("%b").upper()
            )

            assessment["day"] = due_date.day

            assessment["days_until"] = (
                due_date - today
            ).days

            upcoming_assessments.append(
                assessment
            )

        upcoming_assessment_count = len(
            upcoming_assessments
        )

        if upcoming_assessments:
            next_assessment = (
                upcoming_assessments[0]
            )

            next_assessment_days = (
                next_assessment["days_until"]
            )

    # Retrieve the next three incomplete To-Do tasks.
    dashboard_task_rows = connection.execute(
        """
        SELECT
            tasks.*,
            courses.code AS course_code,
            courses.name AS course_name

        FROM tasks

        LEFT JOIN courses
            ON courses.id = tasks.course_id

        WHERE tasks.user_id = ?
          AND tasks.completed = 0

        ORDER BY
            CASE
                WHEN tasks.due_date IS NULL THEN 1
                ELSE 0
            END,
            tasks.due_date,
            tasks.due_time,
            tasks.created_at DESC

        LIMIT 3
        """,
        (session["user_id"],)
    ).fetchall()

    # Prepare the task dates and overdue status.
    for row in dashboard_task_rows:
        task = dict(row)

        task["pretty_due_date"] = None
        task["pretty_due_time"] = None
        task["is_overdue"] = False

        if task["due_date"]:
            due_date = datetime.strptime(
                task["due_date"],
                "%Y-%m-%d"
            ).date()

            task["pretty_due_date"] = (
                due_date.strftime("%b %-d")
            )

            if task["due_time"]:
                due_time = datetime.strptime(
                    task["due_time"],
                    "%H:%M"
                ).time()

                task["pretty_due_time"] = (
                    datetime.strptime(
                        task["due_time"],
                        "%H:%M"
                    ).strftime("%-I:%M %p")
                )

                due_datetime = datetime.combine(
                    due_date,
                    due_time
                )

                task["is_overdue"] = (
                    due_datetime < now
                    and task["completed"] == 0
                )

            else:
                task["is_overdue"] = (
                    due_date < today
                    and task["completed"] == 0
                )

        dashboard_tasks.append(task)

    dashboard_goals = build_goal_list(connection, session["user_id"])
    goal_total = len(dashboard_goals)
    goal_completed = sum(
        1 for goal in dashboard_goals if goal["is_complete"]
    )

    quick_links = connection.execute(
        """
        SELECT * FROM quick_links
        WHERE user_id = ?
        ORDER BY name COLLATE NOCASE
        LIMIT 8
        """,
        (session["user_id"],)
    ).fetchall()


    return render_template(
        "dashboard.html",
        name=session["first_name"],
        semesters=semesters,
        active_semester=active_semester,
        courses=courses,
        projected_average=projected_average,
        next_assessment=next_assessment,
        next_assessment_days=next_assessment_days,
        upcoming_assessments=upcoming_assessments[:3],
        upcoming_assessment_count=upcoming_assessment_count,
        today_events=today_events,
        dashboard_tasks=dashboard_tasks,
        goal_total=goal_total,
        goal_completed=goal_completed,
        quick_links=quick_links,
        today_full=now.strftime(
            "%A, %B %-d, %Y"
        )
    )
