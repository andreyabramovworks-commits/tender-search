import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    String,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .model import merge


class Base(DeclarativeBase):
    pass


class Tender(Base):
    __tablename__ = "tenders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    raw: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenderVersion(Base):
    __tablename__ = "tender_versions"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)


class QueryRun(Base):
    __tablename__ = "query_runs"
    __table_args__ = (UniqueConstraint("run_id", "category", "query", "mode", "scope"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    category: Mapped[str] = mapped_column(String(80))
    query: Mapped[str] = mapped_column(String(500))
    mode: Mapped[str] = mapped_column(String(20))
    scope: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    records: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class TenderDocument(Base):
    __tablename__ = "tender_documents"
    __table_args__ = (UniqueConstraint("tender_id", "url"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(1000))
    url: Mapped[str] = mapped_column(String(4000))
    access_status: Mapped[str] = mapped_column(String(80), default="unknown")
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenderAnalysis(Base):
    __tablename__ = "tender_analyses"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"), index=True)
    status: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Store:
    def __init__(self, database_url):
        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://")
        self.engine = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 15})
        self.lock = self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        if not self.lock.execute(text("SELECT pg_try_advisory_lock(741936201) ")).scalar():
            self.lock.close()
            self.engine.dispose()
            raise RuntimeError("Another tender-search process is running")

    def begin_run(self):
        run_id = uuid.uuid4()
        with Session(self.engine) as session, session.begin():
            session.execute(
                text("UPDATE runs SET status='interrupted',finished_at=now() WHERE status='running'")
            )
            session.add(Run(id=run_id, started_at=datetime.now(UTC), status="running", summary={}))
        return run_id

    def finish_run(self, run_id, status, summary):
        with Session(self.engine) as session, session.begin():
            row = session.get(Run, run_id)
            row.status, row.summary, row.finished_at = status, summary, datetime.now(UTC)

    def record_job(self, run_id, job, status, count, error=None):
        with Session(self.engine) as session, session.begin():
            session.add(
                QueryRun(
                    run_id=run_id,
                    category=job["category"],
                    query=job["query"],
                    mode=job["mode"],
                    scope=job["scope"],
                    status=status,
                    records=count,
                    error=error,
                )
            )

    def upsert(self, incoming, raw, match, now):
        with Session(self.engine) as session, session.begin():
            row = session.get(Tender, int(incoming["id"]))
            previous = row.payload if row else None
            record = merge(previous, incoming, match, now)
            if row is None:
                row = Tender(id=int(record["id"]), payload=record, raw=raw, updated_at=datetime.now(UTC))
                session.add(row)
                session.flush()
            else:
                row.payload, row.raw, row.updated_at = record, raw, datetime.now(UTC)
            if not previous or previous["content_hash"] != record["content_hash"]:
                session.add(TenderVersion(tender_id=row.id, payload=record, captured_at=datetime.now(UTC)))
        return record

    def records(self):
        with Session(self.engine) as session:
            return list(session.scalars(select(Tender.payload).order_by(Tender.id)))

    def seed_document_links(self, records):
        with Session(self.engine) as session, session.begin():
            for record in records:
                for kind, title, url in (
                    ("tenderland_card", "Карточка TenderLand (служебная ссылка)", record.get("url", "")),
                    (
                        "source_entry",
                        "Страница первоисточника (поиск вложений)",
                        record.get("source_url", ""),
                    ),
                ):
                    if not url:
                        continue
                    exists = session.scalar(
                        select(TenderDocument.id).where(
                            TenderDocument.tender_id == int(record["id"]), TenderDocument.url == url
                        )
                    )
                    if not exists:
                        session.add(
                            TenderDocument(
                                tender_id=int(record["id"]),
                                kind=kind,
                                title=title,
                                url=url,
                                access_status="requires_manual_access",
                                document_metadata={},
                                discovered_at=datetime.now(UTC),
                            )
                        )

    def add_document_links(self, tender_id, documents):
        with Session(self.engine) as session, session.begin():
            for document in documents:
                exists = session.scalar(
                    select(TenderDocument.id).where(
                        TenderDocument.tender_id == int(tender_id), TenderDocument.url == document["url"]
                    )
                )
                if not exists:
                    session.add(
                        TenderDocument(
                            tender_id=int(tender_id),
                            kind=document["kind"],
                            title=document["title"],
                            url=document["url"],
                            access_status=document["access_status"],
                            document_metadata={},
                            discovered_at=datetime.now(UTC),
                        )
                    )

    def replace_document_links(self, tender_id, documents):
        """Refresh crawler output without touching the two audit entry links."""
        with Session(self.engine) as session, session.begin():
            session.query(TenderDocument).filter(
                TenderDocument.tender_id == int(tender_id),
                TenderDocument.kind.notin_(("tenderland_card", "source_entry")),
            ).delete(synchronize_session=False)
            for document in documents:
                session.add(
                    TenderDocument(
                        tender_id=int(tender_id),
                        kind=document["kind"],
                        title=document["title"],
                        url=document["url"],
                        access_status=document["access_status"],
                        document_metadata={},
                        discovered_at=datetime.now(UTC),
                    )
                )

    def documents(self, tender_ids=None):
        with Session(self.engine) as session:
            statement = select(TenderDocument).order_by(TenderDocument.tender_id, TenderDocument.id)
            if tender_ids is not None:
                statement = statement.where(TenderDocument.tender_id.in_([int(x) for x in tender_ids]))
            return [
                {
                    "tender_id": str(x.tender_id),
                    "kind": x.kind,
                    "title": x.title,
                    "url": x.url,
                    "access_status": x.access_status,
                    "sha256": x.content_sha256,
                    "discovered_at": x.discovered_at.isoformat(),
                }
                for x in session.scalars(statement)
            ]

    def status(self):
        with Session(self.engine) as session:
            rows = session.scalars(select(Run).order_by(Run.started_at.desc()).limit(5)).all()
            return [
                {
                    "id": str(x.id),
                    "started_at": x.started_at.isoformat(),
                    "status": x.status,
                    "summary": x.summary,
                }
                for x in rows
            ]

    def close(self):
        self.lock.execute(text("SELECT pg_advisory_unlock(741936201)"))
        self.lock.close()
        self.engine.dispose()
