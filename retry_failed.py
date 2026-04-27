#!/usr/bin/env python3
"""
retry_failed.py — Retry previously failed API operations.

Failed operations are logged to .failures/*.json by the pipeline when
API calls fail. This script lets you review those failures and retry them
without re-running the entire pipeline.

Usage:
    # List all failures
    python retry_failed.py --list

    # List failures from a specific service
    python retry_failed.py --list --service archive.org

    # Retry a specific failure by index
    python retry_failed.py --retry 0

    # Retry all failures for a service
    python retry_failed.py --retry-all --service archive.org

    # Clear the failure log
    python retry_failed.py --clear
"""

import argparse
import sys
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from pipeline.resilience import FailureLog
from pipeline.publish import upload_to_archive, upload_to_buzzsprout


def list_failures(service=None):
    """List all failures, optionally filtered by service."""
    log = FailureLog()
    failures = log.list_failures(service=service)

    if not failures:
        print("No failures recorded.")
        return

    filter_text = f" for {service}" if service else ""
    print(f"\nRecorded failures{filter_text}:")
    print("=" * 80)

    for i, failure in enumerate(failures):
        print(f"\n[{i}] {failure.timestamp}")
        print(f"    Service   : {failure.service}")
        print(f"    Operation : {failure.operation}")
        print(f"    Error     : {failure.error[:100]}...")
        if failure.context:
            for key, val in failure.context.items():
                print(f"    {key:12}: {val}")


def retry_failure(index):
    """Retry a specific failure by index."""
    log = FailureLog()
    failures = log.list_failures()

    if not failures:
        print("No failures to retry.")
        return False

    if index < 0 or index >= len(failures):
        print(f"Error: index {index} out of range (0-{len(failures)-1})")
        return False

    failure = failures[index]
    print(f"\nRetrying failure [{index}]:")
    print(f"  Service   : {failure.service}")
    print(f"  Operation : {failure.operation}")
    print(f"  Error     : {failure.error}")

    if failure.service == "archive.org" and failure.operation == "upload_episode":
        return _retry_archive(failure)
    elif failure.service == "buzzsprout" and failure.operation == "upload_episode":
        return _retry_buzzsprout(failure)
    else:
        print(f"Error: don't know how to retry {failure.service}/{failure.operation}")
        return False


def _retry_archive(failure):
    """Retry Internet Archive upload."""
    ctx = failure.context
    file_path = Path(ctx.get("file"))

    if not file_path.exists():
        print(f"Error: file not found: {file_path}")
        return False

    try:
        # Parse the metadata we need from context
        result = upload_to_archive(
            file        = file_path,
            ep_num      = ctx.get("episode"),
            title       = ctx.get("title"),
            description = ctx.get("description", ""),
            date        = datetime.fromisoformat(ctx.get("date")) if ctx.get("date") else datetime.today(),
            show        = ctx.get("show"),
            test        = False,
        )

        if result:
            print(f"✓ Retry successful: {result}")
            return True
        else:
            print("✗ Retry failed (see logs above)")
            return False

    except Exception as e:
        print(f"✗ Retry failed with exception: {e}")
        return False


def _retry_buzzsprout(failure):
    """Retry Buzzsprout upload."""
    ctx = failure.context
    file_path = Path(ctx.get("file"))

    if not file_path.exists():
        print(f"Error: file not found: {file_path}")
        return False

    try:
        result = upload_to_buzzsprout(
            file        = file_path,
            title       = ctx.get("title"),
            description = ctx.get("description", ""),
            date        = datetime.fromisoformat(ctx.get("date")) if ctx.get("date") else datetime.today(),
            ep_num      = ctx.get("episode"),
        )

        if result:
            print(f"✓ Retry successful: episode {result.get('id')}")
            return True
        else:
            print("✗ Retry failed (see logs above)")
            return False

    except Exception as e:
        print(f"✗ Retry failed with exception: {e}")
        return False


def retry_all_for_service(service):
    """Retry all failures for a specific service."""
    log = FailureLog()
    failures = log.list_failures(service=service)

    if not failures:
        print(f"No failures for service: {service}")
        return

    print(f"\nRetrying {len(failures)} failure(s) for {service}:")
    print("=" * 80)

    successes = 0
    for i, failure in enumerate(failures):
        print(f"\n[{i}/{len(failures)}] {failure.timestamp}")
        if _retry_by_service(failure):
            successes += 1

    print(f"\nRetry summary: {successes}/{len(failures)} succeeded")


def _retry_by_service(failure):
    """Retry based on service type."""
    if failure.service == "archive.org":
        return _retry_archive(failure)
    elif failure.service == "buzzsprout":
        return _retry_buzzsprout(failure)
    else:
        print(f"  Error: don't know how to retry {failure.service}")
        return False


def clear_failures(service=None):
    """Clear the failure log."""
    log_dir = Path(".failures")
    if not log_dir.exists():
        print("No failures to clear.")
        return

    filter_text = f" for {service}" if service else ""
    response = input(f"Clear all failures{filter_text}? (y/N) ")
    if response.lower() != "y":
        print("Cancelled.")
        return

    if service:
        # Filter out failures for a specific service
        for log_file in log_dir.glob("*.json"):
            with open(log_file) as f:
                entries = json.load(f)

            # Keep only entries NOT matching the service
            filtered = [e for e in entries if e.get("service") != service]

            if filtered:
                with open(log_file, "w") as f:
                    json.dump(filtered, f, indent=2)
            else:
                log_file.unlink()

        print(f"Cleared failures for {service}")
    else:
        # Clear everything
        for log_file in log_dir.glob("*.json"):
            log_file.unlink()
        log_dir.rmdir()
        print("Cleared all failures")


def main():
    p = argparse.ArgumentParser(
        description="Retry previously failed API operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    p.add_argument("--list", action="store_true",
                   help="List all failures")
    p.add_argument("--service", default=None,
                   help="Filter by service (archive.org, buzzsprout)")
    p.add_argument("--retry", type=int, default=None,
                   help="Retry a specific failure by index")
    p.add_argument("--retry-all", action="store_true",
                   help="Retry all failures (optionally for a service with --service)")
    p.add_argument("--clear", action="store_true",
                   help="Clear the failure log")

    args = p.parse_args()

    if args.list:
        list_failures(service=args.service)
    elif args.retry is not None:
        success = retry_failure(args.retry)
        sys.exit(0 if success else 1)
    elif args.retry_all:
        if args.service:
            retry_all_for_service(args.service)
        else:
            # Retry all failures
            log = FailureLog()
            failures = log.list_failures()
            print(f"\nRetrying all {len(failures)} failure(s):")
            print("=" * 80)
            successes = 0
            for failure in failures:
                if _retry_by_service(failure):
                    successes += 1
            print(f"\nRetry summary: {successes}/{len(failures)} succeeded")
    elif args.clear:
        clear_failures(service=args.service)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
