"""Imprudent."""

from datetime import datetime, timedelta
import json
import os
import random
import uuid

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from pq import PQ
from psycopg2 import connect
import requests
from sqlalchemy.dialects.postgresql import UUID

app = Flask(__name__)

app.secret_key = os.environ["JUDICIOUS_SECRET_KEY"]

DB_URL_DEFAULT = 'postgresql://postgres@localhost/judicious'
DB_URL = os.environ.get("DATABASE_URL", DB_URL_DEFAULT)
app.config['SQLALCHEMY_DATABASE_URI'] = DB_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Create the todo queue.
conn = connect(DB_URL)
pq = PQ(conn)
todo_queue = pq['tasks']


class Task(db.Model):
    """A task to be completed by a judicious participant."""

    __tablename__ = "task"

    id = db.Column(UUID, primary_key=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    last_queued_at = db.Column(db.DateTime)
    type = db.Column(db.String(64), nullable=False)
    parameters = db.Column(db.JSON)
    person_id = db.Column(UUID, db.ForeignKey('person.id'))
    context_id = db.Column(UUID, db.ForeignKey('context.id'), nullable=False)
    last_started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    result = db.Column(db.String(2**16))

    def __init__(self, id, context_id, type, parameters=None):
        self.id = id
        self.context_id = context_id
        self.type = type
        self.parameters = parameters

    def __repr__(self):
        return '<Task %r>' % self.type

    def timeout(self):
        """Placed timed-out tasks back on the queue."""
        app.logger.info("Timeout on task {}".format(self.id))
        self.last_started_at = None
        self.last_queued_at = datetime.now()
        db.session.add(self)
        db.session.commit()
        todo_queue.put({"id": self.id})


class Person(db.Model):
    """An identity to be claimed by a judicious participant."""

    __tablename__ = "person"

    id = db.Column(UUID, primary_key=True, nullable=False)
    context_id = db.Column(UUID, db.ForeignKey('context.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    claimed_at = db.Column(db.DateTime)
    tasks = db.relationship("Task", backref='person')

    def __init__(self, id, context_id):
        self.id = id
        self.context_id = context_id

    def __repr__(self):
        return '<Person %r>' % self.id


class Context(db.Model):
    """A particular run of a script."""

    def __init__(self, id):
        self.id = id

    id = db.Column(UUID, primary_key=True, nullable=False)
    tasks = db.relationship("Task", backref='context')
    persons = db.relationship("Person", backref='context')


@app.route('/ad/')
def ad():
    """Index route."""
    assignmentId = request.values.get("assignmentId", None)
    if assignmentId == "ASSIGNMENT_ID_NOT_AVAILABLE":
        return render_template('ad_mturk.html')
    elif assignmentId:
        return redirect(url_for('consent', **request.args))
    else:
        return render_template('index.html')


@app.route('/consent/', methods=['GET'])
def consent():
    """Render the consent form."""
    if session.get('JUDICIOUS_CONSENTED', False) is True:
        return redirect(url_for('stage', **request.args))
    else:
        return render_template('consent.html')


@app.route('/assent/', methods=['GET'])
def assent():
    """Assent to participanting in the experiment."""
    session['JUDICIOUS_CONSENTED'] = True
    return redirect(url_for('stage', **request.args))


@app.route('/persons/<uuid:id>', methods=['POST'])
def post_person(person_id):
    """Add a new person."""
    person = Person(person_id)
    db.session.add(person)
    db.session.commit()


@app.route('/tasks', methods=['POST'], defaults={'id': str(uuid.uuid4())})
@app.route('/tasks/<uuid:id>', methods=['POST'])
def post_task(id):
    """Add a new task to the queue."""
    task_type = request.values["type"]
    id_string = str(id)
    task_exists = Task.query.filter_by(id=id_string).count() > 0

    if not task_exists:
        app.logger.info("Creating task with id {}".format(id_string))
        app.logger.info("Context is {}".format(request.values["context"]))

        # Create the context.
        context = Context(request.values["context"])
        db.session.add(context)
        db.session.commit()

        # Create the task.
        task = Task(
            id_string,
            request.values["context"],
            task_type,
            json.loads(request.values["parameters"]),
        )
        task.last_queued_at = datetime.now()

        # Check if the person exists, creating if necessary.
        person_id = request.values.get("person")
        if person_id and not Person.query.filter_by(id=person_id).one_or_none():
            person = Person(person_id, request.values["context"])
            db.session.add(person)
            db.session.commit()

        task.person_id = person_id
        db.session.add(task)
        db.session.commit()

        # Put it on the queue.
        priority = int(request.values.get("priority"))

        for i in range(int(os.environ.get("JUDICIOUS_REDUNDANCY", 1))):
            if priority > 0:
                expected_at = timedelta(seconds=i*20)
            else:
                expected_at = timedelta(days=random.randint(1, 365*10))

            todo_queue.put({"id": id_string}, expected_at=expected_at)

        return jsonify(
            status="success",
            message="Task posted.",
            data={
                "id": id_string,
            }
        ), 200

    else:
        app.logger.info("Task with id {} already exists".format(id))
        return jsonify(
            status="success",
            message="Already exists.",
            data={
                "id": id_string,
                "type": type,
            }
        ), 409


@app.route('/tasks/<uuid:id>', methods=['PATCH'])
def patch_task(id):
    """Add a result to the given task."""
    result = request.values["result"]
    id_string = str(id)
    task = Task.query.filter_by(id=id_string).one()
    if task.result:
        return jsonify(
            status="error",
            message="A result has already been added.",
            data={
                "id": id,
            }
        ), 409
    else:
        task.result = result
        task.finished_at = datetime.now()
        db.session.add(task)
        db.session.commit()
        return jsonify(
            status="success",
            message="Result added.",
            data={
                "id": id,
            }
        ), 200


@app.route('/tasks/<uuid:id>', methods=['GET'])
def get_task_result(id):
    """Get the result of a task."""
    task = Task.query.filter_by(id=str(id)).one_or_none()
    if not task:
        return jsonify(
            status="success",
            message="No task with id {} found.".format(id),
            data={
                "id": id,
            }
        ), 404
    elif not task.result:
        return jsonify(
            status="success",
            message="Task is not yet complete.",
            data={
                "id": id,
            }
        ), 202
    else:
        return jsonify(
            status="success",
            message="Task complete.",
            data={
                "id": task.id,
                "type": task.type,
                "result": task.result,
                "created_at": task.created_at,
                "last_started_at": task.last_started_at,
                "last_queued_at": task.last_queued_at,
                "finished_at": task.finished_at,
            }
        ), 200


@app.route('/stage/', methods=['GET'])
def stage():
    """Serve the next task."""
    next_task = todo_queue.get()
    if next_task:
        task = Task.query.filter_by(id=next_task.data['id']).one_or_none()
        task.last_started_at = datetime.now()
        db.session.add(task)
        db.session.commit()
        return render_template(
            "tasks/{}.html".format(task.type),
            id=task.id,
            parameters=task.parameters,
            RECAPTCHA_SITE_KEY=os.environ['RECAPTCHA_SITE_KEY']
        )
    else:
        return render_template("no_tasks.html")


@app.route('/recaptcha/', methods=['POST'])
def recaptcha():
    """Verify the recaptcha."""
    app.logger.info(request.values["response"])
    r = requests.post(
        'https://www.google.com/recaptcha/api/siteverify',
        data={
            'secret': os.environ['RECAPTCHA_SECRET_KEY'],
            'response': request.values["response"],
        }
    )
    return jsonify(
        status="success",
        message="Recaptcha passed.",
        data={
            "solved": r.json()['success'],
        }
    ), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, use_reloader=True)
