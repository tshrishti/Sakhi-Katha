import os, time
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, JSON, select
from sqlalchemy.orm import declarative_base, sessionmaker
import engine as E

URL = os.getenv("DATABASE_URL") or "sqlite:///sakhi.db"
if URL.startswith("postgres://"): URL = URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(URL, connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {})
S = sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True); name = Column(String, nullable=False)
    village = Column(String); block = Column(String); district = Column(String)

class User(Base):                      # role: buyer | seller (SHG member) | leader | officer (mission/federation, aggregate only)
    __tablename__ = "users"
    id = Column(Integer, primary_key=True); phone = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False); role = Column(String, nullable=False); group_id = Column(Integer)
    skills = Column(JSON, default=list); capacity = Column(Integer, default=0)
    active = Column(Boolean, default=True); consent_at = Column(Float); lang = Column(String, default="en")

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True); group_id = Column(Integer, nullable=False); buyer_id = Column(Integer, nullable=False)
    status = Column(String, nullable=False); data = Column(JSON, nullable=False); created = Column(Float)

class LedgerEvent(Base):
    __tablename__ = "ledger"
    id = Column(Integer, primary_key=True); i = Column(Integer, unique=True, nullable=False)
    type = Column(String); actor = Column(String); order_id = Column(Integer); payload = Column(JSON)
    ts = Column(Float); prev = Column(String); hash = Column(String)

class Otp(Base):
    __tablename__ = "otps"
    phone = Column(String, primary_key=True); code_hash = Column(String); expires = Column(Float)
    attempts = Column(Integer, default=0); sent = Column(Integer, default=0); window_start = Column(Float)

class Convo(Base):                     # where each WhatsApp number is in the guided conversation (survives restarts)
    __tablename__ = "convos"
    phone = Column(String, primary_key=True); step = Column(String); data = Column(JSON, default=dict); ts = Column(Float)

class DBLedger:
    """Persistent, append-only, hash-chained audit log. Same chain rules as engine.Ledger."""
    def append(self, type_, payload, actor, order_id=None, s=None):
        own = s is None; s = s or S()
        try:
            last = s.scalars(select(LedgerEvent).order_by(LedgerEvent.i.desc())).first()
            body = {"i": last.i + 1 if last else 0, "type": type_, "actor": actor, "payload": payload,
                    "ts": round(time.time(), 2), "prev": last.hash if last else "0" * 64, "order_id": order_id}
            s.add(LedgerEvent(**body, hash=E.Ledger._h(body))); s.flush()
            if own: s.commit()
        finally:
            if own: s.close()
    def verify(self):
        with S() as s:
            prev, rows = "0" * 64, s.scalars(select(LedgerEvent).order_by(LedgerEvent.i)).all()
            for r in rows:
                body = {"i": r.i, "type": r.type, "actor": r.actor, "payload": r.payload, "ts": r.ts, "prev": r.prev, "order_id": r.order_id}
                if r.prev != prev or E.Ledger._h(body) != r.hash: return {"valid": False, "broken_at": r.i}
                prev = r.hash
            return {"valid": True, "broken_at": None, "events": len(rows)}

def ensure_columns():
    """create_all never alters existing tables: add columns introduced after the first deploy."""
    from sqlalchemy import inspect, text
    try:
        if "lang" not in {c["name"] for c in inspect(engine).get_columns("users")}:
            with engine.begin() as c: c.execute(text("ALTER TABLE users ADD COLUMN lang VARCHAR DEFAULT 'en'"))
    except Exception: pass
