"""Imprudent."""

from datetime import datetime
import os
import uuid

from flask import (
    Flask,
    jsonify,
    request,
)
from flask_sqlalchemy import SQLAlchemy
from pq import PQ
from psycopg2 import connect
from sqlalchemy.dialects.postgresql import UUID

app = Flask(__name__)
app.config[
    'SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres@localhost/judicious'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
db.create_all()

# Create the todo queue.
conn = connect('dbname=judicious user=postgres')
pq = PQ(conn)
todo_queue = pq['tasks']


def timenow():
    """Represent the current date and time."""
    return datetime.now()


class Task(db.Model):
    """A task to be completed by a judicious participant."""

    id = db.Column(UUID, primary_key=True, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=timenow)
    name = db.Column(db.String(64), nullable=False)
    result = db.Column(db.String(128))
    in_progress = db.Column(db.Boolean(), default=False)

    def __init__(self, id, name):
        self.id = id
        self.name = name

    def __repr__(self):
        return '<Task %r>' % self.name


@app.route('/')
def hello_world():
    """Sample route."""
    return 'Hello, World!'


@app.route('/task', methods=['POST'], defaults={'id': str(uuid.uuid4())})
@app.route('/task/<uuid:id>', methods=['POST'])
def post_task(id):
    """Add a new task to the queue."""
    name = request.values["name"]
    id_string = str(id)
    task_exists = Task.query.filter_by(id=id_string).count() > 0
    if not task_exists:
        app.logger.info("Creating task with id {}".format(id_string))
        task = Task(id_string, name)
        db.session.add(task)
        db.session.commit()
        todo_queue.put({"id": id_string})
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
                "name": name,
            }
        ), 409


@app.route('/task/<uuid:id>', methods=['PATCH'])
def patch_task(id):
    """Add a result to the given task."""
    result = request.values["result"]
    id_string = str(id)
    task = Task.query.filter_by(id=id_string).one()
    task.result = result
    task.in_progress = False
    db.session.add(task)
    db.session.commit()
    return jsonify(
        status="success",
        message="Result added.",
        data={
            "id": id,
        }
    ), 200


@app.route('/task/<uuid:id>', methods=['GET'])
def get_task_result(id):
    """Add a new task to the queue."""
    task = Task.query.filter_by(id=str(id)).one()
    if not task.in_progress:
        return jsonify(
            status="success",
            message="Task complete.",
            data={
                "id": task.id,
                "name": task.name,
                "result": task.result,
                "timestamp": task.timestamp,
            }
        ), 200
    else:
        return jsonify(
            status="success",
            message="Task is not yet complete.",
            data={
                "id": id,
            }
        ), 202


@app.route('/task', methods=['GET'])
def get_task_from_queue():
    """Get the next task from the queue."""
    id = todo_queue.get().data['id']
    task = Task.query.filter_by(id=id).one_or_none()
    task.in_progress = True
    db.session.add(task)
    db.session.commit()
    return jsonify(
        status="success",
        message="Task with id {}.".format(id),
        data={
            "id": id,
        }
    ), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, use_reloader=True)