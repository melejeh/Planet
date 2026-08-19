import sqlite3
from datetime import datetime
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

    semesters = connection.execute(
        """
        SELECT * FROM semesters
        WHERE user_id = ?
        ORDER BY start_date DESC
        """,
        (session["user_id"],)
    ).fetchall()

    connection.close()

    return render_template(
        "dashboard.html",
        name=session["first_name"],
        semesters=semesters
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
        SELECT * FROM courses
        WHERE semester_id = ?
        ORDER BY name
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
        (
            course_id,
        )
    ).fetchall()

    connection.close()

    completed_weight = 0
    weighted_points = 0
    next_due = None

    for assessment in assessments:
        if assessment["score"] is not None:
            completed_weight += assessment["weight"]

            weighted_points += (
                assessment["score"]
                * assessment["weight"]
            )

        elif (
            next_due is None
            and assessment["due_date"]
        ):
            next_due = assessment["due_date"]

    if completed_weight > 0:
        current_grade = (
            weighted_points / completed_weight
        )

        current_grade = round(current_grade, 1)

    else:
        current_grade = None

    remaining_weight = 100 - completed_weight

    target_grade = None
    required_grade = None

    if request.method == "POST":
        target_grade = float(
            request.form["target_grade"]
        )

        if remaining_weight > 0:
            required_grade = (
                (target_grade * 100)
                - weighted_points
            ) / remaining_weight

            required_grade = round(
                required_grade,
                1
            )

    completed_weight = round(
        completed_weight,
        1
    )

    remaining_weight = round(
        remaining_weight,
        1
    )

    return render_template(
        "course.html",
        name=session["first_name"],
        course=course,
        assessments=assessments,
        current_grade=current_grade,
        completed_weight=completed_weight,
        remaining_weight=remaining_weight,
        next_due=next_due,
        target_grade=target_grade,
        required_grade=required_grade
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


if __name__ == "__main__":
 app.run(debug=True, port=5000)
