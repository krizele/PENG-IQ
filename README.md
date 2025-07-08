# Queue Management System

A web-based queue management system built with Flask that allows users to create queue tickets for specific time slots and administrators to manage these tickets.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [User Interface](#user-interface)
  - [Public Pages](#public-pages)
  - [Admin Interface](#admin-interface)
- [API Reference](#api-reference)
- [Timezone Handling](#timezone-handling)
- [Database Schema](#database-schema)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

## Overview

This queue management system allows organizations to manage customer queues efficiently. Users can select a time slot, create a queue ticket, and monitor their position in the queue. Administrators can view all tickets, update their status, and manage the queue flow.

The system uses timezone-aware datetime handling to ensure accurate time representation across different regions, with all data stored in UTC and displayed in the configured local timezone (Asia/Singapore by default).

## Features

- **Time Slot Selection**: Users can select from available time slots
- **Queue Ticket Creation**: Users can create a queue ticket for a specific time slot
- **Real-time Queue Status**: Users can view their position in the queue and estimated wait time
- **Admin Dashboard**: Administrators can view and manage all queue tickets
- **Status Management**: Tickets can be marked as waiting, in progress, completed, or cancelled
- **Timezone Support**: All times are stored in UTC and displayed in the local timezone
- **Password Protection**: Location password required to create tickets
- **API Access**: Secure API endpoints for integration with other systems

## Installation

### Prerequisites

- Python 3.6+
- pip (Python package manager)

### Setup

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/queue-system.git
   cd queue-system
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Run the application:
   ```
   python app.py
   ```

4. Access the application at `http://localhost:5000`

## Configuration

The application can be configured by modifying the following constants in `app.py`:

- `MAX_SLOTS_PER_HOUR`: Maximum number of slots available per hour (default: 10)
- `ADMIN_USERNAME`: Username for admin access (default: 'admin')
- `ADMIN_PASSWORD`: Password for admin access (default: 'admin123')
- `LOCAL_TIMEZONE`: Local timezone for displaying times (default: 'Asia/Singapore')

## User Interface

### Public Pages

#### Home Page (`/`)

The home page allows users to create a new queue ticket:

1. Enter your name
2. Enter the location password (changes every minute)
3. Select a preferred time slot from the dropdown
4. Click "Create Queue Ticket"

The time slot dropdown shows:
- Available time slots for the current day
- Number of available slots for each time slot
- Average wait time based on historical data (if available)

#### View My Queue (`/view_my_queue`)

This page shows the status of your current queue ticket:

- Queue code (e.g., "01-9A-XYZ")
- Current status (Waiting, In Progress)
- Time slot
- Number of people ahead of you (if waiting)
- Estimated wait time (if waiting)
- Option to cancel your ticket

The page automatically refreshes every 30 seconds to show the latest status.

### Admin Interface

#### Admin Login (`/admin/login`)

Administrators must log in with the configured username and password.

#### Admin Panel (`/admin/` or `/admin/<date>`)

The admin panel shows all queue tickets for a selected date, organized by status:

- **Waiting**: Tickets that are waiting to be processed
- **In Progress**: Tickets currently being processed
- **Completed**: Tickets that have been completed
- **Cancelled**: Tickets that have been cancelled

For each ticket, administrators can:
- View the queue code, name, and time slot
- Update the status (move to waiting, in progress, or completed)
- See when the ticket was created and completed

The admin panel includes a date selector to view tickets from different dates.

## API Reference

The system provides the following API endpoints:

### Get Current Password

```
GET /api/password
```

**Authentication**: Basic Auth (Admin credentials)

**Response**:
```json
{
  "password": "current_password",
  "expires_at": "2023-05-01T12:30:00+08:00",
  "next_update": 45
}
```

- `password`: Current location password
- `expires_at`: When the password expires (ISO format)
- `next_update`: Seconds until the password expires

### Get Current In-Progress Queue

```
GET /api/current_in_progress
```

**Authentication**: Basic Auth (Admin credentials)

**Response**:
```json
{
  "queue_code": "01-9A-XYZ",
  "name": "John Doe",
  "time_slot": "2023-05-01T09:00:00+00:00",
  "wait_time": 15,
  "status": "in_progress",
  "message": "Queue found"
}
```

If no queue is in progress:
```json
{
  "queue_code": null,
  "name": null,
  "time_slot": null,
  "wait_time": null,
  "status": null,
  "message": "No queue items currently in progress"
}
```

## Timezone Handling

The system handles timezones as follows:

1. All datetime values are stored in UTC in the database
2. Times are displayed to users in the configured local timezone (Asia/Singapore by default)
3. When creating a queue ticket, the local time is converted to UTC before storage
4. When displaying queue information, UTC times are converted back to local timezone

Key timezone functions:
- `local_to_utc(local_dt)`: Converts local datetime to UTC
- `utc_to_local(utc_dt)`: Converts UTC datetime to local timezone
- `ensure_timezone(dt, tz=UTC)`: Ensures a datetime is timezone-aware

## Database Schema

The system uses a SQLite database with the following schema:

### Queue Table

| Column       | Type     | Description                                   |
|--------------|----------|-----------------------------------------------|
| id           | Integer  | Primary key                                   |
| name         | String   | Customer name                                 |
| time_slot    | DateTime | Requested time slot (UTC)                     |
| date         | Date     | Date of the time slot (local timezone date)   |
| queue_code   | String   | Unique queue code (format: "NN-HA-XXX")       |
| browser_id   | String   | Browser identifier for session management     |
| created_at   | DateTime | When the ticket was created (UTC)             |
| completed_at | DateTime | When the ticket was completed (UTC, nullable) |
| status       | String   | Status: waiting, in_progress, completed, cancelled |

## Security

The system implements the following security measures:

1. **Admin Authentication**: Username/password for admin access
2. **Location Password**: Randomly generated password that changes every minute
3. **Session Management**: Browser sessions for tracking user tickets
4. **API Authentication**: Basic Auth for API endpoints

## Troubleshooting

### Common Issues

1. **Ticket not showing in "View My Queue"**:
   - Ensure your browser accepts cookies
   - Try refreshing the page
   - Check if you have accidentally cancelled your ticket

2. **Cannot create a ticket**:
   - Verify you're using the correct location password
   - Check if the time slot is full
   - Ensure all required fields are filled

3. **Time display issues**:
   - The system uses Asia/Singapore timezone by default
   - All times are converted to this timezone for display

### Debugging

For developers, the application includes debug logging:
- Browser ID tracking
- Queue creation and retrieval
- Slot availability calculations
- Timezone conversion operations

To enable more verbose logging, modify the Flask configuration in `app.py`.
