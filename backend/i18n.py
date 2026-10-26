"""Tiny message catalogue (English / Hindi / Hinglish), like MaaSakhi's language choice at registration.
Only member-facing messages are translated. Money maths and ledger text stay language-neutral."""
LANGS = {"1": "en", "2": "hi", "3": "hing", "en": "en", "english": "en", "hi": "hi", "hindi": "hi", "hing": "hing", "hinglish": "hing"}
STAGE_HI = {"cutting": "कटाई", "stitching": "सिलाई", "finishing": "फिनिशिंग", "packing": "पैकिंग"}
M = {
 "menu": {"en": "Welcome to Sakhi Khata.\nChoose your language:\n1 English\n2 हिन्दी\n3 Hinglish",
          "hi": "सखी खाता में स्वागत है।\nभाषा चुनें:\n1 English\n2 हिन्दी\n3 Hinglish",
          "hing": "Sakhi Khata mein swagat hai.\nBhasha chunein:\n1 English\n2 हिन्दी\n3 Hinglish"},
 "ask_name": {"en": "Your name?", "hi": "आपका नाम?", "hing": "Aapka naam?"},
 "ask_role": {"en": "Who are you?\n1 SHG member (I do the work)\n2 Buyer (I place orders)",
              "hi": "आप कौन हैं?\n1 SHG सदस्य (मैं काम करती हूँ)\n2 खरीदार (मैं ऑर्डर देता/देती हूँ)",
              "hing": "Aap kaun hain?\n1 SHG member (main kaam karti hoon)\n2 Buyer (main order deta/deti hoon)"},
 "ask_skill": {"en": "Your skill?\n1 cutting\n2 stitching\n3 finishing\n4 packing",
               "hi": "आपका हुनर?\n1 कटाई\n2 सिलाई\n3 फिनिशिंग\n4 पैकिंग",
               "hing": "Aapka hunar?\n1 cutting\n2 stitching\n3 finishing\n4 packing"},
 "joined": {"en": "Welcome, {name}! You are registered. By continuing you agree that your group keeps your orders and pay records. Send HELP anytime.",
            "hi": "स्वागत है, {name}! आप रजिस्टर हो गईं। आगे बढ़ने का मतलब है कि आपका समूह आपके ऑर्डर और भुगतान का रिकॉर्ड रखेगा। कभी भी HELP भेजें।",
            "hing": "Swagat hai, {name}! Aap register ho gayi. Aage badhne ka matlab hai ki aapka group aapke order aur payment ka record rakhega. Kabhi bhi HELP bhejein."},
 "lang_set": {"en": "Language set to English.", "hi": "भाषा हिन्दी कर दी गई।", "hing": "Bhasha Hinglish kar di gayi."},
 "help_seller": {"en": "TASKS, EARNINGS, DONE <order> <pieces>, SAMPLE <order> (or send a photo), DECLINE <order>, APPROVE <order>, DISPUTE <order> <reason>, LANG 1/2/3.",
                 "hi": "TASKS (काम), EARNINGS (कमाई), DONE <ऑर्डर> <पीस>, SAMPLE <ऑर्डर> (या फोटो भेजें), DECLINE <ऑर्डर>, APPROVE <ऑर्डर>, DISPUTE <ऑर्डर> <कारण>, LANG 1/2/3.",
                 "hing": "TASKS, EARNINGS (ya 'maine kitna kamaya'), DONE <order> <pieces>, SAMPLE <order> (ya photo bhejein), DECLINE <order>, APPROVE <order>, DISPUTE <order> <karan>, LANG 1/2/3."},
 "task": {"en": "Order #{o}: your task is {stage} x{n} (Rs {pay}). Reply DECLINE {o} if you cannot.",
          "hi": "ऑर्डर #{o}: आपका काम {stage} x{n} (Rs {pay}) है। न कर सकें तो DECLINE {o} भेजें।",
          "hing": "Order #{o}: aapka kaam {stage} x{n} (Rs {pay}) hai. Na kar saken to DECLINE {o} bhejein."},
 "task_line": {"en": "Order #{o}: {stage} x{n} = Rs {pay}, done {done}/{n} [{st}]",
               "hi": "ऑर्डर #{o}: {stage} x{n} = Rs {pay}, पूरा {done}/{n} [{st}]",
               "hing": "Order #{o}: {stage} x{n} = Rs {pay}, done {done}/{n} [{st}]"},
 "no_tasks": {"en": "No tasks.", "hi": "अभी कोई काम नहीं।", "hing": "Abhi koi kaam nahi."},
 "no_earn": {"en": "No earnings yet.", "hi": "अभी कोई कमाई दर्ज नहीं।", "hing": "Abhi koi kamai darj nahi."},
 "pay_ready": {"en": "Order #{o}: your pay is ready to review. Send EARNINGS, then APPROVE {o} or DISPUTE {o} <reason>.",
               "hi": "ऑर्डर #{o}: आपका भुगतान देखने के लिए तैयार है। EARNINGS भेजें, फिर APPROVE {o} या DISPUTE {o} <कारण>।",
               "hing": "Order #{o}: aapka payment dekhne ke liye ready hai. EARNINGS bhejein, phir APPROVE {o} ya DISPUTE {o} <karan>."},
 "declined": {"en": "Your task is released. No penalty. The leader will replan.", "hi": "आपका काम छोड़ दिया गया। कोई जुर्माना नहीं। लीडर नई योजना बनाएँगी।", "hing": "Aapka kaam chhod diya gaya. Koi penalty nahi. Leader naya plan banayengi."},
 "disputed": {"en": "Your dispute is recorded. The payment waits until it is resolved.", "hi": "आपकी शिकायत दर्ज हो गई। समाधान तक भुगतान रुका रहेगा।", "hing": "Aapki shikayat darj ho gayi. Resolve hone tak payment ruka rahega."},
 "thanks": {"en": "Thank you. Order status: {st}.", "hi": "धन्यवाद। ऑर्डर की स्थिति: {st}.", "hing": "Dhanyavaad. Order status: {st}."},
 "done_ok": {"en": "Recorded: {n} of {tot} {stage} pieces done for order #{o}.", "hi": "दर्ज: ऑर्डर #{o} में {stage} के {n}/{tot} पीस पूरे।", "hing": "Darj: order #{o} mein {stage} ke {n}/{tot} pieces poore."},
 "sample_ok": {"en": "Sample for order #{o} sent to the leader{photo}. She will review it before you make more.", "hi": "ऑर्डर #{o} का सैंपल लीडर को भेज दिया{photo}। और बनाने से पहले वे जाँच करेंगी।", "hing": "Order #{o} ka sample leader ko bhej diya{photo}. Zyada banane se pehle woh check karengi."},
 "q_pass": {"en": "Order #{o}: your first sample passed. Carry on.", "hi": "ऑर्डर #{o}: आपका पहला सैंपल पास हो गया। आगे बढ़ें।", "hing": "Order #{o}: aapka pehla sample pass ho gaya. Aage badhein."},
 "q_rework": {"en": "Order #{o}: {n} piece(s) need rework. Reason: {why}. Nothing is deducted: rework pieces are paid once they pass.", "hi": "ऑर्डर #{o}: {n} पीस दोबारा ठीक करने हैं। कारण: {why}। कोई कटौती नहीं: ठीक होने पर पूरा भुगतान।", "hing": "Order #{o}: {n} piece(s) dobara theek karne hain. Karan: {why}. Koi katauti nahi: theek hone par poora payment."},
}
def t(key, lang, **kw):
    d = M[key]; return d.get(lang or "en", d["en"]).format(**kw)
