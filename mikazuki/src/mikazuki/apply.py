"""Apply: read a job's application form, fill the factual answers from the profile and draft the open
ones with a local model. Nothing is ever submitted; the page only prepares answers to copy.

Forms come from the Greenhouse public API, Lever's /apply page, or questions pasted by hand. Factual
answers (contact, work rights, languages, salary…) are filled by code, never by the model. Personal
choices (consent boxes, demographics, AI pledges) are left to the candidate. When a form forbids
AI-written answers, nothing is drafted.
"""

from __future__ import annotations

import datetime as dt
import html as _html
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .net import FetchError, fetch

log = logging.getLogger("mikazuki.apply")

DEFAULT_MODEL = "gemma4:12b"
MAX_QUESTIONS = 40

# The prompt that passed the 2026-10-09 test (no invented facts on the trap questions).
SYSTEM = """You draft answers to job application questions for the candidate described in FACTS. The candidate will review and edit every answer before sending it.
Rules:
1. Use ONLY facts stated in FACTS. Never invent employers, degrees, grades, numbers, tools, years of experience or achievements.
2. If the question asks for something FACTS does not contain, do not guess: write [NEEDS INPUT: what is missing] in its place. Be honest about gaps (e.g. study projects are not professional experience).
3. Write in the first person, in the language of the question, plain text, no headings, no markdown, no placeholders like [Company].
4. Respect the length limit. Be specific and concrete; avoid generic filler.
5. If OPTIONS are given, answer with exactly one of them (or [NEEDS INPUT: …] if FACTS can't decide).
6. Answer only what is asked. Don't retell the CV or list every project: pick the one or two facts that matter for this question.
7. Sound like a person, not a template: no stock phrases ("passionate", "dynamic", "I am confident that", "perfect fit", "thrilled", "leverage"), no em dashes.
8. Output only the answer."""

COVER_LETTER_ID = "cover_letter"
COVER_TASK = {
    "en": """Write a cover letter for JOB in English: three short paragraphs, under 220 words in total.
Paragraph 1: name the role and give one specific reason, taken from the job post, why I'm applying (something the company does or the job involves). Don't invent goals or interests that FACTS don't state.
Paragraph 2: the ONE fact from FACTS most relevant to this job, in two or three sentences: what I did and what it shows. At most one more short fact. Don't list projects.
Paragraph 3: one or two sentences: I can start immediately and I'm happy to talk.
Accuracy: copy numbers exactly as FACTS give them. Courses, study projects and labs are not professional experience. Never claim experience in the job's own field that FACTS don't show.
Style: plain, direct sentences, like a competent person writing to another person. No adjectives about myself (disciplined, proactive, passionate, motivated, detail-oriented, eager). Never use: "I am writing to express", "aligns with", "perfect fit", "I am confident", "thrilled", "excited", "valuable asset", "contribute to the team", "proactive approach".
Start with "Dear hiring team," and end with my full name. Never mention where I live, my nationality, citizenship or age.""",
    "es": """Escribe una carta de presentación para JOB en español: tres párrafos breves, menos de 220 palabras en total.
Párrafo 1: nombra el puesto y da un motivo concreto, sacado de la oferta, por el que me presento (algo que hace la empresa o que implica el puesto). No inventes objetivos ni intereses que FACTS no digan.
Párrafo 2: el dato de FACTS que más importa para este puesto, en dos o tres frases: qué hice y qué demuestra. Como mucho, otro dato breve. No enumeres proyectos.
Párrafo 3: una o dos frases: puedo incorporarme de inmediato y estoy disponible para hablar.
Exactitud: copia las cifras tal como aparecen en FACTS. Cursos, proyectos de formación y laboratorios no son experiencia profesional. Nunca afirmes experiencia en el campo del puesto que FACTS no muestre.
Estilo: frases claras y directas, como una persona competente que escribe a otra. Sin adjetivos sobre mí (proactivo, apasionado, motivado, constante, resolutivo). Nunca uses: "me dirijo a ustedes", "me motiva enormemente", "encaja perfectamente", "me encantaría", "gran capacidad", "sólida formación", "aportar valor", "formar parte de su equipo".
Empieza con "Estimado equipo de selección:" y termina con mi nombre completo. Nunca menciones dónde vivo, mi nacionalidad, ciudadanía ni edad.""",
}

