from decimal import Decimal as D
import engine as E
import fixtures

def test_price_floor_and_underpay():
    p = E.price_check(200, 38, 40, 50, 8, 10, 60)
    assert p["per_unit"]["floor"] == "84.74" and p["loss_making"]
    assert p["effective_hourly_at_offer"] == "33.00"

def test_allocation_conserves_units_and_flags_overload():
    d = fixtures.DATA
    a = E.allocate(200, d["stages"], d["members"])
    for st in d["stages"]:
        assert sum(x["units"] for x in a["assignments"] if x["stage"] == st["name"]) == 200
    b = E.allocate(200, d["stages"], d["members"], declined=["m3"])
    assert all(x["member"] != "m3" for x in b["assignments"])

def test_payout_exact():
    r = E.payout(fixtures.DATA["payout_lines"])
    assert r["lines"][0]["paid"] == "1536.00" and r["lines"][0]["total_expected"] == "1600.00"
    assert D(r["total"]) == D("1600") + D("1000") + D("800") + D("800")

def test_ledger_detects_tampering():
    L = E.Ledger()
    for i in range(3): L.append("e", {"n": i}, "leader")
    assert L.verify()["valid"]
    L.events[1]["payload"]["n"] = 99
    assert L.verify() == {"valid": False, "broken_at": 1}

def test_parse_flags_unclear():
    r = E.parse_order(fixtures.DATA["order_text"])
    assert r["order"]["qty"] == 200 and r["order"]["offered_price"] == 60 and r["order"]["deadline_days"] == 5
    assert any("same as" in u for u in r["unclear"])

def test_order_fallback_understands_common_language_and_order_phrases():
    cloth = E.parse_order("Need 200 cloth bags, Rs 60 per piece, by 12 Nov 2027")
    assert cloth["order"]["product"] == "Cloth bag"
    assert cloth["order"]["qty"] == 200 and cloth["order"]["offered_price"] == 60
    assert cloth["order"]["delivery_date"] == "2027-11-12" and cloth["order"]["deadline_days"] > 0
    hinglish = E.parse_order("mujhe do sau thaile chahiye, 60 rupaye, 5 din mein")
    assert hinglish["order"]["qty"] == 200 and hinglish["order"]["offered_price"] == 60
    assert hinglish["order"]["deadline_days"] == 5
    kannada = E.parse_order("ನೂರು ಚೀಲ ಬೇಕು, 50 per piece, 10 ದಿನ")
    assert kannada["order"]["qty"] == 100 and kannada["order"]["product"] == "Cloth bag"


def test_feasibility_options_when_not_viable():
    members = [{"id": 1, "name": "A", "skills": ["cutting", "stitching"], "capacity": 20}]
    stages = [{"name": "cutting", "rate": 5}, {"name": "stitching", "rate": 10}]
    assert E.feasibility_options(100, stages, members, 10, 50, 60) == []            # viable: no options
    o = {x["kind"]: x for x in E.feasibility_options(200, stages, members, 5, 50, 60)}
    assert o["smaller_quantity"]["qty"] == 50 and o["smaller_quantity"]["remaining"] == 150
    assert o["later_deadline"]["deadline_days"] == 20
    assert o["revised_price"]["price"] == "70"                                     # max(50,60)*1.10 = 66 -> 70
    assert E.allocate(o["smaller_quantity"]["qty"], stages, members, days=5)["feasible"]
    assert E.allocate(200, stages, members, days=o["later_deadline"]["deadline_days"])["feasible"]

def test_missing_skill_only_offers_recruit():
    o = E.feasibility_options(50, [{"name": "dyeing", "rate": 5}], [{"id": 1, "name": "A", "skills": ["cutting"], "capacity": 20}], 5, 50, 60)
    assert [x["kind"] for x in o] == ["recruit"]
