from app import app
from models import Professor, db
with app.app_context():
    prof = Professor.query.filter_by(email='jonatansilva3697@gmail.com').first()
    print('Found:', bool(prof))
    if prof:
        print('Before:', prof.is_premium, prof.premium_expires_at)
        prof.is_premium = False
        prof.premium_expires_at = None
        db.session.commit()
        print('Removed premium from user')
    else:
        print('Professor not found')