# Phrases that make a letter read as generated. Flagged on the page, never silently rewritten.
SLOP = re.compile(r"writing to express|aligns? with|perfect(?:ly)? fit|i am confident|thrilled|excited to|valuable asset|"
                  r"contribute to the (?:\w+ )?team|proactive|passionate|eager to|leverage|dynamic|"
                  r"me dirijo a|enormemente|encaja perfectamente|me encantar[ií]a|gran capacidad|s[oó]lida formaci[oó]n|"
                  r"aportar valor|formar parte de su equipo|apasionad|proactiv|\u2014", re.I)


def slop_note(text: str) -> str:
    found = sorted({m.group(0).lower() for m in SLOP.finditer(text or "")})
    return ("Reads as generated, rewrite: " + ", ".join(f'"{f}"' for f in found)) if found else ""


def cover_letter_question(lang: str) -> "Question":
    label = "Carta de presentación" if lang == "es" else "Cover letter"
    return Question(id=COVER_LETTER_ID, label=label, kind="textarea", origin="ai",
                    note="Three short paragraphs, under 250 words. Read it as if you were the recruiter.")


# Seniority the candidate doesn't have: never mirrored into the CV headline.
_SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|manager|head|director|jefe|responsable|architect|arquitecto)\b", re.I)
_ROLE = {
    "soc": r"\bsoc\b|security analyst|cyber ?security analyst|analista (?:de )?(?:seguridad|ciberseguridad|soc)|incident respon|threat (?:intel|hunt)|blue team|security operations",
    "pentest": r"pentest|penetration test|ethical hack|hacker [ée]tico|red team|offensive security|auditor[ae]? (?:de )?(?:seguridad|hacking)|security tester",
    "helpdesk": r"help ?desk|service desk|it support|technical support|desktop support|soporte|t[ée]cnico (?:de )?(?:sistemas|inform[aá]tico|soporte|microinform)",
}


def headline_for(job_title: str, track: str) -> str | None:
    """The job's own title, cleaned, when it names the same role as the CV track at a junior-compatible level."""
    t = re.sub(r"\((?:[mfwhdx]\s*/\s*)+[mfwhdx]\)|\b[mfwhd]/[mfwhd](?:/[mfwhdx])?\b", "", job_title or "", flags=re.I)
    t = re.split(r"\s+[-–|]\s+|,", t)[0]
    t = re.sub(r"\b(remote|remoto|teletrabajo|hybrid|h[ií]brido)\b", "", t, flags=re.I)
    t = " ".join(t.split()).strip(" -–|:")
    rx = _ROLE.get(track)
    if not t or len(t) > 60 or not rx or _SENIOR.search(t) or not re.search(rx, t, re.I):
        return None
    return t


@dataclass
class Question:
    id: str
    label: str
    kind: str = "textarea"  # text | textarea | select | multi | file | consent
    required: bool = False
    options: list[str] = field(default_factory=list)
    answer: str = ""
    origin: str = "ai"  # code | ai | you | edited
    note: str = ""


@dataclass
class Application:
    uid: str
    source: str = "none"  # greenhouse | lever | pasted | none
    form_url: str = ""
    ai_ban: str = ""  # the sentence that forbids AI-written answers, if any
    track: str = ""
    lang: str = "en"
    cv_file: str = ""
    questions: list[Question] = field(default_factory=list)
    status: str = "ready"  # ready | drafting | error
    progress: str = ""
    error: str = ""
    created: str = ""
    updated: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Application":
        qs = [Question(**q) for q in d.pop("questions", [])]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__}, questions=qs)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- storage


class AppStore:
    """One JSON file per job under data/applications/."""

    def __init__(self, root: Path):
        self.dir = root / "applications"

    def _path(self, uid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", uid):
            raise ValueError("bad job id")
        return self.dir / f"{uid}.json"

    def get(self, uid: str) -> Application | None:
        p = self._path(uid)
        if not p.exists():
            return None
        return Application.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, app: Application) -> None:
        self.dir.mkdir(exist_ok=True)
        app.updated = _now()
        app.created = app.created or app.updated
        tmp = self._path(app.uid).with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(app), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path(app.uid))

    def delete(self, uid: str) -> None:
        self._path(uid).unlink(missing_ok=True)


# ---------------------------------------------------------------- reading forms


def _strip(s: str | None) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


