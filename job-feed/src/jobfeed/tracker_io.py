"""Import/export the tracker as a spreadsheet (CSV, or rows for an .xlsx writer).

The column layout follows a common hand-made job-tracking sheet, so moving between a spreadsheet
and the app is lossless for the usual fields. Imported text is untrusted: it's stored as-is and
always escaped when shown.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import unicodedata

from .store import Store

EXPORT_COLUMNS = [
    ("Profile", "profile"), ("Platform", "platform"), ("Job listing URL", "url"), ("Job Title", "title"),
    ("Company", "company"), ("Job Location", "location"), ("Job Description", "text"), ("Job posting", "posted"),
    ("Applied", "_applied"), ("Date of Application", "applied_at"), ("Response", "response"),
    ("Date of Response", "response_at"), ("Search filter used", "search_filter"), ("Status", "status"),
    ("CV used", "cv_file"), ("Notes", "notes"), ("Next action", "next_action"), ("Next action date", "next_at"),
]

# header (folded) → field
_ALIASES = {
    "profile": "profile", "perfil": "profile", "track": "profile",
    "platform": "platform", "plataforma": "platform", "source": "platform", "fuente": "platform",
    "job listing url": "url", "url": "url", "link": "url", "enlace": "url",
    "job title": "title", "title": "title", "role": "title", "role title": "title", "puesto": "title", "titulo": "title",
    "company": "company", "empresa": "company",
    "job location": "location", "location": "location", "ubicacion": "location",
    "job description": "text", "description": "text", "descripcion": "text",
    "job posting": "posted", "date posted": "posted", "fecha publicacion": "posted", "date found": "posted",
    "applied": "_applied", "aplicado": "_applied",
    "date of application": "applied_at", "date applied": "applied_at", "fecha aplicacion": "applied_at",
    "response": "response", "respuesta": "response", "outcome": "response",
    "date of response": "response_at", "outcome date": "response_at", "fecha respuesta": "response_at",
    "search filter used": "search_filter", "status": "status", "estado": "status",
    "cv used": "cv_file", "cv version used": "cv_file", "notes": "notes", "notas": "notes",
    "next action": "next_action", "next action date": "next_at",
}

_RESPONSE_STATUS = [
    (r"\b(offer|oferta|accepted|aceptad)", "offer"),
    (r"\b(interview|entrevista|call|llamada|technical test|prueba)", "interview"),
    (r"\b(denied|rejected|rechaz|descartad|declined|no seleccionad)", "rejected"),
    (r"\b(withdrawn|retirad)", "withdrawn"),
]


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").strip().lower())
    return " ".join("".join(c for c in s if not unicodedata.combining(c)).split())


def parse_date(value) -> str | None:
    """'08/07/2026' (day first), '2026-07-08', datetimes → ISO date."""
    if value in (None, ""):
        return None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    s = str(value).strip()
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        y += 2000 if y < 100 else 0
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    try:
        return dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _truthy(v) -> bool:
    return _fold(str(v)) in ("true", "yes", "si", "1", "x", "✓", "verdadero")


def import_rows(store: Store, rows: list[dict], origin: str = "import") -> dict:
    """rows: dicts keyed by the sheet's headers. Returns counts."""
    added = skipped = empty = 0
    for raw in rows:
        f: dict = {}
        for k, v in raw.items():
            field = _ALIASES.get(_fold(str(k or "")))
            if field and v not in (None, ""):
                f[field] = str(v).strip() if not isinstance(v, (dt.date, dt.datetime)) else v
        if not (f.get("title") or f.get("company") or f.get("url")):
            empty += 1
            continue
        if f.get("url") and not str(f["url"]).lower().startswith(("http://", "https://")):
            f["notes"] = (f.get("notes", "") + f"\nOriginal link (not http): {f['url']}").strip()
            f.pop("url")
        for d in ("posted", "applied_at", "response_at", "next_at"):
            if d in f:
                f[d] = parse_date(f[d])
        applied = _truthy(f.pop("_applied", "")) or bool(f.get("applied_at"))
        status = f.get("status")
        if status and _fold(status) not in ("new", "saved", "applied", "interview", "offer", "rejected", "withdrawn", "dismissed"):
            status = None
        if not status:
            status = "applied" if applied else "saved"
            resp = _fold(f.get("response", ""))
            for pat, st in _RESPONSE_STATUS:
                if re.search(pat, resp):
                    status = st
                    break
            if resp in ("no response", "sin respuesta", "pending", "pendiente", "ghosted"):
                f["response"] = None  # "no response" is computed from the wait time
        f["status"] = _fold(status)
        uid, inserted = store.add_manual_ex(f, origin=origin)
        if not inserted:
            skipped += 1
            continue
        added += 1
        if f.get("applied_at"):
            store.add_event(uid, "applied", "Imported: applied", at=f["applied_at"] + "T00:00:00+00:00")
        if f.get("response_at") and f.get("response"):
            store.add_event(uid, "rejection" if f["status"] == "rejected" else "status", f"Imported response: {f['response']}",
                            at=f["response_at"] + "T00:00:00+00:00")
    return {"added": added, "skipped_existing": skipped, "empty_rows": empty}


def import_csv(store: Store, text: str, origin: str = "import") -> dict:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return import_rows(store, list(csv.DictReader(io.StringIO(text), dialect=dialect)), origin)


def export_rows(store: Store, include_unapplied: bool = True) -> list[list]:
    statuses = ("saved", "applied", "interview", "offer", "rejected", "withdrawn") if include_unapplied else \
        ("applied", "interview", "offer", "rejected", "withdrawn")
    out = [[h for h, _ in EXPORT_COLUMNS]]
    for j in store.tracked(statuses):
        row = []
        for _, key in EXPORT_COLUMNS:
            if key == "_applied":
                row.append("TRUE" if j.get("applied_at") or j["status"] in ("applied", "interview", "offer", "rejected") else "FALSE")
            elif key == "profile":
                row.append(j.get("profile") or j.get("search") or "")
            elif key == "platform":
                row.append(j.get("platform") or j.get("source_name") or "")
            elif key == "response":
                row.append(j.get("response") or ("No response" if j.get("no_response") else ""))
            else:
                v = j.get(key)
                row.append("" if v is None else str(v)[:30000])  # spreadsheet cell limit is 32,767
        out.append(row)
    return out


def export_csv(store: Store) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    for row in export_rows(store):
        # neutralise spreadsheet formula injection (cells starting with = + - @)
        w.writerow([("'" + c) if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c for c in row])
    return buf.getvalue()
