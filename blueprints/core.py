from flask import Blueprint, render_template

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
def home():
    return render_template("index.html")


@core_bp.route("/about")
def about():
    return "Your planet helps students plan their semester."
