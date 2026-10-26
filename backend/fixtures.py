"""TEST-ONLY sample data used by the automated tests. Never imported by the running app."""
DATA = {
    "order_text": "Namaste didi, 200 bags chahiye, blue colour, Rs 60 each, 5 din mein de dena, same as last time.",
    "cost": {"material": 38, "minutes": 40, "hourly": 50, "overhead_pct": 8, "margin_pct": 10},
    "stages": [{"name": "cutting", "rate": 5, "workers": 1}, {"name": "stitching", "rate": 16, "workers": 2},
               {"name": "finishing", "rate": 8, "workers": 2}, {"name": "packing", "rate": 4, "workers": 1}],
    "members": [
        {"id": "m1", "name": "Shobha", "skills": ["cutting"], "capacity": 80},
        {"id": "m2", "name": "Lakshmi", "skills": ["stitching"], "capacity": 25},
        {"id": "m3", "name": "Savitha", "skills": ["stitching"], "capacity": 25},
        {"id": "m4", "name": "Geetha", "skills": ["finishing"], "capacity": 40},
        {"id": "m5", "name": "Padma", "skills": ["finishing", "stitching"], "capacity": 40},
        {"id": "m6", "name": "Rukmini", "skills": ["packing"], "capacity": 80}],
    "cost_args": {"material": 38, "minutes": 40, "hourly": 50, "overhead_pct": 8, "margin_pct": 10},
    "decliner": "m3",
    "rescue": {"done": 30, "needed": 100, "per_day": 12, "days_left": 2},
    "payout_lines": [
        {"member": "m2", "name": "Lakshmi", "stage": "stitching", "accepted": 96, "pending": 4, "rate": 16},
        {"member": "m1", "name": "Shobha", "stage": "cutting", "accepted": 200, "pending": 0, "rate": 5},
        {"member": "m4", "name": "Geetha", "stage": "finishing", "accepted": 100, "pending": 0, "rate": 8},
        {"member": "m6", "name": "Rukmini", "stage": "packing", "accepted": 200, "pending": 0, "rate": 4}],
}

CAPACITY = {"Padma": 50}     # with Padma at 50 pieces/day the plan stays feasible after one member declines

def seed_test_world():
    """Fictional group and people for the automated tests only."""
    import db
    with db.S() as s:
        g = db.Group(name="Test SHG", village="V", block="B", district="D"); s.add(g); s.flush()
        s.add(db.User(phone="+919000000009", name="Test Officer", role="officer"))
        s.add(db.User(phone="+919000000001", name="Test Leader", role="leader", group_id=g.id))
        s.add(db.User(phone="+919000000002", name="Test Buyer", role="buyer"))
        for k, m in enumerate(DATA["members"]):
            s.add(db.User(phone=f"+91900000010{k}", name=m["name"], role="seller", group_id=g.id, skills=m["skills"], capacity=CAPACITY.get(m["name"], m["capacity"])))
        s.commit()
