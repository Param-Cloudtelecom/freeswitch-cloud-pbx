"""
cdr_webhook.py

Small Flask service that FreeSWITCH's dialplan hits (via mod_curl, see
dialplan/tenant_template.xml) on every call hangup. It normalizes the CDR
fields FreeSWITCH sends and writes them into Postgres, independent of (and
faster than) mod_cdr_pg's own batched writes - useful when a downstream
billing/analytics consumer needs the record close to real time.

Run:
    pip install flask psycopg2-binary
    export DATABASE_URL=postgresql://user:pass@localhost/freeswitch_cdr
    python cdr_webhook.py
"""
import os
import json
import logging
from datetime import datetime, timezone

import psycopg2
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cdr_webhook")

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/freeswitch_cdr")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


@app.route("/cdr", methods=["POST"])
def receive_cdr():
    """
    FreeSWITCH's mod_curl posts the channel variables as form-encoded data
    by default. We pull the handful we actually need for billing/reporting
    and stash the full payload in raw_cdr_json for anything we didn't
    explicitly model.
    """
    data = request.form.to_dict() or request.get_json(silent=True) or {}

    call_uuid = data.get("uuid") or data.get("Unique-ID")
    if not call_uuid:
        return jsonify({"error": "missing call uuid"}), 400

    tenant_id = data.get("tenant_id", "unknown")
    direction = data.get("direction", "internal")
    caller_id_number = data.get("caller_id_number", "")
    destination_number = data.get("destination_number", "")
    hangup_cause = data.get("hangup_cause", "")
    sip_term_status = data.get("sip_term_status")
    trunk_used = data.get("sip_h_X-Tenant-Id", tenant_id)

    start_epoch = data.get("start_epoch")
    answer_epoch = data.get("answer_epoch")
    end_epoch = data.get("end_epoch")
    billsec = data.get("billsec", 0)
    duration = data.get("duration", 0)

    def to_ts(epoch):
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc) if epoch else None

    sql = """
        INSERT INTO cdr (
            tenant_id, call_uuid, caller_id_number, destination_number,
            direction, start_stamp, answer_stamp, end_stamp,
            duration_sec, billsec, hangup_cause, sip_term_status,
            trunk_used, raw_cdr_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (call_uuid) DO NOTHING
    """
    params = (
        tenant_id, call_uuid, caller_id_number, destination_number,
        direction, to_ts(start_epoch), to_ts(answer_epoch), to_ts(end_epoch),
        int(duration or 0), int(billsec or 0), hangup_cause,
        int(sip_term_status) if sip_term_status else None,
        trunk_used, json.dumps(data),
    )

    try:
        conn = get_conn()
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
        log.info("Recorded CDR %s for tenant=%s dir=%s", call_uuid, tenant_id, direction)
    except Exception:
        log.exception("Failed to write CDR %s", call_uuid)
        return jsonify({"error": "db write failed"}), 500

    return jsonify({"status": "recorded", "uuid": call_uuid}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8088)
