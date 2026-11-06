import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .classification import DIRECT, NON_TARGET, PARTNER, classify, default_rules
from .config import Settings
from .db import Store
from .sheets import GoogleSheets
from .source import SourceError, TenderLand

LOG = logging.getLogger("tender-search")


def sync_review_queues(sheets, records, rules=None):
    buckets = {"Тендеры": [], "Партнёрам": [], "Нецелевые": []}
    routes = {DIRECT: "Тендеры", PARTNER: "Партнёрам", NON_TARGET: "Нецелевые"}
    for record in records:
        decision = classify(record, rules)
        buckets[routes[decision.bucket]].append({**record, "_decision": decision})
    outcome = sheets.sync_partitioned(buckets)
    outcome["counts"] = {title: len(items) for title, items in buckets.items()}
    return outcome


def now():
    return datetime.now(UTC).isoformat()


def export_snapshot(settings, run_id, summary, records):
    folder = settings.data_dir / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d_%H%M%S_MSK")
    target = folder / f"tenders_{stamp}_{run_id}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"run_id": str(run_id), "summary": summary, "tenders": records}, ensure_ascii=False, indent=2
        ),
        encoding="utf8",
    )
    temporary.replace(target)
    return str(target)


def run(settings, args):
    store = Store(settings.database_url)
    client = TenderLand(settings)
    run_id = store.begin_run()
    summary = {"jobs_ok": 0, "jobs_failed": 0, "matches": 0, "details_failed": 0, "synced": False}
    seen = set()
    details_cache = {}
    fatal = None
    try:
        reference = {
            "keywords": settings.reference_keywords() if hasattr(settings, "reference_keywords") else None,
            "rules": [
                dict(zip(("id", "priority", "bucket", "name", "all", "any", "reason"), row))
                for row in default_rules()
            ],
        }
        if not args.collect_only:
            sheets = GoogleSheets(settings)
            sheets.metadata()  # fail before a long collection if access is not configured
            if hasattr(sheets, "reference"):
                reference = sheets.reference(reference)
        client.login()
        jobs = (
            list(settings.jobs(reference["keywords"]))
            if reference["keywords"] is not None
            else list(settings.jobs())
        )
        summary["jobs_planned"] = len(jobs)
        if args.limit_jobs:
            jobs = jobs[: args.limit_jobs]
        for index, job in enumerate(jobs, 1):
            count = 0
            LOG.info(
                "Job %s/%s: %s %s %s %s",
                index,
                len(jobs),
                job["category"],
                job["mode"],
                job["scope"],
                job["query"],
            )
            try:
                for incoming, raw in client.search(job):
                    tender_id = incoming["id"]
                    if tender_id not in details_cache:
                        try:
                            details_cache[tender_id] = client.enrich(incoming)
                        except SourceError:
                            summary["details_failed"] += 1
                            details_cache[tender_id] = (incoming, {})
                            LOG.warning(
                                "Detail unavailable for tender %s; missing fields remain empty", tender_id
                            )
                    enriched, detail = details_cache[tender_id]
                    store.upsert({**incoming, **enriched}, {"search": raw, "detail": detail}, job, now())
                    seen.add(tender_id)
                    count += 1
                store.record_job(run_id, job, "success", count)
                summary["jobs_ok"] += 1
                summary["matches"] += count
            except (SourceError, ValueError) as error:
                store.record_job(run_id, job, "failed", count, str(error)[:1000])
                summary["jobs_failed"] += 1
                LOG.error("Job failed: %s", error)
        # A tender no longer returned by the active filter may have closed/been cancelled.
        missing = [x for x in store.records() if x["id"] not in seen]
        refreshed = set()
        try:
            for incoming, raw in client.refresh(missing, settings.search_modes[0]):
                try:
                    enriched, detail = client.enrich(incoming)
                except SourceError:
                    LOG.warning("Previously found tender %s could not be refreshed", incoming["id"])
                    continue
                store.upsert(enriched, {"search": raw, "detail": detail}, None, now())
                refreshed.add(incoming["id"])
        except SourceError:
            # An unavailable archived card must not prevent delivery of new tenders.
            LOG.warning("Refresh incomplete; delivering new records with partial run status")
        summary["not_refreshed"] = len(missing) - len(refreshed)
        summary["unique_seen"] = len(seen)
        if not args.collect_only:
            summary["sheets"] = sync_review_queues(sheets, store.records(), reference["rules"])
            if hasattr(store, "seed_document_links"):
                store.seed_document_links(store.records())
                summary["documents"] = sheets.sync_documents(store.documents())
                summary["ai_analysis"] = sheets.ensure_ai_analysis()
            summary["synced"] = True
    except Exception as error:
        # No credential-bearing response bodies/tracebacks in routine logs.
        fatal = type(error).__name__
        LOG.error("Run stopped (%s): %s", fatal, str(error)[:500])
        summary["fatal_error"] = fatal
    finally:
        client.close()
        summary["http_requests"] = client.request_count
        complete = (
            not fatal
            and not summary["jobs_failed"]
            and not summary["details_failed"]
            and not summary.get("not_refreshed")
            and summary["jobs_ok"] == summary.get("jobs_planned")
        )
        status = (
            "success"
            if complete and summary["synced"]
            else "collected"
            if complete and args.collect_only
            else "partial"
            if not fatal
            else "failed"
        )
        summary["status"] = status
        try:
            summary["report"] = export_snapshot(settings, run_id, summary, store.records())
            store.finish_run(run_id, status, summary)
            LOG.info("Run %s: %s", run_id, json.dumps(summary, ensure_ascii=False))
        finally:
            store.close()
    return 0 if status in {"success", "collected"} else 2


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="TenderLand monitoring without AI")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("run")
    collect.add_argument("--collect-only", action="store_true", help="Store results without Google sync")
    collect.add_argument(
        "--limit-jobs", type=int, help="Diagnostic partial run; never reported as full success"
    )
    sub.add_parser("check")
    sub.add_parser("status")
    sub.add_parser("sync")
    sub.add_parser("validate-keywords")
    probe = sub.add_parser("probe")
    probe.add_argument("--query", default="строит++лес")
    probe.add_argument("--scope", choices=["title", "documents"], default="title")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "run":
        if args.limit_jobs is not None and args.limit_jobs < 1:
            parser.error("--limit-jobs must be positive")
        return run(settings, args)
    if args.command in {"probe", "validate-keywords", "check"}:
        client = TenderLand(settings)
        try:
            client.login()
            for mode in settings.search_modes:
                client.open_mode(mode)
                LOG.info("TenderLand %s: access OK", mode)
                if args.command == "probe":
                    job = {"query": args.query, "mode": mode, "scope": args.scope}
                    first = next(client.search(job), None)
                    if first:
                        record, _ = client.enrich(first[0])
                        print(json.dumps(record, ensure_ascii=False, indent=2))
                    else:
                        LOG.info("No matching active tenders in %s", mode)
            if args.command == "validate-keywords":
                for query in dict.fromkeys(j["query"] for j in settings.jobs()):
                    client.validate_syntax(query)
                    LOG.info("Syntax OK: %s", query)
            if args.command == "check":
                store = Store(settings.database_url)
                try:
                    store.records()
                    LOG.info("PostgreSQL schema: OK")
                finally:
                    store.close()
                GoogleSheets(settings).metadata()
                LOG.info("Google Sheets read access: OK (writing is verified by sync)")
        finally:
            client.close()
        return 0
    store = Store(settings.database_url)
    try:
        if args.command == "status":
            print(json.dumps(store.status(), ensure_ascii=False, indent=2))
        elif args.command == "sync":
            sheets = GoogleSheets(settings)
            reference = sheets.reference(
                {
                    "keywords": settings.reference_keywords(),
                    "rules": [
                        dict(zip(("id", "priority", "bucket", "name", "all", "any", "reason"), row))
                        for row in default_rules()
                    ],
                }
            )
            print(
                json.dumps(
                    sync_review_queues(sheets, store.records(), reference["rules"]), ensure_ascii=False
                )
            )
            store.seed_document_links(store.records())
            print(json.dumps(sheets.sync_documents(store.documents()), ensure_ascii=False))
            print(json.dumps(sheets.ensure_ai_analysis(), ensure_ascii=False))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
