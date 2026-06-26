"""
originate.py

Minimal REST API for originating outbound calls through FreeSWITCH via the
Event Socket Library (ESL), and for querying recent CDRs. This is the kind
of automation layer that sits between a Cloud PBX provisioning UI / click-
to-call feature and the FreeSWITCH core itself - nothing touches the
dialplan directly.

Run:
    pip install flask greenswitch psycopg2-binary
    export FS_ESL_HOST=127.0.0.1 FS_ESL_PORT=8021 FS_ESL_PASSWORD=ClueCon
    export DATABASE_URL=postgresql://user:pass@localhost/freeswitch_cdr
    python originate.py

Example:
    curl -X POST http://127.0.0.1:5000/calls \
         -H 'Content-Type: application/json' \
         -d '{"tenant_id": "acme", "from_extension": "1001", "to_number": "14165551234"}'
"""
import os
import logging

import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify
from greenswitch import InboundESL

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("originate")

app = Flask(__name__)

FS_ESL_HOST = os.environ.get("FS_ESL_HOST", "127.0.0.1")
FS_ESL_PORT = int(os.environ.get("FS_ESL_PORT", "8021"))
FS_ESL_PASSWORD = os.environ.get("FS_ESL_PASSWORD", "ClueCon")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/freeswitch_cdr")


def esl_connect():
    client = InboundESL(host=FS_ESL_HOST, port=FS_ESL_PORT, password=FS_ESL_PASSWORD)
    client.connect()
    return client


@app.route("/calls", methods=["POST"])
def originate_call():
    """
    Bridges an internal extension to an external number, tagging the leg
    with tenant_id so it lands in the right dialplan context/trunk group
    and CDR partition.
    """
    body = request.get_json(force=True)
    tenant_id = body.get("tenant_id")
    from_extension = body.get("from_extension")
    to_number = body.get("to_number")

    if not all([tenant_id, from_extension, to_number]):
        return jsonify({"error": "tenant_id, from_extension, to_number are required"}), 400

    originate_str = (
        f"originate {{origination_caller_id_number={from_extension},"
        f"tenant_id={tenant_id}}}user/{from_extension}@{tenant_id} "
        f"9{to_number}"
    )

    try:
        client = esl_connect()
        result = client.send(f"api {originate_str}")
        client.stop()
    except Exception:
        log.exception("ESL originate failed")
        return jsonify({"error": "could not reach FreeSWITCH ESL"}), 502

    log.info("Originate requested: tenant=%s %s -> %s", tenant_id, from_extension, to_number)
    return jsonify({"status": "originate sent", "result": str(result)}), 202


@app.route("/calls/<tenant_id>", methods=["GET"])
def recent_calls(tenant_id):
    """Recent CDRs for a tenant - same table api/cdr_webhook.py writes into."""
    limit = int(request.args.get("limit", 25))
    conn = psycopg2.connect(DATABASE_URL)
    with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT call_uuid, caller_id_number, destination_number, direction,
                   start_stamp, billsec, hangup_cause
            FROM cdr
            WHERE tenant_id = %s
            ORDER BY start_stamp DESC
            LIMIT %s
            """,
            (tenant_id, limit),
        )
        rows = cur.fetchall()

    return jsonify(rows), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
