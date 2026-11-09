import argparse
import json
import logging
import sys
import zipfile
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from .classification import DIRECT, NON_TARGET, PARTNER, classify, default_rules
from .config import Settings
from .db import Store
from .drive import DriveArchive, DriveError
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


def archive_documents(store, records, settings, limit=None):
    """Copy public attachments to Drive only after their link discovery succeeds."""
    if not settings.google_drive_folder_id:
        return {"archived": 0, "skipped": "GOOGLE_DRIVE_FOLDER_ID is missing"}
    by_id = {x["id"]: x for x in records}
    archive = DriveArchive(settings)
    archived = 0
    candidates = []
    for document in store.documents(list(by_id)):
        likely_zip = "zip" in document["title"].lower() or document["kind"].lower() == "zip"
        tenderland_file = "/File/Get?storageId=" in document["url"]
        if document["kind"] in {"tenderland_card", "source_entry"} or (
            document["archive_status"] == "archived" and not likely_zip
        ) or (
            document["archive_status"] == "download_failed" and not tenderland_file
        ):
            continue
        candidates.append(document)
    # Prefer TenderLand's authenticated file store.  Older SourceLink rows are
    # retained for audit/fallback, but must not consume the archive batch first.
    candidates.sort(
        key=lambda document: (0 if "/File/Get?storageId=" in document["url"] else 1, document["tender_id"])
    )
    # A batch must expand coverage across tenders before it fills one huge
    # package.  That creates visible tender folders promptly and makes the
    # document queue useful while the full archive is still running.
    selected, selected_ids = [], set()
    for document in candidates:
        if document["tender_id"] not in selected_ids:
            selected.append(document)
            selected_ids.add(document["tender_id"])
            if limit and len(selected) >= limit:
                break
    if not limit:
        selected = candidates
    for document in selected:
        record = by_id[document["tender_id"]]
        try:
            results, is_zip = archive.archive(record, document)
            if is_zip:
                store.set_archive_result(document["tender_id"], document["url"], None, "unpacked")
                for title, kind, drive_url, digest in results:
                    store.add_extracted_document(
                        document["tender_id"], document["url"], title, kind, drive_url, digest
                    )
                    archived += 1
            else:
                _, _, drive_url, digest = results[0]
                store.set_archive_result(
                    document["tender_id"], document["url"], drive_url, "archived", digest
                )
                archived += 1
        except (DriveError, requests.RequestException, ValueError, zipfile.BadZipFile) as error:
            store.set_archive_result(document["tender_id"], document["url"], None, "download_failed")
            LOG.warning(
                "Drive archive unavailable for tender %s, %s: %s",
                document["tender_id"],
                document["title"][:80],
                str(error)[:180],
            )
    return {"archived": archived}


def discover_and_sync_documents(store, sheets, client, records, rules):
    """Collect attachment URLs for direct tenders, then publish only attachments.

    The collector fetches HTML pages only. It stores URLs and metadata; tender
    files themselves are intentionally not downloaded at this stage.
    """
    direct_records = [x for x in records if classify(x, rules).bucket == DIRECT]
    direct_ids = [x["id"] for x in direct_records]
    store.seed_document_links(direct_records)
    discovered = 0
    for record in direct_records:
        tenderland_links = []
        source_links = []
        try:
            tenderland_links = client.discover_tenderland_documents(record)
        except Exception as error:  # A card may disappear while its original platform remains available.
            LOG.warning(
                "TenderLand files unavailable for tender %s: %s",
                record["id"],
                str(error)[:180],
            )
        try:
            source_links = client.discover_documents(record)
        except Exception as error:  # The external source is only a supplementary discovery route.
            LOG.warning(
                "Source files unavailable for tender %s: %s",
                record["id"],
                str(error)[:180],
            )
        try:
            links = client._merge_document_links(tenderland_links, source_links)
            # The entry pages are audit records, not attachments. Never let a
            # crawler rediscover one and collide with its unique database row.
            links = [
                link
                for link in links
                if link["url"] not in {record.get("url", ""), record.get("source_url", "")}
            ]
            store.replace_document_links(record["id"], links)
            discovered += len(links)
        except Exception as error:  # One unavailable procurement site must not stop the daily run.
            LOG.warning("Document registry unavailable for tender %s: %s", record["id"], str(error)[:180])
    result = archive_documents(store, direct_records, client.settings)
    result.update(sheets.sync_documents(store.documents(direct_ids)))
    result["scanned_tenders"] = len(direct_records)
    result["new_links_seen"] = discovered
    return result


