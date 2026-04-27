# Integration Guide: Adding Resilience to Your Podcast Pipeline

## Quick Start (5 minutes)

### 1. Copy the new resilience module
```bash
cp resilience.py pipeline/
```

### 2. Update `pipeline/publish.py`
Replace your existing `pipeline/publish.py` with the new resilient version, or manually update the upload functions. The key changes are:

**OLD upload_to_archive():**
```python
def upload_to_archive(...):
    from internetarchive import upload
    upload(identifier, files=[str(file)], ...)
    return identifier
```

**NEW upload_to_archive():**
```python
@retry_with_backoff(ARCHIVE_RETRY_CONFIG)
def _upload_to_archive_impl(...):
    from internetarchive import upload
    # ... same as before ...
    return identifier

def upload_to_archive(...):
    try:
        return archive_breaker.call(_upload_to_archive_impl, ...)
    except Exception as e:
        failure_log.record_failure(...)
        return None  # Continue, don't crash
```

See `publish_improved.py` for full implementation.

### 3. Update `run.py` (optional but recommended)
The original `run.py` works fine, but `run_improved.py` has better error handling and reporting. Choose one:

**Option A: Minimal changes (keep your run.py)**
Just update the import and upload calls:
```python
from pipeline.resilience import failure_log

# In the upload section:
try:
    identifier = upload_to_archive(...)
except Exception as e:
    failure_log.record_failure("archive.org", "upload_episode", str(e), {...})
    identifier = None
```

**Option B: Full upgrade (use run_improved.py)**
```bash
cp run_improved.py run.py
```

### 4. Add the retry utility
```bash
cp retry_failed.py .
chmod +x retry_failed.py
```

### 5. Test it
```bash
# Test with --no-upload first
python run.py test.m4a --ep 1 --show test --desc "testing" --no-upload

# If it works, test with uploads
python run.py test.m4a --ep 2 --show test --desc "testing" --archive
```

Done! Your pipeline is now resilient. ✅

---

## Detailed Integration

### File by File

#### `pipeline/resilience.py` (NEW)
**Purpose:** Core resilience primitives

**Contains:**
- Exception hierarchy (RetryableError, PermanentError, CircuitBreakerOpen)
- @retry_with_backoff() decorator
- CircuitBreaker class
- FailureLog class
- Configuration objects

**No changes needed to your code — just import and use.**

**Usage in your code:**
```python
from pipeline.resilience import (
    retry_with_backoff, ARCHIVE_RETRY_CONFIG,
    archive_breaker, failure_log
)
```

#### `pipeline/publish.py` (UPDATED)
**Changes:**
1. Import resilience utilities
2. Wrap upload functions with @retry_with_backoff()
3. Distinguish RetryableError vs PermanentError
4. Use circuit breakers
5. Log failures instead of raising

**What stays the same:**
- Function signatures (API compatible)
- Metadata extraction (parse_date, get_duration, get_file_size)
- Jekyll page generation (generate_markdown)

**What's new:**
- Returns None on failure instead of raising (optional uploads)
- Logs failures to .failures/ for manual recovery
- Automatic retries on transient failures

#### `run.py` (OPTIONAL UPDATE)
**Option 1: Keep your existing run.py**
- Works fine with new publish.py
- Uploads won't crash pipeline
- Failures logged automatically

**Option 2: Use improved run_improved.py**
- Better error reporting
- Detailed success/failure summary
- Gracefully handles optional stages
- Prompts about failed operations

**Recommendation:** If you have a heavily customized run.py, keep it. Otherwise, use run_improved.py.

#### `retry_failed.py` (NEW)
**Purpose:** Manual recovery utility

**Commands:**
```bash
python retry_failed.py --list                    # Show all failures
python retry_failed.py --list --service archive.org
python retry_failed.py --retry 0                 # Retry specific failure
python retry_failed.py --retry-all               # Retry everything
python retry_failed.py --clear                   # Clear failure log
```

**No changes needed — just run when needed.**

---

## Configuration & Tuning

### Retry behavior
Edit `pipeline/resilience.py` to adjust:

```python
ARCHIVE_RETRY_CONFIG = RetryConfig(
    max_attempts=8,           # How many times to try
    initial_delay_sec=3,      # Delay before first retry
    max_delay_sec=180,        # Max delay between attempts
    backoff_factor=2.0,       # Exponential backoff multiplier
)
```

### Circuit breaker behavior
```python
archive_breaker = CircuitBreaker(
    "archive.org",
    failure_threshold=3,      # Open after 3 failures
    timeout_sec=600,          # Wait 10 min before attempting recovery
)
```

### Failure log location
Default: `.failures/` directory (auto-created)
Change in `run.py`:
```python
from pipeline.resilience import FailureLog
log = FailureLog(log_dir=Path("custom/path"))
```

---

## Testing Integration

### Test 1: Normal run (should work)
```bash
python run.py test.m4a --ep 1 --show test --desc "test" --no-upload
```

### Test 2: With graceful failure handling
```bash
# Simulate archive failure by temporarily breaking credentials
unset IA_ACCESS_KEY

python run.py test.m4a --ep 2 --show test --desc "test" --archive

# Should output:
# [FAIL] Internet Archive upload failed
# [RETRY] failure logged to .failures/
# [OK] Pipeline completed (core stages)

# Check failures
python retry_failed.py --list

# Fix credentials and retry
export IA_ACCESS_KEY=...
python retry_failed.py --retry 0
```

