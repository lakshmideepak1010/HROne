"""
Employee Attendance & Analytics API - HROne assignment
Run from project root:
    python -m uvicorn app.main:app --reload --port 8000

Environment:
    MONGO_URI (default mongodb://localhost:27017)
    MONGO_DB  (default attendance_db)
"""
import os
import re
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, field_validator
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# ---------------------------------------------------------------------------
# Indexes and helpers
# ---------------------------------------------------------------------------
@app.on_event("startup")
def create_indexes():
    db.employees.create_index([("emp_code", 1)], unique=True, name="uq_employees_emp_code")
    db.employees.create_index([("department", 1), ("joined_on", 1)], name="ix_employees_department_joined")
    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)], unique=True, name="uq_attendance_emp_code_date"
    )
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)], name="ix_attendance_date_emp")
    db.attendance_logs.create_index([("date", 1), ("late_minutes", -1)], name="ix_attendance_date_late")


def validate_ms(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("timestamp must be an integer in epoch milliseconds")
    if value < 100000000000 or value > 4102444800000:
        raise ValueError("timestamp must be valid epoch milliseconds")
    try:
        datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ValueError("timestamp is outside the supported range")
    return value


def parse_ms(value: Optional[int]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).replace(microsecond=0)


def to_bson_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None)


def epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def api_record(doc: dict) -> dict:
    result = {k: v for k, v in doc.items() if k != "_id"}
    for field in ("punch_in", "punch_out"):
        if isinstance(result.get(field), datetime):
            result[field] = epoch_ms(result[field])
    for entry in result.get("history", []):
        if isinstance(entry.get("at"), datetime):
            entry["at"] = epoch_ms(entry["at"])
        for change in entry.get("changes", {}).values():
            for side in ("from", "to"):
                if isinstance(change.get(side), datetime):
                    change[side] = epoch_ms(change[side])
    return result


def valid_date(value: str, field: str = "date") -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{field} must use YYYY-MM-DD format")
    if parsed.isoformat() != value:
        raise HTTPException(422, f"{field} must use YYYY-MM-DD format")
    return parsed


def month_bounds(month: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise HTTPException(422, "month must use YYYY-MM format")
    year, month_num = map(int, month.split("-"))
    return date(year, month_num, 1), date(year, month_num, calendar.monthrange(year, month_num)[1])


def round_value(value: float, places: int = 2) -> float:
    return float(Decimal(str(value)).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP))


