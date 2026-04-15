from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from database.connection import Base, engine


class JiraBugLink(Base):
    __tablename__ = "jira_bug_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bug_id = Column(Integer, nullable=False, unique=True, index=True, comment="Local bug ID")
    issue_key = Column(String(100), nullable=False, unique=True, index=True, comment="Jira issue key")
    project_key = Column(String(50), comment="Jira project key")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def ensure_jira_bug_links_table():
    JiraBugLink.__table__.create(bind=engine, checkfirst=True)
