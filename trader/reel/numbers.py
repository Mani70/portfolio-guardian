"""Numbers written out the way an Indian narrator says them, so the voice never guesses (it read "2536" wrongly).

The voice text (Devanagari mode) gets Hindi number words - 2536 -> "दो हज़ार पाँच सौ छत्तीस", ₹10 crore -> "दस करोड़
रुपये", 1.34% -> "एक पॉइंट तीन चार percent", 2012-26 -> "दो हज़ार बारह से दो हज़ार छब्बीस". Numbers that are part of an
English name or term stay English, as people say them: "Nifty 50" -> "Nifty fifty", "52-week" -> "fifty-two week".
Roman mode gets English number words throughout. Captions on screen keep the digits.
"""
from __future__ import annotations

import re

HI = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस इक्कीस बाईस "
      "तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस "
      "उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन "
      "तिरेपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरेसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर "
      "सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी "
      "चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे "
      "अट्ठानवे निन्यानवे").split()
EN = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
      "seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
assert len(HI) == 100

# names whose number is always said in English
NAMES = re.compile(r"\b(Nifty(?: Next| Midcap| Smallcap| Bank)?|Midcap|Smallcap|Sensex|Nasdaq|S&P|BSE|Nifty50)\s?(\d+)\b")


def hindi(n: int) -> str:
    if n < 100:
        return HI[n]
    if 1100 <= n <= 1999 and n % 100 != 0 and n // 100 not in (10,):        # 1992: उन्नीस सौ बानवे
        return f"{HI[n // 100]} सौ {HI[n % 100]}"
    out = []
    for size, word in ((10 ** 7, "करोड़"), (10 ** 5, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= size:
            out.append(f"{hindi(n // size)} {word}")
            n %= size
    if n:
        out.append(HI[n])
    return " ".join(out)


def english(n: int) -> str:
    if n < 20:
        return EN[n]
    if n < 100:
        return TENS[n // 10] + ("-" + EN[n % 10] if n % 10 else "")
    out = []
    for size, word in ((10 ** 7, "crore"), (10 ** 5, "lakh"), (1000, "thousand"), (100, "hundred")):
        if n >= size:
            out.append(f"{english(n // size)} {word}")
            n %= size
    if n:
        out.append(english(n))
    return " ".join(out)


def _say(num: str, hi: bool) -> str:
    """'2,536' / '1.34' / '0.44' -> words."""
    words = hindi if hi else english
    whole, _, frac = num.replace(",", "").partition(".")
    s = words(int(whole)) if whole else words(0)
    if frac:
        s += (" पॉइंट " if hi else " point ") + " ".join(words(int(d)) for d in frac)
    return s


NUM = r"\d[\d,]*(?:\.\d+)?"


def speakable(text: str, hindi_words: bool = True) -> str:
    """Every number in text as words: Hindi words (Devanagari voice text) or English words (Roman voice text)."""
    hi = hindi_words
    t = NAMES.sub(lambda m: f"{m.group(1)} {_say(m.group(2), False)}", text)
    # ranges: 2012-26, 2012–2026, 3-4 -> "X से Y" / "X to Y"
    def rng(m):
        a, b = m.group(1), m.group(2)
        if len(a) == 4 and len(b) == 2:
            b = a[:2] + b
        return f"{_say(a, hi)} {'से' if hi else 'to'} {_say(b, hi)}"
    t = re.sub(rf"(?<![\w.])(\d{{1,4}})\s?[-–]\s?(\d{{1,4}})(?![\w.%])", rng, t)
    # number joined to an English word: 52-week, 200-day, 10x
    t = re.sub(rf"({NUM})-([A-Za-z])", lambda m: f"{_say(m.group(1), False)} {m.group(2)}", t)
    t = re.sub(rf"\b({NUM})x\b", lambda m: f"{_say(m.group(1), hi)} {'गुना' if hi else 'times'}", t)
    # rupees: ₹10 crore -> दस करोड़ रुपये
    unit = r"(?:\s?(crore|cr|lakh|करोड़|लाख|हज़ार|thousand))?"
    def rupee(m):
        u = {"cr": "crore"}.get(m.group(2) or "", m.group(2) or "")
        if hi:
            u = {"crore": "करोड़", "lakh": "लाख", "thousand": "हज़ार"}.get(u, u)
        return f"{_say(m.group(1), hi)}{' ' + u if u else ''} {'रुपये' if hi else 'rupees'}"
    t = re.sub(rf"(?:₹|Rs\.?|INR)\s?({NUM}){unit}", rupee, t)
    # signed percentages and plain numbers
    t = re.sub(rf"(?<![\w.])([+\-−])({NUM})\s?%", lambda m: f"{('प्लस' if hi else 'plus') if m.group(1) == '+' else ('माइनस' if hi else 'minus')} "
               f"{_say(m.group(2), hi)} percent", t)
    t = re.sub(rf"({NUM})\s?%", lambda m: f"{_say(m.group(1), hi)} percent", t)
    t = re.sub(rf"#\s?(\d+)", lambda m: f"{'नंबर' if hi else 'number'} {_say(m.group(1), hi)}", t)
    t = re.sub(NUM, lambda m: _say(m.group(0), hi), t)
    return re.sub(r"\s{2,}", " ", t)
