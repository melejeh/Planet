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

    active_semester = None
    courses = []
    next_assessment = None
    projected_average = None

    if session.get("active_semester_id") is not None:
        active_semester = connection.execute(
            """
            SELECT * FROM semesters
            WHERE id = ? AND user_id = ?
            """,
            (
                session["active_semester_id"],
                session["user_id"]
            )
        ).fetchone()

    semesters = connection.execute(
        """
        SELECT * FROM semesters
        WHERE user_id = ?
        ORDER BY start_date DESC
        """,
        (session["user_id"],)
    ).fetchall()

    if active_semester is not None:
        courses = connection.execute(
            """
            SELECT
                courses.*,

                ROUND(
                    SUM(
                        CASE
                            WHEN assessments.score IS NOT NULL
                            THEN assessments.score * assessments.weight
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
            (active_semester["id"],)
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

        next_assessment = connection.execute(
            """
            SELECT
                assessments.*,
                courses.name AS course_name
            FROM assessments

            JOIN courses
                ON courses.id = assessments.course_id

            WHERE courses.semester_id = ?
              AND assessments.due_date >= DATE('now')
              AND assessments.score IS NULL

            ORDER BY assessments.due_date
            LIMIT 1
            """,
            (active_semester["id"],)
        ).fetchone()

    connection.close()

    return render_template(
        "dashboard.html",
        name=session["first_name"],
        semesters=semesters,
        active_semester=active_semester,
        courses=courses,
        projected_average=projected_average,
        next_assessment=next_assessment,
        today_full=datetime.now().strftime("%A, %B %-d, %Y")
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

            # The visible calendar runs from 8:00 AM to 9:00 PM.
            calendar_start = 8 * 60
            pixels_per_hour = 64

            event_data["top"] = max(
                0,
                (start_total - calendar_start)
                / 60
                * pixels_per_hour
            )
            event_data["height"] = max(
                42,
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



if __name__ == "__main__":
 app.run(debug=True, port=5001)