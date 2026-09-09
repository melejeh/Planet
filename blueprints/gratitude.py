from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for

from db import get_db
from utils import planet_now

gratitude_bp = Blueprint("gratitude", __name__)

GRATITUDE_LABELS = {
    "people": "♡ People",
    "small-joys": "☕ Small joys",
    "proud": "✦ Proud of me",
    "moments": "☀ Little moments",
    "faith": "🙏 Faith"
}


@gratitude_bp.route("/gratitude", methods=["GET", "POST"])
def gratitude():
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
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
            return redirect(url_for(
                "gratitude.gratitude",
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
        return redirect(url_for("gratitude.gratitude", saved="Added to your notebook."))

    entries = connection.execute(
        """
        SELECT * FROM gratitude_entries
        WHERE user_id = ?
        ORDER BY entry_date DESC, id DESC
        """,
        (session["user_id"],)
    ).fetchall()

    return render_template(
        "gratitude.html",
        name=session["first_name"],
        entries=entries,
        labels=GRATITUDE_LABELS,
        today=planet_now().date().isoformat(),
        saved=request.args.get("saved"),
        error=request.args.get("error")
    )



@gratitude_bp.route("/gratitude/<int:entry_id>/edit", methods=["POST"])
def edit_gratitude(entry_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

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
        return redirect(url_for("gratitude.gratitude", error="Date and note are required."))

    if not title:
        title = note[:48].rstrip() + ("…" if len(note) > 48 else "")

    connection = get_db()
    connection.execute(
        """
        UPDATE gratitude_entries
        SET entry_date = ?, label = ?, title = ?, note = ?
        WHERE id = ? AND user_id = ?
        """,
        (entry_date, label, title, note, entry_id, session["user_id"])
    )
    connection.commit()
    return redirect(url_for("gratitude.gratitude", saved="Gratitude entry updated."))



@gratitude_bp.route("/gratitude/<int:entry_id>/delete", methods=["POST"])
def delete_gratitude(entry_id):
    if "user_id" not in session:
        return redirect(url_for("core.home"))

    connection = get_db()
    connection.execute(
        "DELETE FROM gratitude_entries WHERE id = ? AND user_id = ?",
        (entry_id, session["user_id"])
    )
    connection.commit()
    return redirect(url_for("gratitude.gratitude", saved="Gratitude entry deleted."))
