import os
import json
import logging
from datetime import datetime, timezone
from hdbcli import dbapi


def load_env_from_dotenv():
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" in s:
                    k, v = s.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if not os.getenv(k):
                        os.environ[k] = v
    except Exception:
        pass


def get_hana_credentials():
    if not (
        os.getenv("HANA_HOST") and os.getenv("HANA_USER") and os.getenv("HANA_PASSWORD")
    ):
        vcap = os.getenv("VCAP_SERVICES")
        if vcap:
            try:
                data = json.loads(vcap)
                creds = None
                for _, services in data.items():
                    for s in services:
                        c = s.get("credentials", {})
                        if (
                            c.get("host")
                            and (c.get("user") or c.get("username"))
                            and c.get("password")
                        ):
                            creds = c
                            break
                    if creds:
                        break
                if creds:
                    os.environ.setdefault("HANA_HOST", str(creds.get("host")))
                    port_val = creds.get("port") or creds.get("port_tls")
                    if port_val is not None:
                        os.environ.setdefault("HANA_PORT", str(port_val))
                    os.environ.setdefault(
                        "HANA_USER", str(creds.get("user") or creds.get("username"))
                    )
                    os.environ.setdefault("HANA_PASSWORD", str(creds.get("password")))
                    if creds.get("schema"):
                        os.environ.setdefault("HANA_SCHEMA", str(creds.get("schema")))
            except Exception:
                pass
    return {
        "host": os.getenv("HANA_HOST"),
        "port": int(os.getenv("HANA_PORT")) if os.getenv("HANA_PORT") else None,
        "user": os.getenv("HANA_USER"),
        "password": os.getenv("HANA_PASSWORD"),
        "schema": os.getenv("HANA_SCHEMA"),
    }


def get_hana_connection():
    c = get_hana_credentials()
    missing = [k for k in ["host", "user", "password", "schema"] if not c.get(k)]
    if missing:
        labels = {
            "host": "HANA_HOST",
            "user": "HANA_USER",
            "password": "HANA_PASSWORD",
            "schema": "HANA_SCHEMA",
        }
        raise ValueError(
            "Faltan variables de entorno para HANA: "
            + ", ".join(labels[m] for m in missing)
        )
    conn = dbapi.connect(
        address=c["host"],
        port=c["port"] or 443,
        user=c["user"],
        password=c["password"],
        encrypt=True,
        sslValidateCertificate=False,
    )
    schema = c["schema"]
    if schema:
        cur = conn.cursor()
        cur.execute(f'SET SCHEMA "{schema}"')
        cur.close()
    return conn


# ── TEMPFARECONVERSION ───────────────────────────────────────────────────────



def insert_fare_conversion(rows, created_by: str = "SYSTEM"):
    """
    Trunca e inserta registros en GLOBALHITSS_EE_TEMPFARECONVERSION_DEV.

    Columnas de la tabla:
        CREATEDAT, CREATEDBY, MODIFIEDAT, MODIFIEDBY,
        FAREPLAIN, FAREREPORT, FAREACTUAL

    rows: lista de dicts con claves farePlain, fareReport, fareActual
    """
    
    SCHEMA = os.getenv("HANA_SCHEMA")  # valor por defecto si no se setea
    TABLE  = os.getenv("TABLE_Temp_Fare_Conv")
    FULL_TABLE = f'"{SCHEMA}"."{TABLE}"'

    print("tabla completa:", FULL_TABLE)


    conn = get_hana_connection()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    try:
        cur = conn.cursor()

        logging.info("Truncando %s ...", FULL_TABLE)
        cur.execute(f"TRUNCATE TABLE {FULL_TABLE}")

        sql = (
            f"INSERT INTO {FULL_TABLE} "
            "(CREATEDAT, CREATEDBY, MODIFIEDAT, MODIFIEDBY, FAREPLAIN, FAREREPORT, FAREACTUAL) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )

        data = [
            (
                now,
                created_by,
                now,
                created_by,
                str(row["farePlain"]) if row["farePlain"] is not None else None,
                str(row["fareReport"]) if row["fareReport"] is not None else None,
                str(row["fareActual"]) if row["fareActual"] is not None else None,
            )
            for row in rows
        ]

        cur.executemany(sql, data)
        conn.commit()
        logging.info("TEMPFARECONVERSION: %d registros insertados.", len(data))
        cur.close()
        return len(data)
    finally:
        conn.close()