"""Posts that aren't job offers: course, bootcamp and master's adverts, and student internships.

Training providers dress courses up as vacancies ("Prácticas en ciberseguridad", "Junior pentester:
bootcamp + empleo"). A real job post may *mention* a master's or offer training; that's fine. What
gives an advert away is the title naming a course, or text that sells one (fees, enrolment, places,
financing, job guarantees). Student internships (prácticas formativas/curriculares, internships tied to
being enrolled) are a separate switch, because they're useful when a work placement is due.
"""

from __future__ import annotations

import re
import unicodedata


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts))


# Titles that name a course, not a role. "Scrum Master" and "Master Data …" are jobs.
COURSE_TITLE = _rx(r"\bbootcamps?\b", r"\bcursos?\b", r"\bcourses?\b", r"\bpostgrado\b", r"\bposgrado\b",
                   r"\bdiplomado\b", r"\bprograma formativo\b", r"(?<!scrum )\bmaste?r\b(?!\s*data)(?!\s*file)")
# Text that sells training.
COURSE_SELL = _rx(r"\bmatricula(?:te|cion)?\b", r"\bprecio del (?:curso|programa|master|bootcamp)\b", r"\bplazas limitadas\b",
                  r"\bfinanciacion (?:disponible|sin intereses|a medida|flexible)\b", r"\bpago a plazos\b", r"\bbonificable\b",
                  r"\bfundae\b", r"\bgarantia de (?:empleo|contratacion|empleabilidad)\b", r"\bjob guarantee\b", r"\btuition\b",
                  r"\benrol(?:l)?ment fee\b", r"\bcourse fee\b", r"\breserva tu plaza\b", r"\btitulacion propia\b",
                  r"\bcertificado de profesionalidad\b", r"\b(?:curso|formacion) (?:gratuit[oa]|subvencionad[oa]|bonificad[oa])\b",
                  r"\binscribete (?:en|al) (?:el )?(?:curso|programa|bootcamp|master)\b", r"\bsalidas profesionales\b",
                  r"\bbefore (?:the )?(?:course|bootcamp) starts\b", r"\bnext cohort\b")
# Fairs and event sign-ups listed as jobs (e.g. Hipo's "Înscrie-te la Angajatori de TOP").
EVENT_TITLE = _rx(r"\binscri(?:eri|e-te|ere)\b", r"\btarg(?:ul)? de (?:cariere|joburi|locuri de munca)\b", r"\b(?:career|job) fair\b",
                  r"\bferia de empleo\b", r"\bacces gratuit\b", r"\bwebinar\b")
# Internships for students.
INTERN_TITLE = _rx(r"\bpracticas?\b", r"\bbecari[oa]s?\b", r"\bbecas?\b", r"\binternships?\b", r"\bintern\b", r"\bstagiaire\b",
                   r"\bstagiu\b", r"\bpractica\b")
INTERN_TEXT = _rx(r"\bpracticas (?:formativas|curriculares|extracurriculares|no laborales|academicas)\b",
                  r"\bconvenio (?:de practicas|de cooperacion educativa|con (?:tu|el|la|su) (?:centro|universidad|escuela))\b",
                  r"\b(?:estar|estes|estas|encontrarte) matriculad[oa]s?\b", r"\bcurrently enrolled\b",
                  r"\buniversity students?\b", r"\bbuscamos estudiantes\b")


def not_a_job(title: str, text: str, *, internships_ok: bool = False) -> str | None:
    """Why this posting isn't a job offer ("event or job fair", "course or bootcamp", "student internship"), or None."""
    t, body = _fold(title), _fold(text)[:20000]
    if EVENT_TITLE.search(t):
        return "event or job fair"
    if COURSE_TITLE.search(t) or COURSE_SELL.search(body):
        return "course or bootcamp"
    if not internships_ok and (INTERN_TITLE.search(t) or INTERN_TEXT.search(body)):
        return "student internship"
    return None