def stage(s, lang): return STAGE_HI.get(s, s) if lang == "hi" else s
def explain(l, lang):
    """Pay slip in the member's language. Numbers come from the engine; this only phrases them."""
    if lang not in ("hi", "hing"):
        import engine as E; return E.explain(l)
    made, st = l["accepted"] + l.get("pending", 0), stage(l["stage"], lang)
    if lang == "hi":
        s = f"{l['name']}, आपने {made} {st} पीस बनाए। {l['accepted']} पास हुए, Rs {l['rate']} प्रति पीस = Rs {l['paid']}।"
        if l.get("pending"): s += f" {l['pending']} पीस दोबारा ठीक करने हैं; पास होने पर Rs {l['pending_amount']} जुड़ेंगे।"
        return s + f" कुल अपेक्षित: Rs {l['total_expected']}। किसी भी एंट्री पर सवाल हो तो DISPUTE भेजें।"
    s = f"{l['name']}, aapne {made} {st} pieces banaye. {l['accepted']} pass hue, Rs {l['rate']} per piece = Rs {l['paid']}."
    if l.get("pending"): s += f" {l['pending']} pieces rework ke liye hain; pass hone par Rs {l['pending_amount']} judenge."
    return s + f" Total expected: Rs {l['total_expected']}. Kisi bhi entry par sawaal ho to DISPUTE bhejein."
