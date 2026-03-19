import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent
AI_SERVICE_DIR = PROJECT_ROOT / "ai-service"
BOT_SCRIPT = PROJECT_ROOT / "whatsapp-bot" / "index.js"
MONITOR_SCRIPT = PROJECT_ROOT / "scripts" / "monitor_contatos_base.py"
SLACK_BOT_SCRIPT = PROJECT_ROOT / "slack-bot" / "main.py"


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orquestra os servicos da Iris (AI + WhatsApp + monitor + Slack Bot)."
    )
    parser.add_argument(
        "--no-monitor",
        dest="monitor",
        action="store_false",
        help="Nao iniciar o monitor de contatos (apenas AI e bot).",
    )
    parser.add_argument(
        "--reset-whatsapp-session",
        action="store_true",
        help="Limpa sessoes WhatsApp (Baileys + WWebJS) antes de iniciar para forcar novo QR Code.",
    )
    parser.add_argument(
        "--no-slack-bot",
        dest="slack_bot",
        action="store_false",
        help="Nao iniciar o Slack Bot.",
    )
    parser.add_argument(
        "--authorized-slack-user",
        type=str,
        default=None,
        help="Override do usuario autorizado no Slack Bot (ex.: UXXXXXXXX).",
    )
    return parser.parse_args(argv)


@dataclass
class ProcSpec:
    name: str
    cmd: list[str]
    cwd: Path
    env: Optional[dict[str, str]] = None
    proc: Optional[subprocess.Popen] = None


def _spawn(spec: ProcSpec) -> None:
    if spec.proc and spec.proc.poll() is None:
        return
    child_env = os.environ.copy()
    if spec.env:
        child_env.update(spec.env)
    spec.proc = subprocess.Popen(spec.cmd, cwd=str(spec.cwd), env=child_env)
    print(f"[MAIN] {spec.name} iniciado (PID: {spec.proc.pid})")


def _stop(spec: ProcSpec, timeout_s: float = 8.0) -> None:
    if not spec.proc:
        return
    p = spec.proc
    if p.poll() is not None:
        return
    try:
        p.terminate()
    except Exception:
        return
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if p.poll() is not None:
            return
        time.sleep(0.2)
    try:
        p.kill()
    except Exception:
        return


def _print_header() -> None:
    print("=" * 80)
    print("[IRIS] ORQUESTRADOR OUTBOUND (AI + WhatsApp + Monitor CSV + Slack Bot)")
    print("=" * 80)
    print()


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    _print_header()

    if not AI_SERVICE_DIR.exists():
        print(f"[MAIN] ERRO: diretorio ai-service nao encontrado: {AI_SERVICE_DIR}")
        return 1
    if not BOT_SCRIPT.exists():
        print(f"[MAIN] ERRO: bot nao encontrado: {BOT_SCRIPT}")
        return 1
    if args.monitor and not MONITOR_SCRIPT.exists():
        print(f"[MAIN] ERRO: monitor nao encontrado: {MONITOR_SCRIPT}")
        return 1
    if args.slack_bot and not SLACK_BOT_SCRIPT.exists():
        print(f"[MAIN] ERRO: slack-bot nao encontrado: {SLACK_BOT_SCRIPT}")
        return 1

    ai = ProcSpec(
        name="AI Service",
        cmd=[sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001"],
        cwd=AI_SERVICE_DIR,
    )
    bot = ProcSpec(
        name="WhatsApp Bot",
        cmd=["node", str(BOT_SCRIPT)],
        cwd=PROJECT_ROOT,
        env={"RESET_SESSION": "true"} if args.reset_whatsapp_session else None,
    )
    monitor = ProcSpec(
        name="Monitor CSV Base",
        cmd=[sys.executable, str(MONITOR_SCRIPT)],
        cwd=PROJECT_ROOT,
    )
    slack_bot = ProcSpec(
        name="Slack Bot",
        cmd=[sys.executable, str(SLACK_BOT_SCRIPT)],
        cwd=PROJECT_ROOT,
        env={"SLACK_AUTHORIZED_USER_ID": args.authorized_slack_user} if args.authorized_slack_user else None,
    )

    core_specs = (ai, bot)
    monitor_enabled = args.monitor
    slack_bot_enabled = args.slack_bot

    active_specs = list(core_specs)
    if monitor_enabled:
        active_specs.append(monitor)
    if slack_bot_enabled:
        active_specs.append(slack_bot)

    stopping = False

    def _handle_stop(_sig, _frame=None):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print("\n[MAIN] Encerrando processos...")
        if monitor_enabled:
            _stop(monitor)
        if slack_bot_enabled:
            _stop(slack_bot)
        _stop(bot)
        _stop(ai)
        print("[MAIN] Encerrado.")

    signal.signal(signal.SIGINT, _handle_stop)
    try:
        signal.signal(signal.SIGTERM, _handle_stop)
    except Exception:
        pass

    for spec in core_specs:
        _spawn(spec)
        time.sleep(2)

    if monitor_enabled:
        _spawn(monitor)
        time.sleep(2)

    if slack_bot_enabled:
        _spawn(slack_bot)
        time.sleep(2)

    while not stopping:
        time.sleep(5)
        for spec in active_specs:
            p = spec.proc
            if not p:
                continue
            code = p.poll()
            if code is None:
                continue
            if stopping:
                break
            print(f"[MAIN] {spec.name} parou (exit_code={code}). Reiniciando em 2s...")
            time.sleep(2)
            _spawn(spec)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
