"""Create an account without storing plaintext seed passwords in the repository."""
import argparse
from getpass import getpass

from backend.auth.service import create_user


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("user_id")
    parser.add_argument("--role", choices=["admin", "worker"], required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = getpass("Password (8+ characters): ")
    if password != getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    create_user(args.user_id, password, args.name, args.role)
    print("Account created:", args.user_id)


if __name__ == "__main__":
    main()
