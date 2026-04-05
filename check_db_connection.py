from models import Professor
from app import app
with app.app_context():
    print('professores:', Professor.query.count())
