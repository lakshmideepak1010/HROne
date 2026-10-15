Starter Code Review

The starter application was reviewed against openapi.yaml, DATA_MODEL.md, and the assignment requirements.

#

Where (function / area)

What is wrong

How to notice it

Fix / status

1

health()

The starter returns {"status": "ok"} without checking MongoDB connectivity.

Stop MongoDB and call /health.

Add a database ping and return an appropriate failure status when MongoDB is unavailable. Verify failure behavior.

2

create_employee()

Duplicate checking before insertion is vulnerable to concurrent requests; the starter also does not validate all contract constraints.

Submit duplicate employee codes, invalid fields, or concurrent requests.

Add request validation and a unique index on emp_code; handle duplicate-key errors. Verify all cases.

3

create_employee()

created_at is generated as a naive datetime and returned without the required epoch-millisecond representation.

Inspect the API response and stored BSON date.

Store a UTC instant and serialize API timestamps as integer epoch milliseconds.

4

list_employees()

Pagination skips too many records on later pages, and total counts all employees instead of applying the department filter.

Request page 1 and page 2; filter by department and compare totals.

Use (page - 1) * page_size, apply the same filter to the count, and validate pagination parameters.

5

punch_in()

Timestamp handling relies on local system time and does not fully implement IST calendar-date and overnight-shift rules.

Test timestamps around midnight and overnight shifts.

Normalize instants to UTC, derive attendance dates using the specified IST rules, and validate epoch milliseconds.

6

punch_in()

A check-then-insert sequence can allow duplicate attendance records under concurrent requests.

Send simultaneous punch-in requests for the same employee and attendance date.

Enforce uniqueness with a compound index on (emp_code, date) and handle duplicate-key errors.

7

punch_in()

The starter exposes an additional attendance id, contrary to the contract, and needs consistent response serialization.

Inspect the response for internal identifiers and timestamp types.

Return the documented fields only; do not expose MongoDB _id or a custom attendance ID.

8

compute_late_minutes()

The starter helper does not account for all IST and overnight-shift cases.

Test grace-period boundaries and overnight shifts.

Apply the R1/R2 rules, including whole-minute flooring and the strictly-greater-than-10-minute grace rule.

9

compute_work_hours()

Python's built-in round() does not guarantee the required decimal half-up rounding.

Test durations whose rounded result falls on a half-way boundary.

Calculate from elapsed seconds and use decimal ROUND_HALF_UP rounding.

10

compute_overtime()

The starter helper does not correctly handle overnight shift-end times or the 30-minute qualifying threshold.

Test ordinary and overnight shifts with less than, exactly, and more than 30 minutes of overtime.

Calculate the correct shift end and apply the R3 threshold and flooring rules.

11

list_attendance()

The starter loads matching records into Python, sorts them there, and slices the result instead of applying filtering, sorting, counting, and pagination in MongoDB.

Inspect query behavior on a large dataset and test later pages.

Push filters, sorting, counting, and pagination into MongoDB; validate parameters and serialize the documented response fields.

12

Missing punch-out endpoint

The starter does not implement POST /attendance/punch-out.

Call the endpoint using the documented request.

Implement record selection, validation, derived calculations, and an atomic update. Verify concurrency and overnight cases.

13

Missing correction endpoint

The starter does not implement PATCH /attendance/{emp_code}/{date}.

Submit a valid correction and inspect the stored record.

Implement corrections with one audit-history entry containing only fields that actually changed. Verify timestamp serialization.

14

Missing analytics endpoints

Monthly employee analytics, department summary, late leaderboard, and department trend are absent from the starter.

Call each required analytics route.

Implement the documented response schemas and business rules using MongoDB aggregation pipelines. Verify against test cases.

15

Missing explain endpoint

The starter does not implement GET /admin/explain/{endpoint}.

Call the documented explain routes and inspect execution statistics.

Implement execution-plan reporting for the supported queries and verify index usage without collection scans.

16

Startup configuration and indexes

The starter does not establish the complete required index setup at startup.

Inspect indexes in MongoDB Compass and restart the application.

Create required indexes idempotently at startup, including the unique employee and attendance keys. Verify startup behavior.

Items Reviewed That Are Not Defects

MongoDB's internal _id field is expected in stored documents. The defect is exposing it through the API, not its existence in the database.

Calendar dates such as joined_on and attendance date are intentionally stored as YYYY-MM-DD strings, not BSON dates.

Shift times are intentionally stored as HH:MM strings in IST.

Missing history and half_day fields in legacy records are permitted by the data model and must be treated as an empty array and false, respectively.

The presence of only a small number of local test records is not itself a defect. Correctness must also be verified against the assignment's larger seeded dataset.

Verification Status

Basic application startup, database connectivity, employee creation, punch-in, punch-out, attendance listing, and one index-backed explain query were tested during development.

The remaining business-rule edge cases, all analytics pipelines, correction-history behavior, and all explain routes must be verified against the contract before submission. The descriptions above document starter-code issues; they should not be treated as proof that every fix has passed testing.