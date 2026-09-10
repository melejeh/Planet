from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db
from utils import planet_now

study_plan_bp = Blueprint("study_plan", __name__)


@study_plan_bp.route("/study-plan", methods=["GET", "POST"])
def study_plan():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    user_id = session["user_id"]

    connection = get_db()

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
            source TEXT NOT NULL DEFAULT 'manual',
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
    if "source" not in study_block_columns:
        connection.execute(
            "ALTER TABLE study_blocks "
            "ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
        # Preserve regenerate support for plans created before the source
        # column existed. These exact notes are only written by Planet.
        connection.execute(
            """
            UPDATE study_blocks
            SET source = 'generated'
            WHERE source = 'manual'
              AND (
                    notes LIKE 'Priority session for %.%'
                    OR notes LIKE 'Focused study time for %.%'
                  )
            """
        )
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

            return redirect(
                url_for(
                    "study_plan.study_plan",
                    error="Please complete all study preferences."
                )
            )

        if earliest_time >= latest_time:

            return redirect(
                url_for(
                    "study_plan.study_plan",
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

        return redirect(
            url_for(
                "study_plan.study_plan",
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

    _reschedule_missed_sessions(
        connection, user_id, settings, _get_user_settings(connection, user_id),
        planet_now()
    )

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

    today = planet_now().date()
    window_end = today + timedelta(days=6)
    has_generated_plan = connection.execute(
        """
        SELECT 1
        FROM study_blocks
        WHERE user_id = ?
          AND source = 'generated'
          AND status = 'planned'
          AND scheduled_date BETWEEN ? AND ?
        LIMIT 1
        """,
        (user_id, today.isoformat(), window_end.isoformat())
    ).fetchone() is not None

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
        has_generated_plan=has_generated_plan,
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


def _get_user_settings(connection, user_id):
    """Fetch the reschedule/grade-priority toggles from user_settings,
    defaulting to enabled (matching the table's own column defaults) for a
    user who has never saved a Settings form yet."""
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
    row = connection.execute(
        "SELECT reschedule_missed, grade_priority FROM user_settings WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    if row is None:
        return {"reschedule_missed": 1, "grade_priority": 1}
    return {
        "reschedule_missed": row["reschedule_missed"],
        "grade_priority": row["grade_priority"]
    }


def _course_grade_boosts(connection, user_id, active_semester_id):
    """When 'Use grade targets to prioritize courses' is on, courses sitting
    further below a strong grade get a bigger priority boost. Courses with
    nothing graded yet get no boost -- there's no signal yet that they need
    extra attention."""
    rows = connection.execute(
        """
        SELECT
            courses.id AS course_id,
            ROUND(
                CAST(SUM(
                    CASE WHEN assessments.score IS NOT NULL
                         THEN assessments.score * assessments.weight
                         ELSE 0 END
                ) AS REAL)
                / NULLIF(SUM(
                    CASE WHEN assessments.score IS NOT NULL
                         THEN assessments.weight ELSE 0 END
                ), 0),
                1
            ) AS current_grade
        FROM courses
        JOIN semesters ON semesters.id = courses.semester_id
        LEFT JOIN assessments ON assessments.course_id = courses.id
        WHERE semesters.user_id = ? AND courses.semester_id = ?
        GROUP BY courses.id
        """,
        (user_id, active_semester_id)
    ).fetchall()

    boosts = {}
    for row in rows:
        if row["current_grade"] is None:
            boosts[row["course_id"]] = 0
        else:
            # 90%+ -> no boost. Roughly one extra point for every 10 points
            # below 90, capped so a struggling course still shares the week
            # with everything else rather than eating the whole plan.
            boosts[row["course_id"]] = max(
                0, min(4, round((90 - row["current_grade"]) / 10))
            )
    return boosts


def _find_next_slot(day, cursor, latest, session_minutes, remaining_minutes, occupied):
    """Find the next open, non-conflicting slot on `day` at or after
    `cursor`. Returns (start, end) in minutes-since-midnight, or None if the
    day has no more room before `latest` (or the budget is spent)."""
    slot_start = cursor
    while slot_start + session_minutes <= latest and remaining_minutes > 0:
        slot_end = slot_start + min(session_minutes, remaining_minutes)
        conflicts = any(
            slot_start < busy_end and slot_end > busy_start
            for busy_start, busy_end in occupied.get(day.isoformat(), [])
        )
        if conflicts:
            slot_start += 15
            continue
        return slot_start, slot_end
    return None


def _reschedule_missed_sessions(connection, user_id, settings, user_settings, now):
    """Any 'planned' session whose time has already passed becomes 'missed'.
    If the user has 'Reschedule missed study blocks' turned on, immediately
    try to book a fresh slot for it later in the current 7-day window."""
    today = now.date()
    current_time_str = now.strftime("%H:%M")

    overdue = connection.execute(
        """
        SELECT * FROM study_blocks
        WHERE user_id = ? AND status = 'planned'
          AND (scheduled_date < ?
               OR (scheduled_date = ? AND end_time <= ?))
        """,
        (user_id, today.isoformat(), today.isoformat(), current_time_str)
    ).fetchall()

    if not overdue:
        return

    connection.execute(
        """
        UPDATE study_blocks SET status = 'missed'
        WHERE user_id = ? AND status = 'planned'
          AND (scheduled_date < ?
               OR (scheduled_date = ? AND end_time <= ?))
        """,
        (user_id, today.isoformat(), today.isoformat(), current_time_str)
    )

    if not user_settings["reschedule_missed"]:
        connection.commit()
        return

    window_end = today + timedelta(days=6)
    active_blocks = connection.execute(
        """
        SELECT scheduled_date, start_time, end_time FROM study_blocks
        WHERE user_id = ? AND scheduled_date BETWEEN ? AND ?
          AND status IN ('planned', 'completed')
        """,
        (user_id, today.isoformat(), window_end.isoformat())
    ).fetchall()
    calendar_events = connection.execute(
        """
        SELECT event_date AS scheduled_date, start_time, end_time FROM events
        WHERE user_id = ? AND event_date BETWEEN ? AND ?
        """,
        (user_id, today.isoformat(), window_end.isoformat())
    ).fetchall()

    occupied = {}
    for item in list(active_blocks) + list(calendar_events):
        try:
            start = _time_to_minutes(item["start_time"])
            end = _time_to_minutes(item["end_time"])
        except (TypeError, ValueError):
            continue
        occupied.setdefault(item["scheduled_date"], []).append((start, end))

    earliest = _time_to_minutes(settings["earliest_time"])
    latest = _time_to_minutes(settings["latest_time"])
    available_days = {
        day.strip().lower()
        for day in settings["available_days"].split(",")
        if day.strip()
    }
    if not settings["include_weekends"]:
        available_days -= {"saturday", "sunday"}

    candidate_days = [
        today + timedelta(days=d) for d in range(7)
        if (today + timedelta(days=d)).strftime("%A").lower() in available_days
    ]

    for block in overdue:
        session_minutes = _time_to_minutes(block["end_time"]) - _time_to_minutes(block["start_time"])
        if session_minutes <= 0:
            continue

        placed = False
        for day in candidate_days:
            day_start = earliest
            if day == today:
                current_minutes = now.hour * 60 + now.minute
                day_start = max(day_start, ((current_minutes + 14) // 15) * 15)

            slot = _find_next_slot(
                day, day_start, latest, session_minutes,
                session_minutes, occupied
            )
            if slot is None:
                continue

            slot_start, slot_end = slot
            connection.execute(
                """
                INSERT INTO study_blocks (
                    user_id, course_id, assessment_id, title, notes,
                    scheduled_date, start_time, end_time, status, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    user_id, block["course_id"], block["assessment_id"],
                    block["title"], block["notes"],
                    day.isoformat(),
                    _minutes_to_time(slot_start),
                    _minutes_to_time(slot_end),
                    block["source"] or "rescheduled"
                )
            )
            occupied.setdefault(day.isoformat(), []).append((slot_start, slot_end))
            placed = True
            break

        # If no open slot was found anywhere in the window, the session
        # simply stays 'missed' -- nothing more Planet can safely do without
        # a spot that respects the user's own availability.
        del placed

    connection.commit()


@study_plan_bp.route("/study-plan/generate", methods=["POST"])
def generate_study_plan():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    user_id = session["user_id"]
    active_semester_id = session.get("active_semester_id")

    if not active_semester_id:
        return redirect(url_for(
            "study_plan.study_plan",
            error="Choose an active semester before generating a plan."
        ))

    connection = get_db()

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
        return redirect(url_for(
            "study_plan.study_plan",
            error="Add at least one course before generating a plan."
        ))

    today = planet_now().date()
    window_end = today + timedelta(days=6)
    replace_generated = request.form.get("replace_generated") == "1"

    if replace_generated:
        connection.execute(
            """
            DELETE FROM study_blocks
            WHERE user_id = ?
              AND source = 'generated'
              AND status = 'planned'
              AND scheduled_date BETWEEN ? AND ?
            """,
            (user_id, today.isoformat(), window_end.isoformat())
        )
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
    user_settings = _get_user_settings(connection, user_id)
    grade_boosts = (
        _course_grade_boosts(connection, user_id, active_semester_id)
        if user_settings["grade_priority"]
        else {}
    )

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

        # "Use grade targets to prioritize courses": a course sitting well
        # below a strong grade gets extra priority points on top of the
        # usual urgency/weight scoring.
        grade_boost = grade_boosts.get(assessment["course_id"], 0)

        plan_targets.append({
            "course_id": assessment["course_id"],
            "assessment_id": assessment["id"],
            "title": f"Study for {assessment['name']}",
            "notes": f"Priority session for {assessment['course_code']}.",
            "due_date": assessment["due_date"],
            "priority_score": urgency_points + weight_points + grade_boost
        })

    if not plan_targets:
        for course in courses:
            plan_targets.append({
                "course_id": course["id"],
                "assessment_id": None,
                "title": f"{course['code']} study session",
                "notes": f"Focused study time for {course['name']}.",
                "due_date": None,
                "priority_score": 1 + grade_boosts.get(course["id"], 0)
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

    def _target_key(target):
        return (
            ("assessment", target["assessment_id"])
            if target["assessment_id"] is not None
            else ("course", target["course_id"])
        )

    generated = []
    target_session_counts = {_target_key(target): 0 for target in plan_targets}
    now = planet_now()

    available_dates = [
        today + timedelta(days=d) for d in range(7)
        if (today + timedelta(days=d)).strftime("%A").lower() in available_days
    ]

    day_cursors = {}
    for study_date in available_dates:
        slot_start = earliest
        if study_date == today:
            current_minutes = now.hour * 60 + now.minute
            slot_start = max(slot_start, ((current_minutes + 14) // 15) * 15)
        day_cursors[study_date] = slot_start

    # Spread sessions round-robin across the available days instead of
    # filling one day completely before moving to the next: each pass tries
    # to give every day that still has room one more session, so a full
    # weekly target doesn't all land on the very first available day.
    active_dates = list(available_dates)
    while remaining_minutes > 0 and active_dates:
        made_progress = False

        for study_date in list(active_dates):
            if remaining_minutes <= 0:
                break

            slot = _find_next_slot(
                study_date, day_cursors[study_date], latest,
                session_minutes, remaining_minutes, occupied
            )
            if slot is None:
                active_dates.remove(study_date)
                continue

            slot_start, slot_end = slot

            eligible_targets = [
                target for target in plan_targets
                if target["due_date"] is None
                or study_date.isoformat() <= target["due_date"]
            ]
            if not eligible_targets:
                active_dates.remove(study_date)
                continue

            # A target's effective score falls each time it receives a
            # session. This creates a weighted, fair rotation: urgent,
            # high-value, and (if enabled) grade-struggling targets receive
            # more sessions, while everything eligible still gets a turn.
            target = max(
                eligible_targets,
                key=lambda item: (
                    item["priority_score"]
                    / (target_session_counts[_target_key(item)] + 1),
                    (
                        -datetime.strptime(
                            item["due_date"], "%Y-%m-%d"
                        ).date().toordinal()
                        if item["due_date"] else 0
                    ),
                    item["priority_score"]
                )
            )
            target_session_counts[_target_key(target)] += 1

            connection.execute(
                """
                INSERT INTO study_blocks (
                    user_id, course_id, assessment_id, title, notes,
                    scheduled_date, start_time, end_time, status, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', 'generated')
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
            day_cursors[study_date] = slot_end + break_minutes
            made_progress = True

        if not made_progress:
            break

    connection.commit()

    if not generated:
        if remaining_minutes == 0:
            message = "Your study plan already meets this week's target."
        else:
            message = "Planet could not find an open time. Adjust your study preferences and try again."
        return redirect(url_for("study_plan.study_plan", error=message))

    session_word = "session" if len(generated) == 1 else "sessions"
    return redirect(url_for(
        "study_plan.study_plan",
        saved=f"Planet generated {len(generated)} study {session_word}."
    ) + "#planned-sessions")



@study_plan_bp.route("/study-plan/sessions/add", methods=["POST"])
def add_study_block():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

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
            "study_plan.study_plan",
            error="Add a title, date, start time and end time."
        ))

    if start_time >= end_time:
        return redirect(url_for(
            "study_plan.study_plan",
            error="The session end time must be later than its start time."
        ))

    connection = get_db()

    if course_id and not _owned_study_plan_course(
        connection, course_id, user_id
    ):
        return redirect(url_for("study_plan.study_plan", error="That course is unavailable."))

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
            return redirect(url_for(
                "study_plan.study_plan",
                error="That assessment is unavailable."
            ))

        if course_id and assessment["course_id"] != course_id:
            return redirect(url_for(
                "study_plan.study_plan",
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

    return redirect(url_for("study_plan.study_plan", saved="Study session added."))



@study_plan_bp.route("/study-plan/sessions/<int:block_id>/edit", methods=["POST"])
def edit_study_block(block_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    user_id = session["user_id"]
    title = request.form.get("title", "").strip()
    notes = request.form.get("notes", "").strip()
    scheduled_date = request.form.get("scheduled_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    course_id = request.form.get("course_id", type=int)
    assessment_id = request.form.get("assessment_id", type=int)

    if not title or not scheduled_date or not start_time or not end_time:
        return redirect(url_for("study_plan.study_plan", error="Complete every required field."))

    if start_time >= end_time:
        return redirect(url_for(
            "study_plan.study_plan",
            error="The session end time must be later than its start time."
        ))

    connection = get_db()

    block = connection.execute(
        "SELECT id FROM study_blocks WHERE id = ? AND user_id = ?",
        (block_id, user_id)
    ).fetchone()

    if block is None:
        return redirect(url_for("study_plan.study_plan", error="Study session not found."))

    if course_id and not _owned_study_plan_course(
        connection, course_id, user_id
    ):
        return redirect(url_for("study_plan.study_plan", error="That course is unavailable."))

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
            return redirect(url_for(
                "study_plan.study_plan",
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

    return redirect(url_for("study_plan.study_plan", saved="Study session updated."))



@study_plan_bp.route("/study-plan/sessions/<int:block_id>/status", methods=["POST"])
def update_study_block_status(block_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    status = request.form.get("status", "planned")
    if status not in {"planned", "completed", "missed"}:
        status = "planned"

    connection = get_db()
    connection.execute(
        "UPDATE study_blocks SET status = ? WHERE id = ? AND user_id = ?",
        (status, block_id, session["user_id"])
    )
    connection.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {
            "success": True,
            "status": status
        }

    return redirect(
        url_for("study_plan.study_plan", saved="Session status updated.")
        + "#planned-sessions"
    )



@study_plan_bp.route("/study-plan/sessions/<int:block_id>/delete", methods=["POST"])
def delete_study_block(block_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    cursor = connection.execute(
        "DELETE FROM study_blocks WHERE id = ? AND user_id = ?",
        (block_id, session["user_id"])
    )
    connection.commit()

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
        url_for("study_plan.study_plan", saved="Study session deleted.")
        + "#planned-sessions"
    )



@study_plan_bp.route("/study-plan/sessions/delete-selected", methods=["POST"])
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
    connection = get_db()
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


    return {
        "success": True,
        "deleted_ids": deleted_ids
    }