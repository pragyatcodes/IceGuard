"""ICEGUARD tables: users, bergs, voyages, forecasts cache, audit, config."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, Boolean
from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(16), nullable=False, default="viewer")  # viewer|operator|admin
    display_name = Column(String(128), default="")
    created_at = Column(DateTime, default=utcnow)


class Berg(Base):
    __tablename__ = "bergs"
    id = Column(Integer, primary_key=True)
    berg_id = Column(String(32), unique=True, nullable=False)  # e.g. A23a
    name = Column(String(128), default="")
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    size_class = Column(String(16), default="large")  # small|medium|large|tabular|A23a-class
    area_km2 = Column(Float, default=10.0)
    source = Column(String(32), default="BYU/NSIDC")
    updated_at = Column(DateTime, default=utcnow)


class Voyage(Base):
    __tablename__ = "voyages"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    title = Column(String(256), default="")
    corridor_json = Column(Text, default="[]")  # [[lat,lon],...]
    ship_class = Column(String(32), default="ice-class")
    status = Column(String(16), default="planned")
    created_by = Column(String(64), default="")
    created_at = Column(DateTime, default=utcnow)

    @property
    def corridor(self):
        try:
            return json.loads(self.corridor_json or "[]")
        except Exception:
            return []


class Feed(Base):
    __tablename__ = "feeds"
    id = Column(Integer, primary_key=True)
    feed_id = Column(String(32), unique=True, nullable=False)  # sar, sic, wind...
    name = Column(String(128), default="")
    age_hours = Column(Float, default=6.0)
    latency_note = Column(String(256), default="")


class Threshold(Base):
    __tablename__ = "thresholds"
    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(Float, nullable=False)
    note = Column(String(256), default="")


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id = Column(Integer, primary_key=True)
    version = Column(String(32), unique=True, nullable=False)
    kind = Column(String(64), default="physics+residual")
    mae_72_km = Column(Float, default=20.0)
    coverage_90 = Column(Float, default=0.90)
    frozen = Column(Boolean, default=False)
    active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=utcnow)
    actor = Column(String(64), default="")
    role = Column(String(16), default="")
    action = Column(String(64), default="")  # advice|override|login|config|freeze|export
    detail = Column(Text, default="")
    badge = Column(String(16), default="")
