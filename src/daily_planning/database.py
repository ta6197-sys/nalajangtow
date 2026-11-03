"""PostgreSQL connection settings shared by migrations and daily planning."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def database_url():
    configured = os.environ.get('DATABASE_URL')
    if configured:
        url = make_url(configured)
        if url.get_backend_name() != 'postgresql':
            raise ValueError('Daily planning requires PostgreSQL')
        return url.set(drivername='postgresql+psycopg')
    password = os.environ.get('POSTGRES_PASSWORD')
    if not password:
        env_path = ROOT / '.env'
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith('POSTGRES_PASSWORD='):
                    password = line.partition('=')[2].strip()
                    break
    if not password or password == 'replace-with-generated-password':
        raise ValueError('Set POSTGRES_PASSWORD or DATABASE_URL before connecting')
    return URL.create('postgresql+psycopg', username=os.environ.get('POSTGRES_USER', 'ulsan_clx'),
                      password=password, host=os.environ.get('POSTGRES_HOST', '127.0.0.1'),
                      port=int(os.environ.get('POSTGRES_PORT', '5432')),
                      database=os.environ.get('POSTGRES_DB', 'ulsan_clx'))


def get_engine():
    return create_engine(database_url(), pool_pre_ping=True, hide_parameters=True,
                         connect_args={'options': '-c timezone=Asia/Seoul'})
