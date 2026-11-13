"""Versioned exact semantic identity, independent of recommendation rank."""
import hashlib
import json


def recommendation_id(quarter, zone_name, ppe_type, education_topic):
    # No lossy slugging, trimming, casefolding or delimiter concatenation.
    # Topic is part of scope; score/rank/generated_at deliberately are not.
    payload = json.dumps([quarter, zone_name, ppe_type, education_topic],
                         ensure_ascii=True, separators=(',', ':'))
    return 'EDU_S1_' + hashlib.sha256(payload.encode('utf-8')).hexdigest()
