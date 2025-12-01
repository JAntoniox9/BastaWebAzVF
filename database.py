from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.types import TypeDecorator
import json

db = SQLAlchemy()

class JSONType(TypeDecorator):
    impl = LONGTEXT
    def process_bind_param(self, value, dialect):
        return json.dumps(value) if value is not None else None
    def process_result_value(self, value, dialect):
        return json.loads(value) if value is not None else None

class SalaDB(db.Model):
    __tablename__ = 'salas'
    codigo = db.Column(db.String(10), primary_key=True)
    datos = db.Column(JSONType)

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()

