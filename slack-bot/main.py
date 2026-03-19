import csv
import json
import logging
import os
import random
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set

import qrcode
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = PROJECT_ROOT / "config.json"
FOLLOW_UPS_FILE = PROJECT_ROOT / "outbound_data" / "follow_ups.json"
SENT_MESSAGES_FILE = PROJECT_ROOT / "outbound_data" / "sent_messages.json"
SLACK_DISPATCH_STATE_FILE = PROJECT_ROOT / "outbound_data" / "slack_dispatch_state.json"
WHATSAPP_READY_FILE = PROJECT_ROOT / "whatsapp_ready.json"
WHATSAPP_QR_FILE = PROJECT_ROOT / "whatsapp_qr.json"
WHATSAPP_RESET_REQUEST_FILE = PROJECT_ROOT / "whatsapp_reset_request.json"

LEADS_CNAE_NOME_FILE = PROJECT_ROOT / "leads_por_cnae_e_nome.csv"
LEADS_CNAE_FILE = PROJECT_ROOT / "leads_por_cnae.csv"
LEADS_NOME_FILE = PROJECT_ROOT / "leads_por_nome.csv"

DEFAULT_AUTH_USERS = []
PREVIEW_STORE: Dict[str, Dict[str, Any]] = {}
RECENT_EVENT_KEYS: Dict[str, float] = {}
LAST_MENU_AT: Dict[str, float] = {}
EVENT_DEDUP_TTL_SECONDS = 120.0
MENU_COOLDOWN_SECONDS = 5.0
DEFAULT_INITIAL_BASE_MESSAGE = (
    "Olá, tudo bem?\n\n"
    "Sou o Eduardo, da Silicon, e trabalhamos com iluminação para o aumento de fotossíntese de mudas.\n\n"
    "Vi que vocês trabalham com produção de mudas e preciso falar com o responsável pelo manejo do viveiro.\n\n"
    "Consegue me ajudar a falar com o responsável?"
)

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now().isoformat()


def _now_ts() -> float:
    return datetime.now().timestamp()


def _cleanup_recent_events(now_ts: float) -> None:
    stale = [k for k, ts in RECENT_EVENT_KEYS.items() if (now_ts - ts) > EVENT_DEDUP_TTL_SECONDS]
    for k in stale:
        RECENT_EVENT_KEYS.pop(k, None)


def _event_dedup_key(body: Dict[str, Any], event: Dict[str, Any]) -> str:
    event_id = str(body.get("event_id") or "").strip()
    if event_id:
        return f"event_id:{event_id}"
    client_msg_id = str(event.get("client_msg_id") or "").strip()
    if client_msg_id:
        return f"client_msg_id:{client_msg_id}"
    return "fallback:{channel}:{user}:{ts}:{text}".format(
        channel=str(event.get("channel") or ""),
        user=str(event.get("user") or ""),
        ts=str(event.get("ts") or ""),
        text=str(event.get("text") or ""),
    )


def _is_duplicate_event(body: Dict[str, Any], event: Dict[str, Any]) -> bool:
    now_ts = _now_ts()
    _cleanup_recent_events(now_ts)
    key = _event_dedup_key(body, event)
    if key in RECENT_EVENT_KEYS:
        return True
    RECENT_EVENT_KEYS[key] = now_ts
    return False


def _can_send_menu(user_id: str, channel_id: str) -> bool:
    now_ts = _now_ts()
    key = f"{user_id}:{channel_id}"
    last = LAST_MENU_AT.get(key, 0.0)
    if (now_ts - last) < MENU_COOLDOWN_SECONDS:
        return False
    LAST_MENU_AT[key] = now_ts
    return True


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return default
        return json.loads(raw)
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> Dict[str, Any]:
    cfg = load_json(CONFIG_FILE, {})
    return cfg if isinstance(cfg, dict) else {}


def _parse_user_ids(raw: str) -> Set[str]:
    return {item.strip() for item in str(raw or "").split(",") if item.strip()}


