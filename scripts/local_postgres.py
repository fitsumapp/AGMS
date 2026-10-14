"""Create an isolated, disposable development cluster; never touches an existing service."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".local"
BIN = Path(os.environ.get("POSTGRES_BIN", "C:/Program Files/PostgreSQL/18/bin"))
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

def run(*args):
    subprocess.run([str(BIN / args[0]), *map(str, args[1:])], check=True, creationflags=FLAGS)

if __name__ == "__main__":
    LOCAL.mkdir(exist_ok=True)
    data = LOCAL / "postgres-validation"
    if sys.argv[1:] == ["stop"]:
        run("pg_ctl", "-D", data, "-m", "fast", "stop")
    elif sys.argv[1:] == ["start"]:
        run("pg_ctl", "-D", data, "-l", LOCAL / "postgres.log", "-w", "start")
    else:
        if data.exists() or (ROOT / ".env").exists():
            raise SystemExit("Refusing initialization: .env or local cluster exists. Use start/stop.")
        password = secrets.token_urlsafe(32)
        (ROOT / ".env").write_text(
            "DJANGO_SECRET_KEY=" + secrets.token_urlsafe(64) + "\n"
            "DJANGO_DEBUG=True\nDJANGO_ALLOWED_HOSTS=127.0.0.1,localhost\n"
            "POSTGRES_DB=gym_erp\nPOSTGRES_USER=gym_local\nPOSTGRES_PASSWORD=" + password + "\n"
            "POSTGRES_HOST=127.0.0.1\nPOSTGRES_PORT=55432\nPOSTGRES_TEST_DB=test_gym_erp\n"
            "GYM_DISPLAY_NAME=Gym ERP\n", encoding="utf-8")
        password_file = LOCAL / "init-password"
        password_file.write_text(password, encoding="utf-8")
        try:
            run("initdb", "-D", data, "-U", "gym_local", "--pwfile", password_file,
                "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C")
        finally:
            password_file.unlink(missing_ok=True)
        with (data / "postgresql.conf").open("a", encoding="utf-8") as handle:
            handle.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n")
        run("pg_ctl", "-D", data, "-l", LOCAL / "postgres.log", "-w", "start")
        import psycopg
        with psycopg.connect(host="127.0.0.1", port=55432, user="gym_local", password=password, dbname="postgres", autocommit=True) as conn:
            conn.execute("CREATE DATABASE gym_erp")
        print("Isolated local cluster ready on 127.0.0.1:55432; credentials saved in ignored .env.")
