# DECISIONS.md

## 1. Indexes

A unique index on `employees.emp_code` prevents duplicate employee identifiers. A unique compound index on `attendance_logs.emp_code` and `date` guarantees at most one attendance record per employee per day. Additional indexes should support attendance filtering, sorting, and analytics. I would reject redundant indexes that duplicate existing index prefixes because they increase storage and write costs without enough benefit.

## 2. Punch-in race

Two identical punch-in requests may both initially find no record. The unique compound index is the final safeguard: one insert succeeds with 201, while the duplicate insert raises a duplicate-key error that must be translated to 409.

## 3. Leaderboard ties

The leaderboard should rank employees by late minutes using a window function. Employees tied at the cutoff receive the same rank, and all employees tied at that rank should be returned even if the result exceeds the requested limit.

## 4. Department headcount

The department summary should start from employees and use a lookup or equivalent aggregation to include employees with no attendance records. Otherwise, employees without logs disappear from the results.

## 5. Scaling to 100x data

I would benchmark with representative data, inspect execution plans, optimize indexes, and consider pre-aggregated analytics if query latency becomes excessive.