### Test 3: Retry mechanism
```bash
# If an upload failed, retry it:
python retry_failed.py --retry 0

# Retry all archive.org failures:
python retry_failed.py --retry-all --service archive.org
```

---

## Troubleshooting Integration

### Import errors
```
ModuleNotFoundError: No module named 'pipeline.resilience'
```

**Fix:** Make sure `resilience.py` is in the `pipeline/` directory:
```bash
ls -la pipeline/resilience.py
```

### FailureLog permissions
```
PermissionError: [Errno 13] Permission denied: '.failures'
```

**Fix:** Pipeline needs write access to project directory:
```bash
chmod 755 .
rm -rf .failures  # Start fresh
```

### Circuit breaker stuck OPEN
```
CircuitBreakerOpen: Circuit breaker OPEN for archive.org
```

**Fix:** Wait for timeout period (default 10 min) or manually reset:
```python
from pipeline.resilience import archive_breaker
archive_breaker.state = archive_breaker.CLOSED
archive_breaker.failure_count = 0
```

---

## Backward Compatibility

The new code is **fully backward compatible**:

1. **Function signatures unchanged:**
   ```python
   # Old code still works
   upload_to_archive(file, ep_num, title, description, date, show)
   ```

2. **Default behavior same for success:**
   ```python
   # If API works, behavior is identical
   identifier = upload_to_archive(...)  # Still returns identifier
   ```

3. **Only difference on failure:**
   ```python
   # OLD: raises exception, crashes pipeline
   # NEW: logs failure, returns None, pipeline continues
   identifier = upload_to_archive(...) or f"archive_missing_ep{ep_num}"
   ```

---

## Performance Impact

| Operation | Time Impact | Notes |
|-----------|------------|-------|
| Success case | ~2ms | Circuit breaker check |
| First retry | +2-3s | Waiting before retry |
| All retries exhausted | +390s (max) | Up to 8 attempts with delays |
| Audio/video processing | 0ms | Completely unaffected |

**In practice:** Almost no impact if APIs are working. Retries only add time if APIs fail.

---

## Monitoring Production

### Check failure logs daily
```bash
# See what failed today
ls .failures/
cat .failures/$(date +%Y-%m-%d).json | jq .

# Count failures
find .failures -name "*.json" -exec wc -l {} + | tail -1
```

### Alert on failures
```bash
# Example: alert if more than 5 failures today
FAILS=$(cat .failures/$(date +%Y-%m-%d).json 2>/dev/null | jq 'length')
if [ $FAILS -gt 5 ]; then
  curl -X POST https://alerts.example.com/ -d "failures=$FAILS"
fi
```

### Retry periodically
```bash
# In a cron job: retry failed uploads daily
0 2 * * * cd /path/to/project && python retry_failed.py --retry-all >> logs/retry.log
```

---

## Uninstalling Resilience (if needed)

To go back to original behavior:

```bash
# Remove new files
rm pipeline/resilience.py
rm retry_failed.py

# Restore original publish.py
cp pipeline/publish.py.backup pipeline/publish.py

# Restore original run.py
cp run.py.backup run.py

# Clean up failure logs
rm -rf .failures/
```

---

## Questions & Support

### "Can I use resilience with my custom upload function?"

Yes! Just wrap it:
```python
from pipeline.resilience import retry_with_backoff, ARCHIVE_RETRY_CONFIG

@retry_with_backoff(ARCHIVE_RETRY_CONFIG)
def my_custom_upload(...):
    # Your code here
    # Raise RetryableError or PermanentError as needed
    ...
```

### "How do I know if an upload is actually retrying?"

Check logs:
```bash
# Run with verbose logging
python -u run.py ... 2>&1 | grep -i "attempt\|retry\|retrying"
```

### "Can I modify retry behavior per episode?"

Yes, use config parameter:
```python
from pipeline.resilience import RetryConfig, retry_with_backoff

custom_config = RetryConfig(max_attempts=10, initial_delay_sec=1)

@retry_with_backoff(custom_config)
def upload_to_archive(...):
    ...
```

### "What if both archive.org AND buzzsprout fail?"

Both failures are logged independently:
```bash
cat .failures/2024-04-27.json | jq '.[] | {service, operation, error}'

# Output:
# {
#   "service": "archive.org",
#   "operation": "upload_episode",
#   "error": "..."
# }
# {
#   "service": "buzzsprout",
#   "operation": "upload_episode",
#   "error": "..."
# }
```

Then retry one or both:
```bash
python retry_failed.py --retry 0          # Retry archive.org
python retry_failed.py --retry 1          # Retry buzzsprout
python retry_failed.py --retry-all        # Retry all
```

---

## Summary

✅ **Copy 4 files** to your project  
✅ **Update 1 file** (publish.py with new upload logic)  
✅ **Test** with `python run.py ... --no-upload`  
✅ **Done!**

Your pipeline now:
- ✓ Retries on transient failures
- ✓ Fails fast on permanent errors
- ✓ Prevents cascading failures
- ✓ Logs failures for recovery
- ✓ Continues when uploads fail
- ✓ Provides recovery utilities

No more broken pipelines! 🎉
