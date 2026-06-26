# freeswitch-cloud-pbx

A multi-tenant FreeSWITCH dialplan + automation layer for a Cloud PBX
platform: per-tenant call routing, click-to-call via the Event Socket
Library (ESL), and a real-time CDR pipeline into PostgreSQL.

This sits *behind* a SIP signaling edge (see
[`kamailio-sbc-router`](https://github.com/Param-Cloudtelecom/kamailio-sbc-router))
— FreeSWITCH here is the call-processing core for one or more tenants, not
the public-facing SBC.

## Architecture

```
            ┌──────────────────────────────────────────────┐
            │                  FreeSWITCH                   │
            │                                                │
 SIP edge ─►│ dialplan/tenant_template.xml                  │
 (per       │   - internal extension routing                │
  tenant    │   - IVR entry points                           │
  context)  │   - outbound trunk selection (tagged tenant_id)│
            │   - hangup hook → CDR webhook                  │
            └───────────┬───────────────────────┬────────────┘
                         │ ESL (api/originate.py) │ HTTP POST on hangup
                         ▼                       ▼
                 click-to-call API        api/cdr_webhook.py
                  (Flask + greenswitch)     (Flask + psycopg2)
                                                  │
                                                  ▼
                                          PostgreSQL (sql/schema.sql)
```

## Key files

- [`dialplan/tenant_template.xml`](dialplan/tenant_template.xml) — per-tenant
  `<context>` block: internal extension dialing, an IVR entry point, tagged
  outbound trunk routing, and a hangup hook that fires the CDR webhook.
- [`api/originate.py`](api/originate.py) — Flask + ESL service: `POST /calls`
  originates a call from an extension to an external number;
  `GET /calls/<tenant_id>` returns recent CDRs for that tenant.
- [`api/cdr_webhook.py`](api/cdr_webhook.py) — receives the hangup hook's
  HTTP POST, normalizes FreeSWITCH's channel variables, and writes a CDR row
  — independent of (and faster than) `mod_cdr_pg`'s batched writes.
- [`sql/schema.sql`](sql/schema.sql) — `cdr` table plus a materialized view
  for per-tenant daily usage (calls/billsec), the kind of summary a billing
  or health dashboard would query.

## Why two separate CDR write paths

`mod_cdr_pg` is the reliable, FreeSWITCH-native path — it'll always capture
the call even if something downstream is briefly unavailable. The HTTP
webhook is for **latency**: a billing system or live-ops dashboard that
needs the record within milliseconds of hangup, not on `mod_cdr_pg`'s next
batch flush. Running both isn't redundant, it's defense in depth for two
different consumers with different requirements.

## Multi-tenancy approach

Every call in this system carries a `tenant_id` channel variable from the
moment it enters the dialplan (`<action application="set" data="tenant_id=acme"/>`).
That one variable is what:

- Selects the right outbound trunk/dispatcher set on the SBC layer
- Tags the CDR row so tenants' call history/billing never cross-contaminate
- Lets `api/originate.py` resolve which tenant's extension to originate from

## Running it locally

```bash
# 1. Load the schema
psql -U postgres -d freeswitch_cdr -f sql/schema.sql

# 2. Drop the dialplan context into FreeSWITCH (adjust tenant name/extensions)
cp dialplan/tenant_template.xml /etc/freeswitch/dialplan/acme.xml
fs_cli -x "reloadxml"

# 3. Start the automation API
cd api && pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres@localhost/freeswitch_cdr
python cdr_webhook.py &      # listens on :8088 for the dialplan's hangup hook
python originate.py &        # listens on :5000 for click-to-call requests

# 4. Trigger a test call
curl -X POST http://127.0.0.1:5000/calls \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id": "acme", "from_extension": "1001", "to_number": "14165551234"}'

# 5. Check it landed in the CDR table
curl http://127.0.0.1:5000/calls/acme
```
