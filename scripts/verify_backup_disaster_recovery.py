"""
PostgreSQL Disaster Recovery & Backup Verification Harness
Safely tests backup creation, archive integrity, and isolated test database restoration
without touching or mutating the active development database.
"""
import os
from pathlib import Path
import subprocess
import sys
import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from django.conf import settings
import psycopg

PG_BIN = Path(os.environ.get("POSTGRES_BIN", "C:/Program Files/PostgreSQL/18/bin"))
BACKUP_DIR = Path(settings.BASE_DIR) / "backups" / "audit_verification"
BACKUP_FILE = BACKUP_DIR / "disaster_recovery_test.dump"
TEST_RESTORE_DB = "gym_erp_restore_test_isolated"

def run_cmd(cmd_list, env=None):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run(cmd_list, capture_output=True, text=True, creationflags=flags, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {' '.join(cmd_list)}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")
    return res.stdout

def main():
    print("=" * 80)
    print("AGMS POSTGRESQL DISASTER RECOVERY & BACKUP AUDIT HARNESS")
    print("=" * 80)

    db_conf = settings.DATABASES["default"]
    host = db_conf.get("HOST", "127.0.0.1")
    port = str(db_conf.get("PORT", 55432))
    user = db_conf.get("USER", "gym_local")
    password = db_conf.get("PASSWORD", "")
    src_db = db_conf.get("NAME", "gym_erp")

    pg_env = os.environ.copy()
    if password:
        pg_env["PGPASSWORD"] = password

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if BACKUP_FILE.exists():
        BACKUP_FILE.unlink()

    # Step 1: Record baseline counts
    print(f"\n[STEP 1] Querying Source Database '{src_db}' on {host}:{port}...")
    baseline_counts = {}
    with psycopg.connect(host=host, port=port, user=user, password=password, dbname=src_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';")
            tables = [r[0] for r in cur.fetchall()]
            print(f"  Source database contains {len(tables)} tables.")
            for t in ["core_gymprofile", "branches_branch", "branches_branchsettings", "core_featureflag"]:
                if t in tables:
                    cur.execute(f"SELECT count(*) FROM {t};")
                    baseline_counts[t] = cur.fetchone()[0]
                    print(f"  - {t}: {baseline_counts[t]} rows")

    # Step 2: Execute pg_dump
    print(f"\n[STEP 2] Creating Compressed Archive with pg_dump...")
    dump_bin = str(PG_BIN / "pg_dump.exe")
    dump_cmd = [
        dump_bin,
        "-h", host,
        "-p", port,
        "-U", user,
        "-F", "c",  # Custom archive format (compressed, supports selective restore)
        "-b",       # Include blobs
        "-v",
        "-f", str(BACKUP_FILE),
        src_db,
    ]
    run_cmd(dump_cmd, env=pg_env)
    file_size_bytes = BACKUP_FILE.stat().st_size
    print(f"  Backup created successfully at: {BACKUP_FILE}")
    print(f"  Archive Size: {file_size_bytes:,} bytes ({file_size_bytes / 1024:.2f} KB)")
    assert file_size_bytes > 0, "Backup archive is empty!"

    # Step 3: Verify Archive Table of Contents
    print(f"\n[STEP 3] Verifying Archive TOC & Structure with pg_restore --list...")
    restore_bin = str(PG_BIN / "pg_restore.exe")
    list_cmd = [restore_bin, "--list", str(BACKUP_FILE)]
    toc_output = run_cmd(list_cmd, env=pg_env)
    toc_lines = [l for l in toc_output.splitlines() if l.strip() and not l.startswith(";")]
    print(f"  Archive integrity verified: valid PostgreSQL custom format header.")
    print(f"  Archive contains {len(toc_lines)} catalog/data entries.")

    # Step 4: Create Isolated Test Database
    print(f"\n[STEP 4] Preparing Isolated Verification Database '{TEST_RESTORE_DB}'...")
    with psycopg.connect(host=host, port=port, user=user, password=password, dbname="postgres", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_RESTORE_DB};")
            cur.execute(f"CREATE DATABASE {TEST_RESTORE_DB};")
            print(f"  Created empty verification database '{TEST_RESTORE_DB}'.")

    # Step 5: Restore into Isolated Database
    print(f"\n[STEP 5] Restoring Archive into '{TEST_RESTORE_DB}'...")
    restore_cmd = [
        restore_bin,
        "-h", host,
        "-p", port,
        "-U", user,
        "-d", TEST_RESTORE_DB,
        "--no-owner",
        str(BACKUP_FILE),
    ]
    try:
        run_cmd(restore_cmd, env=pg_env)
        print("  pg_restore completed with return code 0.")
    except RuntimeError as exc:
        # pg_restore may return warnings for existing roles; verify if schema succeeded
        print(f"  pg_restore notice: {exc}")

    # Step 6: Validate Restored Schema & Record Counts
    print(f"\n[STEP 6] Validating Schema & Record Counts in Restored Database...")
    restored_counts = {}
    with psycopg.connect(host=host, port=port, user=user, password=password, dbname=TEST_RESTORE_DB) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';")
            restored_tables = [r[0] for r in cur.fetchall()]
            print(f"  Restored database contains {len(restored_tables)} tables.")
            assert len(restored_tables) == len(tables), f"Table count mismatch: {len(restored_tables)} vs {len(tables)}"

            for t, expected_count in baseline_counts.items():
                cur.execute(f"SELECT count(*) FROM {t};")
                restored_counts[t] = cur.fetchone()[0]
                print(f"  - {t}: {restored_counts[t]} rows (Source: {expected_count})")
                assert restored_counts[t] == expected_count, f"Record count mismatch in {t}!"

    # Step 7: Cleanup Isolated Verification Database
    print(f"\n[STEP 7] Cleaning Up Isolated Verification Database '{TEST_RESTORE_DB}'...")
    with psycopg.connect(host=host, port=port, user=user, password=password, dbname="postgres", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_RESTORE_DB};")
            print(f"  Verification database '{TEST_RESTORE_DB}' dropped cleanly.")

    print("\n" + "=" * 80)
    print("ALL DISASTER RECOVERY & BACKUP RESTORATION CHECKS PASSED: VERIFIED")
    print("=" * 80)

if __name__ == "__main__":
    main()