def get_authorized_user_ids() -> Set[str]:
    env_ids = _parse_user_ids(os.getenv("SLACK_AUTHORIZED_USER_ID") or "")
    if env_ids:
        return env_ids

    cfg = load_config()
    slack_cfg = cfg.get("slack_bot") or {}

    cfg_ids = slack_cfg.get("authorized_user_ids")
    if isinstance(cfg_ids, list):
        normalized = {str(item).strip() for item in cfg_ids if str(item).strip()}
        if normalized:
            return normalized

    cfg_single = str(slack_cfg.get("authorized_user_id") or "").strip()
    if cfg_single:
        return {cfg_single}

    return set(DEFAULT_AUTH_USERS)


def is_authorized(user_id: str) -> bool:
    return str(user_id or "").strip() in get_authorized_user_ids()


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    if not digits:
        return ""
    if digits.startswith("55"):
        without = digits[2:]
    else:
        without = digits
        if len(without) in (10, 11):
            digits = "55" + without
    if not digits.startswith("55"):
        return ""
    body = digits[2:]
    if len(body) not in (10, 11):
        return ""
    ddd = body[:2]
    if not ddd.isdigit() or int(ddd) < 11 or int(ddd) > 99:
        return ""
    return digits


def read_leads(base_choice: str) -> List[Dict[str, str]]:
    # Base principal consolidada (CNAE + nome)
    if LEADS_CNAE_NOME_FILE.exists():
        file_path = LEADS_CNAE_NOME_FILE
    else:
        # Fallback legado
        file_path = LEADS_CNAE_FILE if base_choice == "cnae" else LEADS_NOME_FILE
    if not file_path.exists():
        return []

    out: List[Dict[str, str]] = []
    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone = normalize_phone(row.get("telefone", ""))
            if not phone:
                continue
            out.append(
                {
                    "phone": phone,
                    "empresa": (row.get("razao_social") or row.get("nome_fantasia") or "Empresa").strip()[:140],
                    "contato": "",
                    "cidade": (row.get("municipio") or "").strip(),
                    "estado": (row.get("uf") or "").strip(),
                    "email": (row.get("email") or "").strip(),
                    "cnpj": (row.get("cnpj") or "").strip(),
                }
            )
    return out


def get_already_contacted_numbers() -> set:
    follow_ups = load_json(FOLLOW_UPS_FILE, {})
    sent = load_json(SENT_MESSAGES_FILE, {})
    numbers = set()
    if isinstance(follow_ups, dict):
        numbers.update([re.sub(r"\D+", "", k) for k in follow_ups.keys()])
    if isinstance(sent, dict):
        numbers.update([re.sub(r"\D+", "", k) for k in sent.keys()])
    return {n for n in numbers if n}


def pick_random_leads(base_choice: str, qty: int) -> Dict[str, Any]:
    all_leads = read_leads(base_choice)
    total_found = len(all_leads)

    dedup: Dict[str, Dict[str, str]] = {}
    for item in all_leads:
        dedup[item["phone"]] = item
    valid_unique = list(dedup.values())

    already = get_already_contacted_numbers()
    available = [x for x in valid_unique if x["phone"] not in already]

    random.shuffle(available)
    selected = available[: qty]

    return {
        "total_found": total_found,
        "valid_unique": len(valid_unique),
        "already_contacted_removed": len(valid_unique) - len(available),
        "selected_count": len(selected),
        "selected": selected,
    }


def ensure_follow_up_entry(lead: Dict[str, str], batch_id: str, base_message: str) -> Dict[str, Any]:
    now = datetime.now()
    phone_digits = re.sub(r"\D+", "", lead.get("phone", ""))
    return {
        "empresa": lead.get("empresa", "Empresa"),
        "contato": lead.get("contato", ""),
        "cargo": "",
        "sent_at": "",
        "responded": False,
        "status": "active",
        "cidade": lead.get("cidade", ""),
        "estado": lead.get("estado", ""),
        "email": lead.get("email", ""),
        "cnpj": lead.get("cnpj", ""),
        "telefone": phone_digits,
        "batch_id": batch_id,
        "base_message": base_message,
        "queued_at": now_iso(),
    }


