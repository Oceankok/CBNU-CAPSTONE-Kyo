"""Opaque revisions plus snapshot predicates for atomic zone-rule mutations."""
import hashlib
import json


SNAPSHOT_FIELDS = ('required_ppe', 'rules', 'updated_at', 'updated_by', 'revision_nonce')


def revision(row) -> str:
    # The nonce detects API ABA/recreation even when timestamp precision is low.
    # Current field values also invalidate snapshots after non-API writes. A
    # direct writer restoring all values and the nonce leaves no history here.
    values = [row['zone_name'], *(row[field] for field in SNAPSHOT_FIELDS), row['risk_level']]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def snapshot_predicate(row):
    # Compare the actual stored values in the UPDATE/DELETE, not only a prior
    # SELECT: PostgreSQL writers need not participate in our advisory lock.
    where = 'zone_name=? AND ' + ' AND '.join(f'{field}=?' for field in SNAPSHOT_FIELDS)
    # CAST gives PostgreSQL a type for the otherwise untyped null bind value.
    where += ' AND (risk_level=? OR (risk_level IS NULL AND CAST(? AS INTEGER) IS NULL))'
    values = (row['zone_name'], *(row[field] for field in SNAPSHOT_FIELDS), row['risk_level'], row['risk_level'])
    return where, values
