"""
Operator account recovery: set a new password for a user from the server (for example when the only
administrator has forgotten theirs). Needs shell access to the server; it is not reachable from the network.

  * The new password is read from the terminal without echo (never pass it on the command line).
  * It must satisfy the staff password rule (8-128 characters, upper- and lower-case letter, digit).
  * The account is re-activated if --activate is given; all of its sessions are ended.
  * An audit-log entry (user.password_reset, reason "operator recovery") is written. The password is never logged.

Usage (from backend/):
  python -m scripts.reset_password <username> [--activate]
In the Docker setup (interactive terminal needed):
  docker compose exec backend python -m scripts.reset_password <username>
"""
import argparse
import getpass
import json
import os
import sys

from sqlalchemy import create_engine, text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("--activate", action="store_true", help="Also re-activate the account if it is deactivated")
    args = ap.parse_args()

    from app.core.security import hash_password
    from app.schemas.auth import check_password_policy

    url = os.environ.get("DATABASE_URL_SYNC")
    if not url:
        from app.core.config import settings
        url = settings.DATABASE_URL_SYNC
    engine = create_engine(url)
    with engine.connect() as conn:
        user = conn.execute(text("SELECT u.id, u.pharmacy_id, u.name, u.is_active, r.name AS role FROM users u "
                                 "JOIN roles r ON r.id = u.role_id WHERE u.username = :u"), {"u": args.username}).mappings().all()
    if len(user) != 1:
        sys.exit(f"No single account with username '{args.username}' (found {len(user)}).")
    user = user[0]
    print(f"Account: {args.username} — {user['name']} ({user['role']}), {'active' if user['is_active'] else 'DEACTIVATED'}")
    if not user["is_active"] and not args.activate:
        sys.exit("The account is deactivated. Re-run with --activate to switch it back on.")

    # Interactive terminal: hidden prompt. Otherwise (e.g. `docker compose exec -T ... < file`): two lines on stdin.
    ask = getpass.getpass if sys.stdin.isatty() else (lambda prompt: sys.stdin.readline().rstrip("\r\n"))
    pw = ask("New password: ")
    if pw != ask("Repeat new password: "):
        sys.exit("The passwords do not match. Nothing was changed.")
    try:
        check_password_policy(pw)
    except ValueError as e:
        sys.exit(f"{e}. Nothing was changed.")

    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET password_hash = :h, is_active = 1 WHERE id = :id"),
                     {"h": hash_password(pw), "id": user["id"]})
        ended = conn.execute(text("UPDATE user_sessions SET is_active = 0 WHERE user_id = :id AND is_active = 1"),
                             {"id": user["id"]}).rowcount
        conn.execute(text("INSERT INTO audit_logs (pharmacy_id, user_id, action, entity_type, entity_id, reason, metadata_json, timestamp) "
                          "VALUES (:p, NULL, 'user.password_reset', 'user', :id, 'operator recovery (server command)', :m, UTC_TIMESTAMP())"),
                     {"p": user["pharmacy_id"], "id": str(user["id"]),
                      "m": json.dumps({"sessions_ended": ended, "reactivated": not user["is_active"]})})
    print(f"Password changed; {ended} session(s) ended. Sign in with the new password.")


if __name__ == "__main__":
    main()
