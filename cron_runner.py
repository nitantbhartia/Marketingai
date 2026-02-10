"""
Cron Runner for Weekly Monitoring Jobs

Schedules and runs:
- Search Console Monitor (Monday 6am UTC)
- Content Health Checker (Monday 8am UTC)
"""

import schedule
import time
from datetime import datetime

from content_quality.monitors.search_console_monitor import run_weekly_search_console_report
from content_quality.monitors.content_health_checker import run_weekly_content_health_check
from content_quality.db import init_database


def job_search_console():
    """Run Search Console monitoring job."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now().isoformat()}] Running Search Console Monitor")
    print(f"{'='*60}")

    try:
        report = run_weekly_search_console_report()
        print(f"✓ Search Console report generated successfully")
    except Exception as e:
        print(f"✗ Error running Search Console monitor: {e}")


def job_content_health():
    """Run Content Health Check job."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now().isoformat()}] Running Content Health Checker")
    print(f"{'='*60}")

    try:
        report = run_weekly_content_health_check()
        print(f"✓ Content health check completed successfully")
    except Exception as e:
        print(f"✗ Error running content health check: {e}")


def run_scheduler():
    """Run the scheduler continuously."""
    print("ClaimCoach Content Monitoring Cron Runner")
    print("=" * 60)
    print("Scheduled jobs:")
    print("  - Search Console Monitor: Every Monday at 06:00 UTC")
    print("  - Content Health Checker: Every Monday at 08:00 UTC")
    print("=" * 60)
    print()

    # Initialize database
    init_database()
    print("✓ Database initialized")
    print()

    # Schedule jobs
    schedule.every().monday.at("06:00").do(job_search_console)
    schedule.every().monday.at("08:00").do(job_content_health)

    print("Scheduler started. Press Ctrl+C to exit.")
    print()

    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


def run_now():
    """Run all jobs immediately (for testing)."""
    print("Running all jobs immediately (testing mode)...")
    print()

    # Initialize database
    init_database()

    # Run jobs
    job_search_console()
    print()
    job_content_health()

    print()
    print("All jobs completed.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        # Run immediately for testing
        run_now()
    else:
        # Run scheduler
        run_scheduler()
