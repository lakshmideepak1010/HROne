Employee Attendance Management API
1. Project Overview

This project implements an employee attendance management REST API using Python, FastAPI, and MongoDB.

The application manages employee records, attendance punch-in and punch-out operations, attendance corrections, and attendance analytics. It applies attendance rules to calculate work hours, late minutes, overtime, and half-day status.

2. Technology Stack

Language: Python 3.11+

Backend framework: FastAPI

Database: MongoDB 6+

Database driver: PyMongo

API documentation and testing: Swagger UI (OpenAPI)

3. Project Structure

candidate_kit/
├── app/
│   └── main.py
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md

4. Prerequisites

Python 3.11 or later

MongoDB 6 or later, running locally or remotely

pip for installing Python dependencies

5. Configuration

Configure the following environment variables before starting the application:

MONGO_URI: MongoDB connection string.

MONGO_DB: Name of the database to use.

Example local configuration:

MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db

Keep credentials and environment-specific configuration out of the public repository.

6. Installation and Execution

Install the dependencies:

pip install -r requirements.txt

Start the API from the project root:

python -m uvicorn app.main:app --reload --port 8000

7. API Documentation

Once the application is running, access:

Interactive Swagger UI: http://127.0.0.1:8000/docs

Health check: http://127.0.0.1:8000/health

The health endpoint checks database connectivity.

8. Main API Endpoints

Method

Endpoint

Purpose

POST

/employees

Create an employee

GET

/employees

List employees

POST

/attendance/punch-in

Record attendance punch-in

POST

/attendance/punch-out

Record attendance punch-out

GET

/attendance

List and filter attendance records

PATCH

/attendance/{emp_code}/{date}

Correct an attendance record

GET

/analytics/employees/{emp_code}/monthly

Retrieve monthly employee analytics

GET

/analytics/departments/summary

Retrieve department summaries

GET

/analytics/leaderboard/late

Retrieve the late-arrival leaderboard

GET

/analytics/departments/{department}/trend

Retrieve department attendance trends

GET

/admin/explain/{endpoint}

Inspect query execution plans

Use Swagger UI to inspect request schemas, required parameters, response formats, and status codes.

9. Database Design

The application uses two MongoDB collections:

employees: employee codes, names, email addresses, departments, shift timings, joining dates, and creation timestamps.

attendance_logs: employee codes, attendance dates, status, punch timestamps, calculated attendance values, and correction history.

Employee codes are the business identifiers. Attendance records are identified by employee code and attendance date. MongoDB indexes are created by the application to enforce uniqueness and support queries.

10. Attendance Rules

The application is designed to handle:

India Standard Time (IST) attendance dates and shift times.

Grace periods for late arrivals.

Minimum qualifying overtime.

Work-hour rounding and half-day classification.

Overnight shifts and attendance corrections.

Stored derived attendance values and correction history.

11. Testing and Verification

The application startup, database health check, employee creation, core attendance operations, attendance listing, and an index-backed explain query have been tested during development.

Additional verification of all business rules, analytics pipelines, correction-history behavior, and query execution plans is required before final submission.

12. Submission

The repository should include the application source code, dependency list, this README, REVIEW.md, and DECISIONS.md. Do not commit credentials, .env files, virtual environments, or database dumps.