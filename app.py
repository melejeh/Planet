import sqlite3
from datetime import datetime, timedelta
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
app.secret_key = "planet-development-key"


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

        return redirect(url_for("dashboard"))

    return render_template("signup.html")



@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("home"))

    connection = sqlite3.connect("planet.db")
    connection.row_factory = sqlite3.Row

    today = datetime.now().date()
    now = datetime.now()

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
        today_full=now.strftime(
            "%A, %B %-d, %Y"
        )
    )

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

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
            >= datetime.now().date().isoformat()
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
        ).date() if requested_date else datetime.now().date()
    except ValueError:
        selected_date = datetime.now().date()

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
            pixels_per_hour = 32

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
        today=datetime.now().date(),
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

    today = datetime.now().date().isoformat()
    current_time = datetime.now().strftime("%H:%M")

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
    now = datetime.now()
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
            courses.name

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

        SET completed =
            CASE
                WHEN completed = 0 THEN 1
                ELSE 0
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

    return render_template(
        "edit_task.html",
        name=session["first_name"],
        task=task,
        courses=courses
    )

if __name__ == "__main__":
 app.run(debug=True, port=5001)