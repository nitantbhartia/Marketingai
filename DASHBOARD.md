# ClaimCoach Review Dashboard

A modern, web-based dashboard for reviewing and managing articles in the ClaimCoach content pipeline.

## Features

### 📊 Dashboard Overview
- Real-time article status tracking
- Filter articles by status (All, Review, Ready to Publish, Revision)
- Quick stats cards showing pipeline health
- Recent notifications feed
- Auto-refresh every 30 seconds

### 📝 Article Review Page
Comprehensive review interface showing:

**Quality Scores:**
- SEO Score (0-100) with visual progress bars
- Readability Score (0-100) with grade level indicators
- Color-coded scoring: Green (excellent), Yellow (good), Red (needs work)

**Validation Metrics:**
- Product Compliance (PASS/FAIL)
- State Accuracy (PASS/FAIL)
- Broken Links Count
- Math Errors Count

**Metadata:**
- Meta Title (with character count)
- Meta Description (with character count)
- URL Slug
- Target Keyword
- Target State
- Word Count
- Revision Count

**CTA Tracking:**
- CTA variant text and positions
- Performance metrics (impressions, clicks, conversions)

**Content Preview:**
- First 2,000 characters preview
- "Show Full Content" button for complete article
- Markdown formatted

**Action History:**
- Timeline of all agent actions
- Timestamps and details for each event

**Review Actions:**
- ✅ Approve & Publish
- ✏️ Request Revision
- 🚀 Publish Immediately
- 👁 View Published (if published)

## Installation & Setup

### 1. Install Dependencies

```bash
pip install fastapi uvicorn jinja2 python-multipart
```

### 2. Configure Dashboard URL

Edit your `config.yaml`:

```yaml
pipeline:
  dashboard_url: "http://localhost:8000"  # Set to empty string to disable notifications
```

### 3. Start Dashboard Server

```bash
# Start the dashboard on default port 8000
python dashboard_server.py

# Or specify a different port
python dashboard_server.py --port 8080
```

### 4. Access Dashboard

Open your browser to:
- Main dashboard: http://localhost:8000
- Article review: http://localhost:8000/article/{article_id}

## Usage

### Reviewing Articles

1. **Navigate to Dashboard**
   - Visit http://localhost:8000
   - See all articles awaiting review

2. **Filter by Status**
   - Click "Review", "Ready to Publish", or "Revision" buttons
   - View articles in specific pipeline stages

3. **Review Article Details**
   - Click "Review" button on any article
   - See comprehensive quality scores and metadata
   - Read validation issues and revision notes
   - View action history

4. **Take Action**

   **Approve:**
   - Click "✅ Approve & Publish"
   - Optionally add notes
   - Article moves to "ready_to_publish" status

   **Request Revision:**
   - Click "✏️ Request Revision"
   - Describe what needs fixing (required)
   - Article returns to Quill for rewriting

   **Publish Immediately:**
   - Click "🚀 Publish Immediately"
   - Ezra generates HTML and publishes
   - Opens published URL

### Keyboard Shortcuts

On article review page:
- `Ctrl/Cmd + Enter` - Approve article
- `Ctrl/Cmd + R` - Request revision
- `Escape` - Close modal dialogs

## API Endpoints

### GET /
Main dashboard showing all articles in review.

**Query Parameters:**
- `status` - Filter by status (review, ready_to_publish, revision)

**Returns:** HTML dashboard

---

### GET /article/{article_id}
Detailed article review page.

**Parameters:**
- `article_id` - Article ID to review

**Returns:** HTML review page

---

### POST /api/article/{article_id}/approve
Approve article for publishing.

**Request Body:**
```json
{
  "notes": "Optional approval notes"
}
```

**Returns:**
```json
{
  "status": "approved",
  "article_id": 123,
  "new_status": "ready_to_publish"
}
```

---

### POST /api/article/{article_id}/reject
Send article back for revision.

**Request Body:**
```json
{
  "reason": "Describe what needs fixing (required)"
}
```

**Returns:**
```json
{
  "status": "rejected",
  "article_id": 123,
  "new_status": "revision"
}
```

---

### POST /api/article/{article_id}/publish
Publish article immediately.

**Returns:**
```json
{
  "status": "published",
  "article_id": 123,
  "published_url": "https://claimcoach.app/blog/article-slug"
}
```

---

### POST /api/notifications/article-ready
Webhook endpoint for agents to POST notifications.

**Request Body:**
```json
{
  "article_id": 123,
  "status": "approved",
  "score": 92.5,
  "agent": "sage",
  "message": "Article 123 is ready for review (score: 92.5/100)"
}
```

**Returns:**
```json
{
  "status": "received",
  "article_id": 123
}
```

## Agent Integration

### Sage Agent

Sage automatically notifies the dashboard when articles are ready for review.

**Notification Triggers:**
- Article scores >= 90 (approved)
- Article scores 70-89 (revision needed)

