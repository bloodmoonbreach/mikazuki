import pytest

from jobfeed.training import not_a_job

STUDENT_AD = ("Prácticas Departamento Ciberseguridad - Remoto",
            "En Acme Security buscamos estudiantes que quieran hacer sus prácticas formativas en el equipo de "
            "ciberseguridad. Aprenderás en proyectos reales.")


@pytest.mark.parametrize("title,text,why", [
    (*STUDENT_AD, "student internship"),
    ("Bootcamp de Ciberseguridad + empleo", "Aprende hacking ético en 12 semanas.", "course or bootcamp"),
    ("Junior Pentester", "Fórmate con nosotros. Plazas limitadas, financiación sin intereses y garantía de empleo.", "course or bootcamp"),
    ("Analista SOC (Curso + prácticas garantizadas)", "", "course or bootcamp"),
    ("Máster en Ciberseguridad", "Titulación propia.", "course or bootcamp"),
    ("Cybersecurity Intern", "Summer internship for university students.", "student internship"),
    ("Becario/a IT", "Convenio de prácticas con tu universidad.", "student internship"),
    ("Înscrie-te la Angajatori de TOP Timisoara - acces gratuit", "", "event or job fair"),
    ("BPO and Shared Services - Inscrieri gratuite Timisoara", "", "event or job fair"),
])
def test_not_jobs_are_caught(title, text, why):
    assert not_a_job(title, text) == why


@pytest.mark.parametrize("title,text", [
    ("Junior SOC Analyst", "Se valorará un máster en ciberseguridad. Formación inicial a cargo de la empresa."),
    ("Scrum Master", "Agile team, hybrid."),
    ("Master Data Analyst", "SAP MDG."),
    ("Técnico de Soporte IT", "Soporte a usuarios, Windows, Office. Contrato indefinido."),
    ("Cybersecurity Trainee (paid)", "Two-year paid trainee programme with a permanent contract afterwards."),
    ("Security Engineer", "We offer a learning budget and conference tickets."),
])
def test_real_jobs_pass(title, text):
    assert not_a_job(title, text) is None


def test_internships_can_be_allowed():
    assert not_a_job(*STUDENT_AD, internships_ok=True) is None
    assert not_a_job("Bootcamp SOC", "", internships_ok=True) == "course or bootcamp"  # courses never


def test_stored_course_ads_are_hidden():
    from jobfeed.pipeline import hide_not_jobs

    class FakeStore:
        def __init__(self):
            self.rows = [{"uid": "a", "title": STUDENT_AD[0], "text": STUDENT_AD[1]},
                         {"uid": "b", "title": "Junior SOC Analyst", "text": "SIEM triage."}]
            self.status, self.events = {}, []
        def jobs(self, status=None, limit=200): return self.rows
        def set_status(self, uid, st, log=True): self.status[uid] = st
        def add_event(self, uid, kind, text, at=None): self.events.append((uid, text))

    st = FakeStore()
    assert hide_not_jobs(st) == 1 and st.status == {"a": "dismissed"}
    assert "student internship" in st.events[0][1]
    assert hide_not_jobs(FakeStore(), internships_ok=True) == 0


@pytest.mark.parametrize("location,mode,good", [
    ("Bangalore", "unknown", False), ("Dallas Infomart Office DAI", "unknown", False), ("Torino", "unknown", False),
    ("Dublin", "unknown", False), ("Merida, City 32", "unknown", False),
    ("Bucuresti, Romania", "unknown", True), ("BUCURESTI ,, RO", "unknown", True), ("Madrid", "unknown", True),
    ("Home based - Worldwide", "remote", True), ("Warsaw", "remote", True), ("EMEA", "unknown", True),
    ("Malaga, Andalusia", "unknown", True), ("Oradea", "unknown", True), ("Herndon, VA", "unknown", False),
])
def test_unknown_mode_needs_a_plausible_place(location, mode, good):
    from jobfeed.pipeline import is_good
    from jobfeed.sources import Search
    s = Search(name="Cybersecurity", keywords=["security"], sources=[], remote_only=False)
    row = {"languages": {}, "mode": mode, "location": location, "best": "yes", "local_match": 0}
    assert is_good(row, s, "likely", True, {"ES", "RO"}) is good


@pytest.mark.parametrize("mode,local_match,good", [
    ("onsite", 0, False), ("hybrid", 0, False),  # "yes" = office in your country, not near home
    ("onsite", 1, True), ("hybrid", 1, True), ("remote", 0, True),
])
def test_office_jobs_match_only_near_home(mode, local_match, good):
    from jobfeed.pipeline import is_good
    from jobfeed.sources import Search
    s = Search(name="Cybersecurity", keywords=["security"], sources=[], remote_only=False)
    row = {"languages": {}, "mode": mode, "location": "Madrid", "best": "yes", "local_match": local_match}
    assert is_good(row, s, "likely", True, {"ES", "RO"}) is good
