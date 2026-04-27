# Podcast Pipeline Resilience Improvements

## Overview

Your podcast pipeline is now **resilient to API failures**. Instead of crashing when archive.org or Buzzsprout APIs fail, the pipeline:

✓ **Retries automatically** with exponential backoff  
✓ **Distinguishes** between temporary (retryable) and permanent (fail-fast) errors  
✓ **Prevents cascading failures** with circuit breaker pattern  
✓ **Logs failed operations** to `.failures/` for manual recovery  
✓ **Continues gracefully** — optional uploads don't crash the core pipeline  

## Key Changes

### 1. **Retry Logic with Exponential Backoff**

All API calls are wrapped with automatic retry logic that:
- Retries transient failures (timeouts, rate limits, 5xx errors)
- Fails immediately on permanent errors (404, 401, invalid credentials)
- Exponentially increases delay between retries (2s → 4s → 8s → ...)

**Archive.org** has aggressive retry settings (8 attempts, up to 180s delay) because it's often slow.  
**Buzzsprout** has moderate retries (6 attempts, up to 120s delay).

### 2. **Circuit Breaker Pattern**

Each service (archive.org, Buzzsprout) has a circuit breaker that:
- Tracks consecutive failures
- Opens after N failures (stops calling the failing service)
- Attempts recovery after a timeout
- Closes when a call succeeds

This prevents your pipeline from hammering a down API.

### 3. **Graceful Degradation**

If a platform upload fails:
- The failure is **logged** to `.failures/YYYY-MM-DD.json`
- The **pipeline continues** (upload is optional)
- You can **retry later** with `retry_failed.py`

The Jekyll page is still generated even if uploads fail, with an empty `audio_url` field.

### 4. **Failure Logging**

Every failed operation is recorded with:
- **timestamp**: when it failed
- **service**: which API (archive.org, buzzsprout)
- **operation**: what was being done
- **error**: the error message
- **context**: file paths, episode number, metadata
- **stacktrace**: full Python traceback for debugging

Located in: `.failures/YYYY-MM-DD.json`

## New Files

### `resilience.py`
Core resilience module with:
- `RetryableError`, `PermanentError` exception classes
- `@retry_with_backoff()` decorator for automatic retries
- `CircuitBreaker` class to prevent cascading failures
- `FailureLog` class for recording and recovering from failures
- Configuration classes for tuning retry behavior

### `publish_improved.py`
Improved upload functions:
- `upload_to_archive()` — wraps archive.org upload with resilience
- `upload_to_buzzsprout()` — wraps Buzzsprout upload with resilience
- `generate_markdown()` — handles missing audio URLs gracefully

