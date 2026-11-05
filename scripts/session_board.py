#!/usr/bin/env python3
"""A local session ledger, not an agent launcher or process-control service."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid


ROLES = (
    "orchestrator", "understanding", "data", "algorithm", "api", "user-ui",
    "operator-ui", "ux-review", "qa", "release",
)
PHASES = ("planning", "development", "freeze", "independent", "submitted")
STATES = ("working", "blocked", "review", "done", "stopped")
TEMPLATES = ("SPRINT.md", "DECISIONS.md", "CONTRACTS.md", "UNDERSTANDING.md")
SKILL_DIR = Path(__file__).resolve().parent.parent
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class BoardError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def fail(code: str, message: str):
    raise BoardError(code, message)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        fail("unsafe-session", "Session IDs need 1-64 ASCII letters, digits, underscores or hyphens.")
    return value


def checked(path: Path, kind: str | None = None) -> bool:
    """Reject link/reparse components before inspecting a destination."""
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            if kind and component == path:
                fail("missing-state", f"Missing {path.name}.")
            return False
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            fail("unsafe-path", f"Links and reparse points are unsupported: {component}")
        if component != path and not stat.S_ISDIR(info.st_mode):
            fail("unsafe-path", f"A path component is not a directory: {component}")
    if kind == "dir" and not stat.S_ISDIR(info.st_mode):
        fail("invalid-state", f"Expected a directory: {path.name}")
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        fail("invalid-state", f"Expected a regular file: {path.name}")
    return True


def project_path(raw: str) -> Path:
    project = Path(os.path.abspath(os.path.expanduser(raw)))
    checked(project, "dir")
    home = Path(os.path.abspath(Path.home()))
    if project == project.parent or os.path.samefile(project, home):
        fail("unsafe-project", "Choose an existing project directory, not a filesystem root or user home.")
    if any(os.path.samefile(component, SKILL_DIR) for component in (project, *project.parents)):
        fail("unsafe-project", "The skill directory and its descendants cannot be a project.")
    return project


def read_json(path: Path):
    checked(path, "file")
    try:
        if path.stat().st_size > 1024 * 1024:
            fail("invalid-state", f"State file is too large: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        fail("invalid-state", f"Invalid UTF-8 JSON: {path.name}")


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def create_file(path: Path, content: bytes):
    checked(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def replace_json(path: Path, value):
    checked(path.parent, "dir")
    if checked(path):
        checked(path, "file")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    created = False
    try:
        create_file(temporary, encoded(value))
        created = True
        checked(path)
        os.replace(temporary, path)
    finally:
        if created and checked(temporary):
            temporary.unlink()


@contextmanager
def mutation_lock(board: Path):
    """Serialize capacity/phase checks. A stranded lock is never stolen."""
    lock = board / ".mutation.lock"
    token = uuid.uuid4().hex.encode("ascii")
    deadline = time.monotonic() + 5
    while True:
        try:
            create_file(lock, token)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                fail("board-busy", "Board is busy or has a reserved lock; inspect it manually. No lock was taken over.")
            time.sleep(0.025)
    try:
        yield
    finally:
        if checked(lock, "file") and lock.read_bytes() == token:
            lock.unlink()


def config_for(board: Path) -> dict:
    checked(board, "dir")
    config = read_json(board / "config.json")
    if not isinstance(config, dict):
        fail("invalid-board", "This .sprint directory is not a compatible session board.")
    count = config.get("requested_sessions")
    templates = config.get("templates")
    if (config.get("kind") != "codex-session-board" or type(config.get("version")) is not int or config["version"] != 1
            or config.get("roles") != list(ROLES) or config.get("phase") not in PHASES
            or type(count) is not int or not 1 <= count <= 10
            or not isinstance(config.get("created_at"), str)
            or not isinstance(templates, list) or any(not isinstance(name, str) or name not in TEMPLATES for name in templates)
            or len(set(templates)) != len(templates)):
        fail("invalid-board", "This .sprint directory is incomplete or incompatible; nothing was overwritten.")
    for directory in ("claims", "handoffs"):
        checked(board / directory, "dir")
    for name in templates:
        checked(board / name, "file")
    return config


def claim_for(board: Path, role: str) -> dict | None:
    path = board / "claims" / f"{role}.json"
    if not checked(path):
        return None
    checked(path, "file")
    try:
        claim = read_json(path)
        if (not isinstance(claim, dict) or claim.get("role") != role
                or not isinstance(claim.get("session"), str)
                or not IDENTIFIER.fullmatch(claim["session"])
                or not isinstance(claim.get("claimed_at"), str)):
            return {"reserved": True}
        return claim
    except BoardError as error:
        if error.code == "invalid-state":
            return {"reserved": True}
        raise


def claims_for(board: Path) -> dict:
    claims = {role: claim_for(board, role) for role in ROLES}
    if not claims["orchestrator"] or claims["orchestrator"].get("reserved"):
        fail("incomplete-board", "The orchestrator claim is missing or reserved; inspect state manually.")
    return claims


def handoff_for(board: Path, role: str, claim: dict | None) -> dict | None:
    path = board / "handoffs" / f"{role}.json"
    if not checked(path):
        return None
    handoff = read_json(path)
    if (not claim or claim.get("reserved") or not isinstance(handoff, dict)
            or handoff.get("role") != role or handoff.get("session") != claim.get("session")
            or handoff.get("state") not in STATES or not isinstance(handoff.get("summary"), str)
            or not isinstance(handoff.get("updated_at"), str)
            or not isinstance(handoff.get("evidence"), list)
            or not all(isinstance(item, str) for item in handoff["evidence"])
            or not isinstance(handoff.get("next"), str)):
        fail("invalid-handoff", f"Unrelated or incompatible handoff for {role}; nothing was overwritten.")
    return handoff


def snapshot(board: Path) -> dict:
    config = config_for(board)
    claims = claims_for(board)
    rows = []
    for role, claim in claims.items():
        handoff = handoff_for(board, role, claim)
        rows.append({
            "role": role,
            "claim_status": "free" if claim is None else "reserved" if claim.get("reserved") else "claimed",
            "session": claim.get("session") if claim else None,
            "handoff_state": handoff["state"] if handoff else "pending",
            "handoff": handoff,
        })
    return {"project": str(board.parent), "requested_sessions": config["requested_sessions"],
            "phase": config["phase"], "roles": rows,
            "process_control": "Ledger only; handoffs acknowledge state and do not stop or verify running processes."}


def active(config: dict):
    if config["phase"] in ("independent", "submitted"):
        fail("ai-stop-phase", f"No joins or handoffs are allowed in phase {config['phase']}.")


def owner(claim: dict | None, session: str, role: str):
    if not claim or claim.get("reserved") or claim.get("session") != session:
        fail("ownership", f"Session does not own {role}; ownership was not changed.")


def init(args, project: Path) -> dict:
    if not 1 <= args.sessions <= 10:
        fail("sessions-range", "--sessions must be between 1 and 10, including the orchestrator.")
    session = identifier(args.session) if args.session is not None else str(uuid.uuid4())
    board = project / ".sprint"
    if checked(board):
        result = snapshot(board)
        return {"ok": True, "status": "existing", "session": result["roles"][0]["session"], **result}
    sources = []
    for name in TEMPLATES:
        source = SKILL_DIR / "templates" / name
        if checked(source):
            checked(source, "file")
            sources.append((name, source.read_bytes()))
    if not args.apply:
        return {"ok": True, "status": "preview", "project": str(project), "session": session,
                "requested_sessions": args.sessions, "roles": list(ROLES),
                "templates": [name for name, _ in sources], "writes": False,
                "note": "Logical roles only; this does not create sessions or guarantee runtime capacity."}
    try:
        board.mkdir()
    except FileExistsError:
        result = snapshot(board)
        return {"ok": True, "status": "existing", "session": result["roles"][0]["session"], **result}
    (board / "claims").mkdir()
    (board / "handoffs").mkdir()
    create_file(board / "claims" / "orchestrator.json", encoded({
        "role": "orchestrator", "session": session, "claimed_at": timestamp(),
    }))
    for name, content in sources:
        create_file(board / name, content)
    create_file(board / "config.json", encoded({
        "kind": "codex-session-board", "version": 1, "requested_sessions": args.sessions,
        "roles": list(ROLES), "phase": "planning", "created_at": timestamp(),
        "templates": [name for name, _ in sources],
    }))
    return {"ok": True, "status": "initialized", "session": session, **snapshot(board)}


def join(args, board: Path) -> dict:
    session = identifier(args.session) if args.session is not None else str(uuid.uuid4())
    with mutation_lock(board):
        config = config_for(board)
        active(config)
        claims = claims_for(board)
        existing = [role for role, claim in claims.items() if claim and claim.get("session") == session]
        if args.role:
            role = args.role
            if claims[role]:
                owner(claims[role], session, role)
                return {"ok": True, "status": "already-joined", "session": session, "role": role, "roles": existing}
        elif existing:
            return {"ok": True, "status": "already-joined", "session": session, "role": existing[0], "roles": existing}
        else:
            role = next((candidate for candidate in ROLES[1:] if claims[candidate] is None), None)
        occupied = {claim["session"] for claim in claims.values() if claim and not claim.get("reserved")}
        reserved = sum(bool(claim and claim.get("reserved")) for claim in claims.values())
        if (not existing and len(occupied) + reserved >= config["requested_sessions"]) or role is None:
            fail("no-slot", "No session slot is available; reserved claims count toward capacity.")
        create_file(board / "claims" / f"{role}.json", encoded({
            "role": role, "session": session, "claimed_at": timestamp(),
        }))
        return {"ok": True, "status": "joined", "session": session, "role": role, "roles": [*existing, role]}


def handoff(args, board: Path) -> dict:
    session = identifier(args.session)
    if not args.summary.strip():
        fail("empty-summary", "A handoff summary must contain text.")
    with mutation_lock(board):
        config = config_for(board)
        active(config)
        claims = claims_for(board)
        owner(claims[args.role], session, args.role)
        handoff_for(board, args.role, claims[args.role])
        value = {"role": args.role, "session": session, "state": args.state,
                 "summary": args.summary, "evidence": args.evidence or [],
                 "next": args.next or "", "updated_at": timestamp()}
        replace_json(board / "handoffs" / f"{args.role}.json", value)
        return {"ok": True, "status": "recorded", "handoff": value}


def phase(args, board: Path) -> dict:
    session = identifier(args.session)
    with mutation_lock(board):
        config = config_for(board)
        claims = claims_for(board)
        owner(claims["orchestrator"], session, "orchestrator")
        if PHASES.index(args.value) < PHASES.index(config["phase"]):
            fail("phase-regression", "Phases cannot move backwards.")
        if args.value in ("independent", "submitted"):
            pending = []
            for role in ROLES[1:]:
                if claims[role]:
                    entry = handoff_for(board, role, claims[role])
                    if not entry or entry["state"] not in ("done", "stopped"):
                        pending.append(role)
            if pending:
                fail("stop-ack-required", "Claimed workers need done/stopped handoffs: " + ", ".join(pending))
        if config["phase"] != args.value:
            config["phase"] = args.value
            replace_json(board / "config.json", config)
        return {"ok": True, "status": "phase-recorded", "phase": args.value,
                "process_control": "Acknowledgements only; this command does not interrupt or verify AI processes."}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        fail("arguments", message)


def parser() -> argparse.ArgumentParser:
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in ("init", "join", "status", "handoff", "phase"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True, help="Existing project directory; never inferred")
        if name in ("init", "join"):
            command.add_argument("--session")
        if name == "init":
            command.add_argument("--sessions", type=int, default=10)
            command.add_argument("--apply", action="store_true")
        elif name == "join":
            command.add_argument("--role", choices=ROLES)
        elif name == "handoff":
            command.add_argument("--role", choices=ROLES, required=True)
            command.add_argument("--session", required=True)
            command.add_argument("--state", choices=STATES, required=True)
            command.add_argument("--summary", required=True)
            command.add_argument("--evidence", action="append")
            command.add_argument("--next")
        elif name == "phase":
            command.add_argument("--session", required=True)
            command.add_argument("--value", choices=PHASES, required=True)
    return root


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = parser().parse_args(argv)
        project = project_path(args.project)
        board = project / ".sprint"
        if args.command == "init":
            result = init(args, project)
        else:
            # Validate before attempting to create even the temporary mutation lock.
            config_for(board)
            if args.command == "status":
                result = {"ok": True, "status": "snapshot", **snapshot(board)}
            else:
                result = {"join": join, "handoff": handoff, "phase": phase}[args.command](args, board)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BoardError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except OSError as error:
        print(json.dumps({"ok": False, "error": "filesystem", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