**Configuration:**
Set `dashboard_url` in config.yaml to enable notifications.

**Example:**
```yaml
pipeline:
  dashboard_url: "http://localhost:8000"
```

If `dashboard_url` is empty, notifications are disabled (no error thrown).

### Custom Agents

Other agents can send notifications too:

```python
import requests

def notify_dashboard(article_id, status, message):
    """Send notification to dashboard."""
    webhook_url = config.pipeline.dashboard_url + "/api/notifications/article-ready"

    payload = {
        "article_id": article_id,
        "status": status,
        "agent": "custom_agent",
        "message": message
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        if response.status_code == 200:
            logger.info(f"Dashboard notified about article {article_id}")
    except Exception as e:
        logger.warning(f"Failed to notify dashboard: {e}")
```

## Deployment

### Development

```bash
# Run locally with auto-reload
uvicorn dashboard_server:app --reload --port 8000
```

### Production

```bash
# Run with Gunicorn (recommended for production)
pip install gunicorn

gunicorn dashboard_server:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -
```

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["gunicorn", "dashboard_server:app", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000"]
```

### Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name dashboard.claimcoach.app;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Security Considerations

### Authentication

The current implementation has **no authentication**. For production use, add authentication:

**Option 1: Basic Auth (Nginx)**
```nginx
auth_basic "Dashboard Login";
auth_basic_user_file /etc/nginx/.htpasswd;
```

**Option 2: OAuth2 (via Nginx)**
Use nginx-oauth2-proxy or similar

**Option 3: FastAPI Dependencies**
```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    # Implement your auth logic
    if not check_credentials(credentials.username, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    return credentials.username

@app.get("/")
async def dashboard(username: str = Depends(verify_credentials)):
    # Protected route
    ...
```

### HTTPS

Always use HTTPS in production:
- Use Let's Encrypt for free SSL certificates
- Configure nginx/Apache with SSL
- Redirect HTTP to HTTPS

### Rate Limiting

Add rate limiting to prevent abuse:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/article/{article_id}/approve")
@limiter.limit("10/minute")
async def approve_article(request: Request, article_id: int):
    ...
```

## Troubleshooting

### Dashboard Won't Start

**Error:** `Address already in use`
- Another process is using port 8000
- Solution: Kill the process or use a different port
  ```bash
  lsof -i :8000  # Find process
  kill -9 <PID>  # Kill it
  # Or use different port:
  python dashboard_server.py --port 8080
  ```

### No Articles Showing

**Problem:** Dashboard is empty
- Check database: `sqlite3 pipeline.db "SELECT * FROM articles WHERE status='review' LIMIT 5;"`
- Verify database path in code matches config
- Check server logs for errors

### Notifications Not Working

**Problem:** Sage doesn't notify dashboard
- Verify `dashboard_url` is set in config.yaml
- Check Sage agent logs for webhook errors
- Verify dashboard server is running
- Test webhook manually:
  ```bash
  curl -X POST http://localhost:8000/api/notifications/article-ready \
    -H "Content-Type: application/json" \
    -d '{"article_id": 1, "status": "approved", "score": 90, "agent": "sage", "message": "Test"}'
  ```

### CSS/JS Not Loading

**Problem:** Dashboard looks broken
- Check static files exist: `ls static/dashboard.css static/dashboard.js`
- Verify static file mounting in FastAPI app
- Check browser console for 404 errors
- Clear browser cache

### Performance Issues

**Problem:** Dashboard is slow
- Enable database indexes (see schema)
- Reduce auto-refresh interval in dashboard.js
- Use pagination for large article lists
- Add caching with Redis/Memcached

## Future Enhancements

Potential improvements:

1. **Real-time Updates**
   - WebSocket support for live notifications
   - No need to refresh page

2. **Batch Operations**
   - Approve/reject multiple articles at once
   - Bulk actions with checkboxes

3. **Advanced Filtering**
   - Filter by keyword, state, score range
   - Search functionality
   - Date range filters

4. **User Management**
   - Multiple reviewer accounts
   - Role-based permissions (reviewer, editor, admin)
   - Audit logs of who approved what

5. **Analytics Dashboard**
   - Charts showing pipeline throughput
   - Average review scores over time
   - Agent performance metrics
   - Revision rate tracking

6. **Inline Editing**
   - Edit article content directly in dashboard
   - WYSIWYG markdown editor
   - Auto-save drafts

7. **Mobile App**
   - React Native or Flutter app
   - Review articles on the go
   - Push notifications

8. **Integrations**
   - Slack notifications
   - Email digests
   - Zapier webhooks

## Support

For issues or questions:
- Check logs: `tail -f dashboard.log`
- Check agent logs: `tail -f pipeline.log`
- Review database: `sqlite3 pipeline.db`
- GitHub Issues: [your-repo]/issues

## License

Same as main ClaimCoach pipeline project.