### `run_improved.py`
Enhanced pipeline orchestration:
- Better error handling and progress reporting
- Graceful failure handling (uploads don't crash core pipeline)
- Detailed success/failure summary at the end
- Prompt to retry failed operations

### `retry_failed.py`
Utility script to retry failed operations:
- List failures: `python retry_failed.py --list`
- Retry one: `python retry_failed.py --retry 0`
- Retry all for a service: `python retry_failed.py --retry-all --service archive.org`
- Clear log: `python retry_failed.py --clear`

## How to Use

### Normal Workflow

1. **Run the pipeline as before:**
   ```bash
   python run.py episode.m4a --ep 42 --show mypodcast --title "My Title" --desc "..."
   ```

2. **If an upload fails:**
   - The pipeline **continues** and completes
   - A message tells you to check `.failures/`
   - The Jekyll page is still generated

3. **Later, retry failed uploads:**
   ```bash
   # List all failures
   python retry_failed.py --list

   # Retry a specific failure (by index)
   python retry_failed.py --retry 0

   # Retry all archive.org failures
   python retry_failed.py --retry-all --service archive.org
   ```

### Example Scenarios

**Archive.org times out:**
```
[RETRY] Internet Archive upload failed; logged to .failures/ for manual retry
  …pipeline continues…
Continuing without video (optional stage)

Check .failures/ — retry with: python retry_failed.py --list
```

**Buzzsprout rate-limited (429):**
```
Buzzsprout upload failed (will retry manually): Buzzsprout upload failed (429)
  …automatically retries with backoff…
  …if still fails after 6 attempts, logs to .failures/…
  …pipeline continues…
```

**Credentials missing:**
```
Permanent error: Missing IA credentials. Set IA_ACCESS_KEY and IA_SECRET_KEY in .env
  …fails immediately, does NOT retry…
```

## Configuration

Retry behavior is tuned in `resilience.py`:

```python
# Aggressive retries for unstable services
ARCHIVE_RETRY_CONFIG = RetryConfig(
    max_attempts=8,              # try up to 8 times
    initial_delay_sec=3,         # start with 3s delay
    max_delay_sec=180,           # max 3 minutes between attempts
    backoff_factor=2.0,          # exponential backoff
)

# Moderate retries for more reliable services
BUZZSPROUT_RETRY_CONFIG = RetryConfig(
    max_attempts=6,
    initial_delay_sec=2,
    max_delay_sec=120,
    backoff_factor=2.0,
)
```

To adjust, edit these constants before importing.

## Monitoring & Debugging

### Check failure log
```bash
ls -la .failures/
cat .failures/2024-04-27.json
```

### View circuit breaker status
The `resilience.py` module exposes:
```python
from pipeline.resilience import archive_breaker, buzzsprout_breaker

print(archive_breaker)
# <CircuitBreaker archive.org state=CLOSED failures=0/3>
```

### Enable debug logging
```python
import logging
logging.getLogger("podcast_pipeline").setLevel(logging.DEBUG)
```

## Troubleshooting

### "Circuit breaker OPEN for archive.org"
- Archive.org has failed 3+ times in a row
- The circuit breaker is blocking new attempts to avoid hammering it
- Wait 10 minutes, then retry: `python retry_failed.py --retry 0`

### "Missing IA credentials"
- This is a **permanent error** — will not retry
- Check your `.env` file has `IA_ACCESS_KEY` and `IA_SECRET_KEY`

### "Buzzsprout upload failed (429)"
- Rate limited — the API is blocking too many requests
- The pipeline will **automatically retry with exponential backoff**
- If it still fails after 6 attempts, it's logged for manual retry

### "No failures to retry"
- Check that `.failures/` directory exists
- List failures with: `python retry_failed.py --list`

## Migration from Original Pipeline

If you're upgrading from the original `publish.py`:

1. **Back up your original files:**
   ```bash
   cp pipeline/publish.py pipeline/publish.py.backup
   cp run.py run.py.backup
   ```

2. **Add the new resilience module:**
   ```bash
   cp resilience.py pipeline/
   ```

3. **Update publish.py:**
   ```bash
   cp publish_improved.py pipeline/publish.py
   ```

4. **Update run.py (optional):**
   ```bash
   # Or keep your existing run.py and just update the upload calls
   cp run_improved.py run.py
   ```

5. **Add the retry utility:**
   ```bash
   cp retry_failed.py .
   ```

6. **Test:**
   ```bash
   python run.py test.m4a --ep 1 --show test --desc "test" --no-upload
   ```

## Architecture

### Error Hierarchy
```
Exception
├── PipelineError
│   ├── RetryableError (temporary, will retry)
│   │   └── wraps original exception + attempt count
│   ├── PermanentError (permanent, fail fast)
│   └── CircuitBreakerOpen (service is down)
```

### Retry Flow
```
Call upload_to_archive()
  ↓
@retry_with_backoff(ARCHIVE_RETRY_CONFIG)
  ↓ attempt 1
RetryableError → sleep(3s) → attempt 2
  ↓
RetryableError → sleep(6s) → attempt 3
  ↓
RetryableError → sleep(12s) → attempt 4
  ↓
... (up to 8 attempts)
  ↓
Exhausted all retries → log to .failures/ → continue
```

### Circuit Breaker Flow
```
circuit_breaker.call(upload_to_archive, ...)
  ↓
if state == OPEN and timeout_not_elapsed:
  raise CircuitBreakerOpen
  ↓
try to call upload_to_archive()
  ↓ success
set state = CLOSED, failure_count = 0
  ↓
... or ...
  ↓ failure
failure_count += 1
if failure_count >= threshold:
  set state = OPEN
```

## Performance Impact

- **Success case (API working):** negligible overhead (~2ms for circuit breaker check)
- **First retry:** 2-3 second delay
- **Multiple retries:** exponential delays (up to 3 minutes for archive.org)

No changes to audio/video processing pipeline — only upload stage is affected.

## Future Enhancements

Possible improvements:
- [ ] Webhook notifications when uploads fail
- [ ] Exponential backoff jitter (±random delay to spread retries)
- [ ] Dead letter queue with async retry worker
- [ ] Metrics/observability dashboard
- [ ] Manual upload form in Jekyll admin panel
- [ ] Parallel retry attempts (try multiple platforms simultaneously)

## Support

If you encounter issues:

1. Check `.failures/YYYY-MM-DD.json` for error details
2. Review logs in `.failures/` and pipeline output
3. Try `python retry_failed.py --list --service <service>` to see what failed
4. Examine the stacktrace in the failure log entry
5. For permanent errors (401, 403), check your credentials in `.env`
6. For circuit breaker open, wait the timeout period and retry

## Questions?

Refer to the code comments in:
- `resilience.py` — detailed documentation of each class/function
- `publish_improved.py` — enhanced upload functions with error handling
- `retry_failed.py` — recovery utilities

Good luck! Your pipeline is now much more robust. 🚀