def weekday_count(start: date, end: date) -> int:
    count = 0
    day = start
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: str) -> int:
    hour, minute = map(int, shift_start.split(":"))
    shift_day = punch_in.date()
    if shift_end <= shift_start and punch_in.strftime("%H:%M") < shift_end:
        shift_day -= timedelta(days=1)
    start = datetime(shift_day.year, shift_day.month, shift_day.day, hour, minute, tzinfo=punch_in.tzinfo)
    seconds = (punch_in - start).total_seconds()
    return 0 if seconds <= 600 else int(seconds // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    return float((seconds / Decimal("3600")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out_ist: datetime, shift_start: str, shift_end: str, attendance_date: str) -> int:
    day = date.fromisoformat(attendance_date)
    hour, minute = map(int, shift_end.split(":"))
    end = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    if shift_end <= shift_start:
        end += timedelta(days=1)
    minutes = int((punch_out_ist - end).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def presence_fraction(doc: dict) -> float:
    if doc.get("status") not in PRESENCE:
        return 0.0
    return 0.5 if doc.get("half_day", False) else 1.0


def pipeline_month_filter(start: date, end: date) -> dict:
    return {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}


def mongo_json(value: Any):
    """Convert BSON values into JSON-safe values for explain responses."""
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): mongo_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mongo_json(v) for v in value]
    if hasattr(value, "__str__") and value.__class__.__name__ == "ObjectId":
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def emp_code_format(cls, value):
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must match EMP followed by 4 to 6 digits")
        return value

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        if not value.strip() or len(value) > 100:
            raise ValueError("name must be non-empty and at most 100 characters")
        return value

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        if len(value) > 120 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("email must be valid and at most 120 characters")
        return value

    @field_validator("department")
    @classmethod
    def department_valid(cls, value):
        if not value.strip() or len(value) > 50:
            raise ValueError("department must be non-empty and at most 50 characters")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def shift_time_valid(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("shift times must use HH:MM")
        return value

    @field_validator("joined_on")
    @classmethod
    def joined_date_valid(cls, value):
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            raise ValueError("joined_on must use YYYY-MM-DD")
        return value

    @field_validator("shift_end")
    @classmethod
    def different_shift_times(cls, value, info):
        if value == info.data.get("shift_start", "09:30"):
            raise ValueError("shift_start and shift_end must differ")
        return value


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator("punched_at", mode="before")
    @classmethod
    def timestamp_valid(cls, value):
        return validate_ms(value)

    @field_validator("status")
    @classmethod
    def presence_status(cls, value):
        if value not in PRESENCE:
            raise ValueError("status must be PRESENT, WFH, or ON_DUTY")
        return value


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def timestamp_valid(cls, value):
        return validate_ms(value)


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str
    by: str
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None

    @field_validator("punch_in", "punch_out", mode="before")
    @classmethod
    def timestamp_valid(cls, value):
        return validate_ms(value)

    @field_validator("status")
    @classmethod
    def status_valid(cls, value):
        if value is not None and value not in STATUSES:
            raise ValueError("Invalid status")
        return value


# ---------------------------------------------------------------------------
# Basic endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = epoch_ms(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department is not None else {}
    total = db.employees.count_documents(query)
    items = list(db.employees.find(query, {"_id": 0}).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size))
    for item in items:
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = epoch_ms(item["created_at"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    instant = parse_ms(body.punched_at) if body.punched_at is not None else datetime.now(timezone.utc).replace(microsecond=0)
    instant_ist = instant.astimezone(IST)
    shift_start, shift_end = employee["shift_start"], employee["shift_end"]
    attendance_day = instant_ist.date()
    if shift_end <= shift_start and instant_ist.strftime("%H:%M") < shift_end:
        attendance_day -= timedelta(days=1)
    doc = {
        "emp_code": body.emp_code, "date": attendance_day.isoformat(), "status": body.status,
        "punch_in": to_bson_utc(instant), "punch_out": None, "work_hours": None,
        "late_minutes": compute_late_minutes(instant_ist, shift_start, shift_end),
        "overtime_minutes": 0, "half_day": False, "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Already punched in for this attendance date")
    return api_record(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    instant = parse_ms(body.punched_at) if body.punched_at is not None else datetime.now(timezone.utc).replace(microsecond=0)
    stored_instant = to_bson_utc(instant)
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": stored_instant}},
        sort=[("punch_in", -1)],
    )
    if record is None:
        raise HTTPException(status_code=404, detail="No punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Already punched out")
    punch_in_utc = record["punch_in"].replace(tzinfo=timezone.utc)
    elapsed = (instant - punch_in_utc).total_seconds()
    if elapsed <= 0 or elapsed > 86400:
        raise HTTPException(status_code=422, detail="Punch-out must be after punch-in and within 24 hours")
    work_hours = compute_work_hours(punch_in_utc, instant)
    overtime = compute_overtime(instant.astimezone(IST), employee["shift_start"], employee["shift_end"], record["date"])
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {"punch_out": stored_instant, "work_hours": work_hours,
                  "overtime_minutes": overtime, "half_day": work_hours < 4.50}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Already punched out")
    return api_record(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from:
        valid_date(date_from, "date_from")
    if date_to:
        valid_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status")
    query: dict[str, Any] = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status is not None:
        query["status"] = status
    total = db.attendance_logs.count_documents(query)
    docs = list(db.attendance_logs.find(query, {"_id": 0}).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size))
    return {"items": [api_record(doc) for doc in docs], "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# Attendance correction with audit history
# ---------------------------------------------------------------------------

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    valid_date(date)
    if not body.reason.strip() or not body.by.strip():
        raise HTTPException(status_code=422, detail="reason and by must not be empty")
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    supplied = body.model_fields_set
    old_status = record.get("status")
    old_in, old_out = record.get("punch_in"), record.get("punch_out")
    new_status = body.status if "status" in supplied and body.status is not None else old_status
    new_in = parse_ms(body.punch_in) if "punch_in" in supplied else (old_in.replace(tzinfo=timezone.utc) if old_in else None)
    new_out = parse_ms(body.punch_out) if "punch_out" in supplied else (old_out.replace(tzinfo=timezone.utc) if old_out else None)

    if new_status in {"ABSENT", "LEAVE"}:
        if ("punch_in" in supplied and body.punch_in is not None) or ("punch_out" in supplied and body.punch_out is not None):
            raise HTTPException(status_code=422, detail="ABSENT and LEAVE cannot have punch times")
        new_in = new_out = None
    elif new_status in PRESENCE and new_in is None:
        raise HTTPException(status_code=422, detail="Presence statuses require punch_in")

    if new_in is not None and new_out is not None:
        seconds = (new_out - new_in).total_seconds()
        if seconds <= 0 or seconds > 86400:
            raise HTTPException(status_code=422, detail="punch_out must be after punch_in and within 24 hours")
    if new_in is not None:
        in_ist = new_in.astimezone(IST)
        expected = in_ist.date()
        if employee["shift_end"] <= employee["shift_start"] and in_ist.strftime("%H:%M") < employee["shift_end"]:
            expected -= timedelta(days=1)
        if expected.isoformat() != date:
            raise HTTPException(status_code=422, detail="punch_in does not belong to the requested attendance date")

    def stored_dt(value):
        return to_bson_utc(value) if value is not None else None

    changes = {}
    if new_status != old_status:
        changes["status"] = {"from": old_status, "to": new_status}
    if (stored_dt(new_in) if new_in else None) != old_in:
        changes["punch_in"] = {"from": old_in, "to": stored_dt(new_in) if new_in else None}
    if (stored_dt(new_out) if new_out else None) != old_out:
        changes["punch_out"] = {"from": old_out, "to": stored_dt(new_out) if new_out else None}
    if not changes:
        raise HTTPException(status_code=422, detail="Request does not change the record")

    if new_status in PRESENCE and new_in is not None:
        late = compute_late_minutes(new_in.astimezone(IST), employee["shift_start"], employee["shift_end"])
    else:
        late = 0
    if new_in is not None and new_out is not None and new_status in PRESENCE:
        work = compute_work_hours(new_in, new_out)
        overtime = compute_overtime(new_out.astimezone(IST), employee["shift_start"], employee["shift_end"], date)
        half = work < 4.50
    else:
        work, overtime, half = None, 0, False

    old_derived = {
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }
    new_derived = {"work_hours": work, "late_minutes": late, "overtime_minutes": overtime, "half_day": half}
    for field, value in new_derived.items():
        if old_derived[field] != value:
            changes[field] = {"from": old_derived[field], "to": value}

    history_changes = {}
    for field, pair in changes.items():
        history_changes[field] = {}
        for side, value in pair.items():
            history_changes[field][side] = epoch_ms(value) if isinstance(value, datetime) else value

    history_entry = {
        "at": to_bson_utc(datetime.now(timezone.utc)),
        "by": body.by.strip(),
        "reason": body.reason.strip(),
        "changes": history_changes,
    }
    filter_doc = {
        "_id": record["_id"],
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "history": record.get("history", []),
    }
    updated = db.attendance_logs.find_one_and_update(
        filter_doc,
        {"$set": {"status": new_status, "punch_in": stored_dt(new_in), "punch_out": stored_dt(new_out),
                  "work_hours": work, "late_minutes": late, "overtime_minutes": overtime, "half_day": half},
         "$push": {"history": history_entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Attendance changed concurrently; retry the correction")
    return api_record(updated)


# ---------------------------------------------------------------------------
# Analytics aggregation pipelines
# ---------------------------------------------------------------------------
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    start, end = month_bounds(month)
    employee = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    joined = date.fromisoformat(employee["joined_on"])
    work_start = max(start, joined)
    work_days = weekday_count(work_start, end) if work_start <= end else 0

    pipeline = [
        {"$match": {"emp_code": emp_code, **pipeline_month_filter(start, end)}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$in": ["$status", list(PRESENCE)]},
                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]}, 0
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
        }},
    ]
    result = list(db.attendance_logs.aggregate(pipeline))
    totals = result[0] if result else {"present_days": 0.0, "leave_days": 0, "late_count": 0}
    present = round_value(totals.get("present_days", 0), 2)
    pct = round_value(present / work_days * 100, 2) if work_days else None
    return {
        "emp_code": emp_code, "month": month, "working_days": work_days,
        "present_days": present, "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0), "attendance_pct": pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None,
):
    start, end = month_bounds(month)
    employee_match: dict[str, Any] = {"joined_on": {"$lte": end.isoformat()}}
    if department is not None:
        employee_match["department"] = department

    # Aggregate employee headcount and attendance stats together; logs remain the source of stored metrics.
    employees = list(db.employees.find(employee_match, {"_id": 0, "emp_code": 1, "department": 1, "joined_on": 1}))
    if not employees:
        return {"month": month, "items": []}
    codes = [e["emp_code"] for e in employees]
    headcount_by_dept = {}
    for e in employees:
        headcount_by_dept[e["department"]] = headcount_by_dept.get(e["department"], 0) + 1

    pipeline = [
        {"$match": {"emp_code": {"$in": codes}, **pipeline_month_filter(start, end)}},
        {"$group": {
            "_id": "$emp_code",
            "department": {"$first": "$emp_code"},
            "present_days": {"$sum": {"$cond": [
                {"$in": ["$status", list(PRESENCE)]},
                {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]}, 0
            ]}},
            "work_sum": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$ne": ["$work_hours", None]}]},
                "$work_hours", 0
            ]}},
            "work_count": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$ne": ["$work_hours", None]}]}, 1, 0
            ]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "late_minutes": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, "$late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        }},
    ]
    employee_stats = {d["_id"]: d for d in db.attendance_logs.aggregate(pipeline)}
    dept_data = {}
    emp_dept = {e["emp_code"]: e["department"] for e in employees}
    for code, stat in employee_stats.items():
        dept = emp_dept.get(code)
        if dept is None:
            continue
        target = dept_data.setdefault(dept, {
            "present_days": 0.0, "work_sum": 0.0, "work_count": 0,
            "late_count": 0, "total_late_minutes": 0, "leave_count": 0, "on_duty_count": 0
        })
        target["present_days"] += stat.get("present_days", 0)
        target["work_sum"] += stat.get("work_sum", 0)
        target["work_count"] += stat.get("work_count", 0)
        target["late_count"] += stat.get("late_count", 0)
        target["total_late_minutes"] += stat.get("late_minutes", 0)
        target["leave_count"] += stat.get("leave_count", 0)
        target["on_duty_count"] += stat.get("on_duty_count", 0)

    items = []
    for dept in sorted(headcount_by_dept):
        stats = dept_data.get(dept, {})
        count = stats.get("work_count", 0)
        items.append({
            "department": dept, "headcount": headcount_by_dept[dept],
            "present_days": round_value(stats.get("present_days", 0), 2),
            "avg_work_hours": round_value(stats["work_sum"] / count, 2) if count else None,
            "late_count": stats.get("late_count", 0),
            "total_late_minutes": stats.get("total_late_minutes", 0),
            "leave_count": stats.get("leave_count", 0),
            "on_duty_count": stats.get("on_duty_count", 0),
        })
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    start, end = month_bounds(month)
    pipeline = [
        {"$match": {**pipeline_month_filter(start, end), "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
    ]
    stats = list(db.attendance_logs.aggregate(pipeline))
    codes = [s["_id"] for s in stats]
    employees = list(db.employees.find(
        {"emp_code": {"$in": codes}, **({"department": department} if department else {})},
        {"_id": 0, "emp_code": 1, "name": 1, "department": 1}
    )) if codes else []
    employee_map = {e["emp_code"]: e for e in employees}
    filtered = [s for s in stats if s["_id"] in employee_map and s["total_late_minutes"] > 0]
    items, rank, previous, position = [], 0, None, 0
    for s in filtered:
        position += 1
        if s["total_late_minutes"] != previous:
            rank = position
            previous = s["total_late_minutes"]
        if rank > limit:
            break
        emp = employee_map[s["_id"]]
        items.append({
            "rank": rank, "emp_code": s["_id"], "name": emp["name"],
            "department": emp["department"], "total_late_minutes": s["total_late_minutes"]
        })
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: Optional[str] = Query(None, alias="from"),
    to: str = Query(...)
):
    if from_date is None:
        raise HTTPException(status_code=422, detail="from is required")

    start = valid_date(from_date, "from")
    end = valid_date(to, "to")

    if end < start:
        raise HTTPException(
            status_code=422,
            detail="to must not be before from"
        )

    if (end - start).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="date range must not exceed 92 days"
        )

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    # MongoDB $densify requires literal BSON dates for explicit bounds.
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start.isoformat(),
                    "$lte": end.isoformat()
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", list(PRESENCE)]},
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1.0
                                ]
                            },
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$set": {
                "dateValue": {
                    "$dateFromString": {"dateString": "$_id"}
                }
            }
        },
        {"$sort": {"dateValue": 1}},
        {
            "$densify": {
                "field": "dateValue",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt]
                }
            }
        },
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "date": "$dateValue",
                        "format": "%Y-%m-%d"
                    }
                }
            }
        },
        {
            "$set": {
                "present_count": {
                    "$ifNull": ["$present_count", 0]
                },
                "late_count": {
                    "$ifNull": ["$late_count", 0]
                }
            }
        },
        {"$sort": {"dateValue": 1}},
        {
            "$set": {
                "is_working_day": {
                    "$in": [
                        {"$dayOfWeek": "$dateValue"},
                        [2, 3, 4, 5, 6]
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"trendDate": "$date"},
                "pipeline": [
                    {"$match": {"department": department}},
                    {
                        "$match": {
                            "$expr": {
                                "$lte": ["$joined_on", "$$trendDate"]
                            }
                        }
                    },
                    {"$count": "n"}
                ],
                "as": "hc"
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$hc.n", 0]},
                        0
                    ]
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {"$divide": ["$present_count", "$headcount"]},
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"dateValue": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        }
    ]

    rows = list(db.attendance_logs.aggregate(pipeline, allowDiskUse=True))
    by_date = {row["date"]: row for row in rows}

    output = []
    day = start

    while day <= end:
        key = day.isoformat()
        row = by_date.get(key)

        if row is None:
            headcount = db.employees.count_documents({
                "department": department,
                "joined_on": {"$lte": key}
            })

            is_working_day = day.weekday() < 5
            attendance_rate = (
                0.0 if is_working_day and headcount > 0 else None
            )

            row = {
                "date": key,
                "is_working_day": is_working_day,
                "headcount": headcount,
                "present_count": 0,
                "late_count": 0,
                "attendance_rate": attendance_rate,
                "moving_avg_7d": None
            }

        row["attendance_rate"] = (
            round_value(row["attendance_rate"], 4)
            if row.get("attendance_rate") is not None
            else None
        )

        output.append(row)
        day += timedelta(days=1)

    # Calculate the seven-calendar-day moving average.
    for index, row in enumerate(output):
        window = [
            item["attendance_rate"]
            for item in output[max(0, index - 6):index + 1]
            if item.get("attendance_rate") is not None
        ]

        row["moving_avg_7d"] = (
            round_value(sum(window) / len(window), 4)
            if window
            else None
        )

    return {
        "department": department,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "items": output
    }