def queue_selected_leads(leads: List[Dict[str, str]], base_choice: str) -> Dict[str, Any]:
    cfg = load_config()
    slack_cfg = cfg.get("slack_bot") or {}
    follow_ups = load_json(FOLLOW_UPS_FILE, {})
    if not isinstance(follow_ups, dict):
        follow_ups = {}

    batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    # Mensagem inicial padrao fixa para manter consistencia de abordagem.
    base_message = DEFAULT_INITIAL_BASE_MESSAGE

    inserted = 0
    for lead in leads:
        phone_digits = re.sub(r"\D+", "", lead.get("phone", ""))
        if not phone_digits:
            continue
        if phone_digits in follow_ups:
            continue
        follow_ups[phone_digits] = ensure_follow_up_entry(lead, batch_id, base_message)
        inserted += 1

    save_json(FOLLOW_UPS_FILE, follow_ups)

    dispatch_state = load_json(SLACK_DISPATCH_STATE_FILE, {})
    if not isinstance(dispatch_state, dict):
        dispatch_state = {}
    dispatch_state.update(
        {
            "paused": False,
            "pause_until": None,
            "pause_reason": "",
            "active_batch_id": batch_id,
            "base_message": base_message,
            "tone": str(slack_cfg.get("default_tone") or "aggressive"),
            "consecutive_errors": 0,
            "batch_sent_count": 0,
            "hourly_sent_timestamps": [],
            "sent_since_break": 0,
            "last_updated_at": now_iso(),
            "source_base": base_choice,
            "requested_count": len(leads),
            "queued_count": inserted,
        }
    )
    save_json(SLACK_DISPATCH_STATE_FILE, dispatch_state)

    return {"batch_id": batch_id, "queued": inserted, "requested": len(leads)}


def get_runtime_status() -> Dict[str, Any]:
    ready = load_json(WHATSAPP_READY_FILE, {})
    dispatch = load_json(SLACK_DISPATCH_STATE_FILE, {})
    follow_ups = load_json(FOLLOW_UPS_FILE, {})
    sent = load_json(SENT_MESSAGES_FILE, {})

    pending = 0
    sent_count = 0
    if isinstance(follow_ups, dict):
        for _, row in follow_ups.items():
            if str((row or {}).get("sent_at", "")).strip():
                sent_count += 1
            else:
                pending += 1

    errors = 0
    if isinstance(sent, dict):
        for _, row in sent.items():
            if str((row or {}).get("status", "")).lower() == "error":
                errors += 1

    return {
        "wpp_ready": bool((ready or {}).get("ready", False)),
        "wpp_event": (ready or {}).get("event", "n/a"),
        "wpp_at": (ready or {}).get("at", "n/a"),
        "pending": pending,
        "sent": sent_count,
        "errors": errors,
        "paused": bool((dispatch or {}).get("paused", False)),
        "pause_until": (dispatch or {}).get("pause_until"),
        "active_batch_id": (dispatch or {}).get("active_batch_id"),
    }


def render_menu_blocks() -> List[Dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Painel de Prospect*\nEscolha uma acao:"},
        },
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Novo disparo"}, "action_id": "open_new_batch"},
                {"type": "button", "text": {"type": "plain_text", "text": "Status"}, "action_id": "quick_status"},
                {"type": "button", "text": {"type": "plain_text", "text": "QR"}, "action_id": "quick_qr"},
                {"type": "button", "text": {"type": "plain_text", "text": "Reset WPP"}, "style": "danger", "action_id": "quick_reset"},
            ],
        },
    ]


def render_home_blocks(user_id: str) -> List[Dict[str, Any]]:
    st = get_runtime_status()
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Grow-ai | Painel Home"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Status WhatsApp:* `{'pronto' if st['wpp_ready'] else 'aguardando'}`\n"
                    f"*Fila pendente:* `{st['pending']}`\n"
                    f"*Enviados:* `{st['sent']}` | *Erros:* `{st['errors']}`\n"
                    f"*Batch ativo:* `{st['active_batch_id'] or 'n/a'}`"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Use a aba *Mensagens* para operar o bot.\n"
                    "Envie qualquer mensagem para abrir o menu de disparo."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Status"}, "action_id": "quick_status"},
                {"type": "button", "text": {"type": "plain_text", "text": "QR"}, "action_id": "quick_qr"},
                {"type": "button", "text": {"type": "plain_text", "text": "Reset WPP"}, "style": "danger", "action_id": "quick_reset"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Usuario: `{user_id}` | Atualizado em: `{now_iso()}`",
                }
            ],
        },
    ]


