"""First-run setup with REAL data, no fictional people. Creates the SHG group and the programme owner from environment variables.
OWNER_PHONE (e.g. 9315168344) becomes a registered user with OWNER_ROLE (default leader). Safe to run on every start: it only adds what is missing."""
import os
import db
from sqlalchemy import select

def owner_phone():
    from auth import norm
    p = os.getenv("OWNER_PHONE", "").strip()
    return norm(p) if p else None

def ensure():
    db.Base.metadata.create_all(db.engine); db.ensure_columns()
    with db.S() as s:
        g = s.scalars(select(db.Group)).first()
        if not g:
            g = db.Group(name=os.getenv("GROUP_NAME", "My SHG"), village=os.getenv("GROUP_VILLAGE", ""), block=os.getenv("GROUP_BLOCK", ""), district=os.getenv("GROUP_DISTRICT", ""))
            s.add(g); s.flush()
        phone = owner_phone()
        if phone and not s.scalars(select(db.User).where(db.User.phone == phone)).first():
            role = os.getenv("OWNER_ROLE", "leader")
            if role not in ("leader", "buyer", "seller", "officer"): role = "leader"
            s.add(db.User(phone=phone, name=os.getenv("OWNER_NAME", "Owner"), role=role, group_id=g.id if role in ("leader", "seller") else None,
                          skills=["cutting", "stitching", "finishing", "packing"] if role == "seller" else [], capacity=30 if role == "seller" else 0, lang=os.getenv("OWNER_LANG", "en")))
        s.commit()
