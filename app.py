import sqlite3
import os
import re
from io import BytesIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlparse
from flask import (
    Flask,
    render_template,
    request,
    session,
    redirect,
    url_for
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)


app = Flask(__name__)

# Used to protect Flask sessions during local development.
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "planet-local-development-key"
)

DEFAULT_TIMEZONE = os.environ.get(
    "PLANET_TIMEZONE", "America/Toronto"
)
try:
    ZoneInfo(DEFAULT_TIMEZONE)
except (ZoneInfoNotFoundError, ValueError):
    DEFAULT_TIMEZONE = "America/Toronto"


def _valid_timezone_name(value):
    timezone_name = str(value or "").strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return timezone_name


def _ensure_user_timezone_column(connection):
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if "timezone" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL "
            "DEFAULT 'America/Toronto'"
        )
        connection.commit()


def planet_now():
    """Return local time for the currently signed-in Planet user."""
    timezone_name = _valid_timezone_name(
        session.get("timezone", DEFAULT_TIMEZONE)
    ) or DEFAULT_TIMEZONE
    return datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/about")
def about():
    return "Your planet helps students plan their semester."


@app.route("/login", methods=["POST"])
def login():
    email = request.form["email"]
    password = request.form["password"]

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    _ensure_user_timezone_column(connection)

    user = connection.execute(
        "SELECT * FROM users WHERE email = ?",
        (email,)
    ).fetchone()

    connection.close()

    if user is None:
        return render_template(
            "index.html",
            error="Invalid email or password."
        )

    if not check_password_hash(
        user["password_hash"],
        password
    ):
        return render_template(
            "index.html",
            error="Invalid email or password."
        )

    session["user_id"] = user["id"]
    session["first_name"] = user["first_name"]
    session["timezone"] = (
        _valid_timezone_name(user["timezone"]) or DEFAULT_TIMEZONE
    )

    return redirect(url_for("dashboard"))

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        first_name = request.form["first_name"]
        last_name = request.form["last_name"]
        email = request.form["email"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            return render_template(
                "signup.html",
                error="Passwords do not match."
            )

        password_hash = generate_password_hash(
            password,
            method="pbkdf2:sha256"
        )

        connection = sqlite3.connect("planet.db")
        _ensure_user_timezone_column(connection)

        existing_user = connection.execute(
            "SELECT id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing_user is not None:
            connection.close()

            return render_template(
                "signup.html",
                error="An account with this email already exists."
            )

        cursor = connection.execute(
            """
            INSERT INTO users (
                first_name,
                last_name,
                email,
                password_hash
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                first_name,
                last_name,
                email,
                password_hash
            )
        )

        new_user_id = cursor.lastrowid

        connection.commit()
        connection.close()

        session["user_id"] = new_user_id
        session["first_name"] = first_name
        session["timezone"] = DEFAULT_TIMEZONE

        return redirect(url_for("dashboard"))

    return render_template("signup.html")



@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

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

    connection.close()

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

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/settings/timezone", methods=["POST"])
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

    connection = sqlite3.connect("planet.db")
    _ensure_user_timezone_column(connection)
    connection.execute(
        "UPDATE users SET timezone = ? WHERE id = ?",
        (timezone_name, session["user_id"])
    )
    connection.commit()
    connection.close()

    session["timezone"] = timezone_name
    return {"success": True, "changed": changed}

@app.route("/semester", methods=["GET", "POST"])
def semester():
    if "user_id" not in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        semester_name = request.form["semester_name"]
        start_date = request.form["start_date"]
        end_date = request.form["end_date"]

        connection = sqlite3.connect("planet.db")

        connection.execute(
            """
            INSERT INTO semesters (
                user_id,
                name,
                start_date,
                end_date
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                session["user_id"],
                semester_name,
                start_date,
                end_date
            )
        )

        connection.commit()
        connection.close()

        return redirect(url_for("semester"))
    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    create_goals_table(connection)
    connection.row_factory = sqlite3.Row
    semesters = connection.execute(
    """
    SELECT * FROM semesters
    WHERE user_id = ?
    ORDER BY start_date DESC
    """,
    (session["user_id"],)
).fetchall()
    courses = []

    if session.get("active_semester_id") is not None:
     courses = connection.execute(
    """
    SELECT
        courses.*,

        ROUND(
            CAST(
                SUM(
                    CASE
                        WHEN assessments.score IS NOT NULL
                        THEN assessments.score * assessments.weight
                        ELSE 0
                    END
                )
                AS REAL
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
                WHEN assessments.due_date >= DATE('now')
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
    (session["active_semester_id"],)
).fetchall()
    connection.close()

    return render_template(
        "semester.html",
        name=session["first_name"],
        semesters=semesters,
        courses= courses
    )

@app.route(
    "/semester/<int:semester_id>/select",
    methods=["POST"]
)
def select_semester(semester_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")

    semester = connection.execute(
        """
        SELECT * FROM semesters
        WHERE id = ? AND user_id = ?
        """,
        (
            semester_id,
            session["user_id"]
        )
    ).fetchone()

    connection.close()

    if semester is not None:
        session["active_semester_id"] = semester_id

    # Keep the user on the page where they changed the semester.
    if request.form.get("next") == "dashboard":
        return redirect(url_for("dashboard"))

    return redirect(url_for("semester"))

    return redirect(url_for("semester"))

@app.route(
    "/semester/<int:semester_id>/delete",
    methods=["POST"]
)
def delete_semester(semester_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")

    connection.execute(
        """
        DELETE FROM semesters
        WHERE id = ? AND user_id = ?
        """,
        (
            semester_id,
            session["user_id"]
        )
    )

    connection.commit()
    connection.close()

    if session.get("active_semester_id") == semester_id:
        session.pop("active_semester_id", None)

    return redirect(url_for("semester"))

@app.route("/courses/add", methods=["POST"])
def add_course():
    if "user_id" not in session:
        return redirect(url_for("home"))

    if "active_semester_id" not in session:
        return redirect(url_for("semester"))

    course_code = request.form["course_code"]
    course_name = request.form["course_name"]
    schedule = request.form["schedule"]
    colour = request.form["colour"]

    connection = sqlite3.connect("planet.db")

    connection.execute(
        """
        INSERT INTO courses (
            semester_id,
            code,
            name,
            schedule,
            colour
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            session["active_semester_id"],
            course_code,
            course_name,
            schedule,
            colour
        )
    )
    connection.commit()
    connection.close()

    return redirect(url_for("semester"))


@app.route("/semester/import-timetable")
def import_timetable():
    if "user_id" not in session:
        return redirect(url_for("home"))

    semester_id = session.get("active_semester_id")
    if semester_id is None:
        return redirect(url_for("semester"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    active_semester = connection.execute(
        "SELECT id, name FROM semesters WHERE id = ? AND user_id = ?",
        (semester_id, session["user_id"])
    ).fetchone()
    connection.close()

    if active_semester is None:
        session.pop("active_semester_id", None)
        return redirect(url_for("semester"))

    return render_template(
        "import_timetable.html",
        name=session["first_name"],
        active_semester=active_semester
    )


@app.route("/semester/import-timetable/confirm", methods=["POST"])
def confirm_timetable_import():
    if "user_id" not in session:
        return redirect(url_for("home"))

    semester_id = session.get("active_semester_id")
    if semester_id is None:
        return redirect(url_for("semester"))

    codes = request.form.getlist("course_code")
    names = request.form.getlist("course_name")
    schedules = request.form.getlist("schedule")
    colours = request.form.getlist("colour")
    allowed_colours = {"berry", "sage", "gold"}

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    owned_semester = connection.execute(
        "SELECT id FROM semesters WHERE id = ? AND user_id = ?",
        (semester_id, session["user_id"])
    ).fetchone()
    if owned_semester is None:
        connection.close()
        session.pop("active_semester_id", None)
        return redirect(url_for("semester"))

    existing_codes = {
        row["code"].strip().upper()
        for row in connection.execute(
            "SELECT code FROM courses WHERE semester_id = ?",
            (semester_id,)
        ).fetchall()
    }

    added = 0
    seen = set(existing_codes)
    for index, raw_code in enumerate(codes):
        code = " ".join(raw_code.strip().upper().split())
        if not code:
            continue

        name = names[index].strip() if index < len(names) else ""
        schedule = schedules[index].strip() if index < len(schedules) else ""
        colour = colours[index] if index < len(colours) else "berry"
        if colour not in allowed_colours:
            colour = "berry"

        if code not in seen:
            connection.execute(
                """
                INSERT INTO courses (semester_id, code, name, schedule, colour)
                VALUES (?, ?, ?, ?, ?)
                """,
                (semester_id, code, name or code, schedule, colour)
            )
            seen.add(code)
            added += 1

    connection.commit()
    connection.close()

    message = f"{added} course{'s' if added != 1 else ''} imported."
    return redirect(url_for("semester", saved=message))


def _course_outline_assessments(text):
    """Extract reviewable assessment rows from a plain-text course outline."""
    rows = []
    assessment_terms = (
        "assignment", "quiz", "test", "midterm", "final", "exam", "laboratory", "lab",
        "tutorial", "project", "presentation", "participation", "attendance", "essay",
        "report", "reflection", "case study", "portfolio", "discussion", "homework",
        "worksheet", "problem set", "proposal", "capstone", "practical", "simulation",
        "coding exercise", "coursework", "term work"
    )
    blocked_terms = (
        "prerequisite", "anti-requisite", "corequisite", "academic consideration",
        "accommodation", "policy", "textbook", "course material", "learning outcome",
        "support service", "scholastic offence", "copyright", "contact information"
    )

    def likely_assessment(name):
        lowered = name.lower()
        return (
            2 < len(name) <= 120
            and not any(term in lowered for term in blocked_terms)
            and any(term in lowered for term in assessment_terms)
        )

    evaluation = re.search(
        r"(?:(?:Method\s+of\s+)?Evaluation\s*:|"
        r"Methods\s+of\s+Evaluation\s+Grading\s+Scheme\s+and\s+Assessment\s+Dates|"
        r"Grading\s+Scheme\s+and\s+Assessment\s+Dates|"
        r"Assessment\s+(?:and|&)\s+Evaluation\s*:|Grading\s+Breakdown\s*:|"
        r"Grade\s+Breakdown\s*:|Assessment\s+Breakdown\s*:|Course\s+Assessment\s*:|"
        r"Course\s+Components\s*:|Marking\s+Scheme\s*:|Distribution\s+of\s+Marks\s*:|"
        r"Basis\s+of\s+Evaluation\s*:|Evaluation\s+Criteria\s*:|"
        r"Assessment\s+Summary\s*:|Assessment\s+Plan\s*:)\s*(.*?)"
        r"(?:\s+Notes\s*:|To obtain a passing grade|Course Component Details|I will post a sheet|"
        r"Use of Generative AI Tools|General information about missed coursework|Course Policies|"
        r"Academic Consideration|Missed Assessments|Assessment Flexibility|Academic Integrity|"
        r"Scholastic Offences|Accommodation|Support Services|Required Materials|Course Materials|"
        r"Learning Outcomes|Attendance Policy|Late Policy|Submission Policy|Additional Statements|"
        r"Copyright|Contact Information)",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )
    evaluation_text = evaluation.group(1) if evaluation else text
    evaluation_text = re.sub(
        r"^.*?overall course grade will be calculated as listed below\s*:\s*",
        "",
        evaluation_text,
        flags=re.IGNORECASE | re.DOTALL
    )
    evaluation_text = re.sub(
        r"(\d+(?:\.\d+)?\s*%)\s+each\s+",
        r"\1\n",
        evaluation_text,
        flags=re.IGNORECASE
    )
    evaluation_text = re.sub(
        r"(%(?:\s*/\s*\d+(?:\.\d+)?\s*%)?)\s+(?=[A-Z][A-Za-z])",
        r"\1\n",
        evaluation_text
    )
    pending_name = None
    for raw_line in evaluation_text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        match = re.match(
            r"(.+?)\s+(\d+(?:\.\d+)?)\s*%\s*(?:/\s*(\d+(?:\.\d+)?)\s*%)?",
            line
        )
        if not match:
            standalone_weight = re.match(
                r"^(\d+(?:\.\d+)?)\s*%\s*(?:/\s*(\d+(?:\.\d+)?)\s*%)?$",
                line
            )
            if standalone_weight and pending_name and likely_assessment(pending_name):
                rows.append({
                    "name": pending_name,
                    "weight": standalone_weight.group(1),
                    "alternative_weight": standalone_weight.group(2) or "",
                    "due_date": ""
                })
                pending_name = None
            elif "%" not in line and line.lower() not in {
                "course component", "weight", "ceab gas assessed"
            }:
                pending_name = re.sub(r"\s+\)", ")", line.strip(" :-"))
            continue
        name = re.sub(r"\s+\)", ")", match.group(1).strip(" :-"))
        if name.lower() in {"course component", "weight"} or not likely_assessment(name):
            continue
        rows.append({
            "name": name,
            "weight": match.group(2),
            "alternative_weight": match.group(3) or "",
            "due_date": ""
        })
        pending_name = None

    month_names = (
        "January|February|March|April|May|June|July|August|"
        "September|October|November|December"
    )
    midterm_date = re.search(
        rf"Midterm(?: Test)?[^.]*?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?,?\s*"
        rf"({month_names})\s+(\d{{1,2}}),?\s+(\d{{4}})",
        text,
        flags=re.IGNORECASE
    )
    if midterm_date:
        try:
            parsed_date = datetime.strptime(
                " ".join(midterm_date.groups()), "%B %d %Y"
            ).date().isoformat()
            for row in rows:
                if "midterm" in row["name"].lower():
                    row["due_date"] = parsed_date
        except ValueError:
            pass
    return rows


@app.route("/courses/<int:course_id>/import-outline", methods=["GET", "POST"])
def import_course_outline(course_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    course = connection.execute(
        """
        SELECT courses.* FROM courses
        JOIN semesters ON semesters.id = courses.semester_id
        WHERE courses.id = ? AND semesters.user_id = ?
        """,
        (course_id, session["user_id"])
    ).fetchone()
    connection.close()
    if course is None:
        return redirect(url_for("semester"))

    assessments = []
    error = None
    warning = None
    if request.method == "POST":
        extracted_text = request.form.get("extracted_text", "").strip()
        outline = request.files.get("course_outline")
        if extracted_text:
            if len(extracted_text) > 100000:
                error = "That image contained too much text to review safely."
            else:
                assessments = _course_outline_assessments(extracted_text)
                if not assessments:
                    error = "Planet could not find assessment names and percentages. You can add review rows manually."
        elif not outline or not outline.filename:
            error = "Choose a PDF or screenshot of the evaluation section first."
        elif not outline.filename.lower().endswith(".pdf"):
            error = "For images, choose PNG, JPG or WEBP and wait for the screenshot reader to finish."
        else:
            contents = outline.read(8 * 1024 * 1024 + 1)
            if len(contents) > 8 * 1024 * 1024:
                error = "Choose a PDF smaller than 8 MB."
            else:
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(contents))
                    text = "\n".join(page.extract_text() or "" for page in reader.pages)
                    assessments = _course_outline_assessments(text)
                    if not assessments:
                        error = "Planet could not find an evaluation table. You can add review rows manually."
                except Exception:
                    error = "Planet could not read that PDF. Try exporting it again as a text-based PDF."

    if assessments:
        primary_total = round(
            sum(float(row["weight"]) for row in assessments),
            1
        )
        if primary_total < 95 or primary_total > 105:
            warning = (
                f"Planet detected {primary_total:g}% of course weight, not approximately 100%. "
                "It may have missed a component, or a weight marked 'each' may need to be split or multiplied."
            )
        elif len(assessments) > 12:
            warning = (
                "Planet found an unusually large number of assessment rows. "
                "Review them carefully before importing."
            )

    return render_template(
        "import_course_outline.html",
        name=session["first_name"],
        course=course,
        assessments=assessments,
        error=error,
        warning=warning
    )


@app.route("/courses/<int:course_id>/import-outline/confirm", methods=["POST"])
def confirm_course_outline_import(course_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    course = connection.execute(
        """
        SELECT courses.id FROM courses
        JOIN semesters ON semesters.id = courses.semester_id
        WHERE courses.id = ? AND semesters.user_id = ?
        """,
        (course_id, session["user_id"])
    ).fetchone()
    if course is None:
        connection.close()
        return redirect(url_for("semester"))

    names = request.form.getlist("assessment_name")
    weights = request.form.getlist("weight")
    due_dates = request.form.getlist("due_date")
    existing = {
        row["name"].strip().lower()
        for row in connection.execute(
            "SELECT name FROM assessments WHERE course_id = ?", (course_id,)
        ).fetchall()
    }
    added = 0
    for index, raw_name in enumerate(names):
        name = raw_name.strip()
        if not name or name.lower() in existing:
            continue
        try:
            weight = float(weights[index])
        except (IndexError, TypeError, ValueError):
            continue
        if weight < 0 or weight > 100:
            continue
        due_date = due_dates[index].strip() if index < len(due_dates) else ""
        connection.execute(
            "INSERT INTO assessments (course_id, name, weight, score, due_date) VALUES (?, ?, ?, NULL, ?)",
            (course_id, name, weight, due_date or None)
        )
        existing.add(name.lower())
        added += 1

    connection.commit()
    connection.close()
    return redirect(url_for("course_details", course_id=course_id, imported=added))

@app.route(
    "/courses/<int:course_id>/delete",
    methods=["POST"]
)
def delete_course(course_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")

    connection.execute(
        """
        DELETE FROM courses
        WHERE id = ?
        AND semester_id IN (
            SELECT id FROM semesters
            WHERE user_id = ?
        )
        """,
        (
            course_id,
            session["user_id"]
        )
    )

    connection.commit()
    connection.close()

    return redirect(url_for("semester"))

@app.route(
    "/courses/<int:course_id>/assessments/add",
    methods=["POST"]
)
def add_assessment(course_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    assessment_name = request.form["assessment_name"]
    weight = request.form["weight"]
    score = request.form["score"]
    due_date = request.form["due_date"]

    # Store an unfinished assessment without a score.
    if score == "":
        score = None

    connection = sqlite3.connect("planet.db")

    # Check that this course belongs to the signed-in user.
    course = connection.execute(
        """
        SELECT courses.id
        FROM courses
        JOIN semesters
            ON courses.semester_id = semesters.id
        WHERE courses.id = ?
        AND semesters.user_id = ?
        """,
        (
            course_id,
            session["user_id"]
        )
    ).fetchone()

    if course is None:
        connection.close()
        return redirect(url_for("semester"))

    connection.execute(
        """
        INSERT INTO assessments (
            course_id,
            name,
            weight,
            score,
            due_date
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            course_id,
            assessment_name,
            weight,
            score,
            due_date
        )
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for(
            "course_details",
            course_id=course_id
        )
    )
@app.route(
    "/assessments/<int:assessment_id>/delete",
    methods=["POST"]
)
def delete_assessment(assessment_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    # Find the assessment and verify that it belongs to this user.
    assessment = connection.execute(
        """
        SELECT
            assessments.id,
            assessments.course_id
        FROM assessments
        JOIN courses
            ON assessments.course_id = courses.id
        JOIN semesters
            ON courses.semester_id = semesters.id
        WHERE assessments.id = ?
        AND semesters.user_id = ?
        """,
        (
            assessment_id,
            session["user_id"]
        )
    ).fetchone()

    if assessment is None:
        connection.close()
        return redirect(url_for("semester"))

    course_id = assessment["course_id"]

    connection.execute(
        """
        DELETE FROM assessments
        WHERE id = ?
        """,
        (
            assessment_id,
        )
    )

    connection.commit()
    connection.close()

    return redirect(
        url_for(
            "course_details",
            course_id=course_id
        )
    )
@app.route(
    "/assessments/<int:assessment_id>/edit",
    methods=["GET", "POST"]
)
def edit_assessment(assessment_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    # Retrieve the assessment and verify ownership.
    assessment = connection.execute(
        """
        SELECT
            assessments.*,
            courses.name AS course_name
        FROM assessments
        JOIN courses
            ON assessments.course_id = courses.id
        JOIN semesters
            ON courses.semester_id = semesters.id
        WHERE assessments.id = ?
        AND semesters.user_id = ?
        """,
        (
            assessment_id,
            session["user_id"]
        )
    ).fetchone()

    if assessment is None:
        connection.close()
        return redirect(url_for("semester"))

    if request.method == "POST":
        assessment_name = request.form["assessment_name"]
        weight = request.form["weight"]
        score = request.form["score"]
        due_date = request.form["due_date"]

        if score == "":
            score = None

        connection.execute(
            """
            UPDATE assessments
            SET
                name = ?,
                weight = ?,
                score = ?,
                due_date = ?
            WHERE id = ?
            """,
            (
                assessment_name,
                weight,
                score,
                due_date,
                assessment_id
            )
        )

        connection.commit()
        connection.close()

        return redirect(
            url_for(
                "course_details",
                course_id=assessment["course_id"]
            )
        )

    connection.close()

    return render_template(
        "edit_assessment.html",
        name=session["first_name"],
        assessment=assessment
    )

@app.route(
    "/courses/<int:course_id>",
    methods=["GET", "POST"]
)
def course_details(course_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    course = connection.execute(
        """
        SELECT
            courses.*,
            semesters.name AS semester_name
        FROM courses
        JOIN semesters
            ON courses.semester_id = semesters.id
        WHERE courses.id = ?
        AND semesters.user_id = ?
        """,
        (
            course_id,
            session["user_id"]
        )
    ).fetchone()

    if course is None:
        connection.close()
        return redirect(url_for("semester"))

    assessments = connection.execute(
        """
        SELECT * FROM assessments
        WHERE course_id = ?
        ORDER BY due_date
        """,
        (course_id,)
    ).fetchall()

    connection.close()

    graded_weight = 0
    earned_points = 0
    next_due = None
    ungraded_assessments = []

    for assessment in assessments:
        weight = float(assessment["weight"])

        if assessment["score"] is not None:
            score = float(assessment["score"])
            graded_weight += weight
            earned_points += score * weight / 100
        else:
            # Only assessments without a score belong in the planner.
            ungraded_assessments.append(assessment)

        if (
            assessment["score"] is None
            and assessment["due_date"]
            and assessment["due_date"]
            >= planet_now().date().isoformat()
        ):
            if (
                next_due is None
                or assessment["due_date"] < next_due
            ):
                next_due = assessment["due_date"]

    completed_weight = round(graded_weight, 1)
    remaining_weight = round(
        max(0, 100 - completed_weight),
        1
    )

    if graded_weight > 0:
        current_grade = round(
            earned_points / graded_weight * 100,
            1
        )
    else:
        current_grade = None

    target_grade = None
    required_grade = None
    planned_scores = {}
    assessment_targets = []

    if request.method == "POST":
        target_grade = float(
            request.form["target_grade"]
        )

        # Read predicted grades only for ungraded assessments.
        for assessment in ungraded_assessments:
            field_name = (
                f"expected_score_{assessment['id']}"
            )

            entered_score = request.form.get(
                field_name,
                ""
            ).strip()

            if entered_score:
                planned_scores[assessment["id"]] = float(
                    entered_score
                )

        planned_points = earned_points
        unplanned_weight = 0

        for assessment in ungraded_assessments:
            assessment_id = assessment["id"]
            weight = float(assessment["weight"])

            if assessment_id in planned_scores:
                planned_points += (
                    planned_scores[assessment_id]
                    * weight
                    / 100
                )
            else:
                unplanned_weight += weight

        if unplanned_weight > 0:
            required_grade = round(
                (
                    target_grade
                    - planned_points
                )
                * 100
                / unplanned_weight,
                1
            )

            # Suggest a score only for assessments that are still
            # ungraded and do not already have a planned score.
            for assessment in ungraded_assessments:
                if assessment["id"] not in planned_scores:
                    assessment_targets.append({
                        "name": assessment["name"],
                        "required_score": required_grade
                    })
        elif planned_points >= target_grade:
            required_grade = 0
        else:
            required_grade = None

    return render_template(
        "course.html",
        name=session["first_name"],
        course=course,
        assessments=assessments,
        ungraded_assessments=ungraded_assessments,
        planned_scores=planned_scores,
        current_grade=current_grade,
        completed_weight=completed_weight,
        remaining_weight=remaining_weight,
        next_due=next_due,
        target_grade=target_grade,
        required_grade=required_grade,
        assessment_targets=assessment_targets
    )

@app.template_filter("pretty_date")
def pretty_date(value):
    if not value:
        return ""

    formatted_date = datetime.strptime(
        value,
        "%Y-%m-%d"
    ).strftime("%B %d, %Y")

    return formatted_date.replace(" 0", " ")
@app.route(
    "/courses/<int:course_id>/edit",
    methods=["GET", "POST"]
)
def edit_course(course_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    course = connection.execute(
        """
        SELECT courses.*
        FROM courses
        JOIN semesters
            ON courses.semester_id = semesters.id
        WHERE courses.id = ?
          AND semesters.user_id = ?
        """,
        (
            course_id,
            session["user_id"]
        )
    ).fetchone()

    if course is None:
        connection.close()
        return redirect(url_for("semester"))

    if request.method == "POST":
        course_code = request.form["course_code"]
        course_name = request.form["course_name"]
        schedule = request.form["schedule"]
        colour = request.form["colour"]

        connection.execute(
            """
            UPDATE courses
            SET code = ?,
                name = ?,
                schedule = ?,
                colour = ?
            WHERE id = ?
            """,
            (
                course_code,
                course_name,
                schedule,
                colour,
                course_id
            )
        )

        connection.commit()
        connection.close()

        return redirect(
            url_for(
                "course_details",
                course_id=course_id
            )
        )

    connection.close()

    return render_template(
        "edit_course.html",
        course=course,
        name=session["first_name"]
    )


@app.route("/calendar", methods=["GET", "POST"])
def calendar():
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

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
    connection.commit()

    if request.method == "POST":
        title = request.form["title"].strip()
        event_date = request.form["event_date"]
        start_time = request.form["start_time"]
        end_time = request.form["end_time"]
        category = request.form["category"]
        notes = request.form.get("notes", "").strip()
        repeat_type = request.form.get("repeat_type", "none")
        repeat_until = request.form.get("repeat_until", "")
        repeat_days = {
            int(day)
            for day in request.form.getlist("repeat_days")
        }

        if title and end_time > start_time:
            first_date = datetime.strptime(
                event_date,
                "%Y-%m-%d"
            ).date()
            event_dates = [first_date]

            if repeat_type != "none" and repeat_until:
                final_date = datetime.strptime(
                    repeat_until,
                    "%Y-%m-%d"
                ).date()

                # Keep one repeating series within one year.
                final_date = min(
                    final_date,
                    first_date + timedelta(days=365)
                )

                if final_date >= first_date:
                    event_dates = []
                    current_date = first_date

                    while current_date <= final_date:
                        should_add = False

                        if repeat_type == "daily":
                            should_add = True
                        elif repeat_type == "weekly":
                            should_add = (
                                current_date.weekday()
                                == first_date.weekday()
                            )
                        elif repeat_type == "custom":
                            should_add = (
                                current_date.weekday()
                                in repeat_days
                            )

                        if should_add:
                            event_dates.append(current_date)

                        current_date += timedelta(days=1)

                    if not event_dates:
                        event_dates = [first_date]

            connection.executemany(
                """
                INSERT INTO events (
                    user_id,
                    title,
                    event_date,
                    start_time,
                    end_time,
                    category,
                    notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session["user_id"],
                        title,
                        repeated_date.isoformat(),
                        start_time,
                        end_time,
                        category,
                        notes
                    )
                    for repeated_date in event_dates
                ]
            )
            connection.commit()
            connection.close()

            return redirect(
                url_for("calendar", week=event_date)
            )

    requested_date = request.args.get("week")

    try:
        selected_date = datetime.strptime(
            requested_date,
            "%Y-%m-%d"
        ).date() if requested_date else planet_now().date()
    except ValueError:
        selected_date = planet_now().date()

    # Planet's weekly calendar runs Sunday through Saturday.
    days_since_sunday = (selected_date.weekday() + 1) % 7
    week_start = selected_date - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)

    events = connection.execute(
        """
        SELECT * FROM events
        WHERE user_id = ?
          AND event_date BETWEEN ? AND ?
        ORDER BY event_date, start_time
        """,
        (
            session["user_id"],
            week_start.isoformat(),
            week_end.isoformat()
        )
    ).fetchall()

    days = []

    for day_offset in range(7):
        day_date = week_start + timedelta(days=day_offset)

        day_events = []

        for event in events:
            if event["event_date"] != day_date.isoformat():
                continue

            event_data = dict(event)
            start_hour, start_minute = map(
                int,
                event["start_time"].split(":")
            )
            end_hour, end_minute = map(
                int,
                event["end_time"].split(":")
            )

            start_total = start_hour * 60 + start_minute
            end_total = end_hour * 60 + end_minute

            # The visible calendar runs from 6:00 AM to midnight.
            calendar_start = 6 *60
            pixels_per_hour = 42

            event_data["top"] = max(
                0,
                (start_total - calendar_start)
                / 60
                * pixels_per_hour
            )
            event_data["height"] = max(
                26,
                (end_total - start_total)
                / 60
                * pixels_per_hour
            )
            event_data["display_time"] = datetime.strptime(
                event["start_time"],
                "%H:%M"
            ).strftime("%-I:%M %p")

            day_events.append(event_data)

        days.append({
            "date": day_date,
            "events": day_events
        })

    connection.close()

    return render_template(
        "calendar.html",
        name=session["first_name"],
        days=days,
        today=planet_now().date(),
        week_start=week_start,
        week_end=week_end,
        previous_week=(week_start - timedelta(days=7)).isoformat(),
        next_week=(week_start + timedelta(days=7)).isoformat()
    )


@app.route("/calendar/events/<int:event_id>/delete", methods=["POST"])
def delete_event(event_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")

    connection.execute(
        """
        DELETE FROM events
        WHERE id = ? AND user_id = ?
        """,
        (event_id, session["user_id"])
    )

    connection.commit()
    connection.close()

    return redirect(request.referrer or url_for("calendar"))


@app.route("/calendar/events/<int:event_id>/edit", methods=["POST"])
def edit_event(event_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    title = request.form.get("title", "").strip()
    event_date = request.form.get("event_date", "")
    start_time = request.form.get("start_time", "")
    end_time = request.form.get("end_time", "")
    category = request.form.get("category", "personal")
    notes = request.form.get("notes", "").strip()

    if not title or not event_date or not start_time or end_time <= start_time:
        return redirect(url_for("calendar", week=event_date or None))

    connection = sqlite3.connect("planet.db")
    connection.execute(
        """
        UPDATE events
        SET title = ?, event_date = ?, start_time = ?, end_time = ?,
            category = ?, notes = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            title,
            event_date,
            start_time,
            end_time,
            category,
            notes,
            event_id,
            session["user_id"]
        )
    )
    connection.commit()
    connection.close()

    return redirect(url_for("calendar", week=event_date))


@app.route("/calendar/events/<int:event_id>/move", methods=["POST"])
def move_event(event_id):
    if "user_id" not in session:
        return {"success": False, "message": "You are not signed in."}, 401

    move_data = request.get_json(silent=True) or {}
    event_date = str(move_data.get("event_date", ""))
    start_time = str(move_data.get("start_time", ""))

    try:
        datetime.strptime(event_date, "%Y-%m-%d")
        new_start = datetime.strptime(start_time, "%H:%M")
    except ValueError:
        return {"success": False, "message": "That date or time is invalid."}, 400

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    event = connection.execute(
        """
        SELECT start_time, end_time
        FROM events
        WHERE id = ? AND user_id = ?
        """,
        (event_id, session["user_id"])
    ).fetchone()

    if event is None:
        connection.close()
        return {"success": False, "message": "Event not found."}, 404

    old_start = datetime.strptime(event["start_time"], "%H:%M")
    old_end = datetime.strptime(event["end_time"], "%H:%M")
    duration = old_end - old_start
    new_end = new_start + duration

    # Keep the event within the visible day.
    if new_end.date() != new_start.date():
        new_end = new_start.replace(hour=23, minute=59)

    connection.execute(
        """
        UPDATE events
        SET event_date = ?, start_time = ?, end_time = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            event_date,
            new_start.strftime("%H:%M"),
            new_end.strftime("%H:%M"),
            event_id,
            session["user_id"]
        )
    )
    connection.commit()
    connection.close()

    return {"success": True}
@app.route("/todo", methods=["GET", "POST"])
def todo():
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

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

    connection.commit()

    if request.method == "POST":
        title = request.form["title"].strip()
        course_id = request.form.get("course_id") or None
        due_date = request.form.get("due_date") or None
        due_time = request.form.get("due_time") or None
        priority = request.form.get(
            "priority",
            "normal"
        )

        if title:
            connection.execute(
                """
                INSERT INTO tasks (
                    user_id,
                    course_id,
                    title,
                    due_date,
                    due_time,
                    priority
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    course_id,
                    title,
                    due_date,
                    due_time,
                    priority
                )
            )

            connection.commit()

        connection.close()

        return redirect(url_for("todo"))

    task_filter = request.args.get(
        "filter",
        "all"
    )
    search_query = request.args.get(
        "q",
        ""
    ).strip()

    valid_filters = {
        "all",
        "today",
        "overdue",
        "upcoming",
        "completed"
    }

    if task_filter not in valid_filters:
        task_filter = "all"

    task_conditions = [
        "tasks.user_id = ?"
    ]

    task_values = [
        session["user_id"]
    ]
    if search_query:
        task_conditions.append(
            """
            (
                tasks.title LIKE ?
                OR courses.code LIKE ?
                OR courses.name LIKE ?
            )
            """
        )

        search_pattern = f"%{search_query}%"

        task_values.extend([
            search_pattern,
            search_pattern,
            search_pattern
        ])

    today = planet_now().date().isoformat()
    current_time = planet_now().strftime("%H:%M")

    if task_filter == "today":
        task_conditions.append(
            "tasks.due_date = ?"
        )

        task_conditions.append(
            "tasks.completed = 0"
        )

        task_values.append(today)

    elif task_filter == "overdue":
        task_conditions.append(
            """
            tasks.completed = 0
            AND tasks.due_date IS NOT NULL
            AND (
                tasks.due_date < ?
                OR (
                    tasks.due_date = ?
                    AND tasks.due_time IS NOT NULL
                    AND tasks.due_time < ?
                )
            )
            """
        )

        task_values.extend([
            today,
            today,
            current_time
        ])    

    elif task_filter == "upcoming":
        task_conditions.append(
            "tasks.due_date > ?"
        )

        task_conditions.append(
            "tasks.completed = 0"
        )

        task_values.append(today)

    elif task_filter == "completed":
        task_conditions.append(
            "tasks.completed = 1"
        )

    where_statement = " AND ".join(
        task_conditions
    )

    task_rows = connection.execute(
        f"""
        SELECT
            tasks.*,
            courses.name AS course_name,
            courses.code AS course_code

        FROM tasks

        LEFT JOIN courses
            ON courses.id = tasks.course_id

        WHERE {where_statement}

        ORDER BY
            tasks.completed,
            CASE
                WHEN tasks.due_date IS NULL THEN 1
                ELSE 0
            END,
            tasks.due_date,
            tasks.due_time,
            tasks.created_at DESC
        """,
        task_values
       ).fetchall()
    tasks = []
    now = planet_now()
    today_date = now.date()

    for row in task_rows:
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

                task["pretty_due_time"] = datetime.strptime(
                    task["due_time"],
                    "%H:%M"
                ).strftime("%-I:%M %p")

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
                    due_date < today_date
                    and task["completed"] == 0
                )

        tasks.append(task)
    

    courses = connection.execute(
        """
        SELECT
            courses.id,
            courses.code,
            courses.name,
            courses.colour

        FROM courses

        JOIN semesters
            ON semesters.id = courses.semester_id

        WHERE semesters.user_id = ?

        ORDER BY courses.name
        """,
        (session["user_id"],)
    ).fetchall()
    task_counts = connection.execute(
        """
        SELECT
            COUNT(*) AS all_count,

            SUM(
                CASE
                    WHEN completed = 0
                     AND due_date = ?
                    THEN 1
                    ELSE 0
                END
            ) AS today_count,

            SUM(
                CASE
                    WHEN completed = 0
                     AND due_date IS NOT NULL
                     AND (
                        due_date < ?
                        OR (
                            due_date = ?
                            AND due_time IS NOT NULL
                            AND due_time < ?
                        )
                     )
                    THEN 1
                    ELSE 0
                END
            ) AS overdue_count,

            SUM(
                CASE
                    WHEN completed = 0
                     AND due_date > ?
                    THEN 1
                    ELSE 0
                END
            ) AS upcoming_count,

            SUM(
                CASE
                    WHEN completed = 1
                    THEN 1
                    ELSE 0
                END
            ) AS completed_count

        FROM tasks

        WHERE user_id = ?
        """,
        (
            today,
            today,
            today,
            current_time,
            today,
            session["user_id"]
        )
    ).fetchone()
    

    connection.close()

    return render_template(
        "todo.html",
        name=session["first_name"],
        tasks=tasks,
        courses=courses,
        today=today,
        task_filter=task_filter,
        search_query=search_query,
        task_counts=task_counts,
    )
@app.route("/tasks/<int:task_id>/toggle", methods=["POST"])
def toggle_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")

    connection.execute(
        """
        UPDATE tasks

        SET completed = CASE WHEN completed = 0 THEN 1 ELSE 0 END,
            completed_at = CASE
                WHEN completed = 0 THEN CURRENT_TIMESTAMP
                ELSE NULL
            END

        WHERE id = ?
          AND user_id = ?
        """,
        (
            task_id,
            session["user_id"]
        )
    )

    connection.commit()
    connection.close()

    return redirect(url_for("todo"))

@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")

    connection.execute(
        """
        DELETE FROM tasks

        WHERE id = ?
          AND user_id = ?
        """,
        (
            task_id,
            session["user_id"]
        )
    )

    connection.commit()
    connection.close()

    return redirect(url_for("todo"))

@app.route(
    "/tasks/<int:task_id>/edit",
    methods=["GET", "POST"]
)
@app.route(
    "/tasks/<int:task_id>/edit",
    methods=["GET", "POST"]
)
def edit_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    task = connection.execute(
        """
        SELECT *
        FROM tasks

        WHERE id = ?
          AND user_id = ?
        """,
        (
            task_id,
            session["user_id"]
        )
    ).fetchone()

    if task is None:
        connection.close()
        return redirect(url_for("todo"))

    if request.method == "POST":
        title = request.form["title"].strip()
        course_id = request.form.get("course_id") or None
        due_date = request.form.get("due_date") or None
        due_time = request.form.get("due_time") or None
        priority = request.form.get("priority", "normal")

        if title:
            connection.execute(
                """
                UPDATE tasks

                SET title = ?,
                    course_id = ?,
                    due_date = ?,
                    due_time = ?,
                    priority = ?

                WHERE id = ?
                  AND user_id = ?
                """,
                (
                    title,
                    course_id,
                    due_date,
                    due_time,
                    priority,
                    task_id,
                    session["user_id"]
                )
            )

            connection.commit()

        connection.close()
        return redirect(url_for("todo"))

    courses = connection.execute(
        """
        SELECT
            courses.id,
            courses.code,
            courses.name,
            courses.colour

        FROM courses

        JOIN semesters
            ON semesters.id = courses.semester_id

        WHERE semesters.user_id = ?

        ORDER BY courses.name
        """,
        (session["user_id"],)
    ).fetchall()

    connection.close()

    return render_template(
        "edit_task.html",
        name=session["first_name"],
        task=task,
        courses=courses
    )

def create_goals_table(connection):
    connection.row_factory = sqlite3.Row
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


@app.route("/goals", methods=["GET", "POST"])
def goals():
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
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

        connection.close()
        return redirect(url_for("goals"))

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
    connection.close()

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


@app.route("/goals/<int:goal_id>/progress", methods=["POST"])
def update_goal_progress(goal_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    try:
        current_value = max(
            0,
            float(request.form.get("current_value") or 0)
        )
    except ValueError:
        current_value = 0

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
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
    connection.close()
    return redirect(url_for("goals", _anchor=f"goal-{goal_id}"))


@app.route("/goals/<int:goal_id>/edit", methods=["POST"])
def edit_goal(goal_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

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

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
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

    connection.close()
    return redirect(url_for("goals", _anchor=f"goal-{goal_id}"))


@app.route("/goals/<int:goal_id>/toggle", methods=["POST"])
def toggle_goal(goal_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
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
    connection.close()
    return redirect(url_for("goals", _anchor=f"goal-{goal_id}"))


@app.route("/goals/<int:goal_id>/delete", methods=["POST"])
def delete_goal(goal_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
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
    connection.close()
    return redirect(url_for("goals"))


@app.route("/focus", methods=["GET", "POST"])
def focus():
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

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
                connection.close()
                return redirect(url_for("focus"))

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

        connection.close()

        return redirect(url_for("focus"))

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

    connection.close()

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
@app.route(
    "/focus/<int:focus_session_id>/edit",
    methods=["GET", "POST"]
)
def edit_focus_session(focus_session_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

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
        connection.close()
        return redirect(url_for("focus"))

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

        connection.close()

        return redirect(url_for("focus"))

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

    connection.close()

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


@app.route(
    "/focus/<int:focus_session_id>/delete",
    methods=["POST"]
)
def delete_focus_session(focus_session_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")

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
    connection.close()

    return redirect(url_for("focus"))

@app.route(
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

    connection = sqlite3.connect("planet.db")

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
        connection.close()

        return {
            "success": False,
            "message": "Planet could not save this focus session."
        }, 500

    connection.close()

    return {
        "success": True,
        "message": "Focus session saved.",
        "duration_minutes": duration_minutes,
        "duration_seconds": elapsed_seconds
    }

@app.route("/study-plan", methods=["GET", "POST"])
def study_plan():
    if "user_id" not in session:
        return redirect(url_for("home"))

    user_id = session["user_id"]

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

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
            available_days TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS study_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER,
            assessment_id INTEGER,
            title TEXT NOT NULL,
            notes TEXT,
            scheduled_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (assessment_id) REFERENCES assessments(id)
        )
        """
    )

    study_block_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(study_blocks)"
        ).fetchall()
    }
    if "notes" not in study_block_columns:
        connection.execute("ALTER TABLE study_blocks ADD COLUMN notes TEXT")
    connection.commit()

    if request.method == "POST":
        earliest_time = request.form.get(
            "earliest_time",
            "09:00"
        )
        latest_time = request.form.get(
            "latest_time",
            "21:00"
        )
        weekly_target_hours = request.form.get(
            "weekly_target_hours",
            type=float
        )
        preferred_session_minutes = request.form.get(
            "preferred_session_minutes",
            type=int
        )
        break_minutes = request.form.get(
            "break_minutes",
            type=int
        )

        include_weekends = (
            1
            if request.form.get("include_weekends")
            else 0
        )

        available_days = request.form.getlist(
            "available_days"
        )
        available_days_text = ",".join(available_days)

        if (
            not earliest_time
            or not latest_time
            or weekly_target_hours is None
            or weekly_target_hours <= 0
            or preferred_session_minutes is None
            or preferred_session_minutes <= 0
            or break_minutes is None
            or break_minutes < 0
            or not available_days
        ):
            connection.close()

            return redirect(
                url_for(
                    "study_plan",
                    error="Please complete all study preferences."
                )
            )

        if earliest_time >= latest_time:
            connection.close()

            return redirect(
                url_for(
                    "study_plan",
                    error="Your latest study time must be later than your earliest time."
                )
            )

        connection.execute(
            """
            INSERT INTO study_plan_settings (
                user_id,
                earliest_time,
                latest_time,
                weekly_target_hours,
                preferred_session_minutes,
                break_minutes,
                include_weekends,
                available_days
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                earliest_time = excluded.earliest_time,
                latest_time = excluded.latest_time,
                weekly_target_hours = excluded.weekly_target_hours,
                preferred_session_minutes =
                    excluded.preferred_session_minutes,
                break_minutes = excluded.break_minutes,
                include_weekends = excluded.include_weekends,
                available_days = excluded.available_days
            """,
            (
                user_id,
                earliest_time,
                latest_time,
                weekly_target_hours,
                preferred_session_minutes,
                break_minutes,
                include_weekends,
                available_days_text
            )
        )

        connection.commit()
        connection.close()

        return redirect(
            url_for(
                "study_plan",
                saved="Study preferences saved."
            )
        )

    settings = connection.execute(
        """
        SELECT *
        FROM study_plan_settings
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    if settings is None:
        settings = {
            "earliest_time": "09:00",
            "latest_time": "21:00",
            "weekly_target_hours": 8,
            "preferred_session_minutes": 45,
            "break_minutes": 10,
            "include_weekends": 1,
            "available_days":
                "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
        }

    selected_days = settings["available_days"].split(",")

    active_semester_id = session.get(
        "active_semester_id"
    )

    courses = []

    if active_semester_id:
     courses = connection.execute(
        """
        SELECT
            courses.id,
            courses.code,
            courses.name,
            courses.colour
        FROM courses
        JOIN semesters
          ON semesters.id = courses.semester_id
        WHERE semesters.user_id = ?
          AND courses.semester_id = ?
        ORDER BY
            courses.code,
            courses.name
        """,
        (
            user_id,
            active_semester_id
        )
    ).fetchall()
    assessments = []

    if active_semester_id:
     assessments = connection.execute(
        """
        SELECT
            assessments.id,
            assessments.name,
            assessments.due_date,
            assessments.weight,
            courses.id AS course_id,
            courses.code AS course_code,
            courses.name AS course_name
        FROM assessments
        JOIN courses
          ON courses.id = assessments.course_id
        JOIN semesters
          ON semesters.id = courses.semester_id
        WHERE semesters.user_id = ?
          AND courses.semester_id = ?
          AND assessments.score IS NULL
          AND assessments.due_date IS NOT NULL
        ORDER BY
            assessments.due_date ASC,
            assessments.weight DESC
        """,
        (
            user_id,
            active_semester_id
        )
    ).fetchall()

    study_blocks = connection.execute(
        """
        SELECT
            study_blocks.*,
            courses.code AS course_code,
            courses.name AS course_name,
            courses.colour AS course_colour,
            assessments.name AS assessment_name
        FROM study_blocks
        LEFT JOIN courses
          ON courses.id = study_blocks.course_id
        LEFT JOIN assessments
          ON assessments.id = study_blocks.assessment_id
        WHERE study_blocks.user_id = ?
        ORDER BY
            study_blocks.scheduled_date ASC,
            study_blocks.start_time ASC
        """,
        (user_id,)
    ).fetchall()

    study_block_groups = []
    groups_by_date = {}

    for block in study_blocks:
        date_key = block["scheduled_date"]

        if date_key not in groups_by_date:
            try:
                block_date = datetime.strptime(date_key, "%Y-%m-%d")
                day_number = block_date.day

                if 10 < day_number % 100 < 14:
                    suffix = "th"
                else:
                    suffix = {
                        1: "st",
                        2: "nd",
                        3: "rd"
                    }.get(day_number % 10, "th")

                date_label = (
                    f"{block_date.strftime('%A')}, "
                    f"{day_number}{suffix} "
                    f"{block_date.strftime('%B %Y')}"
                )
            except (TypeError, ValueError):
                date_label = date_key or "Date unavailable"

            group = {
                "date": date_key,
                "label": date_label,
                "blocks": []
            }
            groups_by_date[date_key] = group
            study_block_groups.append(group)

        groups_by_date[date_key]["blocks"].append(block)

    # Prepare the course cards here rather than making Jinja recalculate them.
    # Completed sessions are grouped by their Monday-Sunday week so every
    # course card can act as a small, expandable study archive.
    allowed_colours = {"berry", "sage", "gold"}
    course_cards = []

    for course in courses:
        colour = (course["colour"] or "berry").strip().lower()
        if colour not in allowed_colours:
            colour = "berry"

        planned_count = 0
        completed_count = 0
        completed_weeks = {}

        for block in study_blocks:
            if block["course_id"] != course["id"]:
                continue

            if block["status"] == "completed":
                completed_count += 1

                try:
                    session_date = datetime.strptime(
                        block["scheduled_date"],
                        "%Y-%m-%d"
                    ).date()
                    week_start = session_date - timedelta(
                        days=session_date.weekday()
                    )
                    week_end = week_start + timedelta(days=6)
                    week_key = week_start.isoformat()
                    week_label = (
                        f"{week_start.strftime('%b %d')}–"
                        f"{week_end.strftime('%b %d, %Y')}"
                    )
                except (TypeError, ValueError):
                    week_key = "unknown"
                    week_label = "Earlier sessions"

                completed_weeks.setdefault(
                    week_key,
                    {"label": week_label, "sessions": []}
                )["sessions"].append({
                    "id": block["id"],
                    "title": block["title"],
                    "date": block["scheduled_date"],
                    "start_time": block["start_time"],
                    "end_time": block["end_time"]
                })
            elif block["status"] == "planned":
                planned_count += 1

        history = [
            completed_weeks[key]
            for key in sorted(completed_weeks, reverse=True)
        ]

        course_cards.append({
            "id": course["id"],
            "code": course["code"],
            "name": course["name"],
            "colour": colour,
            "planned_count": planned_count,
            "completed_count": completed_count,
            "history": history
        })

    connection.close()

    return render_template(
        "study_plan.html",
        name=session["first_name"],
        settings=settings,
        selected_days=selected_days,
        courses=courses,
        course_cards=course_cards,
        assessments=assessments,
        study_blocks=study_blocks,
        study_block_groups=study_block_groups,
        error=request.args.get("error"),
        saved=request.args.get("saved")
    )


def _owned_study_plan_course(connection, course_id, user_id):
    if not course_id:
        return None

    return connection.execute(
        """
        SELECT courses.id
        FROM courses
        JOIN semesters
          ON semesters.id = courses.semester_id
        WHERE courses.id = ?
          AND semesters.user_id = ?
        """,
        (course_id, user_id)
    ).fetchone()


def _time_to_minutes(value):
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def _minutes_to_time(value):
    return f"{value // 60:02d}:{value % 60:02d}"


@app.route("/study-plan/generate", methods=["POST"])
def generate_study_plan():
    if "user_id" not in session:
        return redirect(url_for("home"))

    user_id = session["user_id"]
    active_semester_id = session.get("active_semester_id")

    if not active_semester_id:
        return redirect(url_for(
            "study_plan",
            error="Choose an active semester before generating a plan."
        ))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    settings = connection.execute(
        "SELECT * FROM study_plan_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if settings is None:
        settings = {
            "earliest_time": "09:00",
            "latest_time": "21:00",
            "weekly_target_hours": 8,
            "preferred_session_minutes": 45,
            "break_minutes": 10,
            "include_weekends": 1,
            "available_days":
                "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
        }

    courses = connection.execute(
        """
        SELECT courses.id, courses.code, courses.name
        FROM courses
        JOIN semesters ON semesters.id = courses.semester_id
        WHERE semesters.user_id = ? AND courses.semester_id = ?
        ORDER BY courses.code, courses.name
        """,
        (user_id, active_semester_id)
    ).fetchall()

    if not courses:
        connection.close()
        return redirect(url_for(
            "study_plan",
            error="Add at least one course before generating a plan."
        ))

    today = planet_now().date()
    window_end = today + timedelta(days=6)
    selected_assessment_ids = request.form.getlist("assessment_ids", type=int)
    assessments = []

    if selected_assessment_ids:
        placeholders = ",".join("?" for _ in selected_assessment_ids)
        assessments = connection.execute(
            f"""
            SELECT assessments.id, assessments.name, assessments.due_date,
                   assessments.weight, courses.id AS course_id,
                   courses.code AS course_code
            FROM assessments
            JOIN courses ON courses.id = assessments.course_id
            JOIN semesters ON semesters.id = courses.semester_id
            WHERE semesters.user_id = ?
              AND courses.semester_id = ?
              AND assessments.score IS NULL
              AND assessments.id IN ({placeholders})
              AND assessments.due_date >= ?
            ORDER BY assessments.due_date ASC, assessments.weight DESC
            """,
            (
                user_id,
                active_semester_id,
                *selected_assessment_ids,
                today.isoformat()
            )
        ).fetchall()

    # If no assessments are selected, Planet balances sessions by course.
    plan_targets = []
    for assessment in assessments:
        due_date = datetime.strptime(
            assessment["due_date"], "%Y-%m-%d"
        ).date()
        days_until_due = max(0, (due_date - today).days)

        # Closer deadlines receive more urgency points. Assessment weight adds
        # importance, but is capped so one large exam cannot take every slot.
        urgency_points = max(1, 8 - min(days_until_due, 7))
        try:
            assessment_weight = max(0.0, float(assessment["weight"] or 0))
        except (TypeError, ValueError):
            assessment_weight = 0.0
        weight_points = max(1, min(5, round(assessment_weight / 10)))

        plan_targets.append({
            "course_id": assessment["course_id"],
            "assessment_id": assessment["id"],
            "title": f"Study for {assessment['name']}",
            "notes": f"Priority session for {assessment['course_code']}.",
            "due_date": assessment["due_date"],
            "priority_score": urgency_points + weight_points
        })

    if not plan_targets:
        for course in courses:
            plan_targets.append({
                "course_id": course["id"],
                "assessment_id": None,
                "title": f"{course['code']} study session",
                "notes": f"Focused study time for {course['name']}.",
                "due_date": None
            })

    existing_blocks = connection.execute(
        """
        SELECT scheduled_date, start_time, end_time
        FROM study_blocks
        WHERE user_id = ?
          AND scheduled_date BETWEEN ? AND ?
          AND status IN ('planned', 'completed')
        """,
        (user_id, today.isoformat(), window_end.isoformat())
    ).fetchall()

    calendar_events = connection.execute(
        """
        SELECT event_date AS scheduled_date, start_time, end_time
        FROM events
        WHERE user_id = ? AND event_date BETWEEN ? AND ?
        """,
        (user_id, today.isoformat(), window_end.isoformat())
    ).fetchall()

    occupied = {}
    existing_minutes = 0
    for item in existing_blocks:
        try:
            start = _time_to_minutes(item["start_time"])
            end = _time_to_minutes(item["end_time"])
        except (TypeError, ValueError):
            continue
        occupied.setdefault(item["scheduled_date"], []).append((start, end))
        existing_minutes += max(0, end - start)

    for item in calendar_events:
        try:
            start = _time_to_minutes(item["start_time"])
            end = _time_to_minutes(item["end_time"])
        except (TypeError, ValueError):
            continue
        occupied.setdefault(item["scheduled_date"], []).append((start, end))

    target_minutes = int(float(settings["weekly_target_hours"]) * 60)
    remaining_minutes = max(0, target_minutes - existing_minutes)
    session_minutes = int(settings["preferred_session_minutes"])
    break_minutes = int(settings["break_minutes"])
    earliest = _time_to_minutes(settings["earliest_time"])
    latest = _time_to_minutes(settings["latest_time"])
    available_days = {
        day.strip().lower()
        for day in settings["available_days"].split(",")
        if day.strip()
    }

    if not settings["include_weekends"]:
        available_days -= {"saturday", "sunday"}

    generated = []
    target_index = 0
    target_session_counts = {
        target["assessment_id"]: 0
        for target in plan_targets
        if target["assessment_id"] is not None
    }
    now = planet_now()

    for day_offset in range(7):
        if remaining_minutes <= 0:
            break

        study_date = today + timedelta(days=day_offset)
        if study_date.strftime("%A").lower() not in available_days:
            continue

        slot_start = earliest
        if study_date == today:
            current_minutes = now.hour * 60 + now.minute
            slot_start = max(
                slot_start,
                ((current_minutes + 14) // 15) * 15
            )

        while slot_start + session_minutes <= latest and remaining_minutes > 0:
            slot_end = slot_start + min(session_minutes, remaining_minutes)
            conflicts = any(
                slot_start < busy_end and slot_end > busy_start
                for busy_start, busy_end in occupied.get(
                    study_date.isoformat(), []
                )
            )

            if conflicts:
                slot_start += 15
                continue

            eligible_targets = [
                target for target in plan_targets
                if target["due_date"] is None
                or study_date.isoformat() <= target["due_date"]
            ]
            if not eligible_targets:
                slot_start += 15
                continue

            if assessments:
                # A target's effective score falls each time it receives a
                # session. This creates a weighted, fair rotation: urgent and
                # high-value assessments receive more sessions, while every
                # selected deadline can still receive study time.
                target = max(
                    eligible_targets,
                    key=lambda item: (
                        item["priority_score"]
                        / (
                            target_session_counts[item["assessment_id"]] + 1
                        ),
                        -datetime.strptime(
                            item["due_date"], "%Y-%m-%d"
                        ).date().toordinal(),
                        item["priority_score"]
                    )
                )
                target_session_counts[target["assessment_id"]] += 1
            else:
                target = eligible_targets[target_index % len(eligible_targets)]
                target_index += 1

            connection.execute(
                """
                INSERT INTO study_blocks (
                    user_id, course_id, assessment_id, title, notes,
                    scheduled_date, start_time, end_time, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned')
                """,
                (
                    user_id,
                    target["course_id"],
                    target["assessment_id"],
                    target["title"],
                    target["notes"],
                    study_date.isoformat(),
                    _minutes_to_time(slot_start),
                    _minutes_to_time(slot_end)
                )
            )
            generated.append((slot_start, slot_end))
            occupied.setdefault(study_date.isoformat(), []).append(
                (slot_start, slot_end)
            )
            remaining_minutes -= slot_end - slot_start
            slot_start = slot_end + break_minutes

    connection.commit()
    connection.close()

    if not generated:
        if remaining_minutes == 0:
            message = "Your study plan already meets this week's target."
        else:
            message = "Planet could not find an open time. Adjust your study preferences and try again."
        return redirect(url_for("study_plan", error=message))

    session_word = "session" if len(generated) == 1 else "sessions"
    return redirect(url_for(
        "study_plan",
        saved=f"Planet generated {len(generated)} study {session_word}."
    ) + "#planned-sessions")


@app.route("/study-plan/sessions/add", methods=["POST"])
def add_study_block():
    if "user_id" not in session:
        return redirect(url_for("home"))

    user_id = session["user_id"]
    title = request.form.get("title", "").strip()
    notes = request.form.get("notes", "").strip()
    scheduled_date = request.form.get("scheduled_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    course_id = request.form.get("course_id", type=int)
    assessment_id = request.form.get("assessment_id", type=int)

    if not title or not scheduled_date or not start_time or not end_time:
        return redirect(url_for(
            "study_plan",
            error="Add a title, date, start time and end time."
        ))

    if start_time >= end_time:
        return redirect(url_for(
            "study_plan",
            error="The session end time must be later than its start time."
        ))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    if course_id and not _owned_study_plan_course(
        connection, course_id, user_id
    ):
        connection.close()
        return redirect(url_for("study_plan", error="That course is unavailable."))

    if assessment_id:
        assessment = connection.execute(
            """
            SELECT assessments.id, assessments.course_id
            FROM assessments
            JOIN courses ON courses.id = assessments.course_id
            JOIN semesters ON semesters.id = courses.semester_id
            WHERE assessments.id = ?
              AND semesters.user_id = ?
            """,
            (assessment_id, user_id)
        ).fetchone()

        if assessment is None:
            connection.close()
            return redirect(url_for(
                "study_plan",
                error="That assessment is unavailable."
            ))

        if course_id and assessment["course_id"] != course_id:
            connection.close()
            return redirect(url_for(
                "study_plan",
                error="The assessment does not belong to that course."
            ))

        course_id = assessment["course_id"]

    connection.execute(
        """
        INSERT INTO study_blocks (
            user_id, course_id, assessment_id, title, notes,
            scheduled_date, start_time, end_time, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned')
        """,
        (
            user_id, course_id, assessment_id, title, notes,
            scheduled_date, start_time, end_time
        )
    )
    connection.commit()
    connection.close()

    return redirect(url_for("study_plan", saved="Study session added."))


@app.route("/study-plan/sessions/<int:block_id>/edit", methods=["POST"])
def edit_study_block(block_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    user_id = session["user_id"]
    title = request.form.get("title", "").strip()
    notes = request.form.get("notes", "").strip()
    scheduled_date = request.form.get("scheduled_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    course_id = request.form.get("course_id", type=int)
    assessment_id = request.form.get("assessment_id", type=int)

    if not title or not scheduled_date or not start_time or not end_time:
        return redirect(url_for("study_plan", error="Complete every required field."))

    if start_time >= end_time:
        return redirect(url_for(
            "study_plan",
            error="The session end time must be later than its start time."
        ))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    block = connection.execute(
        "SELECT id FROM study_blocks WHERE id = ? AND user_id = ?",
        (block_id, user_id)
    ).fetchone()

    if block is None:
        connection.close()
        return redirect(url_for("study_plan", error="Study session not found."))

    if course_id and not _owned_study_plan_course(
        connection, course_id, user_id
    ):
        connection.close()
        return redirect(url_for("study_plan", error="That course is unavailable."))

    if assessment_id:
        assessment = connection.execute(
            """
            SELECT assessments.id, assessments.course_id
            FROM assessments
            JOIN courses ON courses.id = assessments.course_id
            JOIN semesters ON semesters.id = courses.semester_id
            WHERE assessments.id = ? AND semesters.user_id = ?
            """,
            (assessment_id, user_id)
        ).fetchone()
        if assessment is None or (
            course_id and assessment["course_id"] != course_id
        ):
            connection.close()
            return redirect(url_for(
                "study_plan",
                error="Choose an assessment from the selected course."
            ))
        course_id = assessment["course_id"]

    connection.execute(
        """
        UPDATE study_blocks
        SET course_id = ?, assessment_id = ?, title = ?, notes = ?,
            scheduled_date = ?, start_time = ?, end_time = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            course_id, assessment_id, title, notes, scheduled_date,
            start_time, end_time, block_id, user_id
        )
    )
    connection.commit()
    connection.close()

    return redirect(url_for("study_plan", saved="Study session updated."))


@app.route("/study-plan/sessions/<int:block_id>/status", methods=["POST"])
def update_study_block_status(block_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    status = request.form.get("status", "planned")
    if status not in {"planned", "completed", "missed"}:
        status = "planned"

    connection = sqlite3.connect("planet.db")
    connection.execute(
        "UPDATE study_blocks SET status = ? WHERE id = ? AND user_id = ?",
        (status, block_id, session["user_id"])
    )
    connection.commit()
    connection.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {
            "success": True,
            "status": status
        }

    return redirect(
        url_for("study_plan", saved="Session status updated.")
        + "#planned-sessions"
    )


@app.route("/study-plan/sessions/<int:block_id>/delete", methods=["POST"])
def delete_study_block(block_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    cursor = connection.execute(
        "DELETE FROM study_blocks WHERE id = ? AND user_id = ?",
        (block_id, session["user_id"])
    )
    connection.commit()
    connection.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if cursor.rowcount == 0:
            return {
                "success": False,
                "message": "Study session not found."
            }, 404

        return {
            "success": True,
            "deleted_id": block_id
        }

    return redirect(
        url_for("study_plan", saved="Study session deleted.")
        + "#planned-sessions"
    )


@app.route("/study-plan/sessions/delete-selected", methods=["POST"])
def delete_selected_study_blocks():
    if "user_id" not in session:
        return {"success": False, "message": "Sign in required."}, 401

    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("block_ids", [])

    if not isinstance(raw_ids, list):
        return {"success": False, "message": "Choose sessions to delete."}, 400

    block_ids = []
    for value in raw_ids[:200]:
        try:
            block_id = int(value)
        except (TypeError, ValueError):
            continue
        if block_id > 0 and block_id not in block_ids:
            block_ids.append(block_id)

    if not block_ids:
        return {"success": False, "message": "Choose sessions to delete."}, 400

    placeholders = ",".join("?" for _ in block_ids)
    connection = sqlite3.connect("planet.db")
    owned_rows = connection.execute(
        f"""
        SELECT id FROM study_blocks
        WHERE user_id = ? AND id IN ({placeholders})
        """,
        (session["user_id"], *block_ids)
    ).fetchall()
    deleted_ids = [row[0] for row in owned_rows]

    if deleted_ids:
        delete_placeholders = ",".join("?" for _ in deleted_ids)
        connection.execute(
            f"""
            DELETE FROM study_blocks
            WHERE user_id = ? AND id IN ({delete_placeholders})
            """,
            (session["user_id"], *deleted_ids)
        )
        connection.commit()

    connection.close()

    return {
        "success": True,
        "deleted_ids": deleted_ids
    }


GRATITUDE_LABELS = {
    "people": "♡ People",
    "small-joys": "☕ Small joys",
    "proud": "✦ Proud of me",
    "moments": "☀ Little moments",
    "faith": "🙏 Faith"
}


@app.route("/gratitude", methods=["GET", "POST"])
def gratitude():
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gratitude_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            label TEXT NOT NULL,
            title TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    connection.commit()

    if request.method == "POST":
        entry_date = request.form.get("entry_date", "").strip()
        label = request.form.get("label", "small-joys").strip()
        title = request.form.get("title", "").strip()
        note = request.form.get("note", "").strip()

        if label not in GRATITUDE_LABELS:
            label = "small-joys"

        try:
            datetime.strptime(entry_date, "%Y-%m-%d")
        except ValueError:
            entry_date = ""

        if not entry_date or not note:
            connection.close()
            return redirect(url_for(
                "gratitude",
                error="Choose a date and write something you are grateful for."
            ))

        if not title:
            title = note[:48].rstrip()
            if len(note) > 48:
                title += "…"

        connection.execute(
            """
            INSERT INTO gratitude_entries (
                user_id, entry_date, label, title, note
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (session["user_id"], entry_date, label, title, note)
        )
        connection.commit()
        connection.close()
        return redirect(url_for("gratitude", saved="Added to your notebook."))

    entries = connection.execute(
        """
        SELECT * FROM gratitude_entries
        WHERE user_id = ?
        ORDER BY entry_date DESC, id DESC
        """,
        (session["user_id"],)
    ).fetchall()
    connection.close()

    return render_template(
        "gratitude.html",
        name=session["first_name"],
        entries=entries,
        labels=GRATITUDE_LABELS,
        today=planet_now().date().isoformat(),
        saved=request.args.get("saved"),
        error=request.args.get("error")
    )


@app.route("/gratitude/<int:entry_id>/edit", methods=["POST"])
def edit_gratitude(entry_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    entry_date = request.form.get("entry_date", "").strip()
    label = request.form.get("label", "small-joys").strip()
    title = request.form.get("title", "").strip()
    note = request.form.get("note", "").strip()

    if label not in GRATITUDE_LABELS:
        label = "small-joys"

    try:
        datetime.strptime(entry_date, "%Y-%m-%d")
    except ValueError:
        entry_date = ""

    if not entry_date or not note:
        return redirect(url_for("gratitude", error="Date and note are required."))

    if not title:
        title = note[:48].rstrip() + ("…" if len(note) > 48 else "")

    connection = sqlite3.connect("planet.db")
    connection.execute(
        """
        UPDATE gratitude_entries
        SET entry_date = ?, label = ?, title = ?, note = ?
        WHERE id = ? AND user_id = ?
        """,
        (entry_date, label, title, note, entry_id, session["user_id"])
    )
    connection.commit()
    connection.close()
    return redirect(url_for("gratitude", saved="Gratitude entry updated."))


@app.route("/gratitude/<int:entry_id>/delete", methods=["POST"])
def delete_gratitude(entry_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.execute(
        "DELETE FROM gratitude_entries WHERE id = ? AND user_id = ?",
        (entry_id, session["user_id"])
    )
    connection.commit()
    connection.close()
    return redirect(url_for("gratitude", saved="Gratitude entry deleted."))


@app.context_processor
def planet_appearance():
    if "user_id" not in session:
        return {}

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    try:
        preferences = connection.execute(
            "SELECT theme, compact_dashboard FROM user_settings WHERE user_id = ?",
            (session["user_id"],)
        ).fetchone()
    except (sqlite3.OperationalError, IndexError):
        preferences = None
    connection.close()

    return {
        "planet_theme": preferences["theme"] if preferences else "editorial",
        "planet_compact": bool(preferences["compact_dashboard"]) if preferences else False
    }


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "user_id" not in session:
        return redirect(url_for("home"))

    user_id = session["user_id"]
    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

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
            connection.close()
            return redirect(url_for("settings", error="Please complete every required setting."))

        duplicate_email = connection.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?",
            (email, user_id)
        ).fetchone()
        if duplicate_email:
            connection.close()
            return redirect(url_for("settings", error="That email is already connected to another account."))

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
        connection.close()
        session["first_name"] = first_name
        return redirect(url_for("settings", saved="Settings saved."))

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
    connection.close()

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


@app.route("/settings/change-password", methods=["POST"])
def change_password():
    if "user_id" not in session:
        return redirect(url_for("home"))

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    def security_redirect(message, is_error=False):
        key = "security_error" if is_error else "security_saved"
        return redirect(url_for("settings", **{key: message}) + "#security")

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

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row
    user = connection.execute(
        "SELECT password_hash FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    if user is None or not check_password_hash(
        user["password_hash"], current_password
    ):
        connection.close()
        return security_redirect("Your current password is incorrect.", True)

    if check_password_hash(user["password_hash"], new_password):
        connection.close()
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
    connection.close()

    return security_redirect("Password changed successfully.")


@app.route("/settings/quick-links/add", methods=["POST"])
def add_quick_link():
    if "user_id" not in session:
        return redirect(url_for("home"))

    name = request.form.get("name", "").strip()
    link_url = request.form.get("url", "").strip()
    category = request.form.get("category", "other").strip()
    allowed_categories = {"academics", "learning", "email", "library", "other"}

    if category not in allowed_categories:
        category = "other"

    parsed_url = urlparse(link_url)
    if not name or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return redirect(url_for(
            "settings",
            error="Add a link name and a complete website address beginning with https://."
        ))

    connection = sqlite3.connect("planet.db")
    connection.execute(
        """
        INSERT INTO quick_links (user_id, name, url, category)
        VALUES (?, ?, ?, ?)
        """,
        (session["user_id"], name, link_url, category)
    )
    connection.commit()
    connection.close()
    return redirect(url_for("settings", saved="Quick link added.") + "#quick-links")


@app.route("/settings/quick-links/<int:link_id>/delete", methods=["POST"])
def delete_quick_link(link_id):
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.execute(
        "DELETE FROM quick_links WHERE id = ? AND user_id = ?",
        (link_id, session["user_id"])
    )
    connection.commit()
    connection.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"success": True}

    return redirect(url_for("settings", saved="Quick link removed.") + "#quick-links")

if __name__ == "__main__":
 app.run(debug=True, port=5001)