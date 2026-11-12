"""Telegram bot for faresnipe-multiuser.

Implements a long-polling bot using only stdlib ``urllib`` (no external
dependencies). Supports private chats and groups, with admin-only
subscription management in groups, ``my_chat_member`` handling for bot
removal, and a conversation state machine for the /subscribe flow.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from .config import AppConfig
from .models import ChatType, SubscriberTier
from .user_store import UserStore

IATA_RE = re.compile(r"^[A-Z]{3}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Conversation states
STATE_AWAIT_ORIGIN = "await_origin"
STATE_AWAIT_DESTINATION = "await_destination"
STATE_AWAIT_DATE = "await_date"
STATE_AWAIT_FLEX = "await_flex"
STATE_AWAIT_MAX_PRICE = "await_max_price"
STATE_AWAIT_UNSUBSCRIBE = "await_unsubscribe"


class TelegramError(Exception):
    """Raised for Telegram API errors."""


class TelegramBot:
    """Long-polling Telegram bot with command handling and conversation state."""

    def __init__(
        self,
        config: AppConfig,
        user_store: UserStore,
        opener: Callable[..., object] = urllib.request.urlopen,
        error_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.user_store = user_store
        self.opener = opener
        self._error_sink = error_sink or (lambda msg: print(f"telegram bot: {msg}"))
        self._offset: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, cycles: int | None = None) -> None:
        """Run the polling loop. If cycles is None, run forever."""
        try:
            self._set_commands()
        except Exception:
            pass  # Ignore errors setting commands - they are optional
        cycle = 0
        while cycles is None or cycle < cycles:
            cycle += 1
            try:
                updates = self._get_updates()
                for update in updates:
                    self._handle_update(update)
            except Exception as exc:
                self._error_sink(f"polling error: {exc}")
            time.sleep(self.config.telegram.polling_interval)

    # ------------------------------------------------------------------
    # Telegram API
    # ------------------------------------------------------------------

    def _api(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a Telegram Bot API method."""
        url = API_BASE.format(token=self.config.telegram.bot_token, method=method)
        data = json.dumps(params or {}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TelegramError(f"Telegram API error {exc.code}: {exc.read().decode('utf-8')}") from exc
        except Exception as exc:
            raise TelegramError(f"Telegram API request failed: {exc}") from exc

        if not body.get("ok"):
            raise TelegramError(f"Telegram API error: {body.get('description', 'unknown')}")
        return body.get("result", {})

    def _get_updates(self) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.config.telegram.polling_timeout,
            "allowed_updates": ["message", "my_chat_member"],
        }
        if self._offset is not None:
            params["offset"] = self._offset
        result = self._api("getUpdates", params)
        if isinstance(result, list):
            for update in result:
                update_id = update.get("update_id")
                if update_id is not None:
                    self._offset = int(update_id) + 1
            return result
        return []

    def _set_commands(self) -> None:
        commands = [
            {"command": "start", "description": "Register and show welcome"},
            {"command": "subscribe", "description": "Subscribe to a route"},
            {"command": "list", "description": "List your subscriptions"},
            {"command": "unsubscribe", "description": "Remove a subscription"},
            {"command": "upgrade", "description": "Upgrade to premium"},
            {"command": "status", "description": "Show your status"},
            {"command": "help", "description": "Show help"},
        ]
        try:
            self._api("setMyCommands", {"commands": commands})
        except TelegramError as exc:
            self._error_sink(f"setMyCommands failed: {exc}")

    def _send_message(self, chat_id: int, text: str) -> None:
        self._api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

    def _get_chat_member(self, chat_id: int, user_id: int) -> dict[str, Any]:
        return self._api("getChatMember", {"chat_id": chat_id, "user_id": user_id})

    # ------------------------------------------------------------------
    # Update handling
    # ------------------------------------------------------------------

    def _handle_update(self, update: dict[str, Any]) -> None:
        if "my_chat_member" in update:
            self._handle_my_chat_member(update["my_chat_member"])
            return
        if "message" not in update:
            return
        message = update["message"]
        chat = message.get("chat", {})
        chat_id = int(chat.get("id", 0))
        chat_type = chat.get("type", "private")
        text = message.get("text", "").strip()

        # Register subscriber on any message
        self._ensure_subscriber(chat_id, chat_type, chat)

        # Handle commands
        if text.startswith("/"):
            self._handle_command(chat_id, chat_type, text, message)
            return

        # Handle conversation state
        self._handle_conversation(chat_id, text)

    def _ensure_subscriber(self, chat_id: int, chat_type: str, chat: dict[str, Any]) -> None:
        from_user = chat.get("from") or {}
        self.user_store.upsert_subscriber(
            telegram_chat_id=chat_id,
            chat_type=ChatType(chat_type),
            chat_title=chat.get("title"),
            username=from_user.get("username"),
            first_name=from_user.get("first_name"),
        )

    def _handle_my_chat_member(self, event: dict[str, Any]) -> None:
        """Handle bot being added/removed from a group."""
        chat = event.get("chat", {})
        chat_id = int(chat.get("id", 0))
        new_status = event.get("new_chat_member", {}).get("status", "")

        if new_status in ("left", "kicked"):
            # Bot was removed from the group — deactivate subscriber
            self.user_store.set_subscriber_active(chat_id, False)
        elif new_status in ("member", "administrator", "creator"):
            # Bot was added back — reactivate
            self.user_store.set_subscriber_active(chat_id, True)

    def _handle_command(
        self, chat_id: int, chat_type: str, text: str, message: dict[str, Any]
    ) -> None:
        command = text.split()[0].lower()

        if command == "/start":
            self._cmd_start(chat_id, chat_type)
        elif command == "/subscribe":
            self._cmd_subscribe(chat_id, chat_type, message)
        elif command == "/list":
            self._cmd_list(chat_id)
        elif command == "/unsubscribe":
            self._cmd_unsubscribe(chat_id, chat_type, message)
        elif command == "/upgrade":
            self._cmd_upgrade(chat_id, chat_type)
        elif command == "/status":
            self._cmd_status(chat_id)
        elif command == "/help":
            self._cmd_help(chat_id)
        elif command == "/cancel":
            self.user_store.set_conversation_state(chat_id, None)
            self._send_message(chat_id, "❌ Operação cancelada.")
        else:
            self._send_message(chat_id, "Comando desconhecido. Use /help para ver os comandos.")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _cmd_start(self, chat_id: int, chat_type: str) -> None:
        if chat_type == "private":
            self._send_message(
                chat_id,
                "✈️ Bem-vindo ao Faresnipe!\n\n"
                "Eu monitoro preços de passagens aéreas e te aviso quando encontrar promoções.\n\n"
                "Comandos:\n"
                "/subscribe - Assinar uma rota\n"
                "/list - Ver suas assinaturas\n"
                "/unsubscribe - Remover assinatura\n"
                "/status - Ver seu status\n"
                "/upgrade - Upgrade para premium\n"
                "/help - Ajuda",
            )
        else:
            self._send_message(
                chat_id,
                "✈️ Faresnipe adicionado ao grupo!\n\n"
                "Para configurar assinaturas, um administrador deve usar /subscribe.",
            )

    def _cmd_subscribe(self, chat_id: int, chat_type: str, message: dict[str, Any]) -> None:
        # In groups, only admins can subscribe
        if chat_type != "private":
            if not self._is_admin(chat_id, message):
                self._send_message(chat_id, "⛔ Apenas administradores podem configurar assinaturas neste grupo.")
                return

        self.user_store.set_conversation_state(
            chat_id, {"state": STATE_AWAIT_ORIGIN, "data": {}}
        )
        self._send_message(chat_id, "✈️ Qual o aeroporto de origem? (ex: YUL)")

    def _cmd_list(self, chat_id: int) -> None:
        subscriber = self.user_store.get_subscriber(chat_id)
        if not subscriber:
            self._send_message(chat_id, "Você ainda não está registrado. Use /start.")
            return
        subs = self.user_store.list_subscriptions(subscriber.id)
        if not subs:
            self._send_message(chat_id, "Você não tem assinaturas ativas. Use /subscribe para adicionar.")
            return
        lines = ["📋 Suas assinaturas:\n"]
        for i, sub in enumerate(subs, 1):
            lines.append(
                f"{i}. {sub.origin} → {sub.destination} | {sub.departure_date} "
                f"(±{sub.departure_flex_days}d)"
            )
        self._send_message(chat_id, "\n".join(lines))

    def _cmd_unsubscribe(self, chat_id: int, chat_type: str, message: dict[str, Any]) -> None:
        if chat_type != "private":
            if not self._is_admin(chat_id, message):
                self._send_message(chat_id, "⛔ Apenas administradores podem remover assinaturas neste grupo.")
                return

        subscriber = self.user_store.get_subscriber(chat_id)
        if not subscriber:
            self._send_message(chat_id, "Você ainda não está registrado. Use /start.")
            return
        subs = self.user_store.list_subscriptions(subscriber.id)
        if not subs:
            self._send_message(chat_id, "Você não tem assinaturas ativas.")
            return

        lines = ["Selecione a assinatura para remover (envie o número):\n"]
        for i, sub in enumerate(subs, 1):
            lines.append(f"{i}. {sub.origin} → {sub.destination} | {sub.departure_date}")
        self.user_store.set_conversation_state(
            chat_id, {"state": STATE_AWAIT_UNSUBSCRIBE, "data": {"subs": [s.id for s in subs]}}
        )
        self._send_message(chat_id, "\n".join(lines))

    def _cmd_upgrade(self, chat_id: int, chat_type: str) -> None:
        if chat_type != "private":
            self._send_message(
                chat_id,
                "💎 Para upgrade premium do grupo, um administrador deve entrar em contato "
                "com o suporte.",
            )
            return
        self._send_message(
            chat_id,
            "💎 Plano Premium\n\n"
            "Benefícios:\n"
            "• Prioridade na entrega de promoções\n"
            "• Sem limite diário de notificações\n"
            "• Todas as promoções, incluindo as menores\n\n"
            "Para ativar, entre em contato com o suporte.",
        )

    def _cmd_status(self, chat_id: int) -> None:
        subscriber = self.user_store.get_subscriber(chat_id)
        if not subscriber:
            self._send_message(chat_id, "Você ainda não está registrado. Use /start.")
            return
        tier = "💎 Premium" if subscriber.tier == SubscriberTier.PREMIUM else "🆓 Free"
        sent_today = self.user_store.daily_notification_count(subscriber.id)
        self._send_message(
            chat_id,
            f"📊 Status\n\n"
            f"Plano: {tier}\n"
            f"Notificações hoje: {sent_today}\n"
            f"Assinaturas ativas: {len(self.user_store.list_subscriptions(subscriber.id))}",
        )

    def _cmd_help(self, chat_id: int) -> None:
        self._send_message(
            chat_id,
            "🤖 Comandos disponíveis:\n\n"
            "/start - Registrar e ver boas-vindas\n"
            "/subscribe - Assinar uma rota (origem → destino + data)\n"
            "/list - Ver suas assinaturas\n"
            "/unsubscribe - Remover uma assinatura\n"
            "/upgrade - Ver informações do plano premium\n"
            "/status - Ver seu status e notificações\n"
            "/cancel - Cancelar operação em andamento\n"
            "/help - Mostrar esta ajuda",
        )

    # ------------------------------------------------------------------
    # Conversation handling
    # ------------------------------------------------------------------

    def _handle_conversation(self, chat_id: int, text: str) -> None:
        state = self.user_store.get_conversation_state(chat_id)
        if not state:
            return

        step = state.get("state")
        data = state.get("data", {})

        if step == STATE_AWAIT_ORIGIN:
            origin = text.upper()
            if not IATA_RE.match(origin):
                self._send_message(chat_id, "❌ Código de aeroporto inválido. Use 3 letras (ex: YUL).")
                return
            data["origin"] = origin
            self.user_store.set_conversation_state(
                chat_id, {"state": STATE_AWAIT_DESTINATION, "data": data}
            )
            self._send_message(chat_id, "✈️ Qual o destino? (ex: GRU)")

        elif step == STATE_AWAIT_DESTINATION:
            destination = text.upper()
            if not IATA_RE.match(destination):
                self._send_message(chat_id, "❌ Código de aeroporto inválido. Use 3 letras (ex: GRU).")
                return
            data["destination"] = destination
            self.user_store.set_conversation_state(
                chat_id, {"state": STATE_AWAIT_DATE, "data": data}
            )
            self._send_message(chat_id, "📅 Qual a data aproximada de embarque? (YYYY-MM-DD)")

        elif step == STATE_AWAIT_DATE:
            if not DATE_RE.match(text):
                self._send_message(chat_id, "❌ Data inválida. Use o formato YYYY-MM-DD (ex: 2026-09-15).")
                return
            try:
                departure_date = date.fromisoformat(text)
            except ValueError:
                self._send_message(chat_id, "❌ Data inválida. Use o formato YYYY-MM-DD (ex: 2026-09-15).")
                return
            data["departure_date"] = departure_date.isoformat()
            self.user_store.set_conversation_state(
                chat_id, {"state": STATE_AWAIT_FLEX, "data": data}
            )
            self._send_message(
                chat_id,
                "📏 Quantos dias de flexibilidade na data? (padrão: 7)\n"
                "Envie um número ou /skip para usar 7.",
            )

        elif step == STATE_AWAIT_FLEX:
            if text == "/skip":
                flex = 7
            else:
                try:
                    flex = int(text)
                    if flex < 0 or flex > 30:
                        raise ValueError
                except ValueError:
                    self._send_message(chat_id, "❌ Número inválido. Use 0-30 ou /skip.")
                    return
            data["flex"] = flex
            self.user_store.set_conversation_state(
                chat_id, {"state": STATE_AWAIT_MAX_PRICE, "data": data}
            )
            self._send_message(
                chat_id,
                "💰 Preço máximo? (ex: 500)\n"
                "Envie um número ou /skip para sem limite.",
            )

        elif step == STATE_AWAIT_MAX_PRICE:
            if text == "/skip":
                max_price = None
            else:
                try:
                    max_price = Decimal(text)
                    if max_price <= 0:
                        raise ValueError
                except ValueError:
                    self._send_message(chat_id, "❌ Preço inválido. Use um número positivo ou /skip.")
                    return
            data["max_price"] = str(max_price) if max_price else None
            self._complete_subscription(chat_id, data)

        elif step == STATE_AWAIT_UNSUBSCRIBE:
            try:
                index = int(text) - 1
                sub_ids = data.get("subs", [])
                if index < 0 or index >= len(sub_ids):
                    raise ValueError
            except ValueError:
                self._send_message(chat_id, "❌ Número inválido. Envie o número da assinatura.")
                return
            sub_id = sub_ids[index]
            self.user_store.remove_subscription(sub_id)
            self.user_store.set_conversation_state(chat_id, None)
            self._send_message(chat_id, "✅ Assinatura removida!")

    def _complete_subscription(self, chat_id: int, data: dict[str, Any]) -> None:
        subscriber = self.user_store.get_subscriber(chat_id)
        if not subscriber:
            self._send_message(chat_id, "Erro: usuário não registrado. Use /start.")
            return

        departure_date = date.fromisoformat(data["departure_date"])
        max_price = Decimal(data["max_price"]) if data.get("max_price") else None

        self.user_store.add_subscription(
            subscriber_id=subscriber.id,
            origin=data["origin"],
            destination=data["destination"],
            departure_date=departure_date,
            departure_flex_days=int(data.get("flex", 7)),
            max_price=max_price,
        )
        self.user_store.set_conversation_state(chat_id, None)
        self._send_message(
            chat_id,
            f"✅ Assinatura criada!\n\n"
            f"{data['origin']} → {data['destination']}\n"
            f"Data: {departure_date} (±{data.get('flex', 7)} dias)\n"
            f"Preço máx: {max_price if max_price else 'Sem limite'}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_admin(self, chat_id: int, message: dict[str, Any]) -> bool:
        """Check if the message sender is an admin of the group."""
        from_user = message.get("from", {})
        user_id = from_user.get("id")
        if not user_id:
            return False
        try:
            member = self._get_chat_member(chat_id, int(user_id))
            status = member.get("status", "")
            return status in ("creator", "administrator")
        except TelegramError:
            return False