_GH_JOB = re.compile(r"^/([A-Za-z0-9_-]+)/jobs/(\d+)")
_LEVER_JOB = re.compile(r"^/([A-Za-z0-9_.-]+)/([0-9a-f-]{36})")


def form_source(url: str) -> tuple[str, str] | None:
    """Job URL → (kind, form/API URL) for the systems whose forms can be read without a browser."""
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    if re.fullmatch(r"(job-boards|boards)(\.eu)?\.greenhouse\.io", host):
        m = _GH_JOB.match(u.path)
        if m:
            return "greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}?questions=true"
    if re.fullmatch(r"jobs(\.eu)?\.lever\.co", host):
        m = _LEVER_JOB.match(u.path)
        if m:
            return "lever", f"https://{host}/{m.group(1)}/{m.group(2)}/apply"
    return None


_GH_KINDS = {"input_text": "text", "textarea": "textarea", "multi_value_single_select": "select",
             "multi_value_multi_select": "multi", "input_file": "file"}


def parse_greenhouse(data: dict) -> tuple[list[Question], list[str]]:
    """Greenhouse job JSON → questions, plus extra text to scan for AI clauses."""
    qs, extra = [], [_strip(data.get("ai_disclaimer"))]
    for q in (data.get("questions") or []) + (data.get("location_questions") or []):
        fields = [f for f in q.get("fields") or [] if f.get("type") != "input_hidden"]
        if not fields:
            continue
        kind = "file" if any(f["type"] == "input_file" for f in fields) else _GH_KINDS.get(fields[0]["type"], "text")
        options = [_strip(v.get("label")) for v in fields[0].get("values") or []][:400]
        label = _strip(q.get("label"))
        if q.get("description"):
            extra.append(_strip(q["description"]))
        qs.append(Question(id=fields[0]["name"], label=label, kind=kind, required=bool(q.get("required")), options=options))
    return qs, extra


def parse_lever(page: str) -> list[Question]:
    qs = []
    for i, (_cls, blk) in enumerate(re.findall(r'<li class="application-question([^"]*)"[^>]*>(.*?)</li>', page, re.S)):
        names = [n for n in re.findall(r'name="([^"]+)"', blk) if n != "selectedLocation"]
        if not names:
            continue
        lab = re.search(r'class="application-label[^"]*"[^>]*>(.*?)</div>', blk, re.S)
        label = _strip(lab.group(1)) if lab else ""
        if not label:
            lab = re.search(r'<div class="text">(.*?)</div>', blk, re.S)
            label = _strip(lab.group(1)) if lab else names[0]
        required = "✱" in label or "required" in blk
        label = label.replace("✱", "").strip()
        types = set(re.findall(r'type="([a-z]+)"', blk))
        if "<textarea" in blk:
            kind = "textarea"
        elif "file" in types:
            kind = "file"
        elif "radio" in types or "<select" in blk:
            kind = "select"
        elif "checkbox" in types:
            kind = "consent" if names[0].startswith("consent") else "multi"
        else:
            kind = "text"
        options = [_strip(o) for o in re.findall(r'<label>\s*(?:<input[^>]*>\s*)+(.*?)</label>', blk, re.S)]
        options = [o for o in options if o]
        options += [_strip(o) for o in re.findall(r"<option[^>]*>(.*?)</option>", blk, re.S) if _strip(o)]
        if not label or label.startswith(("consent[", "cards[")):
            label = options[0] if options else names[0]
        qs.append(Question(id=names[0] or f"q{i}", label=label[:1000], kind=kind, required=required, options=options[:400]))
    return qs


def parse_pasted(text: str) -> list[Question]:
    """One question per paragraph (or per line when there are no blank lines)."""
    text = (text or "").replace("\r\n", "\n").strip()
    parts = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    qs = []
    for i, p in enumerate(x for x in (" ".join(p.split()) for p in parts) if len(x) > 2):
        required = p.startswith("*") or p.endswith("*")
        qs.append(Question(id=f"p{i}", label=p.strip("* ")[:1000], kind="textarea", required=required))
    return qs[:MAX_QUESTIONS]


def read_form(url: str) -> tuple[str, str, list[Question], list[str]]:
    """Fetch and parse a job's form. Returns (source, form_url, questions, extra texts). Raises FetchError."""
    found = form_source(url)
    if not found:
        return "none", "", [], []
    kind, form_url = found
    body = fetch(form_url, timeout=20)
    if kind == "greenhouse":
        try:
            data = json.loads(body)
        except ValueError as e:
            raise FetchError("Greenhouse sent something that isn't JSON") from e
        qs, extra = parse_greenhouse(data)
        return kind, url, qs[:MAX_QUESTIONS], extra
    return kind, form_url, parse_lever(body)[:MAX_QUESTIONS], []