def make_status_text() -> str:
    st = get_runtime_status()
    return (
        "*Status Atual*\n"
        f"- WhatsApp pronto: `{st['wpp_ready']}`\n"
        f"- Ultimo evento WPP: `{st['wpp_event']}` em `{st['wpp_at']}`\n"
        f"- Fila pendente: `{st['pending']}`\n"
        f"- Enviados: `{st['sent']}`\n"
        f"- Erros: `{st['errors']}`\n"
        f"- Pausado: `{st['paused']}`\n"
        f"- Pause until: `{st['pause_until'] or 'n/a'}`\n"
        f"- Batch ativo: `{st['active_batch_id'] or 'n/a'}`"
    )


def send_qr_image(client, channel: str) -> None:
    qr_data = load_json(WHATSAPP_QR_FILE, {})
    qr_raw = str((qr_data or {}).get("qr") or "").strip()
    if not qr_raw:
        client.chat_postMessage(channel=channel, text="QR indisponivel agora. Aguarde evento de login e tente `qr` novamente.")
        return

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    img = qrcode.make(qr_raw)
    img.save(tmp_path)
    try:
        client.files_upload_v2(
            channel=channel,
            file=str(tmp_path),
            filename="whatsapp_qr.png",
            title="QR Code WhatsApp",
            initial_comment="Escaneie este QR no WhatsApp > Aparelhos conectados > Conectar um aparelho.",
        )
    except Exception:
        with tmp_path.open("rb") as f:
            client.files_upload(
                channels=channel,
                file=f,
                filename="whatsapp_qr.png",
                title="QR Code WhatsApp",
                initial_comment="Escaneie este QR no WhatsApp > Aparelhos conectados > Conectar um aparelho.",
            )
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def request_reset(user_id: str) -> None:
    save_json(
        WHATSAPP_RESET_REQUEST_FILE,
        {"requested_by": user_id, "requested_at": now_iso(), "reason": "slack_command_reset"},
    )


