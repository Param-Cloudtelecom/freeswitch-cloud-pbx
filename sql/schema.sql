-- CDR schema for multi-tenant FreeSWITCH deployment.
-- Populated two ways (intentionally redundant for reliability):
--   1) mod_cdr_pg writing directly from FreeSWITCH core
--   2) api/cdr_webhook.py, hit from the dialplan's hangup hook,
--      for consumers that need the record before mod_cdr_pg's batch flush

CREATE TABLE IF NOT EXISTS cdr (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    call_uuid       UUID NOT NULL UNIQUE,
    caller_id_number TEXT,
    destination_number TEXT,
    direction       TEXT CHECK (direction IN ('inbound', 'outbound', 'internal')),
    start_stamp     TIMESTAMPTZ NOT NULL,
    answer_stamp    TIMESTAMPTZ,
    end_stamp       TIMESTAMPTZ,
    duration_sec    INTEGER,
    billsec         INTEGER,
    hangup_cause    TEXT,
    sip_term_status SMALLINT,
    trunk_used      TEXT,
    raw_cdr_json    JSONB
);

CREATE INDEX IF NOT EXISTS idx_cdr_tenant_start ON cdr (tenant_id, start_stamp DESC);
CREATE INDEX IF NOT EXISTS idx_cdr_destination ON cdr (destination_number);

-- Per-tenant trunk/billing summary, refreshed by a scheduled job - useful
-- for the same kind of "is this tenant's trunk healthy" dashboard that
-- pairs with the dispatcher health checks in kamailio-sbc-router.
CREATE MATERIALIZED VIEW IF NOT EXISTS tenant_daily_usage AS
SELECT
    tenant_id,
    date_trunc('day', start_stamp) AS call_day,
    direction,
    count(*) AS call_count,
    sum(billsec) AS total_billsec
FROM cdr
GROUP BY tenant_id, call_day, direction;
