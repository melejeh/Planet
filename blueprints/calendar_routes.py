from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db
from utils import planet_now

calendar_bp = Blueprint("calendar_bp", __name__)


@calendar_bp.route("/calendar", methods=["GET", "POST"])
def calendar():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

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

            return redirect(
                url_for("calendar_bp.calendar", week=event_date)
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

    study_blocks = connection.execute(
        """
        SELECT
            study_blocks.id,
            study_blocks.title,
            study_blocks.notes,
            study_blocks.scheduled_date AS event_date,
            study_blocks.start_time,
            study_blocks.end_time,
            study_blocks.status,
            courses.code AS course_code,
            courses.colour AS course_colour
        FROM study_blocks
        LEFT JOIN courses
          ON courses.id = study_blocks.course_id
        WHERE study_blocks.user_id = ?
          AND study_blocks.scheduled_date BETWEEN ? AND ?
          AND study_blocks.status IN ('planned', 'completed')
        ORDER BY study_blocks.scheduled_date, study_blocks.start_time
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

        for block in study_blocks:
            if block["event_date"] != day_date.isoformat():
                continue

            block_data = dict(block)
            start_hour, start_minute = map(
                int,
                block["start_time"].split(":")
            )
            end_hour, end_minute = map(
                int,
                block["end_time"].split(":")
            )
            start_total = start_hour * 60 + start_minute
            end_total = end_hour * 60 + end_minute
            allowed_colours = {"berry", "sage", "gold"}
            course_colour = (
                block["course_colour"] or "berry"
            ).strip().lower()
            if course_colour not in allowed_colours:
                course_colour = "berry"

            block_data.update({
                "source_type": "study_plan",
                "category": "study",
                "course_colour": course_colour,
                "top": max(0, (start_total - 6 * 60) / 60 * 42),
                "height": max(26, (end_total - start_total) / 60 * 42),
                "display_time": datetime.strptime(
                    block["start_time"], "%H:%M"
                ).strftime("%-I:%M %p")
            })
            day_events.append(block_data)

        day_events.sort(key=lambda item: item["start_time"])

        days.append({
            "date": day_date,
            "events": day_events
        })


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



@calendar_bp.route("/calendar/events/<int:event_id>/delete", methods=["POST"])
def delete_event(event_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()

    connection.execute(
        """
        DELETE FROM events
        WHERE id = ? AND user_id = ?
        """,
        (event_id, session["user_id"])
    )

    connection.commit()

    return redirect(request.referrer or url_for("calendar_bp.calendar"))



@calendar_bp.route("/calendar/events/<int:event_id>/edit", methods=["POST"])
def edit_event(event_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    title = request.form.get("title", "").strip()
    event_date = request.form.get("event_date", "")
    start_time = request.form.get("start_time", "")
    end_time = request.form.get("end_time", "")
    category = request.form.get("category", "personal")
    notes = request.form.get("notes", "").strip()

    if not title or not event_date or not start_time or end_time <= start_time:
        return redirect(url_for("calendar_bp.calendar", week=event_date or None))

    connection = get_db()
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

    return redirect(url_for("calendar_bp.calendar", week=event_date))



@calendar_bp.route("/calendar/events/<int:event_id>/move", methods=["POST"])
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

    connection = get_db()
    event = connection.execute(
        """
        SELECT start_time, end_time
        FROM events
        WHERE id = ? AND user_id = ?
        """,
        (event_id, session["user_id"])
    ).fetchone()

    if event is None:
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

    return {"success": True}