# ---------------------------------------------------------------- AI-ban detection

_AI = r"\b(?:ai|a\.i\.|artificial intelligence|chatgpt|gpt|generative|llms?|ia|inteligencia artificial)\b"
_NO = r"(?:disqualif\w*|prohibit\w*|forbid\w*|not (?:be )?(?:allowed|permitted|accepted)|reject\w*|descalific\w*|prohibid\w*|no (?:se )?permit\w*)"
_BAN = re.compile(rf"only (?:my|your|their) own words|{_AI}[^.!?]{{0,80}}{_NO}|{_NO}[^.!?]{{0,80}}{_AI}|sin (?:usar|utilizar|el uso de) (?:la )?(?:ia|inteligencia artificial)", re.I)


def ai_ban(texts: list[str]) -> str:
    """The first sentence that forbids AI-written answers, or ''."""
    for t in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", t or ""):
            if _BAN.search(sentence):
                return sentence.strip()[:400]
    return ""


# ---------------------------------------------------------------- answers by code

COUNTRY = {"ES": ("Spain", "España"), "RO": ("Romania", "Rumanía")}
NATIONALITY = {"RO": ("Romanian", "rumana"), "ES": ("Spanish", "española")}
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
      "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}

# (rule, label pattern). Order matters: the first match wins.
RULES = [
    ("you", r"gender|g[eé]nero|\brace\b|ethnic|etnia|veteran|disabilit|discapacidad|pronoun|sexual orientation|hispanic|latino"),
    ("you", r"own words|plagiari|use of (?:ai|artificial)"),
    ("you", r"privacy|policy|consent|i agree|acknowledge|terms and conditions|pol[ií]tica de privacidad|acepto|consentimiento"),
    ("cv", r"\b(?:resume|cv|curr[ií]culum)\b"),
    ("you", r"cover letter|carta de presentaci[oó]n"),
    ("first_name", r"^first name|given name|^nombre$"),
    ("last_name", r"^last name|surname|family name|apellido"),
    ("full_name", r"^(?:full )?name$|^nombre (?:completo|y apellidos)"),
    ("email", r"e-?mail|correo"),
    ("phone", r"phone|tel[eé]fono|m[oó]vil|mobile"),
    ("linkedin", r"linkedin"),
    ("github", r"github"),
    ("website", r"website|portfolio|personal site|web personal|blog|other site"),
    ("work_auth", r"(?:authori[sz]|eligib|right|permit|permiso|legally)[^?]{0,60}\bwork|work (?:authori[sz]|permit)|derecho a trabajar|permiso de trabajo|autorizaci[oó]n (?:de|para) trabajar"),
    ("sponsorship", r"sponsor|patrocin"),
    ("country", r"which country|what country|country (?:of residence|you (?:live|reside|currently))|in which country do you|pa[ií]s de residencia|currently (?:live|reside|work) in"),
    ("nationality", r"nationalit|citizenship|nacionalidad|ciudadan"),
    ("location", r"current (?:location|city)|where are you (?:based|located)|^location$|^ubicaci[oó]n|^ciudad|city of residence"),
    ("current_company", r"current (?:company|employer)|empresa actual"),
    ("salary", r"salary|compensation|pay expectation|salari|retribuci|sueldo|remuneraci"),
    ("availability", r"notice period|preaviso|start date|when can you start|available to start|earliest|incorporaci|disponibilidad"),
    ("relocate", r"relocat|reubic|traslad|mudarte"),
    ("driving_licence", r"driv(?:ing|er'?s) licen[cs]e|carn[eé]t? de conducir|permiso de conducir"),
    ("travel", r"travel|viaj|meet in person|in person \d"),
    ("work_mode", r"remote|hybrid|on-?site|in the office|teletrabajo|presencial|h[ií]brido"),
    ("source", r"how did you (?:hear|find|learn)|c[oó]mo (?:nos )?(?:conociste|encontraste|te enteraste)|referred|where did you (?:see|find)"),
    ("languages", r"(?<!programming )\blanguages? (?:do you speak|spoken)|which languages|idiomas"),
]
_RULES = [(k, re.compile(p, re.I)) for k, p in RULES]


