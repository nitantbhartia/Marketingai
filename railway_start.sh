#!/bin/bash
# Combined startup script for Railway
# Dashboard is the primary web process bound to $PORT
# Pipeline scheduler runs agents (Scout/Quill/Sage/Ezra/Herald) on cron schedules
# Cron worker runs weekly monitoring jobs in background

set -e

echo "Starting ClaimCoach Pipeline Services..."
echo ""

# Start pipeline scheduler in background (runs Scout/Quill/Sage/Ezra/Herald/Morgan)
echo "Starting pipeline scheduler (article agents)..."
python -m pipeline.cli start &
SCHEDULER_PID=$!
echo "  Scheduler started (PID: $SCHEDULER_PID)"

# Start cron worker in background (non-critical — OK if it fails)
echo "Starting cron worker (scheduled monitoring)..."
python cron_runner.py &
WORKER_PID=$!
echo "  Worker started (PID: $WORKER_PID)"

# Start dashboard in foreground (uses $PORT from Railway)
# This is the only process that must bind to $PORT for Railway health checks
echo "Starting dashboard on port ${PORT:-8000}..."
echo ""
exec python dashboard_server.py --port ${PORT:-8000}
