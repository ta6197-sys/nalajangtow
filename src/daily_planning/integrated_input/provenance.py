import hashlib
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

KST=ZoneInfo('Asia/Seoul')
VERSION='V3_NORMALIZATION_V1'


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(raw): return hashlib.sha256(raw).hexdigest()


def cell(value):
    if isinstance(value,datetime):
        return (value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)).isoformat()
    if isinstance(value,(float,Decimal)): return str(value)
    return value
