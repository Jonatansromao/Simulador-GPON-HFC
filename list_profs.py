from app import app
from models import Professor
with app.app_context():
    prots = Professor.query.filter(Professor.email.ilike("%jonatan%" )).all()
    print("Count:", len(prots))
    for p in prots:
        print(p.email, p.is_premium, p.premium_expires_at)