# ---------------------------------------------------------------------------
# Explain endpoint
# ---------------------------------------------------------------------------
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    allowed = {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}
    if endpoint not in allowed:
        raise HTTPException(status_code=422, detail="Unknown endpoint")
    if endpoint == "attendance_list":
        if date_from:
            valid_date(date_from, "date_from")
        if date_to:
            valid_date(date_to, "date_to")
        query: dict[str, Any] = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        if status:
            if status not in STATUSES:
                raise HTTPException(422, "Invalid status")
            query["status"] = status
        explain = db.command({
            "explain": {"find": "attendance_logs", "filter": query,
                        "sort": {"date": -1, "emp_code": 1},
                        "skip": (page - 1) * page_size, "limit": page_size},
            "verbosity": "executionStats"
        })
        collection = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        start, end = month_bounds(month)
        pipeline = [{"$match": {"emp_code": emp_code, **pipeline_month_filter(start, end)}},
                    {"$group": {"_id": "$emp_code", "n": {"$sum": 1}}}]
        explain = db.command({"explain": {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
                              "verbosity": "executionStats"})
        collection = "attendance_logs"
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        start, end = month_bounds(month)
        match = {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}
        pipeline = [{"$match": match}, {"$group": {"_id": "$emp_code", "n": {"$sum": 1}}}]
        explain = db.command({"explain": {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
                              "verbosity": "executionStats"})
        collection = "attendance_logs"
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        start, end = month_bounds(month)
        pipeline = [{"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
                                "late_minutes": {"$gt": 0}}},
                    {"$group": {"_id": "$emp_code", "total": {"$sum": "$late_minutes"}}}]
        explain = db.command({"explain": {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
                              "verbosity": "executionStats"})
        collection = "attendance_logs"
    else:
        if not department or not from_date or not to:
            raise HTTPException(422, "department, from, and to are required")
        start, end = valid_date(from_date, "from"), valid_date(to, "to")
        if end < start or (end - start).days + 1 > 92:
            raise HTTPException(422, "Invalid trend date range")
        pipeline = [{"$match": {"date": {"$gte": start.isoformat(), "$lte": end.isoformat()}}},
                    {"$group": {"_id": "$date", "n": {"$sum": 1}}}]
        explain = db.command({"explain": {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
                              "verbosity": "executionStats"})
        collection = "attendance_logs"
    return {"endpoint": endpoint, "collection": collection, "explain": mongo_json(explain)}