def set_pause(paused: bool, reason: str = "manual") -> None:
    state = load_json(SLACK_DISPATCH_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state["paused"] = paused
    state["pause_reason"] = reason if paused else ""
    state["pause_until"] = None if not paused else state.get("pause_until")
    state["last_updated_at"] = now_iso()
    save_json(SLACK_DISPATCH_STATE_FILE, state)


def ensure_authorized_dm(event: Dict[str, Any]) -> bool:
    if event.get("channel_type") != "im":
        return False
    return is_authorized(event.get("user", ""))


def build_app() -> App:
    load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"))

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    app_token = os.getenv("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        raise RuntimeError("SLACK_BOT_TOKEN e SLACK_APP_TOKEN sao obrigatorios")

    log_level = os.getenv("SLACK_LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("SLACK_LOG_FILE", "") or str(PROJECT_ROOT / "outbound_data" / "slack-bot.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )

    app = App(token=bot_token, signing_secret=os.getenv("SLACK_SIGNING_SECRET", "unused-in-socket-mode"))
    logger.info("logging to %s level %s", log_file, log_level)

    @app.use
    def log_all_events(logger, body, next):
        event = (body or {}).get("event", {}) if isinstance(body, dict) else {}
        logger.info(
            "slack incoming type=%s event_type=%s subtype=%s user=%s channel=%s channel_type=%s",
            (body or {}).get("type") if isinstance(body, dict) else None,
            event.get("type"),
            event.get("subtype"),
            event.get("user"),
            event.get("channel"),
            event.get("channel_type"),
        )
        next()

    @app.event("message")
    def on_message(body, event, say, client):
        text = str(event.get("text", "")).strip().lower()
        user_id = event.get("user")
        channel_id = str(event.get("channel") or "")
        logger.info(
            "slack event message user=%s channel=%s channel_type=%s subtype=%s text=%s",
            user_id,
            event.get("channel"),
            event.get("channel_type"),
            event.get("subtype"),
            text or "<empty>",
        )
        if event.get("subtype") is not None:
            logger.info("ignoring subtype event subtype=%s", event.get("subtype"))
            return
        if _is_duplicate_event(body, event):
            logger.info("ignoring duplicate message event")
            return

        if not ensure_authorized_dm(event):
            if event.get("channel_type") == "im":
                logger.warning("rejected unauthorized user %s channel %s", user_id, event.get("channel"))
                say("Usuario nao autorizado para este bot.")
            else:
                logger.info("ignoring non-dm event from user=%s channel_type=%s", user_id, event.get("channel_type"))
            return

        logger.info(
            "received message from %s channel=%s channel_type=%s text=%s",
            user_id,
            event.get("channel"),
            event.get("channel_type"),
            text or "<empty>",
        )

        def _send_menu() -> None:
            if not _can_send_menu(str(user_id or ""), channel_id):
                logger.info("skipping menu due to cooldown user=%s channel=%s", user_id, channel_id)
                return
            logger.info("sending menu to %s", user_id)
            say(blocks=render_menu_blocks(), text="Menu")

        if text == "status":
            logger.info("responding status request for %s", user_id)
            say(make_status_text())
            _send_menu()
            return
        if text == "qr":
            logger.info("responding qr request for %s", user_id)
            send_qr_image(client, event["channel"])
            _send_menu()
            return
        if text == "reset":
            logger.info("requesting reset from %s", user_id)
            request_reset(user_id)
            say("Reset solicitado. Aguarde alguns segundos e use `qr` para receber o novo codigo.")
            _send_menu()
            return
        if text == "parar":
            logger.info("pausing dispatch for %s", user_id)
            set_pause(True, reason="manual_pause")
            say("Envio pausado.")
            _send_menu()
            return
        if text == "retomar":
            logger.info("resuming dispatch for %s", user_id)
            set_pause(False)
            say("Envio retomado.")
            _send_menu()
            return

        logger.info("presenting menu after fallback for %s", user_id)
        _send_menu()

    @app.event("app_home_opened")
    def on_app_home_opened(event, client, logger):
        user_id = str(event.get("user") or "")
        logger.info("app_home_opened user=%s", user_id)
        try:
            client.views_publish(
                user_id=user_id,
                view={
                    "type": "home",
                    "blocks": render_home_blocks(user_id),
                },
            )
        except Exception as e:
            logger.error("failed to publish app home for %s: %s", user_id, e)

    @app.action("open_new_batch")
    def open_new_batch(ack, body, client):
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not is_authorized(user_id):
            return

        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "new_batch_submit",
                "title": {"type": "plain_text", "text": "Novo Disparo"},
                "submit": {"type": "plain_text", "text": "Gerar Preview"},
                "close": {"type": "plain_text", "text": "Cancelar"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "base_block",
                        "label": {"type": "plain_text", "text": "Base de leads"},
                        "element": {
                            "type": "static_select",
                            "action_id": "base_choice",
                            "options": [
                                {"text": {"type": "plain_text", "text": "leads_por_cnae_e_nome.csv"}, "value": "cnae_nome"},
                            ],
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "qty_block",
                        "label": {"type": "plain_text", "text": "Quantidade"},
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "qty_value",
                            "placeholder": {"type": "plain_text", "text": "Ex.: 40"},
                        },
                    },
                    {
                        "type": "input",
                        "optional": True,
                        "block_id": "dry_block",
                        "label": {"type": "plain_text", "text": "Dry-run"},
                        "element": {
                            "type": "checkboxes",
                            "action_id": "dry_check",
                            "options": [{"text": {"type": "plain_text", "text": "Nao inserir na fila (somente preview)"}, "value": "dry"}],
                        },
                    },
                ],
            },
        )

    @app.view("new_batch_submit")
    def on_new_batch_submit(ack, body, view, client):
        ack()
        user_id = body.get("user", {}).get("id", "")
        if not is_authorized(user_id):
            return

        values = view.get("state", {}).get("values", {})
        base_choice = values["base_block"]["base_choice"]["selected_option"]["value"]
        qty_raw = values["qty_block"]["qty_value"].get("value", "0")
        dry_values = values.get("dry_block", {}).get("dry_check", {}).get("selected_options", [])
        dry_run = any(x.get("value") == "dry" for x in dry_values)

        try:
            qty = int(str(qty_raw).strip())
        except Exception:
            qty = 0
        qty = max(1, min(80, qty))

        pick = pick_random_leads(base_choice, qty)
        logger.info(
            "batch preview user=%s base=%s requested=%s total_found=%s valid_unique=%s selected=%s",
            user_id,
            base_choice,
            qty,
            pick["total_found"],
            pick["valid_unique"],
            pick["selected_count"],
        )
        PREVIEW_STORE[user_id] = {
            "base_choice": base_choice,
            "qty": qty,
            "dry_run": dry_run,
            "selected": pick["selected"],
            "summary": pick,
            "created_at": now_iso(),
        }

        channel = body.get("user", {}).get("id")
        summary = pick
        preview_text = (
            "*Preview do lote*\n"
            f"- Base: `{base_choice}`\n"
            f"- Solicitado: `{qty}`\n"
            f"- Total encontrado: `{summary['total_found']}`\n"
            f"- Validos unicos: `{summary['valid_unique']}`\n"
            f"- Ja abordados removidos: `{summary['already_contacted_removed']}`\n"
            f"- Selecionados para fila: `{summary['selected_count']}`\n"
            f"- Dry-run: `{dry_run}`"
        )
        client.chat_postMessage(
            channel=channel,
            text=preview_text,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": preview_text}},
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "Confirmar envio"}, "style": "primary", "action_id": "confirm_batch"},
                        {"type": "button", "text": {"type": "plain_text", "text": "Cancelar"}, "action_id": "cancel_batch"},
                    ],
                },
            ],
        )

    @app.action("confirm_batch")
    def confirm_batch(ack, body, client):
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel_id = (body.get("channel") or {}).get("id") or user_id
        if not is_authorized(user_id):
            return
        data = PREVIEW_STORE.get(user_id)
        if not data:
            client.chat_postMessage(channel=channel_id, text="Nenhum preview pendente. Use `iniciar`.")
            return

        if data.get("dry_run"):
            client.chat_postMessage(channel=channel_id, text="Dry-run ativo: nada foi inserido na fila.")
            PREVIEW_STORE.pop(user_id, None)
            return

        result = queue_selected_leads(data.get("selected", []), data.get("base_choice", ""))
        logger.info(
            "batch confirmed user=%s base=%s requested=%s queued=%s batch_id=%s",
            user_id,
            data.get("base_choice", ""),
            result["requested"],
            result["queued"],
            result["batch_id"],
        )
        PREVIEW_STORE.pop(user_id, None)
        client.chat_postMessage(
            channel=channel_id,
            text=(
                "Lote confirmado com sucesso.\n"
                f"- Batch ID: `{result['batch_id']}`\n"
                f"- Solicitados: `{result['requested']}`\n"
                f"- Enfileirados: `{result['queued']}`\n"
                "Use `status` para acompanhar."
            ),
        )

    @app.action("cancel_batch")
    def cancel_batch(ack, body, client):
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel_id = (body.get("channel") or {}).get("id") or user_id
        PREVIEW_STORE.pop(user_id, None)
        client.chat_postMessage(channel=channel_id, text="Preview cancelado.")

    @app.action("quick_status")
    def quick_status(ack, body, client):
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel_id = (body.get("channel") or {}).get("id") or user_id
        if not is_authorized(user_id):
            return
        client.chat_postMessage(channel=channel_id, text=make_status_text())

    @app.action("quick_qr")
    def quick_qr(ack, body, client):
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel_id = (body.get("channel") or {}).get("id") or user_id
        if not is_authorized(user_id):
            return
        send_qr_image(client, channel_id)

    @app.action("quick_reset")
    def quick_reset(ack, body, client):
        ack()
        user_id = body.get("user", {}).get("id", "")
        channel_id = (body.get("channel") or {}).get("id") or user_id
        if not is_authorized(user_id):
            return
        request_reset(user_id)
        client.chat_postMessage(channel=channel_id, text="Reset solicitado. Aguarde e depois use `qr`.")

    return app


def main() -> int:
    app = build_app()
    app_token = os.getenv("SLACK_APP_TOKEN")
    print("[SLACK_BOT] starting in socket mode...")
    SocketModeHandler(app, app_token).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
