from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db
from utils import planet_now

tasks_bp = Blueprint("tasks", __name__)


@tasks_bp.route("/todo", methods=["GET", "POST"])
def todo():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

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


        return redirect(url_for("tasks.todo"))

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



@tasks_bp.route("/tasks/<int:task_id>/toggle", methods=["POST"])
def toggle_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

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

    return redirect(url_for("tasks.todo"))



@tasks_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

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

    return redirect(url_for("tasks.todo"))



@tasks_bp.route(
    "/tasks/<int:task_id>/edit",
    methods=["GET", "POST"]
)
def edit_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

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
        return redirect(url_for("tasks.todo"))

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

        return redirect(url_for("tasks.todo"))

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


    return render_template(
        "edit_task.html",
        name=session["first_name"],
        task=task,
        courses=courses
    )