SHORT_ONLY = {"travel", "work_mode"}  # in an open question these words are context, not the question


def classify(q: Question) -> str:
    if q.kind == "consent":
        return "you"
    for key, rx in _RULES:
        if key in SHORT_ONLY and q.kind == "textarea":
            continue
        if rx.search(q.label):
            if key == "languages" and re.search(r"programm|coding|code", q.label, re.I):
                continue
            return key
    if q.kind == "file":
        return "you"
    return "ai"


def facts(master: dict, profile: dict, job: dict, lang: str, cv_file: str, track: str = "") -> dict:
    """Everything the code may answer with. Missing values stay None (→ [NEEDS INPUT])."""
    from cvtracks import i18n

    p = master.get("person") or {}
    raw = profile.get("apply") or {}
    a = {k: (i18n.text(v, lang) if isinstance(v, dict) else v) for k, v in raw.items()}  # {en = …, es = …} values
    es = lang == "es"
    name = (p.get("name") or "").split()
    first = " ".join(name[:1]) if a.get("first_name") is None else a["first_name"]
    last = " ".join(name[1:]) if a.get("last_name") is None else a["last_name"]
    if len(name) > 2 and a.get("first_name") is None:  # "Ana Maria Pop": two given names
        first, last = " ".join(name[:-1]), name[-1]
    countries = profile.get("countries") or ["ES"]
    cits = profile.get("citizenships") or []
    country = COUNTRY.get(countries[0], (countries[0], countries[0]))[1 if es else 0]
    nat = ", ".join(NATIONALITY.get(c, (c, c))[1 if es else 0] for c in cits) or None
    eu = any(c in EU for c in cits)
    langs = "; ".join(f"{i18n.text(x.get('name'), lang)}: {i18n.text(x.get('level'), lang)}" for x in master.get("languages") or [])
    url = lambda v, base="": (v if not v or v.startswith("http") else f"https://{base}{v}")  # noqa: E731
    exp = sorted(master.get("experience") or [], key=lambda e: str(e.get("start") or ""))
    last_job = exp[-1] if exp else None
    if last_job and not last_job.get("end"):
        current = last_job.get("company")
    elif last_job:
        current = (f"Ninguna actualmente (última: {last_job.get('company')}, hasta {last_job.get('end')})" if es
                   else f"None at the moment (last: {last_job.get('company')}, until {last_job.get('end')})")
    else:
        current = None
    work = None
    if eu:
        work = ("Sí. Tengo permiso para trabajar en la UE/EEE sin necesidad de patrocinio." if es
                else "Yes. I have the right to work in the EU/EEA without sponsorship.")
    return {
        "first_name": first or None, "last_name": last or None, "full_name": p.get("name"),
        "email": p.get("email"), "phone": p.get("phone"),
        "linkedin": url(a.get("linkedin"), "www.linkedin.com/in/") or None,
        "github": url(p.get("github"), "github.com/") or None,
        "website": url(p.get("website")) or None,
        "location": i18n.text(p.get("location"), lang) or None,
        "country": country, "nationality": nat,
        "work_auth": work, "work_auth_short": ("Yes" if eu else None),
        "sponsorship": ("No" if eu else None),
        "current_company": current,
        "salary": a.get(f"salary_{track}") or a.get("salary"), "availability": a.get("availability"),
        "relocate": a.get("relocate"), "driving_licence": a.get("driving_licence"), "travel": a.get("travel"),
        "work_mode": a.get("work_mode"),
        "source": a.get("source") or job.get("source_name") or None,
        "languages": langs or None,
        "cv": cv_file or None,
    }


# Personal data that can lead to automatic rejection or isn't needed to assess a candidate: never filled
# in for the candidate. The form may still require it; that's the candidate's call.
PRIVATE = {"nationality", "location", "country"}

NOTES = {
    "you": "Your decision: answer this yourself.",
    "private": "Personal data: give it only if the form really requires it.",
    "cv": "Upload the tailored CV linked above.",
    "work_auth": "Assumes the job is in the EU/EEA. Check it if the role is elsewhere.",
    "sponsorship": "Assumes the job is in the EU/EEA.",
}


