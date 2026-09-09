import os

from flask import Flask

from blueprints.auth import auth_bp
from blueprints.calendar_routes import calendar_bp
from blueprints.core import core_bp
from blueprints.courses import courses_bp
from blueprints.dashboard import dashboard_bp
from blueprints.focus import focus_bp
from blueprints.goals import goals_bp
from blueprints.gratitude import gratitude_bp
from blueprints.imports import imports_bp
from blueprints.settings import planet_appearance, settings_bp
from blueprints.study_plan import study_plan_bp
from blueprints.tasks import tasks_bp
from db import init_app as init_db
from utils import pretty_date

app = Flask(__name__)

# Used to protect Flask sessions during local development.
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "planet-local-development-key"
)

init_db(app)

app.register_blueprint(core_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(courses_bp)
app.register_blueprint(imports_bp)
app.register_blueprint(calendar_bp)
app.register_blueprint(tasks_bp)
app.register_blueprint(goals_bp)
app.register_blueprint(focus_bp)
app.register_blueprint(study_plan_bp)
app.register_blueprint(gratitude_bp)
app.register_blueprint(settings_bp)

app.template_filter("pretty_date")(pretty_date)
app.context_processor(planet_appearance)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
