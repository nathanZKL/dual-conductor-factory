"""Command-line entry point for ai-eng."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import CONFIG_PATH, STACKS, ConfigError, default_config, detect_stack, dump, load_config, load_manifest
from .gitrepo import GitError, current_branch, default_branch, dirty_paths, origin_url, repo_root, rev
from .planner import CHANGES, Action, StaleError, apply, diff, plan
from .util import redact, run, write_text

EXIT_OK, EXIT_ERROR, EXIT_CONFLICT, EXIT_ESCALATE = 0, 1, 2, 4

ORCA_MANUAL = """Orca: `ai-eng run` starts every agent as an Orca worker. Add this repository to Orca once:
  orca repo add --path "{root}"
  then run `ai-eng run "<idea>"` from a terminal inside Orca, in the main checkout."""


def _interactive(args) -> bool:
    return sys.stdin.isatty() and not getattr(args, "yes", False) and not getattr(args, "dry_run", False)


def _ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")


def _root(args) -> Path:
    return repo_root(Path(args.repo).expanduser() if args.repo else Path.cwd())


def _print_actions(actions: List[Action]) -> None:
    width = max(len(a.op) for a in actions)
    for a in actions:
        print(f"  {a.op:<{width}}  {a.path}" + (f"  ({a.detail})" if a.detail else ""))
    for a in actions:
        if a.op in ("update", "append", "merge") or (a.op == "conflict" and a.old is not None and a.new is not None):
            print(f"\n--- proposed change: {a.path}")
            print(diff(a), end="")


# ---------------------------------------------------------------- init / upgrade

def cmd_init(args) -> int:
    root = _root(args)
    cfg = load_config(root)
    actions: List[Action] = []
    if cfg is None:
        stack = args.stack or detect_stack(root)
        base = args.base or default_branch(root)
        description = args.description or ""
        if _interactive(args):
            description = _ask("One-line project description", description or f"{root.name} project")
            stack = _ask(f"Stack ({'/'.join(STACKS)})", stack)
            base = _ask("Base branch", base)
        if stack not in STACKS:
            print(f"error: stack must be one of {', '.join(STACKS)}", file=sys.stderr)
            return EXIT_ERROR
        cfg = default_config(root.name, description or f"{root.name} project", stack, base)
        actions.append(Action(CONFIG_PATH, "create", "your answers; edit to change models, branches, limits", new=dump(cfg)))
    actions += plan(root, cfg, load_manifest(root))

    dirty = dirty_paths(root)
    print(f"Repository: {root.name}  (origin: {redact(origin_url(root) or 'none')})")
    print(f"Base branch: {cfg['project']['base_branch']}" + ("" if rev(root, cfg["project"]["base_branch"]) else "  (WARNING: does not exist yet)"))
    print(f"Stack: {cfg['project']['stack']}" + ("  (no CI workflow for this stack)" if cfg["project"]["stack"] == "none" else ""))
    if dirty:
        print(f"Uncommitted changes: {len(dirty)} path(s). ai-eng leaves them alone; it only touches the files below.")
    print("\nPlanned changes:")
    _print_actions(actions)

    conflicts = [a for a in actions if a.op == "conflict"]
    changes = [a for a in actions if a.op in CHANGES]
    if args.dry_run:
        print("\nDry run: nothing was written.")
        return EXIT_CONFLICT if conflicts else EXIT_OK
    mode = args.on_conflict
    if conflicts and mode is None and _interactive(args):
        print(f"\n{len(conflicts)} conflict(s). ai-eng never overwrites them.")
        mode = _ask("Choose: stop, skip (leave them), side (write <file>.ai-eng-new next to them)", "stop")
    if conflicts and mode not in ("skip", "side"):
        print(f"\nStopped: {len(conflicts)} conflict(s). Nothing was written.")
        print("Re-run with --on-conflict skip (leave them) or --on-conflict side (write <file>.ai-eng-new beside them).")
        return EXIT_CONFLICT
    if not changes and not (conflicts and mode == "side"):
        print("\nNothing to change: everything is already current.")
        return EXIT_OK
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nNot running in a terminal: re-run with --yes to apply, or --dry-run to preview.")
            return EXIT_ERROR
        if not _confirm(f"\nApply {len(changes)} change(s)?"):
            print("Cancelled. Nothing was written.")
            return EXIT_ERROR
    try:
        undo = apply(root, actions, mode or "stop")
    except StaleError as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return EXIT_CONFLICT
    print("\nDone. To undo any change:")
    for line in undo:
        print(f"  {line}")
    print("\nNext steps:")
    print(f"  1. Review and commit the new files on '{cfg['project']['base_branch']}'.")
    print("  2. Create the conductor worktrees: ai-eng worktrees")
    print("  3. Check everything: ai-eng doctor")
    return EXIT_OK


def cmd_upgrade(args) -> int:
    if not args.plan:
        print("Only `ai-eng upgrade --plan` exists in this version. To apply updates, run `ai-eng init`; it shows the same plan and asks first.")
        return EXIT_ERROR
    root = _root(args)
    cfg = load_config(root)
    if cfg is None:
        print("No .ai-eng/config.json: run `ai-eng init` first.")
        return EXIT_ERROR
    actions = plan(root, cfg, load_manifest(root))
    print(f"Template updates for {root.name} (ai-eng {__version__}). Nothing is applied:")
    _print_actions(actions)
    return EXIT_CONFLICT if any(a.op == "conflict" for a in actions) else EXIT_OK


# ---------------------------------------------------------------- worktrees

def cmd_worktrees(args) -> int:
    from .worktrees import display, plan_worktrees

    root = _root(args)
    cfg = load_config(root)
    if cfg is None:
        print("No .ai-eng/config.json: run `ai-eng init` first.")
        return EXIT_ERROR
    steps, problems = plan_worktrees(root, cfg)
    print("Conductor worktrees (the main checkout is never modified):")
    for step in steps:
        where = display(step["path"], root)
        if step["status"] == "exists":
            print(f"  exists  {step['name']}: {where} on {step['branch']}")
        else:
            print(f"  create  {step['name']}: {where} on {step['branch']} ({step['note']})")
            print(f"          $ {' '.join(step['cmd'])}")
    for problem in problems:
        print(f"  BLOCKED {problem}")
    if problems:
        print("\nNothing was changed. Fix the blocked items, then run `ai-eng worktrees` again.")
        return EXIT_CONFLICT
    todo = [s for s in steps if s["status"] == "create"]
    if not todo:
        print("\nBoth worktrees already exist.")
        return EXIT_OK
    if args.dry_run:
        print("\nDry run: nothing was created.")
        return EXIT_OK
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nNot running in a terminal: re-run with --yes to create them, or --dry-run to preview.")
            return EXIT_ERROR
        if not _confirm(f"\nCreate {len(todo)} worktree(s)?"):
            print("Cancelled.")
            return EXIT_ERROR
    for step in todo:
        code, out, err = run(step["cmd"], cwd=root, timeout=120)
        if code != 0:
            print(f"error: {' '.join(step['cmd'])} failed: {err or out}", file=sys.stderr)
            return EXIT_ERROR
        print(f"  created {display(step['path'], root)} on {step['branch']}")
    print("\nTo undo: git worktree remove <path> (only after checking it has no uncommitted work).")
    print(ORCA_MANUAL.format(root=root))
    return EXIT_OK


# ---------------------------------------------------------------- doctor / handoff

def cmd_doctor(args) -> int:
    from .doctor import format_text, run_doctor

    root = _root(args)
    checks = run_doctor(root, online=args.online)
    if args.json:
        print(json.dumps({"tool_version": __version__, "checks": checks}, indent=2))
    else:
        print(f"ai-eng doctor: {root.name}  (read-only)\n")
        print(format_text(checks))
    return EXIT_ERROR if any(c["level"] == "fail" for c in checks) else EXIT_OK


def cmd_handoff(args) -> int:
    from .handoff import build

    root = _root(args)
    cfg = load_config(root)
    if cfg is None:
        print("No .ai-eng/config.json: run `ai-eng init` first.")
        return EXIT_ERROR
    text = build(root, cfg, args.note or [], online=args.online)
    if args.output == "-":
        print(text, end="")
        return EXIT_OK
    path = Path(args.output) if args.output else root / ".ai-eng" / "handoffs" / time.strftime("handoff-%Y%m%d-%H%M%S.md", time.gmtime())
    write_text(path, text)
    print(f"Wrote {path}. It is local only; read it before sharing it anywhere.")
    return EXIT_OK


# ---------------------------------------------------------------- review

def cmd_review(args) -> int:
    from . import review

    root = _root(args)
    cfg = load_config(root)
    if cfg is None:
        print("No .ai-eng/config.json: run `ai-eng init` first.")
        return EXIT_ERROR
    records = review.load_all(root)

    if args.review_cmd == "list":
        if not records:
            print("No review records.")
        for r in records:
            target = r["sha"][:12] if r["kind"] == "commit" and r.get("sha") else "plan"
            print(f"{r['kind']:<6} {r['key']:<30} r{r['round']:<2} {r['verdict']:<5} {r['reviewer']} -> {r['author']}  {target}  {r['recorded_at']}")
        return EXIT_OK

    kind, key, current = _review_target(root, args)
    if key is None:
        return EXIT_ERROR

    if args.review_cmd == "check":
        ok, message = review.check(records, kind, key, current)
        print(("OK: " if ok else "NOT READY: ") + message)
        return EXIT_OK if ok else EXIT_ERROR

    if args.kind and args.kind != kind:
        print(f"error: --kind {args.kind} does not match the target (use --plan-id for plan, --branch for commit)", file=sys.stderr)
        return EXIT_ERROR
    limit = cfg["limits"]["max_review_rounds"]
    try:
        rec = review.record(root, kind, key, args.verdict, args.reviewer, args.author, current,
                            args.test or [], args.finding or [], args.scope or "", args.conditions or "",
                            max_rounds=limit)
    except review.EscalatedError as exc:
        print(f"ESCALATE: {exc}", file=sys.stderr)
        return EXIT_ESCALATE
    except (review.ReviewError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Recorded {kind} review for '{key}': {rec['verdict']} (round {rec['round']}).\n")
    print(review.summary_markdown(rec))
    hist = review.history(review.load_all(root), kind, key)
    if rec["verdict"] == "NO-GO" and review.nogo_count(hist) >= limit:
        print(f"\nESCALATE: {review.nogo_count(hist)} NO-GO rounds (limit {limit}). Stop and ask the owner to decide.")
        return EXIT_ESCALATE
    return EXIT_OK


def _review_target(root: Path, args):
    from . import review

    if args.plan_id:
        try:
            review.plan_path(args.plan_id)
        except review.ReviewError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return "plan", None, None
        return "plan", args.plan_id, review.plan_version(root, args.plan_id)
    branch = args.branch or current_branch(root)
    if not branch:
        print("error: pass --branch <name> or --plan-id <id> (HEAD is detached)", file=sys.stderr)
        return "commit", None, None
    sha = rev(root, "refs/heads/" + branch)
    if sha is None:
        print(f"error: branch '{branch}' not found", file=sys.stderr)
        return "commit", None, None
    return "commit", branch, sha


# ---------------------------------------------------------------- run

def cmd_run(args) -> int:
    from . import runner

    root = _root(args)
    cfg = load_config(root)
    if cfg is None:
        print("No .ai-eng/config.json: run `ai-eng init` first.")
        return EXIT_ERROR
    try:
        if args.status is not None:
            return runner.status(root, cfg, args.status or None)
        if args.cont:
            return runner.resume(root, cfg, args.cont)
        if not args.idea:
            print('Usage: ai-eng run "<feature idea>"  |  ai-eng run --continue <plan-id>  |  ai-eng run --status')
            return EXIT_ERROR
        return runner.start(root, cfg, args.idea, args.plan_id)
    except runner.RunStop as exc:
        print(f"\nStopped: {exc}", file=sys.stderr)
        return exc.code
    except runner.review.ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="path inside the target Git repository (default: current directory)")

    parser = argparse.ArgumentParser(prog="ai-eng", description="Set up and verify a dual-conductor (Claude Code + Codex) workflow in a Git repository.")
    parser.add_argument("--version", action="version", version=f"ai-eng {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common], help="preview, approve and write the workflow files")
    p.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    p.add_argument("--yes", action="store_true", help="apply without asking (needed when not in a terminal)")
    p.add_argument("--on-conflict", choices=("stop", "skip", "side"), help="how to handle files ai-eng must not overwrite (default: stop)")
    p.add_argument("--description", help="one-line project description")
    p.add_argument("--stack", choices=STACKS, help="CI preset (default: detected)")
    p.add_argument("--base", help="base branch (default: origin's default branch)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("worktrees", parents=[common], help="create the two conductor worktrees")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_worktrees)

    p = sub.add_parser("doctor", parents=[common], help="read-only diagnostics")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--online", action="store_true", help="also query GitHub (CI runs, branch protection) through gh")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("handoff", parents=[common], help="write a sanitized status handoff")
    p.add_argument("-o", "--output", help="file to write, or - for stdout (default: .ai-eng/handoffs/)")
    p.add_argument("--note", action="append", help="a decision or blocker to include (repeatable)")
    p.add_argument("--online", action="store_true", help="include open PRs through gh")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("review", help="record and check GO/NO-GO reviews")
    rsub = p.add_subparsers(dest="review_cmd", required=True)
    for name, text in (("record", "record a verdict"), ("check", "exit 0 only if the latest verdict is GO for the current version"), ("list", "list all records")):
        rp = rsub.add_parser(name, parents=[common], help=text)
        if name == "list":
            continue
        target = rp.add_mutually_exclusive_group()
        target.add_argument("--branch", help="task branch for a commit review (default: current branch)")
        target.add_argument("--plan-id", help="plan id for a plan review (docs/ai-eng/plans/<id>.md)")
        if name == "record":
            rp.add_argument("--kind", choices=("plan", "commit"), help="optional; inferred from --plan-id / --branch")
            rp.add_argument("--verdict", required=True, choices=("GO", "NO-GO"))
            rp.add_argument("--reviewer", required=True, help="who reviewed, e.g. codex")
            rp.add_argument("--author", required=True, help="who wrote it, e.g. claude or coordinator")
            rp.add_argument("--test", action="append", help="'command: result' you actually ran (repeatable)")
            rp.add_argument("--finding", action="append", help="a finding (repeatable; required for NO-GO)")
            rp.add_argument("--scope", help="what was reviewed")
            rp.add_argument("--conditions", help="conditions attached to a GO")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("run", parents=[common], help="automate the loop: plan, reviews, builds, draft PRs")
    p.add_argument("idea", nargs="?", help="feature idea for the coordinator")
    p.add_argument("--plan-id", help="plan id (default: from the idea)")
    p.add_argument("--continue", dest="cont", metavar="PLAN_ID", help="continue a run after you edited the plan, or after a stop")
    p.add_argument("--status", nargs="?", const="", metavar="PLAN_ID", help="show runs")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("upgrade", parents=[common], help="show template updates")
    p.add_argument("--plan", action="store_true", help="show updates and conflicts without applying them")
    p.set_defaults(func=cmd_upgrade)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "kind", None) == "plan" and not getattr(args, "plan_id", None):
        print("error: --kind plan needs --plan-id", file=sys.stderr)
        return EXIT_ERROR
    try:
        return args.func(args)
    except (GitError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return EXIT_ERROR


def entry() -> None:
    sys.exit(main())