def _pick(options: list[str], value: str) -> str | None:
    v = value.lower()
    for o in options:
        if o.lower() == v:
            return o
    for o in options:
        if o.lower().startswith(v) or v.startswith(o.lower()):
            return o
    return None


def fill_by_code(qs: list[Question], f: dict) -> None:
    for q in qs:
        if q.origin == "edited" or q.id == COVER_LETTER_ID:
            continue
        key = classify(q)
        if key == "ai":
            q.origin, q.answer, q.note = "ai", "", ""
            continue
        if key == "you":
            q.origin, q.answer, q.note = "you", "", NOTES["you"]
            continue
        if key in PRIVATE:
            q.origin, q.answer, q.note = "you", "", NOTES["private"]
            continue
        value = f.get(key)
        if key == "work_auth" and q.kind in ("select", "multi") and f.get("work_auth_short"):
            value = f["work_auth_short"]
        q.origin, q.note = "code", NOTES.get(key, "")
        if value is None:
            q.answer = f"[NEEDS INPUT: {key.replace('_', ' ')}]"
            q.note = {"salary": "Add [apply] salary to profile.toml (Settings) to fill this every time.",
                      "availability": "Add [apply] availability to profile.toml.",
                      "relocate": "Add [apply] relocate to profile.toml.",
                      "linkedin": "Add [apply] linkedin to profile.toml.",
                      "driving_licence": "Add [apply] driving_licence to profile.toml.",
                      "travel": "Add [apply] travel to profile.toml.",
                      "work_mode": "Add [apply] work_mode to profile.toml."}.get(key, "Not in your profile yet.")
            continue
        value = str(value)
        if q.options:
            picked = _pick(q.options, value)
            if picked:
                value = picked
            else:
                q.note = (q.note + " " if q.note else "") + "No option matched exactly: pick the closest one."
        q.answer = value


# ---------------------------------------------------------------- drafting with the local model


@dataclass
class LLM:
    base: str  # Ollama root (http://192.168.1.50:11434), or an OpenAI-compatible base URL (…/v1)
    model: str = DEFAULT_MODEL
    timeout: float = 300.0
    openai: bool = False  # True: OpenAI-compatible /chat/completions; False: Ollama's native /api/chat
    api_key: str | None = None

    @classmethod
    def from_profile(cls, profile: dict, api_key: str | None = None) -> "LLM | None":
        """[llm] provider = "none" | "ollama" | "openai". Without a provider, a base_url means Ollama."""
        a, l = profile.get("apply") or {}, profile.get("llm") or {}
        provider = l.get("provider") or ("ollama" if (a.get("ollama_url") or l.get("base_url")) else "none")
        if provider == "none":
            return None
        if provider == "openai":
            if not l.get("base_url") or not l.get("model"):
                return None
            return cls(l["base_url"].rstrip("/"), a.get("model") or l["model"], float(a.get("timeout", 300)),
                       openai=True, api_key=api_key)
        base = (a.get("ollama_url") or l.get("base_url") or "").rstrip("/")
        if not base:
            return None
        base = re.sub(r"/v1$", "", base)
        return cls(base, a.get("model") or l.get("model") or DEFAULT_MODEL, float(a.get("timeout", 300)))


class LLMError(RuntimeError):
    pass


def _length(q: Question) -> str:
    m = re.search(r"(\d{2,5})\s*(characters|caracteres|chars|words|palabras)", q.label, re.I)
    if m:
        return f"max {m.group(1)} {m.group(2)}"
    if q.kind in ("select", "multi"):
        return "one of the OPTIONS"
    return "one short line (under 25 words)" if q.kind == "text" else "80 to 180 words"