def refresh_without_search(settings):
    """Refresh queues, documents and the AI registry without a new TenderLand search."""
    store = Store(settings.database_url)
    try:
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
        records = store.records()
        summary = {"queues": sync_review_queues(sheets, records, reference["rules"])}
        client = TenderLand(settings)
        try:
            client.login()
            summary["documents"] = discover_and_sync_documents(
                store, sheets, client, records, reference["rules"]
            )
        finally:
            client.close()
        summary["ai_analysis"] = sheets.clean_ai_analysis()
        return summary
    finally:
        store.close()


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
            current_records = store.records()
            summary["sheets"] = sync_review_queues(sheets, current_records, reference["rules"])
            if hasattr(store, "seed_document_links"):
                summary["documents"] = discover_and_sync_documents(
                    store, sheets, client, current_records, reference["rules"]
                )
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
    sub.add_parser("sync-documents")
    sub.add_parser("rebuild-documents")
    sub.add_parser("sync-queues")
    sub.add_parser("clean-ai-analysis")
    sub.add_parser("refresh-workflow")
    sub.add_parser("collect-documents")
    archive = sub.add_parser("archive-documents")
    archive.add_argument("--limit", type=int, default=10, help="Maximum pending files per safe archive batch")
    inspect_files = sub.add_parser("inspect-tenderland-files")
    inspect_files.add_argument("--id", required=True, help="TenderLand ID for authenticated file-route diagnostics")
    sub.add_parser("inspect-document-sheet")
    cleanup = sub.add_parser("cleanup-tender-artifacts")
    cleanup.add_argument("--id", required=True, help="Tender ID whose prior Office XML artefacts will be moved to Drive trash")
    duplicates = sub.add_parser("cleanup-drive-duplicates")
    duplicates.add_argument("--folder-id", required=True, help="Google Drive tender folder whose exact-name duplicates will be moved to trash")
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
    if args.command in {"probe", "validate-keywords", "check", "inspect-tenderland-files"}:
        client = TenderLand(settings)
        try:
            client.login()
            if args.command == "inspect-tenderland-files":
                record = {"id": args.id}
                entity = client.entity(record)
                page = client.entity_pages[args.id]
                positions = [page.find("StorageId"), page.find("ConvertedStorageId"), page.find("SourceLink")]
                excerpts = [page[max(0, pos - 500) : pos + 1000] for pos in positions if pos >= 0]
                print(json.dumps({"files": entity.get("Files", []), "page_excerpts": excerpts}, ensure_ascii=False))
                return 0
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
    if args.command == "inspect-document-sheet":
        sheets = GoogleSheets(settings)
        print(json.dumps(sheets.values("'Документы'!A1:I5"), ensure_ascii=False))
        return 0
    if args.command == "cleanup-drive-duplicates":
        removed = DriveArchive(settings).purge_exact_duplicates(args.folder_id)
        print(json.dumps({"drive_duplicates_trashed": removed}, ensure_ascii=False))
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
            current_records = store.records()
            print(
                json.dumps(
                    sync_review_queues(sheets, current_records, reference["rules"]), ensure_ascii=False
                )
            )
            client = TenderLand(settings)
            try:
                # Document lists are available from the authenticated TenderLand
                # card, not from an anonymous browser session.
                client.login()
                print(
                    json.dumps(
                        discover_and_sync_documents(
                            store, sheets, client, current_records, reference["rules"]
                        ),
                        ensure_ascii=False,
                    )
                )
            finally:
                client.close()
            print(json.dumps(sheets.ensure_ai_analysis(), ensure_ascii=False))
        elif args.command == "sync-documents":
            sheets = GoogleSheets(settings)
            # The registry is the document queue for direct work only, never
            # the full historical database of target/partner/non-target rows.
            tender_ids = [
                record["id"] for record in store.records() if classify(record).bucket == DIRECT
            ]
            print(json.dumps(sheets.sync_documents(store.documents(tender_ids)), ensure_ascii=False))
            print(json.dumps(sheets.ensure_ai_analysis(), ensure_ascii=False))
        elif args.command == "rebuild-documents":
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
            tender_ids = [
                record["id"] for record in store.records() if classify(record, reference["rules"]).bucket == DIRECT
            ]
            print(json.dumps(sheets.sync_documents(store.documents(tender_ids)), ensure_ascii=False))
            print(json.dumps(sheets.ensure_ai_analysis(), ensure_ascii=False))
        elif args.command == "clean-ai-analysis":
            sheets = GoogleSheets(settings)
            print(json.dumps(sheets.clean_ai_analysis(), ensure_ascii=False))
        elif args.command == "refresh-workflow":
            # This is intentionally separate from `run`: no search queries are
            # sent to TenderLand, but all existing queues and document records
            # are reconciled before the AI registry is pruned by TenderLand ID.
            print(json.dumps(refresh_without_search(settings), ensure_ascii=False))
        elif args.command == "sync-queues":
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
            print(json.dumps(sync_review_queues(sheets, store.records(), reference["rules"]), ensure_ascii=False))
        elif args.command == "collect-documents":
            sheets = GoogleSheets(settings)
            records = [record for record in store.records() if classify(record).bucket == DIRECT]
            client = TenderLand(settings)
            try:
                client.login()
                for record in records:
                    try:
                        links = client.discover_tenderland_documents(record)
                        store.replace_document_links(record["id"], links)
                    except SourceError as error:
                        LOG.warning("TenderLand files unavailable for tender %s: %s", record["id"], error)
            finally:
                client.close()
            print(json.dumps(sheets.sync_documents(store.documents([record["id"] for record in records])), ensure_ascii=False))
        elif args.command == "archive-documents":
            if args.limit < 1:
                parser.error("--limit must be positive")
            sheets = GoogleSheets(settings)
            records = [record for record in store.records() if classify(record).bucket == DIRECT]
            result = archive_documents(store, records, settings, limit=args.limit)
            result.update(sheets.sync_documents(store.documents([record["id"] for record in records])))
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "cleanup-tender-artifacts":
            record = next((x for x in store.records() if str(x["id"]) == str(args.id)), None)
            if not record:
                parser.error("Tender ID not found")
            removed_drive = DriveArchive(settings).purge_office_artifacts(record)
            removed_db = store.delete_extracted_artifacts(record["id"])
            sheets = GoogleSheets(settings)
            direct_ids = [x["id"] for x in store.records() if classify(x).bucket == DIRECT]
            sheets.sync_documents(store.documents(direct_ids))
            print(json.dumps({"drive_trashed": removed_drive, "registry_removed": removed_db}, ensure_ascii=False))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