def ask(llm: LLM, cv_facts: str, job_line: str, q: Question, lang: str = "en") -> str:
    if q.id == COVER_LETTER_ID:
        user = f"FACTS:\n{cv_facts}\n\nJOB: {job_line}\nTASK: {COVER_TASK.get(lang, COVER_TASK['en'])}"
    else:
        user = f"FACTS:\n{cv_facts}\n\nJOB: {job_line}\nQUESTION: {q.label}\nLENGTH: {_length(q)}"
    if q.options:
        user += "\nOPTIONS:\n" + "\n".join(f"- {o}" for o in q.options[:60])
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    if llm.openai:
        ans = _openai_chat(llm, messages)
        if q.options:
            ans = _pick(q.options, ans.strip(" .")) or ans
        return ans[:4000]
    body = {"model": llm.model, "stream": False, "think": False, "keep_alive": "5m", "messages": messages,
            "options": {"temperature": 0.3, "num_ctx": 8192, "seed": 7}}

    def call(b: dict) -> dict:
        req = urllib.request.Request(f"{llm.base}/api/chat", json.dumps(b).encode(),
                                     {"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=llm.timeout) as resp:  # trusted, configured host
            return json.loads(resp.read(2_000_000))

    try:
        try:
            r = call(body)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            body.pop("think")  # older Ollama or a model without thinking support
            r = call(body)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise LLMError(f"couldn't reach the local model at {llm.base} ({e}). Is the model host running?") from e
    ans = ((r.get("message") or {}).get("content") or "").strip()
    ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.S).strip()
    if q.options:
        ans = _pick(q.options, ans.strip(" .")) or ans
    return ans[:4000]


def _openai_chat(llm: LLM, messages: list[dict]) -> str:
    """OpenAI-compatible chat. Asks for no reasoning first; endpoints that reject that get a plain request."""
    headers = {"Content-Type": "application/json"}
    if llm.api_key:
        headers["Authorization"] = f"Bearer {llm.api_key}"
    base = {"model": llm.model, "temperature": 0.3, "messages": messages}
    for body in ({**base, "reasoning_effort": "none"}, base):
        req = urllib.request.Request(f"{llm.base}/chat/completions", json.dumps(body).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=llm.timeout) as resp:  # trusted, configured host
                r = json.loads(resp.read(2_000_000))
            break
        except urllib.error.HTTPError as e:
            if e.code == 400 and "reasoning_effort" in body:
                continue
            detail = e.read(300).decode(errors="replace")
            raise LLMError(f"the AI provider answered HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise LLMError(f"couldn't reach the AI provider at {llm.base} ({e})") from e
    try:
        ans = (r["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise LLMError(f"unexpected reply from the AI provider: {str(r)[:200]}") from e
    return re.sub(r"<think>.*?</think>", "", ans, flags=re.S).strip()


_draft_lock = threading.Lock()  # one drafting job at a time: the GPU serves one model
_running: set[str] = set()


def is_running(uid: str) -> bool:
    return uid in _running


def draft_async(store: AppStore, uid: str, llm: LLM, cv_facts: str, job_line: str, only: int | None = None) -> bool:
    """Draft the open questions in a background thread. False if this job is already drafting."""
    if uid in _running:
        return False
    _running.add(uid)
    app = store.get(uid)
    app.status, app.error, app.progress = "drafting", "", "waiting for the model"
    store.save(app)

    def work():
        try:
            with _draft_lock:
                todo = [i for i, q in enumerate(app.questions) if q.origin == "ai" and (only is None or i == only)]
                for n, i in enumerate(todo, 1):
                    cur = store.get(uid)
                    cur.progress = f"question {n} of {len(todo)}"
                    store.save(cur)
                    answer = ask(llm, cv_facts, job_line, cur.questions[i], lang=cur.lang)
                    cur = store.get(uid)
                    if cur.questions[i].origin == "ai":  # don't overwrite an edit made meanwhile
                        cur.questions[i].answer = answer
                        flagged = slop_note(answer)
                        cur.questions[i].note = ("Draft by the local model: check every fact before sending."
                                                 + (f" {flagged}" if flagged else ""))
                    store.save(cur)
            cur = store.get(uid)
            cur.status, cur.progress = "ready", ""
            store.save(cur)
        except Exception as e:  # noqa: BLE001 - shown on the page
            log.warning("drafting %s failed: %s", uid, e)
            cur = store.get(uid)
            cur.status, cur.progress, cur.error = "error", "", str(e)[:500]
            store.save(cur)
        finally:
            _running.discard(uid)

    threading.Thread(target=work, daemon=True, name=f"apply-{uid}").start()
    return True


def guess_lang(text: str, languages: list[str]) -> str:
    words = re.findall(r"[a-záéíóúñ]+", (text or "").lower())[:3000]
    es = sum(w in {"el", "la", "los", "las", "de", "y", "para", "con", "en", "del", "una", "experiencia"} for w in words)
    en = sum(w in {"the", "and", "of", "to", "with", "for", "you", "we", "our", "experience"} for w in words)
    if "es" in languages and es > en:
        return "es"
    return "en" if "en" in languages else (languages[0] if languages else "en")
