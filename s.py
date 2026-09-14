import os
import io
import json
import re
import asyncio
import unicodedata
from pathlib import Path
from typing import Optional, Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from dotenv import load_dotenv
from google import genai
from google.genai import types

import brain

import random


# ============================================================
# RE:VU TICKET MANAGER
# KOMPLETTER NEUAUFBAU
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")


# ============================================================
# ENV
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY fehlt in der .env")


# ============================================================
# DATEIEN
# ============================================================

RULES_FILE = BASE_DIR / "rules.json"
WARNINGS_FILE = BASE_DIR / "warnings.json"
CONFIG_FILE = BASE_DIR / "config.json"
BRAIN_FILE = BASE_DIR / "brain.json"


# ============================================================
# DEBUG
# ============================================================

print("=" * 70)
print("ReVu Ticket Manager")
print("=" * 70)
print(f"[PATH] s.py:     {BASE_DIR}")
print(f"[PATH] brain.py: {brain.__file__}")
print(f"[BRAIN] VERSION: {getattr(brain, 'VERSION', 'UNBEKANNT')}")
print(f"[AI] Modell:     {GEMINI_MODEL}")
print("=" * 70)


# ============================================================
# GEMINI
# ============================================================

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


async def call_gemini(
    system_prompt: str,
    user_prompt: str
) -> str:

    last_error = None

    for attempt in range(3):

        try:

            print(
                f"[GEMINI] Anfrage {attempt + 1}/3"
            )

            response = await asyncio.to_thread(
                gemini.models.generate_content,
                model=GEMINI_MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.1,
                    automatic_function_calling=(
                        types.AutomaticFunctionCallingConfig(
                            disable=True
                        )
                    )
                )
            )

            text = getattr(
                response,
                "text",
                None
            )

            if text and text.strip():

                print("[GEMINI] Antwort erhalten.")

                return text.strip()

            raise RuntimeError(
                "Gemini lieferte keine Textantwort."
            )

        except Exception as error:

            last_error = error

            print(
                f"[GEMINI] Fehler Versuch "
                f"{attempt + 1}: {error!r}"
            )

            if attempt < 2:
                await asyncio.sleep(
                    2 + attempt
                )

    raise last_error


async def gemini_json_call(
    prompt: str
) -> str:

    system_prompt = """
Du bist die KI eines Discord-Beschwerdesystems.

Antworte ausschließlich mit gültigem JSON.

VERBOTEN:

- Markdown
- ```json
- Text außerhalb des JSON
- erfundene Regeln
- erfundene Regelnummern
- erfundene Beweise
- erfundene Personen
- erfundene Tatsachen
- erfundene Konsequenzen

WICHTIG:

Eine Behauptung ist kein Beweis.

Verwende ausschließlich Regeln aus rules.json.

Wenn keine ausreichenden Beweise existieren,
darf kein Regelverstoß behauptet werden.

Wenn ausdrücklich gesagt wird, dass keine Beweise
vorhanden sind, muss dies als insufficient_evidence
behandelt werden.

Erfinde niemals fehlende Informationen.
"""

    return await call_gemini(
        system_prompt,
        prompt
    )


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()

intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# LOCKS
# ============================================================

_ticket_locks: dict[int, asyncio.Lock] = {}


def ticket_lock(
    channel_id: int
) -> asyncio.Lock:

    if channel_id not in _ticket_locks:
        _ticket_locks[channel_id] = asyncio.Lock()

    return _ticket_locks[channel_id]


# ============================================================
# JSON
# ============================================================

def save_json(
    path: Path,
    data: Any
) -> bool:

    try:

        temp_path = path.with_suffix(
            path.suffix + ".tmp"
        )

        with temp_path.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                indent=4,
                ensure_ascii=False
            )

        temp_path.replace(path)

        return True

    except Exception as error:

        print(
            f"[JSON] Speichern fehlgeschlagen "
            f"{path.name}: {error!r}"
        )

        return False


def load_json(
    path: Path,
    default: Any
) -> Any:

    try:

        if not path.exists():

            save_json(
                path,
                default
            )

            return default

        with path.open(
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as error:

        print(
            f"[JSON] Lesen fehlgeschlagen "
            f"{path.name}: {error!r}"
        )

        return default


# ============================================================
# CONFIG
# ============================================================

DEFAULT_CONFIG = {
    "staff_role_id": None,
    "complaint_log_channel_id": None,

    "verify": {
        "join_role_id": None,
        "verified_role_id": None,
        "channel_id": None,
        "log_channel_id": None,
        "panel_message_id": None
    },

    "complaint_log_settings": {
        "send_evidence": True,
        "send_complaint": True,
        "send_ai_result": True
    }
}


config = load_json(
    CONFIG_FILE,
    DEFAULT_CONFIG.copy()
)

if not isinstance(config, dict):
    config = {}

config.setdefault(
    "staff_role_id",
    None
)

config.setdefault(
    "complaint_log_channel_id",
    None
)

if not isinstance(
    config.get("complaint_log_settings"),
    dict
):
    config["complaint_log_settings"] = {}

for key in (
    "send_evidence",
    "send_complaint",
    "send_ai_result"
):

    config["complaint_log_settings"].setdefault(
        key,
        True
    )

save_json(
    CONFIG_FILE,
    config
)


# ============================================================
# DATEN
# ============================================================

warnings = load_json(
    WARNINGS_FILE,
    {}
)

if not isinstance(warnings, dict):
    warnings = {}


brain_data = load_json(
    BRAIN_FILE,
    {}
)

if not isinstance(brain_data, dict):
    brain_data = {}


# ============================================================
# NORMALISIERUNG
# ============================================================

def normalize(
    text: Any
) -> str:

    if text is None:
        return ""

    value = unicodedata.normalize(
        "NFKC",
        str(text)
    )

    return value.strip().lower()


def clean_text(
    text: Any,
    limit: int = 4000
) -> str:

    if text is None:
        return ""

    return str(text).strip()[:limit]


# ============================================================
# REGELN
# ============================================================

def load_rules() -> dict:

    data = load_json(
        RULES_FILE,
        {}
    )

    if not isinstance(data, dict):
        print("[RULES] rules.json ist ungültig.")
        return {}

    return data


def rules_text() -> str:

    return json.dumps(
        load_rules(),
        ensure_ascii=False,
        indent=2
    )


# ============================================================
# WARNUNGEN
# ============================================================

def get_warnings(
    guild_id: int,
    user_id: int
) -> list:

    guild_data = warnings.get(
        str(guild_id),
        {}
    )

    if not isinstance(
        guild_data,
        dict
    ):
        return []

    data = guild_data.get(
        str(user_id),
        []
    )

    if not isinstance(
        data,
        list
    ):
        return []

    return data


def set_warnings(
    guild_id: int,
    user_id: int,
    data: list
):

    warnings.setdefault(
        str(guild_id),
        {}
    )

    warnings[str(guild_id)][
        str(user_id)
    ] = data

    save_json(
        WARNINGS_FILE,
        warnings
    )


# ============================================================
# BRAIN STATE
# ============================================================

def create_state() -> dict:

    state = brain.new_case_state()

    if not isinstance(
        state,
        dict
    ):
        state = {}

    return state


def get_state(
    channel_id: int
) -> dict:

    key = str(channel_id)

    state = brain_data.get(key)

    if not isinstance(
        state,
        dict
    ):

        state = create_state()

        brain_data[key] = state

        save_json(
            BRAIN_FILE,
            brain_data
        )

    return state


def save_state(
    channel_id: int,
    state: dict
):

    brain_data[
        str(channel_id)
    ] = state

    save_json(
        BRAIN_FILE,
        brain_data
    )


def reset_state(
    channel_id: int
):

    brain_data[
        str(channel_id)
    ] = create_state()

    save_json(
        BRAIN_FILE,
        brain_data
    )


# ============================================================
# MITGLIED SUCHEN
# ============================================================

def find_member(
    guild: discord.Guild,
    text: str
) -> Optional[discord.Member]:

    if not text:
        return None

    text = str(text).strip()

    mention = re.fullmatch(
        r"<@!?(\d+)>",
        text
    )

    if mention:

        return guild.get_member(
            int(mention.group(1))
        )

    target = normalize(text)

    exact = []

    for member in guild.members:

        values = {
            normalize(member.name),
            normalize(member.display_name),
            normalize(
                f"{member.name}#{member.discriminator}"
            )
        }

        if target in values:
            exact.append(member)

    if len(exact) == 1:
        return exact[0]

    possible = []

    for member in guild.members:

        name = normalize(
            member.name
        )

        display = normalize(
            member.display_name
        )

        if (
            target in name
            or target in display
        ):
            possible.append(member)

    if len(possible) == 1:
        return possible[0]

    return None


def resolve_accused(
    guild: discord.Guild,
    state: dict
) -> Optional[discord.Member]:

    accused_id = state.get(
        "accused_user_id"
    )

    if accused_id:

        try:

            member = guild.get_member(
                int(accused_id)
            )

            if member:
                return member

        except Exception:
            pass

    hint = state.get(
        "accused_user_id_hint"
    )

    if hint:

        member = find_member(
            guild,
            str(hint)
        )

        if member:
            return member

    candidates = state.get(
        "accused_candidates",
        []
    )

    if (
        isinstance(candidates, list)
        and len(candidates) == 1
    ):

        return find_member(
            guild,
            str(candidates[0])
        )

    return None


# ============================================================
# TICKET
# ============================================================

def is_complaint_ticket(
    channel
) -> bool:

    if not isinstance(
        channel,
        discord.TextChannel
    ):
        return False

    name = normalize(
        channel.name
    )

    patterns = (
        r"ticket-\d+$",
        r"complaint-\d+$",
        r"beschwerde-\d+$",
        r".+-\d+$"
    )

    return any(
        re.fullmatch(
            pattern,
            name
        )
        for pattern in patterns
    )


# ============================================================
# URLS
# ============================================================

def extract_urls(
    text: str
) -> list[str]:

    if not text:
        return []

    return re.findall(
        r"https?://[^\s<>]+",
        text
    )


# ============================================================
# TICKET HISTORY
# ============================================================

async def get_ticket_messages(
    channel: discord.TextChannel,
    limit: int = 250
) -> list[discord.Message]:

    messages = []

    try:

        async for message in channel.history(
            limit=limit,
            oldest_first=True
        ):

            if message.author.bot:
                continue

            messages.append(
                message
            )

    except Exception as error:

        print(
            f"[TICKET] History Fehler: {error!r}"
        )

    return messages


async def build_conversation(
    channel: discord.TextChannel
) -> str:

    messages = await get_ticket_messages(
        channel
    )

    lines = []

    for message in messages:

        author = (
            message.author.display_name
        )

        content = (
            message.content or ""
        ).strip()

        if content:

            lines.append(
                f"{author}: {content}"
            )

        for attachment in message.attachments:

            lines.append(
                f"{author}: "
                f"[BEWEIS-ANHANG] "
                f"{attachment.filename} -> "
                f"{attachment.url}"
            )

        for url in extract_urls(
            content
        ):

            lines.append(
                f"{author}: "
                f"[BEWEIS-LINK] "
                f"{url}"
            )

    return "\n".join(lines)


# ============================================================
# BEWEISE
# ============================================================

async def collect_evidence(
    channel: discord.TextChannel
) -> list[dict]:

    evidence = []

    messages = await get_ticket_messages(
        channel
    )

    for message in messages:

        for attachment in message.attachments:

            evidence.append({
                "type": "attachment",
                "url": attachment.url,
                "filename": attachment.filename,
                "content_type": (
                    attachment.content_type
                    or ""
                ),
                "author": (
                    message.author.display_name
                ),
                "author_id": message.author.id,
                "message_id": message.id
            })

        for url in extract_urls(
            message.content or ""
        ):

            evidence.append({
                "type": "link",
                "url": url,
                "author": (
                    message.author.display_name
                ),
                "author_id": message.author.id,
                "message_id": message.id
            })

    return evidence


def is_image(
    evidence: dict
) -> bool:

    content_type = normalize(
        evidence.get(
            "content_type"
        )
    )

    if content_type.startswith(
        "image/"
    ):
        return True

    filename = normalize(
        evidence.get(
            "filename",
            ""
        )
    )

    return filename.endswith(
        (
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp"
        )
    )


async def download_evidence(
    evidence: list[dict]
) -> list[discord.File]:

    attachments = [
        item
        for item in evidence
        if item.get("type") == "attachment"
    ]

    if not attachments:
        return []

    files = []

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            for item in attachments[:10]:

                try:

                    async with session.get(
                        item["url"]
                    ) as response:

                        if response.status != 200:
                            continue

                        data = await response.read()

                        if not data:
                            continue

                        files.append(
                            discord.File(
                                io.BytesIO(data),
                                filename=item.get(
                                    "filename",
                                    "beweis"
                                )
                            )
                        )

                except Exception as error:

                    print(
                        f"[EVIDENCE] Downloadfehler: "
                        f"{error!r}"
                    )

    except Exception as error:

        print(
            f"[EVIDENCE] Sessionfehler: "
            f"{error!r}"
        )

    return files


# ============================================================
# STAFF
# ============================================================

def has_staff_access(
    member
) -> bool:

    if not isinstance(
        member,
        discord.Member
    ):
        return False

    permissions = member.guild_permissions

    if permissions.administrator:
        return True

    if permissions.manage_guild:
        return True

    role_id = config.get(
        "staff_role_id"
    )

    if not role_id:
        return False

    try:

        role = member.guild.get_role(
            int(role_id)
        )

    except Exception:

        return False

    return bool(
        role and role in member.roles
    )


# ============================================================
# CONFIDENCE
# ============================================================

def confidence(
    value
) -> float:

    try:

        number = float(value)

        if number > 1:
            number /= 100

        return max(
            0.0,
            min(1.0, number)
        )

    except Exception:

        return 0.0


# ============================================================
# RESULT EMBED
# ============================================================

def build_result_embed(
    state: dict
) -> discord.Embed:

    status = state.get(
        "overall_status",
        "manual_review"
    )

    judge = state.get(
        "judge_result"
    ) or {}

    rule_id = (
        state.get("final_rule")
        or state.get("confirmed_rule_id")
        or judge.get("rule_id")
    )

    rule_text = (
        state.get("final_rule_text")
        or state.get("confirmed_rule_text")
        or judge.get("rule_text")
    )

    consequence = (
        state.get("final_consequence")
        or judge.get("consequence")
    )

    explanation = (
        state.get("final_explanation")
        or judge.get("explanation")
        or "Keine Begründung vorhanden."
    )

    conf = confidence(
        state.get(
            "confidence",
            judge.get(
                "confidence",
                0
            )
        )
    )

    if status == "likely_violation":

        title = "⚠️ Möglicher Regelverstoß"
        color = discord.Color.red()

    elif status == "no_violation":

        title = "✅ Kein ausreichender Regelverstoß"
        color = discord.Color.green()

    elif status == "insufficient_evidence":

        title = "📎 Nicht genügend Beweise"
        color = discord.Color.orange()

    else:

        title = "❓ Manuelle Prüfung"
        color = discord.Color.orange()

    embed = discord.Embed(
        title=title,
        color=color,
        timestamp=discord.utils.utcnow()
    )

    accused = (
        state.get("accused_user_id")
        or state.get("accused_user_id_hint")
        or "Unbekannt"
    )

    try:

        member_id = int(accused)

        member = (
            discord.utils.get(
                bot.get_all_members(),
                id=member_id
            )
        )

        if member:
            accused = member.mention

    except Exception:
        pass

    embed.add_field(
        name="👤 Beschuldigter",
        value=str(accused)[:1024],
        inline=True
    )

    if rule_id:

        embed.add_field(
            name="📜 Regel",
            value=str(rule_id)[:1024],
            inline=True
        )

    if rule_text:

        embed.add_field(
            name="📖 Regeltext",
            value=str(rule_text)[:1024],
            inline=False
        )

    if consequence:

        embed.add_field(
            name="⚠️ Konsequenz",
            value=str(consequence)[:1024],
            inline=False
        )

    embed.add_field(
        name="🧠 Begründung",
        value=str(explanation)[:1024],
        inline=False
    )

    embed.add_field(
        name="📊 Konfidenz",
        value=f"{conf:.0%}",
        inline=True
    )

    return embed


# ============================================================
# COMPLAINT LOG CHANNEL
# ============================================================

async def get_log_channel(
    guild: discord.Guild
) -> Optional[discord.TextChannel]:

    log_id = config.get(
        "complaint_log_channel_id"
    )

    if not log_id:
        return None

    try:
        channel = guild.get_channel(
            int(log_id)
        )

        if channel:
            return channel

        fetched = await guild.fetch_channel(
            int(log_id)
        )

        if isinstance(
            fetched,
            discord.TextChannel
        ):
            return fetched

    except Exception as error:

        print(
            f"[LOG] Channel nicht gefunden: "
            f"{error!r}"
        )

    return None


# ============================================================
# COMPLAINT LOG
# ============================================================

async def send_to_complaint_log(
    channel: discord.TextChannel,
    state: dict,
    *,
    custom_message: Optional[str] = None,
    title: str = "🚨 Beschwerde an Teamler"
):

    log_channel = await get_log_channel(
        channel.guild
    )

    if not log_channel:

        raise RuntimeError(
            "Kein gültiger Complaint-Log-Channel gesetzt."
        )

    settings = config.get(
        "complaint_log_settings",
        {}
    )

    send_evidence = bool(
        settings.get(
            "send_evidence",
            True
        )
    )

    send_complaint = bool(
        settings.get(
            "send_complaint",
            True
        )
    )

    send_ai = bool(
        settings.get(
            "send_ai_result",
            True
        )
    )

    status = state.get(
        "overall_status",
        "unbekannt"
    )

    if status == "insufficient_evidence":

        color = discord.Color.orange()

    elif status == "no_violation":

        color = discord.Color.green()

    elif status == "likely_violation":

        color = discord.Color.red()

    else:

        color = discord.Color.blurple()

    embed = discord.Embed(
        title=title,
        color=color,
        timestamp=discord.utils.utcnow()
    )

    # --------------------------------------------------------
    # BESCHULDIGTER
    # --------------------------------------------------------

    accused = (
        state.get("accused_user_id")
        or state.get("accused_user_id_hint")
        or "Unbekannt"
    )

    try:

        member = channel.guild.get_member(
            int(accused)
        )

        if member:
            accused = member.mention

    except Exception:
        pass

    embed.add_field(
        name="👤 Beschuldigter",
        value=str(accused)[:1024],
        inline=True
    )

    embed.add_field(
        name="🎫 Ticket",
        value=channel.mention,
        inline=True
    )

    embed.add_field(
        name="📊 Status",
        value=str(status)[:1024],
        inline=True
    )

    # --------------------------------------------------------
    # BESCHWERDE
    # --------------------------------------------------------

    if send_complaint:

        conversation = await build_conversation(
            channel
        )

        if conversation:

            embed.add_field(
                name="📝 Beschwerde",
                value=conversation[-1000:],
                inline=False
            )

    # --------------------------------------------------------
    # KI
    # --------------------------------------------------------

    if send_ai:

        judge = state.get(
            "judge_result"
        ) or {}

        rule_id = (
            state.get("final_rule")
            or state.get("confirmed_rule_id")
            or judge.get("rule_id")
        )

        rule_text = (
            state.get("final_rule_text")
            or state.get("confirmed_rule_text")
            or judge.get("rule_text")
        )

        if rule_id:

            value = str(
                rule_id
            )

            if rule_text:

                value += (
                    f"\n{rule_text}"
                )

            embed.add_field(
                name="📜 Regel",
                value=value[:1024],
                inline=False
            )

        explanation = (
            state.get("final_explanation")
            or judge.get("explanation")
            or "Keine KI-Auswertung."
        )

        embed.add_field(
            name="🧠 KI-Auswertung",
            value=str(
                explanation
            )[:1024],
            inline=False
        )

        consequence = (
            state.get("final_consequence")
            or judge.get("consequence")
        )

        if consequence:

            embed.add_field(
                name="⚠️ Konsequenz",
                value=str(
                    consequence
                )[:1024],
                inline=False
            )

    # --------------------------------------------------------
    # MANUELLE NACHRICHT
    # --------------------------------------------------------

    if custom_message:

        embed.add_field(
            name="📨 Nachricht",
            value=str(
                custom_message
            )[:1024],
            inline=False
        )

    # --------------------------------------------------------
    # BEWEISE
    # --------------------------------------------------------

    evidence = []

    if send_evidence:

        evidence = await collect_evidence(
            channel
        )

        lines = []

        for item in evidence:

            if item.get("type") == "attachment":

                lines.append(
                    f"📎 **{item.get('filename', 'Datei')}**\n"
                    f"{item.get('url', '')}"
                )

            else:

                lines.append(
                    f"🔗 {item.get('url', '')}"
                )

        if lines:

            evidence_text = "\n\n".join(
                lines
            )

            if len(evidence_text) > 1000:

                evidence_text = (
                    evidence_text[:970]
                    + "\n… weitere Beweise im Ticket."
                )

        else:

            evidence_text = "Keine Beweise gefunden."

        embed.add_field(
            name="📎 Beweise",
            value=evidence_text,
            inline=False
        )

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    embed.add_field(
        name="🔗 Ticket-Link",
        value=channel.jump_url,
        inline=False
    )

    embed.set_footer(
        text=f"ReVu • {channel.guild.name}"
    )

    # --------------------------------------------------------
    # DATEIEN
    # --------------------------------------------------------

    files = []

    if send_evidence and evidence:

        files = await download_evidence(
            evidence
        )

    await log_channel.send(
        embed=embed,
        files=files
    )

    # --------------------------------------------------------
    # BILDER
    # --------------------------------------------------------

    if send_evidence:

        images = [
            item
            for item in evidence
            if (
                item.get("type") == "attachment"
                and is_image(item)
            )
        ]

        for item in images[:9]:

            image_embed = discord.Embed(
                title=(
                    "📎 Beweis: "
                    f"{item.get('filename', 'Bild')}"
                ),
                color=discord.Color.blurple()
            )

            image_embed.set_image(
                url=item["url"]
            )

            image_embed.set_footer(
                text=(
                    f"Von "
                    f"{item.get('author', 'Unbekannt')}"
                )
            )

            try:

                await log_channel.send(
                    embed=image_embed
                )

            except Exception as error:

                print(
                    f"[LOG] Bildfehler: {error!r}"
                )

    print(
        f"[LOG] Beschwerde erfolgreich geloggt: "
        f"{channel.name}"
    )

    return True


# ============================================================
# UI DUPLIKAT-SCHUTZ
# ============================================================

def mark_ui(
    state: dict,
    key: str
) -> bool:

    if state.get(
        "_last_ui_key"
    ) == key:

        return False

    state[
        "_last_ui_key"
    ] = key

    return True


# ============================================================
# REGEL-BUTTONS
# ============================================================

class RuleConfirmView(
    discord.ui.View
):

    def __init__(
        self,
        channel_id: int
    ):

        super().__init__(
            timeout=None
        )

        self.channel_id = channel_id

    async def handle_no(
        self,
        interaction: discord.Interaction
    ):

        channel = interaction.channel

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return

        lock = ticket_lock(
            self.channel_id
        )

        async with lock:

            state = get_state(
                self.channel_id
            )

            if not state.get(
                "rule_confirmation_pending"
            ):

                await interaction.response.send_message(
                    "❌ Diese Regelabfrage ist nicht mehr aktiv.",
                    ephemeral=True
                )

                return

            await interaction.response.defer()

            try:

                # brain.reject_rule() benötigt nur den State.
                new_state = brain.reject_rule(
                    state
                )

            except Exception as error:

                print(
                    f"[RULE NO] {error!r}"
                )

                await interaction.followup.send(
                    "❌ Beim Suchen einer anderen Regel "
                    "ist ein Fehler aufgetreten.",
                    ephemeral=True
                )

                return

            save_state(
                self.channel_id,
                new_state
            )

            if not new_state.get(
                "suggested_rule_id"
            ):

                new_state["overall_status"] = (
                    "manual_review"
                )

                new_state["next_action"] = (
                    "manual_review"
                )

                save_state(
                    self.channel_id,
                    new_state
                )

                await interaction.message.edit(
                    content=(
                        "❌ **Keine weitere passende Regel gefunden.**\n"
                        "Die Beschwerde wird nicht automatisch "
                        "einer anderen Regel zugeordnet."
                    ),
                    embed=None,
                    view=None
                )

                return

            await interaction.message.edit(
                content=None,
                embed=build_rule_embed(
                    new_state
                ),
                view=RuleConfirmView(
                    self.channel_id
                )
            )

    @discord.ui.button(
        label="✅ Ja",
        style=discord.ButtonStyle.success,
        custom_id="revu_rule_yes"
    )
    async def yes(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        channel = interaction.channel

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return

        lock = ticket_lock(
            self.channel_id
        )

        async with lock:

            state = get_state(
                self.channel_id
            )

            if not state.get(
                "rule_confirmation_pending"
            ):

                await interaction.response.send_message(
                    "❌ Diese Regelabfrage ist nicht mehr aktiv.",
                    ephemeral=True
                )

                return

            rule_id = state.get(
                "suggested_rule_id"
            )

            if not rule_id:

                await interaction.response.send_message(
                    "❌ Keine vorgeschlagene Regel gefunden.",
                    ephemeral=True
                )

                return

            try:

                # WICHTIG:
                # confirm_rule() erwartet:
                # confirm_rule(state, rule_id)
                new_state = brain.confirm_rule(
                    state,
                    rule_id
                )

            except Exception as error:

                print(
                    f"[RULE YES] {error!r}"
                )

                await interaction.response.send_message(
                    "❌ Die Regel konnte nicht bestätigt werden.",
                    ephemeral=True
                )

                return

            new_state[
                "_last_ui_key"
            ] = "evidence_question"

            save_state(
                self.channel_id,
                new_state
            )

            await interaction.response.edit_message(
                content=(
                    "✅ **Regel bestätigt.**\n\n"
                    "Jetzt können Beweise hinzugefügt werden."
                ),
                embed=None,
                view=EvidenceView(
                    self.channel_id
                )
            )

    @discord.ui.button(
        label="❌ Nein",
        style=discord.ButtonStyle.danger,
        custom_id="revu_rule_no"
    )
    async def no(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await self.handle_no(
            interaction
        )


# ============================================================
# REGEL-EMBED
# ============================================================

def build_rule_embed(
    state: dict
) -> discord.Embed:

    rule_id = state.get(
        "suggested_rule_id"
    )

    # Regel direkt aus rules.json holen.
    # Dadurch wird immer der echte Regeltext angezeigt.
    rule = brain.get_rule_by_id(
        rule_id
    )

    rule_text = ""

    consequence = ""

    if rule:

        rule_text = str(
            rule.get(
                "text",
                ""
            )
        ).strip()

        consequence = str(
            rule.get(
                "consequence",
                ""
            )
        ).strip()

    # Fallback, falls der Text bereits im State vorhanden ist.
    if not rule_text:

        rule_text = str(
            state.get(
                "suggested_rule_text",
                ""
            )
        ).strip()

    confidence_value = confidence(
        state.get(
            "suggested_rule_confidence",
            state.get(
                "confidence",
                0
            )
        )
    )

    embed = discord.Embed(
        title="📜 Welche Regel passt?",
        description=(
            "Die KI hat den bisherigen "
            "Gesprächsverlauf analysiert und "
            "schlägt folgende Regel vor:"
        ),
        color=discord.Color.blurple()
    )

    # --------------------------------------------------------
    # REGELNUMMER
    # --------------------------------------------------------

    if rule_id:

        embed.add_field(
            name="📌 Vorgeschlagene Regel",
            value=f"**Regel {rule_id}**",
            inline=True
        )

    # --------------------------------------------------------
    # REGELTEXT
    # --------------------------------------------------------

    if rule_text:

        embed.add_field(
            name="📖 Regeltext",
            value=rule_text[:1024],
            inline=False
        )

    else:

        embed.add_field(
            name="📖 Regeltext",
            value="Kein Regeltext gefunden.",
            inline=False
        )

    # --------------------------------------------------------
    # KONSEQUENZ
    # --------------------------------------------------------

    if consequence:

        embed.add_field(
            name="⚖️ Konsequenz",
            value=consequence[:1024],
            inline=False
        )

    # --------------------------------------------------------
    # KI-CONFIDENCE
    # --------------------------------------------------------

    embed.add_field(
        name="🧠 KI-Einschätzung",
        value=f"{confidence_value:.0%}",
        inline=True
    )

    embed.set_footer(
        text="Du musst keine Regelnummer eingeben."
    )

    return embed
# ============================================================
# BEWEIS-BUTTONS
# ============================================================

class EvidenceView(
    discord.ui.View
):

    def __init__(
        self,
        channel_id: int
    ):

        super().__init__(
            timeout=None
        )

        self.channel_id = channel_id

    @discord.ui.button(
        label="📎 Beweise vorhanden",
        style=discord.ButtonStyle.success,
        custom_id="revu_evidence_yes"
    )
    async def yes(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        lock = ticket_lock(
            self.channel_id
        )

        async with lock:

            state = get_state(
                self.channel_id
            )

            try:

                if hasattr(
                    brain,
                    "mark_evidence_available"
                ):

                    state = brain.mark_evidence_available(
                        state
                    )

                else:

                    state["evidence_status"] = (
                        "awaiting"
                    )

                    state["next_action"] = (
                        "await_evidence"
                    )

                    state["question"] = (
                        "Bitte lade die Beweise jetzt "
                        "hier im Ticket hoch."
                    )

            except Exception as error:

                print(
                    f"[EVIDENCE YES] {error!r}"
                )

                state[
                    "evidence_status"
                ] = "awaiting"

                state[
                    "next_action"
                ] = "await_evidence"

            save_state(
                self.channel_id,
                state
            )

            await interaction.response.edit_message(
                content=(
                    "📎 **Alles klar.**\n\n"
                    "Lade die Beweise jetzt hier hoch "
                    "oder sende den entsprechenden Link."
                ),
                embed=None,
                view=None
            )

    @discord.ui.button(
        label="❌ Keine Beweise",
        style=discord.ButtonStyle.danger,
        custom_id="revu_evidence_no"
    )
    async def no(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        channel = interaction.channel

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return

        lock = ticket_lock(
            self.channel_id
        )

        async with lock:

            state = get_state(
                self.channel_id
            )

            try:

                state = brain.mark_no_evidence(
                    state
                )

            except Exception as error:

                print(
                    f"[EVIDENCE NO] {error!r}"
                )

                state[
                    "evidence_status"
                ] = "missing"

                state[
                    "evidence_explicitly_missing"
                ] = True

                state[
                    "overall_status"
                ] = "insufficient_evidence"

                state[
                    "next_action"
                ] = "complete"

            save_state(
                self.channel_id,
                state
            )

            await interaction.response.edit_message(
                content=(
                    "📎 **Keine Beweise angegeben.**\n\n"
                    "Der Vorwurf kann deshalb nicht "
                    "als Regelverstoß bestätigt werden."
                ),
                embed=None,
                view=None
            )

            await asyncio.sleep(0.5)

            await finalize_case(
                channel,
                state,
                "📎 Fall abgeschlossen – nicht genügend Beweise"
            )


# ============================================================
# FALL ABSCHLIESSEN
# ============================================================

async def finalize_case(
    channel: discord.TextChannel,
    state: dict,
    message: Optional[str] = None
):

    if state.get(
        "_case_logged"
    ):
        return

    state[
        "_case_logged"
    ] = True

    save_state(
        channel.id,
        state
    )

    if message:

        try:
            await channel.send(
                message
            )
        except Exception:
            pass

    try:

        await channel.send(
            embed=build_result_embed(
                state
            )
        )

    except Exception as error:

        print(
            f"[FINAL] Embed Fehler: {error!r}"
        )

    try:

        await send_to_complaint_log(
            channel,
            state,
            title=(
                "📎 Beschwerde abgeschlossen – "
                "nicht genügend Beweise"
            )
        )

    except Exception as error:

        print(
            f"[FINAL] Log Fehler: {error!r}"
        )

        try:

            await channel.send(
                "⚠️ Der Fall wurde ausgewertet, "
                "aber das Teamler-Log konnte nicht "
                f"gesendet werden: `{str(error)[:500]}`"
            )

        except Exception:
            pass


# ============================================================
# STAFF BESTÄTIGUNG
# ============================================================

class ComplaintConfirmView(
    discord.ui.View
):

    def __init__(
        self,
        channel_id: int
    ):

        super().__init__(
            timeout=None
        )

        self.channel_id = channel_id

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if not has_staff_access(
            interaction.user
        ):

            await interaction.response.send_message(
                "❌ Du bist nicht berechtigt.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="✅ Bestätigen",
        style=discord.ButtonStyle.success,
        custom_id="revu_staff_confirm"
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        channel = interaction.channel

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return

        lock = ticket_lock(
            self.channel_id
        )

        async with lock:

            state = get_state(
                self.channel_id
            )

            if state.get(
                "_case_logged"
            ):

                await interaction.response.send_message(
                    "❌ Dieser Fall wurde bereits verarbeitet.",
                    ephemeral=True
                )

                return

            state[
                "_case_logged"
            ] = True

            state[
                "next_action"
            ] = "complete"

            state[
                "overall_status"
            ] = "likely_violation"

            save_state(
                self.channel_id,
                state
            )

            await interaction.response.defer()

            try:

                await send_to_complaint_log(
                    channel,
                    state,
                    title="🚨 Beschwerde bestätigt"
                )

                await interaction.message.edit(
                    content=(
                        "✅ **Beschwerde bestätigt.**\n"
                        "Der Fall wurde ins Teamler-Log eingetragen."
                    ),
                    embed=None,
                    view=None
                )

            except Exception as error:

                state[
                    "_case_logged"
                ] = False

                save_state(
                    self.channel_id,
                    state
                )

                await interaction.message.edit(
                    content=(
                        "❌ **Loggen fehlgeschlagen.**\n\n"
                        f"`{str(error)[:1000]}`"
                    ),
                    embed=None,
                    view=None
                )

    @discord.ui.button(
        label="❌ Ablehnen",
        style=discord.ButtonStyle.danger,
        custom_id="revu_staff_reject"
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        channel = interaction.channel

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return

        lock = ticket_lock(
            self.channel_id
        )

        async with lock:

            state = get_state(
                self.channel_id
            )

            state[
                "overall_status"
            ] = "no_violation"

            state[
                "next_action"
            ] = "complete"

            state[
                "_case_logged"
            ] = True

            save_state(
                self.channel_id,
                state
            )

            await interaction.response.defer()

            try:

                await send_to_complaint_log(
                    channel,
                    state,
                    title="❌ Beschwerde abgelehnt"
                )

            except Exception as error:

                print(
                    f"[STAFF REJECT LOG] {error!r}"
                )

            await interaction.message.edit(
                content=(
                    "❌ **Beschwerde abgelehnt.**\n"
                    "Der Fall wurde als kein Regelverstoß abgeschlossen."
                ),
                embed=None,
                view=None
            )


# ============================================================
# BRAIN RESULT
# ============================================================

async def handle_brain_result(
    channel: discord.TextChannel,
    state: dict
):

    if not state:
        return

    status = state.get(
        "overall_status"
    )

    action = state.get(
        "next_action"
    )

    # --------------------------------------------------------
    # REGEL BESTÄTIGUNG
    # --------------------------------------------------------

    if (
        action == "confirm_rule"
        or state.get(
            "rule_confirmation_pending"
        )
    ):

        rule_id = state.get(
            "suggested_rule_id"
        )

        if not rule_id:
            return

        ui_key = f"rule:{rule_id}"

        if not mark_ui(
            state,
            ui_key
        ):
            return

        save_state(
            channel.id,
            state
        )

        await channel.send(
            embed=build_rule_embed(
                state
            ),
            view=RuleConfirmView(
                channel.id
            )
        )

        return

    # --------------------------------------------------------
    # BEWEISE
    # --------------------------------------------------------

    if (
        action == "await_evidence"
        or (
            state.get("rule_confirmed")
            and state.get("evidence_status")
            in ("unknown", "awaiting")
        )
    ):

        ui_key = "evidence_question"

        if not mark_ui(
            state,
            ui_key
        ):
            return

        save_state(
            channel.id,
            state
        )

        await channel.send(
            embed=discord.Embed(
                title="📎 Beweise",
                description=(
                    "Für die weitere Prüfung brauchen wir "
                    "Beweise für den Vorwurf."
                ),
                color=discord.Color.blurple()
            ),
            view=EvidenceView(
                channel.id
            )
        )

        return

    # --------------------------------------------------------
    # NICHT GENÜGEND BEWEISE
    # --------------------------------------------------------

    if status == "insufficient_evidence":

        if state.get(
            "_case_logged"
        ):
            return

        await finalize_case(
            channel,
            state,
            "📎 **Nicht genügend Beweise.**\n"
            "Der Vorwurf wird nicht als Regelverstoß bestätigt."
        )

        return

    # --------------------------------------------------------
    # KEIN VERSTOSS
    # --------------------------------------------------------

    if status == "no_violation":

        if state.get(
            "_result_sent"
        ):
            return

        state[
            "_result_sent"
        ] = True

        save_state(
            channel.id,
            state
        )

        await channel.send(
            embed=build_result_embed(
                state
            )
        )

        if not state.get(
            "_case_logged"
        ):

            try:

                state[
                    "_case_logged"
                ] = True

                save_state(
                    channel.id,
                    state
                )

                await send_to_complaint_log(
                    channel,
                    state,
                    title="✅ Beschwerde abgelehnt"
                )

            except Exception as error:

                print(
                    f"[NO VIOLATION LOG] {error!r}"
                )

        return

    # --------------------------------------------------------
    # MANUAL REVIEW
    # --------------------------------------------------------

    if (
        status == "manual_review"
        or action == "manual_review"
    ):

        if state.get(
            "_manual_review_sent"
        ):
            return

        state[
            "_manual_review_sent"
        ] = True

        save_state(
            channel.id,
            state
        )

        judge = state.get(
            "judge_result"
        ) or {}

        reason = (
            state.get("final_explanation")
            or judge.get("explanation")
            or (
                "Die KI konnte den Fall "
                "nicht zuverlässig entscheiden."
            )
        )

        await channel.send(
            "⚠️ **Manuelle Prüfung erforderlich**\n\n"
            + str(reason)[:4000]
        )

        return

    # --------------------------------------------------------
    # MÖGLICHER VERSTOSS
    # --------------------------------------------------------

    if status == "likely_violation":

        if state.get(
            "_result_sent"
        ):
            return

        state[
            "_result_sent"
        ] = True

        save_state(
            channel.id,
            state
        )

        await channel.send(
            embed=build_result_embed(
                state
            )
        )

        await channel.send(
            "### ⚖️ Teamler-Entscheidung\n"
            "Die KI sieht einen möglichen Regelverstoß.\n"
            "Soll der Fall offiziell ins Teamler-Log?",
            view=ComplaintConfirmView(
                channel.id
            )
        )

        return


# ============================================================
# BRAIN PROMPT
# ============================================================

def build_brain_prompt(
    channel: discord.TextChannel,
    conversation: str,
    state: dict,
    accused: Optional[discord.Member],
    warning_text: str,
    evidence: list[dict]
) -> str:

    evidence_text = json.dumps(
        evidence,
        ensure_ascii=False,
        indent=2
    )

    return f"""
SERVER:
{channel.guild.name}

TICKET:
{channel.name}

BESCHWERDEVERLAUF:
{conversation}

AKTUELLER STATE:
{json.dumps(
    state,
    ensure_ascii=False,
    indent=2
)}

BESCHULDIGTER:
{
    accused.display_name
    if accused
    else "Noch nicht eindeutig bekannt"
}

BEWEISE:
{evidence_text}

WARNUNGEN:
{warning_text}

REGELN:
{rules_text()}

============================================================
WICHTIGE REGELN
============================================================

- Nutze ausschließlich rules.json.
- Erfinde niemals eine Regel.
- Erfinde niemals eine Regelnummer.
- Erfinde niemals Beweise.
- Eine Behauptung ist kein Beweis.
- Warnungen sind nur Kontext.
- Fehlende Beweise dürfen nicht als Regelverstoß
  interpretiert werden.
- Wenn keine Beweise vorhanden sind:
  insufficient_evidence.
- Wenn ein Beschuldigter bereits eindeutig bekannt ist,
  frage nicht erneut nach ihm.
- Wenn eine Regel bestätigt wurde, untersuche nur diese Regel.
- Wenn eine vorgeschlagene Regel abgelehnt wurde,
  darf dieselbe Regel nicht erneut vorgeschlagen werden.
- Wenn mehrere Regeln möglich sind, darf nur eine echte
  Regel aus rules.json vorgeschlagen werden.
- Konsequenzen dürfen ausschließlich aus rules.json stammen.
- Keine automatische Bestrafung.
- Bei Unsicherheit konservativ entscheiden.
""".strip()


# ============================================================
# TICKET VERARBEITUNG
# ============================================================

async def process_ticket(
    channel: discord.TextChannel,
    trigger_message_id: Optional[int] = None
):

    if not is_complaint_ticket(
        channel
    ):
        return

    lock = ticket_lock(
        channel.id
    )

    async with lock:

        try:

            state = get_state(
                channel.id
            )

            # ------------------------------------------------
            # ABGESCHLOSSEN
            # ------------------------------------------------

            if (
                state.get("next_action")
                == "complete"
            ):
                return

            # ------------------------------------------------
            # VERLAUF
            # ------------------------------------------------

            conversation = await build_conversation(
                channel
            )

            if not conversation:
                return

            # ------------------------------------------------
            # BEWEISE
            # ------------------------------------------------

            evidence = await collect_evidence(
                channel
            )

            # ------------------------------------------------
            # BEWEISSTATUS AKTUALISIEREN
            # ------------------------------------------------

            if evidence:

                if not isinstance(
                    state.get("evidence"),
                    list
                ):

                    state[
                        "evidence"
                    ] = []

                existing_urls = {
                    item.get("url")
                    for item in state[
                        "evidence"
                    ]
                    if isinstance(
                        item,
                        dict
                    )
                }

                for item in evidence:

                    if item.get("url") not in existing_urls:

                        state[
                            "evidence"
                        ].append(
                            item
                        )

                state[
                    "evidence_status"
                ] = "provided"

                state[
                    "evidence_explicitly_missing"
                ] = False

            # ------------------------------------------------
            # BESCHULDIGTER
            # ------------------------------------------------

            accused = resolve_accused(
                channel.guild,
                state
            )

            if accused:

                state[
                    "accused_user_id"
                ] = str(
                    accused.id
                )

            # ------------------------------------------------
            # WARNUNGEN
            # ------------------------------------------------

            warning_text = (
                "Keine Warnungen bekannt."
            )

            if accused:

                user_warnings = get_warnings(
                    channel.guild.id,
                    accused.id
                )

                if user_warnings:

                    warning_text = json.dumps(
                        user_warnings,
                        ensure_ascii=False,
                        indent=2
                    )

            # ------------------------------------------------
            # BRAIN PROMPT
            # ------------------------------------------------

            prompt = build_brain_prompt(
                channel,
                conversation,
                state,
                accused,
                warning_text,
                evidence
            )

            print("=" * 70)
            print("[BRAIN] brain_tick startet")
            print("[BRAIN] Ticket:", channel.name)
            print("[BRAIN] Trigger:", trigger_message_id)
            print("[BRAIN] Version:", getattr(
                brain,
                "VERSION",
                "UNBEKANNT"
            ))
            print("=" * 70)

            # ------------------------------------------------
            # BRAIN
            # ------------------------------------------------

            new_state = await brain.brain_tick(
                state=state,
                user_message=(
                    channel.last_message.content
                    if getattr(
                        channel,
                        "last_message",
                        None
                    )
                    else ""
                ),
                accused_user_id=(
                    str(accused.id)
                    if accused
                    else None
                ),
                ai_call=gemini_json_call,
                rules_text=rules_text(),
                conversation=conversation,
                evidence=evidence
            )

            if not isinstance(
                new_state,
                dict
            ):

                raise RuntimeError(
                    "brain_tick lieferte keinen gültigen State."
                )

            state = new_state

            # ------------------------------------------------
            # MESSAGE ID
            # ------------------------------------------------

            if trigger_message_id:

                state[
                    "_last_processed_message_id"
                ] = int(
                    trigger_message_id
                )

            # ------------------------------------------------
            # SPEICHERN
            # ------------------------------------------------

            save_state(
                channel.id,
                state
            )

            print(
                "[BRAIN] Status:",
                state.get(
                    "overall_status"
                )
            )

            print(
                "[BRAIN] Action:",
                state.get(
                    "next_action"
                )
            )

            # ------------------------------------------------
            # AUSGABE
            # ------------------------------------------------

            await handle_brain_result(
                channel,
                state
            )

        except Exception as error:

            print(
                "[PROCESS TICKET ERROR]",
                repr(error)
            )

            try:

                await channel.send(
                    "⚠️ **Technischer Fehler bei der "
                    "KI-Prüfung.**\n"
                    "Der Fallzustand wurde nicht gelöscht."
                )

            except Exception:
                pass


# ============================================================
# /MESSAGE
# ============================================================

MESSAGE_TEMPLATES = {

    "insufficient_evidence": (
        "Für diesen Fall liegen derzeit nicht genügend "
        "Beweise vor. Der Vorwurf kann deshalb aktuell "
        "nicht bestätigt werden."
    ),

    "more_evidence": (
        "Für die weitere Prüfung werden zusätzliche "
        "Beweise benötigt."
    ),

    "under_review": (
        "Der Fall wurde an die Teamler weitergeleitet "
        "und wird aktuell geprüft."
    ),

    "accepted": (
        "Der Fall wurde von den Teamlern angenommen."
    ),

    "rejected": (
        "Der Fall wurde nach Prüfung abgelehnt."
    ),

    "custom": ""
}


MESSAGE_NAMES = {
    "insufficient_evidence": "Nicht genügend Beweise",
    "more_evidence": "Weitere Beweise benötigt",
    "under_review": "Fall wird geprüft",
    "accepted": "Fall angenommen",
    "rejected": "Fall abgelehnt",
    "custom": "Eigene Nachricht"
}


MESSAGE_CHOICES = [

    app_commands.Choice(
        name="Nicht genügend Beweise",
        value="insufficient_evidence"
    ),

    app_commands.Choice(
        name="Weitere Beweise benötigt",
        value="more_evidence"
    ),

    app_commands.Choice(
        name="Fall wird geprüft",
        value="under_review"
    ),

    app_commands.Choice(
        name="Fall angenommen",
        value="accepted"
    ),

    app_commands.Choice(
        name="Fall abgelehnt",
        value="rejected"
    ),

    app_commands.Choice(
        name="Eigene Nachricht",
        value="custom"
    )
]


class MessageBuilder:

    def __init__(
        self,
        channel,
        author_id,
        message_type
    ):

        self.channel = channel
        self.author_id = author_id
        self.message_type = message_type

        self.text = MESSAGE_TEMPLATES.get(
            message_type,
            ""
        )

        self.finished = False


class MessageEditModal(
    discord.ui.Modal
):

    def __init__(
        self,
        builder
    ):

        super().__init__(
            title="📨 Nachricht bearbeiten"
        )

        self.builder = builder

        self.message_input = discord.ui.TextInput(
            label="Nachricht",
            style=discord.TextStyle.paragraph,
            default=builder.text[:4000],
            placeholder="Text für die Teamler...",
            max_length=4000,
            required=True
        )

        self.add_item(
            self.message_input
        )

    async def on_submit(
        self,
        interaction
    ):

        if not has_staff_access(
            interaction.user
        ):

            await interaction.response.send_message(
                "❌ Du bist nicht berechtigt.",
                ephemeral=True
            )

            return

        text = str(
            self.message_input.value
        ).strip()

        if not text:

            await interaction.response.send_message(
                "❌ Die Nachricht darf nicht leer sein.",
                ephemeral=True
            )

            return

        self.builder.text = text

        await interaction.response.send_message(
            embed=build_message_preview(
                self.builder
            ),
            view=MessagePreviewView(
                self.builder
            ),
            ephemeral=True
        )


def build_message_preview(
    builder
) -> discord.Embed:

    names = {
        "insufficient_evidence":
            "Nicht genügend Beweise",

        "more_evidence":
            "Weitere Beweise benötigt",

        "under_review":
            "Fall wird geprüft",

        "accepted":
            "Fall angenommen",

        "rejected":
            "Fall abgelehnt",

        "custom":
            "Eigene Nachricht"
    }

    embed = discord.Embed(
        title="📨 Nachrichten-Vorschau",
        description=builder.text[:4096],
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🎫 Ticket",
        value=builder.channel.mention,
        inline=True
    )

    embed.add_field(
        name="📋 Vorlage",
        value=names.get(
            builder.message_type,
            "Eigene Nachricht"
        ),
        inline=True
    )

    return embed


class MessagePreviewView(
    discord.ui.View
):

    def __init__(
        self,
        builder
    ):

        super().__init__(
            timeout=600
        )

        self.builder = builder

    async def interaction_check(
        self,
        interaction
    ):

        if (
            interaction.user.id
            != self.builder.author_id
        ):

            await interaction.response.send_message(
                "❌ Diese Nachricht gehört jemand anderem.",
                ephemeral=True
            )

            return False

        if not has_staff_access(
            interaction.user
        ):

            await interaction.response.send_message(
                "❌ Du bist nicht berechtigt.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="✏️ Bearbeiten",
        style=discord.ButtonStyle.secondary
    )
    async def edit(
        self,
        interaction,
        button
    ):

        await interaction.response.send_modal(
            MessageEditModal(
                self.builder
            )
        )

    @discord.ui.button(
        label="📨 Absenden",
        style=discord.ButtonStyle.success
    )
    async def send(
        self,
        interaction,
        button
    ):

        await interaction.response.edit_message(
            content=(
                "⚠️ **Bist du sicher?**\n\n"
                "Die Nachricht wird im Teamler-Log gespeichert."
            ),
            embed=None,
            view=MessageConfirmView(
                self.builder
            )
        )

    @discord.ui.button(
        label="❌ Abbrechen",
        style=discord.ButtonStyle.danger
    )
    async def cancel(
        self,
        interaction,
        button
    ):

        await interaction.response.edit_message(
            content="❌ Nachricht verworfen.",
            embed=None,
            view=None
        )


class MessageConfirmView(
    discord.ui.View
):

    def __init__(
        self,
        builder
    ):

        super().__init__(
            timeout=300
        )

        self.builder = builder

    async def interaction_check(
        self,
        interaction
    ) -> bool:

        if (
            interaction.user.id
            != self.builder.author_id
        ):

            await interaction.response.send_message(
                "❌ Diese Aktion gehört jemand anderem.",
                ephemeral=True
            )

            return False

        if not has_staff_access(
            interaction.user
        ):

            await interaction.response.send_message(
                "❌ Du bist nicht berechtigt.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="✅ Ja, absenden",
        style=discord.ButtonStyle.success
    )
    async def confirm(
        self,
        interaction,
        button
    ):

        if self.builder.finished:
            return

        self.builder.finished = True

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            state = get_state(
                self.builder.channel.id
            )

            await send_to_complaint_log(
                self.builder.channel,
                state,
                custom_message=self.builder.text,
                title="📨 Manuelle Nachricht an Teamler"
            )

            await interaction.edit_original_response(
                content="✅ **An die Teamler gesendet.**",
                embed=None,
                view=None
            )

        except Exception as error:

            await interaction.edit_original_response(
                content=(
                    "❌ Nachricht konnte nicht gesendet werden.\n\n"
                    f"`{str(error)[:1000]}`"
                ),
                embed=None,
                view=None
            )

    @discord.ui.button(
        label="❌ Nein",
        style=discord.ButtonStyle.danger
    )
    async def cancel(
        self,
        interaction,
        button
    ):

        self.builder.finished = True

        await interaction.response.edit_message(
            content="❌ Versand abgebrochen.",
            embed=None,
            view=None
        )


@bot.tree.command(
    name="message",
    description="Erstellt eine manuelle Nachricht für die Teamler."
)
@app_commands.describe(
    option="Welche Nachricht möchtest du senden?"
)
@app_commands.choices(
    option=MESSAGE_CHOICES
)
async def message_command(
    interaction,
    option: app_commands.Choice[str]
):

    if not has_staff_access(
        interaction.user
    ):

        await interaction.response.send_message(
            "❌ Du bist nicht berechtigt.",
            ephemeral=True
        )

        return

    if not is_complaint_ticket(
        interaction.channel
    ):

        await interaction.response.send_message(
            "❌ Dieser Befehl funktioniert nur "
            "in einem Beschwerdeticket.",
            ephemeral=True
        )

        return

    builder = MessageBuilder(
        interaction.channel,
        interaction.user.id,
        option.value
    )

    await interaction.response.send_modal(
        MessageEditModal(
            builder
        )
    )


# ============================================================
# LOG SETTINGS
# ============================================================

def log_settings_embed():

    settings = config.get(
        "complaint_log_settings",
        {}
    )

    log_id = config.get(
        "complaint_log_channel_id"
    )

    embed = discord.Embed(
        title="⚙️ Complaint-Log Einstellungen",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="📁 Ziel-Channel",
        value=(
            f"<#{log_id}>"
            if log_id
            else "❌ Nicht gesetzt"
        ),
        inline=False
    )

    embed.add_field(
        name="📎 Beweise",
        value=(
            "✅ AN"
            if settings.get(
                "send_evidence",
                True
            )
            else "❌ AUS"
        ),
        inline=True
    )

    embed.add_field(
        name="📝 Beschwerde",
        value=(
            "✅ AN"
            if settings.get(
                "send_complaint",
                True
            )
            else "❌ AUS"
        ),
        inline=True
    )

    embed.add_field(
        name="🧠 KI",
        value=(
            "✅ AN"
            if settings.get(
                "send_ai_result",
                True
            )
            else "❌ AUS"
        ),
        inline=True
    )

    return embed


class ComplaintLogSettingsView(
    discord.ui.View
):

    def __init__(
        self,
        owner_id
    ):

        super().__init__(
            timeout=600
        )

        self.owner_id = owner_id

    async def interaction_check(
        self,
        interaction
    ) -> bool:

        if interaction.user.id != self.owner_id:

            await interaction.response.send_message(
                "❌ Nur der Ersteller kann diese "
                "Einstellungen ändern.",
                ephemeral=True
            )

            return False

        if not has_staff_access(
            interaction.user
        ):

            await interaction.response.send_message(
                "❌ Du bist nicht berechtigt.",
                ephemeral=True
            )

            return False

        return True

    async def toggle(
        self,
        interaction,
        key
    ):

        settings = config[
            "complaint_log_settings"
        ]

        settings[key] = not settings.get(
            key,
            True
        )

        save_json(
            CONFIG_FILE,
            config
        )

        await interaction.response.edit_message(
            embed=log_settings_embed(),
            view=self
        )

    @discord.ui.button(
        label="📎 Beweise",
        style=discord.ButtonStyle.primary
    )
    async def evidence(
        self,
        interaction,
        button
    ):

        await self.toggle(
            interaction,
            "send_evidence"
        )

    @discord.ui.button(
        label="📝 Beschwerde",
        style=discord.ButtonStyle.primary
    )
    async def complaint(
        self,
        interaction,
        button
    ):

        await self.toggle(
            interaction,
            "send_complaint"
        )

    @discord.ui.button(
        label="🧠 KI",
        style=discord.ButtonStyle.primary
    )
    async def ai(
        self,
        interaction,
        button
    ):

        await self.toggle(
            interaction,
            "send_ai_result"
        )


# ============================================================
# /SETCOMPLAINTLOG
# ============================================================

@bot.tree.command(
    name="setcomplaintlog",
    description="Setzt den Beschwerde-Log."
)
@app_commands.describe(
    channel="Ziel-Channel für Teamler-Beschwerden."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def setcomplaintlog(
    interaction,
    channel: discord.TextChannel
):

    config[
        "complaint_log_channel_id"
    ] = channel.id

    save_json(
        CONFIG_FILE,
        config
    )

    await interaction.response.send_message(
        embed=log_settings_embed(),
        view=ComplaintLogSettingsView(
            interaction.user.id
        ),
        ephemeral=True
    )


# ============================================================
# /SETSTAFF
# ============================================================

@bot.tree.command(
    name="setstaff",
    description="Setzt die Staff-Rolle."
)
@app_commands.describe(
    role="Staff-Rolle"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def setstaff(
    interaction,
    role: discord.Role
):

    config[
        "staff_role_id"
    ] = role.id

    save_json(
        CONFIG_FILE,
        config
    )

    await interaction.response.send_message(
        f"✅ Staff-Rolle gesetzt: {role.mention}",
        ephemeral=True
    )


# ============================================================
# WARN SYSTEM
# ============================================================

warn_group = app_commands.Group(
    name="warn",
    description="Warnsystem"
)


@warn_group.command(
    name="set",
    description="Setzt die Warnanzahl."
)
@app_commands.describe(
    member="Benutzer",
    amount="Anzahl Warnungen"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def warn_set(
    interaction,
    member: discord.Member,
    amount: int
):

    amount = max(
        0,
        amount
    )

    data = []

    for _ in range(amount):

        data.append({
            "reason": "Manuell gesetzt",
            "by": interaction.user.id
        })

    set_warnings(
        interaction.guild.id,
        member.id,
        data
    )

    await interaction.response.send_message(
        f"⚠️ {member.mention} hat jetzt "
        f"**{amount} Warnungen**."
    )


@warn_group.command(
    name="add",
    description="Fügt eine Warnung hinzu."
)
@app_commands.describe(
    member="Benutzer",
    reason="Grund"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def warn_add(
    interaction,
    member: discord.Member,
    reason: str
):

    data = get_warnings(
        interaction.guild.id,
        member.id
    )

    data.append({
        "reason": reason,
        "by": interaction.user.id
    })

    set_warnings(
        interaction.guild.id,
        member.id,
        data
    )

    await interaction.response.send_message(
        f"⚠️ Warnung hinzugefügt.\n"
        f"{member.mention}: **{len(data)} Warnungen**"
    )


@warn_group.command(
    name="remove",
    description="Entfernt die letzte Warnung."
)
@app_commands.describe(
    member="Benutzer"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def warn_remove(
    interaction,
    member: discord.Member
):

    data = get_warnings(
        interaction.guild.id,
        member.id
    )

    if not data:

        await interaction.response.send_message(
            "❌ Dieser Benutzer hat keine Warnungen."
        )

        return

    data.pop()

    set_warnings(
        interaction.guild.id,
        member.id,
        data
    )

    await interaction.response.send_message(
        f"✅ Warnung entfernt.\n"
        f"{member.mention}: **{len(data)} Warnungen**"
    )


@warn_group.command(
    name="reset",
    description="Setzt alle Warnungen zurück."
)
@app_commands.describe(
    member="Benutzer"
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def warn_reset(
    interaction,
    member: discord.Member
):

    set_warnings(
        interaction.guild.id,
        member.id,
        []
    )

    await interaction.response.send_message(
        f"✅ Warnungen von "
        f"{member.mention} zurückgesetzt."
    )


@warn_group.command(
    name="view",
    description="Zeigt die Warnungen."
)
@app_commands.describe(
    member="Benutzer"
)
async def warn_view(
    interaction,
    member: discord.Member
):

    data = get_warnings(
        interaction.guild.id,
        member.id
    )

    if not data:

        await interaction.response.send_message(
            f"✅ {member.mention} hat keine Warnungen."
        )

        return

    lines = []

    for index, warning in enumerate(
        data,
        start=1
    ):

        lines.append(
            f"**{index}.** "
            f"{warning.get('reason', 'Kein Grund')}"
        )

    embed = discord.Embed(
        title=(
            f"⚠️ Warnungen — "
            f"{member.display_name}"
        ),
        description="\n".join(
            lines
        )[:4096],
        color=discord.Color.orange()
    )

    await interaction.response.send_message(
        embed=embed
    )


bot.tree.add_command(
    warn_group
)


# ============================================================
# /RESOLVE
# ============================================================

@bot.tree.command(
    name="resolve",
    description="Setzt einen Beschwerdefall zurück."
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def resolve(
    interaction
):

    if not is_complaint_ticket(
        interaction.channel
    ):

        await interaction.response.send_message(
            "❌ Dieser Kanal ist kein Beschwerdeticket.",
            ephemeral=True
        )

        return

    reset_state(
        interaction.channel.id
    )

    await interaction.response.send_message(
        "✅ Fall zurückgesetzt."
    )


# ============================================================
# /BRAINRESET
# ============================================================

@bot.tree.command(
    name="brainreset",
    description="Setzt das KI-Gehirn des Tickets zurück."
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def brainreset(
    interaction
):

    if not is_complaint_ticket(
        interaction.channel
    ):

        await interaction.response.send_message(
            "❌ Kein Beschwerdeticket.",
            ephemeral=True
        )

        return

    reset_state(
        interaction.channel.id
    )

    await interaction.response.send_message(
        "🧠 KI-Fallzustand wurde zurückgesetzt."
    )


# ============================================================
# NEUES TICKET
# ============================================================

@bot.event
async def on_guild_channel_create(
    channel
):

    await asyncio.sleep(3)

    if not is_complaint_ticket(
        channel
    ):
        return

    reset_state(
        channel.id
    )

    try:

        await channel.send(
            "🧠 **KI-Beschwerdeprüfung aktiviert.**\n\n"
            "Beschreibe deinen Fall möglichst genau.\n"
            "Nenne den Beschuldigten und füge vorhandene "
            "Beweise hinzu."
        )

    except Exception as error:

        print(
            f"[TICKET CREATE] {error!r}"
        )


# ============================================================
# NACHRICHTEN
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):

    if message.author.bot:
        return

    # --------------------------------------------------------
    # AUTOMOD
    # --------------------------------------------------------

    was_moderated = await automod_check_message(
        message
    )

    if was_moderated:
        return

    await bot.process_commands(
        message
    )

    channel = message.channel

    if not is_complaint_ticket(
        channel
    ):
        return

    state = get_state(
        channel.id
    )

    # --------------------------------------------------------
    # BOT HAT DIESES TICKET VERLASSEN
    # --------------------------------------------------------

    if state.get(
        "bot_left",
        False
    ):
        return

    # --------------------------------------------------------
    # ABGESCHLOSSENER FALL
    # --------------------------------------------------------

    if (
        state.get("next_action")
        == "complete"
    ):

        return

    # --------------------------------------------------------
    # BEWEIS NACH "KEINE BEWEISE"
    # --------------------------------------------------------

    if (
        state.get("overall_status")
        == "insufficient_evidence"
    ):

        has_evidence = bool(
            message.attachments
            or extract_urls(
                message.content or ""
            )
        )

        if not has_evidence:
            return

        print(
            "[BRAIN] Neuer Beweis nach "
            "insufficient_evidence."
        )

        state = get_state(
            channel.id
        )

        state[
            "overall_status"
        ] = "investigation"

        state[
            "next_action"
        ] = "investigate"

        state[
            "_case_logged"
        ] = False

        save_state(
            channel.id,
            state
        )

    await asyncio.sleep(
        0.8
    )

    await process_ticket(
        channel,
        trigger_message_id=message.id
    )


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print("=" * 70)
    print(
        f"✅ Bot online: {bot.user}"
    )
    print(
        f"🌐 Server: {len(bot.guilds)}"
    )
    print(
        f"🧠 Gemini: {GEMINI_MODEL}"
    )
    print(
        f"🧠 Brain: {brain.__file__}"
    )
    print(
        f"🧠 Brain-Version: "
        f"{getattr(brain, 'VERSION', 'UNBEKANNT')}"
    )
    print(
        f"📜 rules.json: "
        f"{RULES_FILE.exists()}"
    )
    print(
        f"🧠 brain.json: "
        f"{BRAIN_FILE.exists()}"
    )
    print("=" * 70)

    # Persistent Buttons registrieren
    try:

        bot.add_view(
            RuleConfirmView(0)
        )

        bot.add_view(
            EvidenceView(0)
        )

        bot.add_view(
            ComplaintConfirmView(0)
        )

    except Exception as error:

        print(
            f"[VIEW] Persistent View Fehler: {error!r}"
        )

    try:

        synced = await bot.tree.sync()

        print(
            f"✅ Slash Commands synchronisiert: "
            f"{len(synced)}"
        )

        for command in synced:

            print(
                f"   /{command.name}"
            )

    except Exception as error:

        print(
            f"❌ Command-Sync Fehler: {error!r}"
        )


# ============================================================
# SLASH COMMAND FEHLER
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction,
    error
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        if not interaction.response.is_done():

            await interaction.response.send_message(
                "❌ Dafür fehlen dir die Berechtigungen.",
                ephemeral=True
            )

        return

    print(
        f"[COMMAND ERROR] {error!r}"
    )

    try:

        if not interaction.response.is_done():

            await interaction.response.send_message(
                "❌ Beim Befehl ist ein Fehler aufgetreten.",
                ephemeral=True
            )

    except Exception:
        pass

# ============================================================
# /LEAVE – BOT AUS DIESEM TICKET ENTFERNEN
# ============================================================

@bot.tree.command(
    name="leave",
    description="Der Bot verlässt dieses Ticket und reagiert dort nicht mehr."
)
async def leave_ticket(
    interaction: discord.Interaction
):

    channel = interaction.channel

    # --------------------------------------------------------
    # NUR IN BESCHWERDE-TICKETS
    # --------------------------------------------------------

    if not is_complaint_ticket(channel):

        await interaction.response.send_message(
            "❌ Dieser Befehl kann nur in einem Ticket verwendet werden.",
            ephemeral=True
        )

        return

    # --------------------------------------------------------
    # STATE LADEN
    # --------------------------------------------------------

    state = get_state(
        channel.id
    )

    # --------------------------------------------------------
    # BEREITS VERLASSEN
    # --------------------------------------------------------

    if state.get(
        "bot_left",
        False
    ):

        await interaction.response.send_message(
            "ℹ️ Ich bin aus diesem Ticket bereits raus.",
            ephemeral=True
        )

        return

    # --------------------------------------------------------
    # BOT AUS TICKET ENTFERNEN
    # --------------------------------------------------------

    state[
        "bot_left"
    ] = True

    state[
        "bot_left_by"
    ] = str(
        interaction.user.id
    )

    state[
        "bot_left_at"
    ] = discord.utils.utcnow().isoformat()

    save_state(
        channel.id,
        state
    )

    # --------------------------------------------------------
    # BESTÄTIGUNG IM TICKET
    # --------------------------------------------------------

    await interaction.response.send_message(
        "🚪 **Bot verlassen**\n\n"
        "Ich bin nicht länger für dieses Ticket zuständig "
        "und werde hier keine weiteren Nachrichten oder "
        "KI-Prüfungen durchführen."
    )

    print(
        f"[LEAVE] Bot hat Ticket verlassen: "
        f"{channel.name} | "
        f"ausgelöst von {interaction.user} "
        f"({interaction.user.id})"
    )
	
# ============================================================
# /HIDE – ANONYME BOT-NACHRICHT
# ============================================================

@bot.tree.command(
    name="hide",
    description="Sendet eine Nachricht anonym über den Bot."
)
@app_commands.describe(
    message="Die Nachricht, die der Bot senden soll."
)
@app_commands.checks.has_permissions(administrator=True)
async def hide(
    interaction: discord.Interaction,
    message: str
):
    await interaction.response.defer(ephemeral=True)

    if interaction.channel is None:
        await interaction.followup.send(
            "❌ Hier kann keine Nachricht gesendet werden.",
            ephemeral=True
        )
        return

    await interaction.channel.send(message)

    await interaction.followup.send(
        "✅ Nachricht anonym gesendet.",
        ephemeral=True
    )
# ============================================================
# AUTO-MOD – BELEIDIGUNGSFILTER
# ============================================================

AUTOMOD_TIMEOUT_SECONDS = 10 * 60  # 10 Minuten

# Wörter können später jederzeit ergänzt werden.
# Bewusst als einzelne Begriffe/Varianten gespeichert.
AUTOMOD_BAD_WORDS = {
    "arsch",
    "arschloch",
    "idiot",
    "idioten",
    "idiotin",
    "depp",
    "deppen",
    "dummkopf",
    "blödmann",
    "blödmann",
    "trottel",
    "vollidiot",
    "vollidiotin",
    "honk",
    "spasti",
    "spacko",
    "opfer",
    "wichser",
    "wixxer",
    "wixer",
    "hurensohn",
    "huso",
    "bastard",
    "missgeburt",
    "scheiße",
    "scheisse",
    "scheiss",
    "fick",
    "ficker",
    "fick dich",
    "nigga",
    "nigger",
    "hs",
    "nga",
    "hure",
    "spaßt",
    "hurens0hn",
    "hur3nsohn",
    "hur3ns0hn",
    "schlampe",
    "Penis",
    "ngga",
    "N1gga",
    "Neger",
    "Nega",
}

# Wörter, die trotz Treffer nicht automatisch bestraft werden.
# Hier kannst du später eigene Ausnahmen eintragen.
AUTOMOD_WHITELIST = {
    # "beispiel",
}


def normalize_automod_text(text: str) -> str:
    """
    Normalisiert Text, damit einfache Umgehungen
    des Filters erkannt werden.
    """

    if not text:
        return ""

    text = unicodedata.normalize(
        "NFKC",
        text
    ).lower()

    # Häufige Trennzeichen entfernen.
    text = re.sub(
        r"[\s._\-*~`|/\\]+",
        "",
        text
    )

    return text


def automod_detect_bad_word(
    text: str
) -> Optional[str]:

    if not text:
        return None

    original = normalize(text)

    # Whitelist zuerst prüfen.
    for allowed in AUTOMOD_WHITELIST:

        if normalize(allowed) in original:
            return None

    normalized = normalize_automod_text(
        text
    )

    for word in AUTOMOD_BAD_WORDS:

        word_normalized = normalize_automod_text(
            word
        )

        if not word_normalized:
            continue

        # Bei kurzen Wörtern nur als eigenes Wort erkennen,
        # damit nicht normale Wörter versehentlich getroffen werden.
        if len(word_normalized) <= 4:

            pattern = (
                rf"(?<![a-zäöüß])"
                rf"{re.escape(word_normalized)}"
                rf"(?![a-zäöüß])"
            )

            if re.search(
                pattern,
                normalized
            ):
                return word

        else:

            if word_normalized in normalized:
                return word

    return None


async def automod_check_message(
    message: discord.Message
) -> bool:
    """
    Gibt True zurück, wenn die Nachricht moderiert wurde.
    """

    if message.author.bot:
        return False

    if not message.guild:
        return False

    # Administratoren werden nicht automatisch bestraft.
    if isinstance(
        message.author,
        discord.Member
    ):

        if message.author.guild_permissions.administrator:
            return False

    detected = automod_detect_bad_word(
        message.content or ""
    )

    if not detected:
        return False

    member = message.author

    if not isinstance(
        member,
        discord.Member
    ):
        return False

    # Bot braucht Moderate Members.
    if not message.guild.me:
        return False

    if not message.guild.me.guild_permissions.moderate_members:
        print(
            "[AUTOMOD] ❌ Bot hat keine "
            "Moderate-Members-Berechtigung."
        )
        return False

    # Bots dürfen keine höher stehenden Mitglieder timeouten.
    if member.top_role >= message.guild.me.top_role:
        print(
            f"[AUTOMOD] ❌ Kann {member} nicht timeouten: "
            "Rolle ist zu hoch."
        )
        return False

    try:

        # Nachricht löschen.
        try:
            await message.delete()
        except Exception as error:
            print(
                f"[AUTOMOD] Nachricht konnte nicht gelöscht werden: "
                f"{error!r}"
            )

        # Timeout setzen.
        until = (
            discord.utils.utcnow()
            + __import__("datetime").timedelta(
                seconds=AUTOMOD_TIMEOUT_SECONDS
            )
        )

        await member.edit(
            timed_out_until=until,
            reason=(
                f"AutoMod: Beleidigung erkannt "
                f"({detected})"
            )
        )

        print(
            f"[AUTOMOD] 🔇 Timeout: "
            f"{member} ({member.id}) | "
            f"Treffer: {detected} | "
            f"Dauer: {AUTOMOD_TIMEOUT_SECONDS}s"
        )

        return True

    except discord.Forbidden:

        print(
            f"[AUTOMOD] ❌ Keine Berechtigung für "
            f"{member} ({member.id})"
        )

    except Exception as error:

        print(
            f"[AUTOMOD] ❌ Fehler bei "
            f"{member}: {error!r}"
        )

    return False
# ============================================================
# VERIFY SYSTEM
# ============================================================

DEFAULT_VERIFY_CONFIG = {
    "join_role_id": None,
    "verified_role_id": None,
    "channel_id": None,
    "log_channel_id": None,
    "panel_message_id": None
}


def get_verify_config(
    guild: discord.Guild
) -> dict:

    verify_config = config.get(
        "verify",
        DEFAULT_VERIFY_CONFIG.copy()
    )

    if not isinstance(
        verify_config,
        dict
    ):
        return DEFAULT_VERIFY_CONFIG.copy()

    return verify_config


def get_verify_role(
    guild: discord.Guild,
    key: str
) -> Optional[discord.Role]:

    verify_config = get_verify_config(
        guild
    )

    role_id = verify_config.get(
        key
    )

    if not role_id:
        return None

    try:

        return guild.get_role(
            int(role_id)
        )

    except Exception:

        return None


def get_verify_channel(
    guild: discord.Guild
) -> Optional[discord.TextChannel]:

    verify_config = get_verify_config(
        guild
    )

    channel_id = verify_config.get(
        "channel_id"
    )

    if not channel_id:
        return None

    try:

        channel = guild.get_channel(
            int(channel_id)
        )

        if isinstance(
            channel,
            discord.TextChannel
        ):
            return channel

    except Exception:
        pass

    return None


def get_verify_log_channel(
    guild: discord.Guild
) -> Optional[discord.TextChannel]:

    verify_config = get_verify_config(
        guild
    )

    channel_id = verify_config.get(
        "log_channel_id"
    )

    if not channel_id:
        return None

    try:

        channel = guild.get_channel(
            int(channel_id)
        )

        if isinstance(
            channel,
            discord.TextChannel
        ):
            return channel

    except Exception:
        pass

    return None


# ============================================================
# VERIFY EMBED
# ============================================================

def build_verify_embed() -> discord.Embed:

    embed = discord.Embed(
        title="🛡️ Server-Verifizierung",
        description=(
            "Willkommen auf **ReVu**!\n\n"
            "Um Zugriff auf den Server zu erhalten, "
            "musst du dich zuerst verifizieren.\n\n"
            "Klicke auf **✅ Verifizieren**, um deine "
            "Member-Rolle zu erhalten.\n\n"
            "Nach erfolgreicher Verifizierung wird deine "
            "New-Rolle automatisch entfernt und du erhältst "
            "Zugriff auf den restlichen Server."
        ),
        color=discord.Color.blurple()
    )

    embed.set_footer(
        text="ReVu • Verifizierungssystem"
    )

    return embed


# ============================================================
# VERIFY BERECHTIGUNGEN
# ============================================================

async def configure_verify_permissions(
    guild: discord.Guild,
    verify_channel: discord.TextChannel,
    join_role: discord.Role,
    verified_role: discord.Role
) -> bool:

    """
    Richtet das Verify-System automatisch ein.

    JOIN-ROLLE:
        - sieht den Verify-Channel
        - sieht keine anderen Channels
        - kann im Verify-Channel nicht schreiben

    VERIFIED-ROLLE:
        - wird nach der Verifizierung vergeben
        - die Join-Rolle wird entfernt
        - danach gelten die normalen Server-Berechtigungen

    Die normalen Channel-Berechtigungen werden nicht
    für die Verified-Rolle überschrieben.
    """

    try:

        # ----------------------------------------------------
        # ALLE CHANNELS
        # ----------------------------------------------------

        for channel in guild.channels:

            # Verify-Channel
            if channel.id == verify_channel.id:

                await channel.set_permissions(
                    join_role,
                    view_channel=True,
                    read_message_history=True,
                    send_messages=False,
                    add_reactions=False,
                    reason="ReVu Verify-System • Verify-Channel"
                )

                await channel.set_permissions(
                    verified_role,
                    view_channel=True,
                    read_message_history=True,
                    reason="ReVu Verify-System • Verified-Zugriff"
                )

                continue

            # ------------------------------------------------
            # ALLE ANDEREN CHANNELS
            # ------------------------------------------------

            try:

                await channel.set_permissions(
                    join_role,
                    view_channel=False,
                    reason="ReVu Verify-System • New-Rolle verstecken"
                )

            except discord.Forbidden:

                print(
                    f"[VERIFY] ⚠️ Keine Berechtigung für "
                    f"Channel #{channel.name}"
                )

            except Exception as error:

                print(
                    f"[VERIFY] ⚠️ Fehler bei "
                    f"#{channel.name}: {error!r}"
                )

        return True

    except discord.Forbidden:

        print(
            "[VERIFY] ❌ Keine Berechtigung, "
            "die Verify-Berechtigungen einzurichten."
        )

        return False

    except Exception as error:

        print(
            f"[VERIFY] ❌ Permission-Fehler: {error!r}"
        )

        return False


# ============================================================
# VERIFY VIEW
# ============================================================

class VerifyView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=None
        )

    @discord.ui.button(
        label="Verifizieren",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="revu_verify_button"
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        guild = interaction.guild

        if guild is None:

            await interaction.response.send_message(
                "❌ Dieser Button funktioniert nur auf einem Server.",
                ephemeral=True
            )

            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):

            await interaction.response.send_message(
                "❌ Benutzer konnte nicht erkannt werden.",
                ephemeral=True
            )

            return

        # ----------------------------------------------------
        # ROLLEN HOLEN
        # ----------------------------------------------------

        join_role = get_verify_role(
            guild,
            "join_role_id"
        )

        verified_role = get_verify_role(
            guild,
            "verified_role_id"
        )

        if not join_role or not verified_role:

            await interaction.response.send_message(
                "❌ Das Verify-System wurde noch nicht "
                "vollständig eingerichtet.",
                ephemeral=True
            )

            return

        # ----------------------------------------------------
        # BEREITS VERIFIZIERT
        # ----------------------------------------------------

        if verified_role in member.roles:

            await interaction.response.send_message(
                "ℹ️ Du bist bereits verifiziert.",
                ephemeral=True
            )

            return

        # ----------------------------------------------------
        # BOT
        # ----------------------------------------------------

        bot_member = guild.me

        if bot_member is None:

            await interaction.response.send_message(
                "❌ Der Bot konnte nicht erkannt werden.",
                ephemeral=True
            )

            return

        # ----------------------------------------------------
        # ROLLEN-HIERARCHIE
        # ----------------------------------------------------

        if verified_role >= bot_member.top_role:

            await interaction.response.send_message(
                "❌ Ich kann die Member-Rolle nicht vergeben.\n\n"
                "Meine Bot-Rolle muss **über** der "
                "Member-Rolle stehen.",
                ephemeral=True
            )

            return

        if join_role >= bot_member.top_role:

            await interaction.response.send_message(
                "❌ Ich kann die New-Rolle nicht entfernen.\n\n"
                "Meine Bot-Rolle muss **über** der "
                "New-Rolle stehen.",
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            # ------------------------------------------------
            # VERIFIED GEBEN
            # ------------------------------------------------

            await member.add_roles(
                verified_role,
                reason="ReVu Verify-System • Verifiziert"
            )

            # ------------------------------------------------
            # NEW ENTFERNEN
            # ------------------------------------------------

            if join_role in member.roles:

                await member.remove_roles(
                    join_role,
                    reason="ReVu Verify-System • Verifiziert"
                )

            # ------------------------------------------------
            # ERFOLG
            # ------------------------------------------------

            await interaction.followup.send(
                "✅ **Erfolgreich verifiziert!**\n\n"
                f"Du hast jetzt {verified_role.mention}.",
                ephemeral=True
            )

            # ------------------------------------------------
            # LOG
            # ------------------------------------------------

            log_channel = get_verify_log_channel(
                guild
            )

            if log_channel:

                embed = discord.Embed(
                    title="✅ User verifiziert",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow()
                )

                embed.add_field(
                    name="👤 Benutzer",
                    value=(
                        f"{member.mention}\n"
                        f"`{member.id}`"
                    ),
                    inline=True
                )

                embed.add_field(
                    name="🎭 Rolle",
                    value=verified_role.mention,
                    inline=True
                )

                embed.set_footer(
                    text="ReVu • Verify-System"
                )

                try:

                    await log_channel.send(
                        embed=embed
                    )

                except Exception as error:

                    print(
                        f"[VERIFY LOG] Fehler: {error!r}"
                    )

            print(
                f"[VERIFY] ✅ {member} "
                f"({member.id}) verifiziert."
            )

        except discord.Forbidden:

            await interaction.followup.send(
                "❌ Ich habe keine Berechtigung, "
                "die Rollen zu verwalten.\n\n"
                "Prüfe die Rollen-Hierarchie.",
                ephemeral=True
            )

        except Exception as error:

            print(
                f"[VERIFY] Fehler: {error!r}"
            )

            await interaction.followup.send(
                "❌ Bei der Verifizierung ist "
                "ein technischer Fehler aufgetreten.",
                ephemeral=True
            )


# ============================================================
# /VERIFY
# ============================================================

verify_group = app_commands.Group(
    name="verify",
    description="Verifizierungssystem verwalten"
)


# ============================================================
# /VERIFY SETUP
# ============================================================

@verify_group.command(
    name="setup",
    description="Richtet das Verify-System ein."
)
@app_commands.describe(
    join_role="Rolle für neue Benutzer.",
    verified_role="Rolle für verifizierte Benutzer.",
    channel="Verify-Channel.",
    log_channel="Optionaler Log-Channel."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def verify_setup(
    interaction: discord.Interaction,
    join_role: discord.Role,
    verified_role: discord.Role,
    channel: discord.TextChannel,
    log_channel: Optional[discord.TextChannel] = None
):

    guild = interaction.guild

    if guild is None:

        await interaction.response.send_message(
            "❌ Dieser Befehl funktioniert nur auf einem Server.",
            ephemeral=True
        )

        return

    bot_member = guild.me

    if bot_member is None:

        await interaction.response.send_message(
            "❌ Bot-Mitglied konnte nicht erkannt werden.",
            ephemeral=True
        )

        return

    # --------------------------------------------------------
    # ROLLEN PRÜFEN
    # --------------------------------------------------------

    if join_role == verified_role:

        await interaction.response.send_message(
            "❌ New-Rolle und Verified-Rolle dürfen "
            "nicht identisch sein.",
            ephemeral=True
        )

        return

    if join_role >= bot_member.top_role:

        await interaction.response.send_message(
            "❌ Die New-Rolle ist zu hoch.\n\n"
            "Meine Bot-Rolle muss **über** der New-Rolle "
            "stehen.",
            ephemeral=True
        )

        return

    if verified_role >= bot_member.top_role:

        await interaction.response.send_message(
            "❌ Die Verified-Rolle ist zu hoch.\n\n"
            "Meine Bot-Rolle muss **über** der Verified-Rolle "
            "stehen.",
            ephemeral=True
        )

        return

    # --------------------------------------------------------
    # ALTE PANEL-ID SICHERN
    # --------------------------------------------------------

    old_verify_config = get_verify_config(
        guild
    )

    old_message_id = old_verify_config.get(
        "panel_message_id"
    )

    # --------------------------------------------------------
    # CONFIG SPEICHERN
    # --------------------------------------------------------

    config["verify"] = {
        "join_role_id": join_role.id,
        "verified_role_id": verified_role.id,
        "channel_id": channel.id,
        "log_channel_id": (
            log_channel.id
            if log_channel
            else None
        ),
        "panel_message_id": old_message_id
    }

    save_json(
        CONFIG_FILE,
        config
    )

    # --------------------------------------------------------
    # SERVER-BERECHTIGUNGEN EINRICHTEN
    # --------------------------------------------------------

    permissions_ok = (
        await configure_verify_permissions(
            guild,
            channel,
            join_role,
            verified_role
        )
    )

    # --------------------------------------------------------
    # PANEL
    # --------------------------------------------------------

    panel_message = None

    if old_message_id:

        try:

            panel_message = await channel.fetch_message(
                int(old_message_id)
            )

            await panel_message.edit(
                embed=build_verify_embed(),
                view=VerifyView()
            )

        except Exception as error:

            print(
                f"[VERIFY] Altes Panel nicht gefunden: "
                f"{error!r}"
            )

            panel_message = None

    # --------------------------------------------------------
    # NEUES PANEL
    # --------------------------------------------------------

    if panel_message is None:

        panel_message = await channel.send(
            embed=build_verify_embed(),
            view=VerifyView()
        )

    # --------------------------------------------------------
    # PANEL-ID SPEICHERN
    # --------------------------------------------------------

    config["verify"][
        "panel_message_id"
    ] = panel_message.id

    save_json(
        CONFIG_FILE,
        config
    )

    # --------------------------------------------------------
    # BESTÄTIGUNG
    # --------------------------------------------------------

    embed = discord.Embed(
        title="✅ Verify-System eingerichtet",
        color=discord.Color.green()
    )

    embed.add_field(
        name="🆕 New-Rolle",
        value=join_role.mention,
        inline=True
    )

    embed.add_field(
        name="✅ Verified-Rolle",
        value=verified_role.mention,
        inline=True
    )

    embed.add_field(
        name="📁 Verify-Channel",
        value=channel.mention,
        inline=True
    )

    embed.add_field(
        name="📋 Log-Channel",
        value=(
            log_channel.mention
            if log_channel
            else "❌ Kein Log"
        ),
        inline=True
    )

    embed.add_field(
        name="🔐 New-Rollen-Schutz",
        value=(
            "✅ New sieht nur den Verify-Channel"
            if permissions_ok
            else "⚠️ Berechtigungen konnten nicht vollständig eingerichtet werden"
        ),
        inline=False
    )

    embed.add_field(
        name="🔘 Panel",
        value=(
            f"[Verify-Nachricht]"
            f"({panel_message.jump_url})"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# /VERIFY CONFIG
# ============================================================

@verify_group.command(
    name="config",
    description="Zeigt die Verify-Konfiguration."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def verify_config_command(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:

        await interaction.response.send_message(
            "❌ Nur auf einem Server verfügbar.",
            ephemeral=True
        )

        return

    verify_config = get_verify_config(
        guild
    )

    join_role = get_verify_role(
        guild,
        "join_role_id"
    )

    verified_role = get_verify_role(
        guild,
        "verified_role_id"
    )

    channel = get_verify_channel(
        guild
    )

    log_channel = get_verify_log_channel(
        guild
    )

    embed = discord.Embed(
        title="⚙️ Verify-Konfiguration",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🆕 New-Rolle",
        value=(
            join_role.mention
            if join_role
            else "❌ Nicht gesetzt"
        ),
        inline=True
    )

    embed.add_field(
        name="✅ Verified-Rolle",
        value=(
            verified_role.mention
            if verified_role
            else "❌ Nicht gesetzt"
        ),
        inline=True
    )

    embed.add_field(
        name="📁 Verify-Channel",
        value=(
            channel.mention
            if channel
            else "❌ Nicht gesetzt"
        ),
        inline=True
    )

    embed.add_field(
        name="📋 Log-Channel",
        value=(
            log_channel.mention
            if log_channel
            else "❌ Kein Log"
        ),
        inline=True
    )

    embed.add_field(
        name="🆔 Panel-ID",
        value=str(
            verify_config.get(
                "panel_message_id"
            )
            or "Nicht gesetzt"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# /VERIFY PANEL
# ============================================================

@verify_group.command(
    name="panel",
    description="Erstellt eine neue Verify-Nachricht."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def verify_panel(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:

        await interaction.response.send_message(
            "❌ Nur auf einem Server verfügbar.",
            ephemeral=True
        )

        return

    channel = get_verify_channel(
        guild
    )

    if not channel:

        await interaction.response.send_message(
            "❌ Es wurde noch kein Verify-Channel "
            "eingerichtet.\nNutze zuerst `/verify setup`.",
            ephemeral=True
        )

        return

    message = await channel.send(
        embed=build_verify_embed(),
        view=VerifyView()
    )

    config["verify"][
        "panel_message_id"
    ] = message.id

    save_json(
        CONFIG_FILE,
        config
    )

    await interaction.response.send_message(
        "✅ Neue Verify-Nachricht erstellt:\n"
        f"{message.jump_url}",
        ephemeral=True
    )


# ============================================================
# /VERIFY RESET
# ============================================================

@verify_group.command(
    name="reset",
    description="Setzt das Verify-System zurück."
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def verify_reset(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:

        await interaction.response.send_message(
            "❌ Nur auf einem Server verfügbar.",
            ephemeral=True
        )

        return

    verify_config = get_verify_config(
        guild
    )

    panel_message_id = verify_config.get(
        "panel_message_id"
    )

    channel = get_verify_channel(
        guild
    )

    # --------------------------------------------------------
    # ALTES PANEL ENTFERNEN
    # --------------------------------------------------------

    if panel_message_id and channel:

        try:

            message = await channel.fetch_message(
                int(panel_message_id)
            )

            await message.delete()

        except Exception as error:

            print(
                f"[VERIFY RESET] Panel konnte "
                f"nicht gelöscht werden: {error!r}"
            )

    # --------------------------------------------------------
    # CONFIG ZURÜCKSETZEN
    # --------------------------------------------------------

    config["verify"] = {
        "join_role_id": None,
        "verified_role_id": None,
        "channel_id": None,
        "log_channel_id": None,
        "panel_message_id": None
    }

    save_json(
        CONFIG_FILE,
        config
    )

    await interaction.response.send_message(
        "🗑️ **Verify-System zurückgesetzt.**\n"
        "Das gespeicherte Verify-Panel wurde ebenfalls entfernt.",
        ephemeral=True
    )


bot.tree.add_command(
    verify_group
)


# ============================================================
# USER BEI JOIN AUTOMATISCH AUF NEW SETZEN
# ============================================================

@bot.event
async def on_member_join(
    member: discord.Member
):

    if member.bot:
        return

    join_role = get_verify_role(
        member.guild,
        "join_role_id"
    )

    if not join_role:

        print(
            f"[VERIFY] Keine New-Rolle für "
            f"{member.guild.name} konfiguriert."
        )

        return

    bot_member = member.guild.me

    if bot_member is None:
        return

    if join_role >= bot_member.top_role:

        print(
            f"[VERIFY] ❌ New-Rolle zu hoch: "
            f"{join_role.name}"
        )

        return

    try:

        await member.add_roles(
            join_role,
            reason="ReVu Verify-System • Neuer User"
        )

        print(
            f"[VERIFY] 🆕 New-Rolle vergeben: "
            f"{member} ({member.id})"
        )

    except discord.Forbidden:

        print(
            f"[VERIFY] ❌ Keine Berechtigung, "
            f"{join_role.name} zu vergeben."
        )

    except Exception as error:

        print(
            f"[VERIFY] ❌ Join-Fehler: "
            f"{error!r}"
        )
# ============================================================
# 🎭 IMPOSTER – WER IST DER IMPOSTER?
# ============================================================

# Aktive Spiele im RAM.
# Pro Server maximal ein Imposter-Spiel.
IMPOSTER_GAMES: dict[int, dict] = {}


# ------------------------------------------------------------
# WORTDATENBANK
# ------------------------------------------------------------

IMPOSTER_WORDS = {

    "Alltag": [
        ("Auto", "Man sieht es oft unterwegs.", "mittel"),
        ("Handy", "Viele Menschen benutzen es jeden Tag.", "leicht"),
        ("Schlüssel", "Man braucht es oft, bevor man irgendwo reinkommt.", "mittel"),
        ("Rucksack", "Man kann viele Dinge darin transportieren.", "leicht"),
        ("Regenschirm", "Er kann bei schlechtem Wetter nützlich sein.", "mittel"),
        ("Uhr", "Sie zeigt etwas an, das ständig weiterläuft.", "leicht"),
        ("Bett", "Man verbringt dort normalerweise mehrere Stunden.", "leicht"),
        ("Tür", "Sie kann etwas voneinander trennen.", "leicht"),
        ("Lampe", "Sie kann einen dunklen Raum heller machen.", "leicht"),
        ("Spiegel", "Man kann darin etwas von sich selbst sehen.", "leicht"),
    ],

    "Essen": [
        ("Pizza", "Sie wird häufig geteilt.", "leicht"),
        ("Burger", "Man kann ihn mit den Händen essen.", "leicht"),
        ("Pommes", "Sie werden häufig als Beilage gegessen.", "leicht"),
        ("Schokolade", "Viele verbinden sie mit etwas Süßem.", "leicht"),
        ("Apfel", "Er wächst an einem Baum.", "leicht"),
        ("Banane", "Sie hat eine auffällige Schale.", "leicht"),
        ("Eis", "Es wird meistens kalt gegessen.", "leicht"),
        ("Nudeln", "Es gibt sie in sehr vielen Formen.", "leicht"),
        ("Kuchen", "Man bekommt ihn häufig bei Feiern.", "leicht"),
        ("Popcorn", "Man sieht es häufig im Kino.", "leicht"),
    ],

    "Tiere": [
        ("Hund", "Viele Menschen halten ihn als Haustier.", "leicht"),
        ("Katze", "Sie kann sehr eigenständig sein.", "leicht"),
        ("Pinguin", "Er kann nicht normal fliegen.", "mittel"),
        ("Elefant", "Er ist für seine Größe bekannt.", "mittel"),
        ("Löwe", "Er wird oft als König der Tiere bezeichnet.", "mittel"),
        ("Hai", "Er lebt im Wasser.", "leicht"),
        ("Adler", "Er kann hoch über dem Boden fliegen.", "mittel"),
        ("Delfin", "Er lebt im Wasser und gilt als sehr intelligent.", "mittel"),
        ("Pferd", "Menschen können auf ihm reiten.", "leicht"),
        ("Frosch", "Er kann springen und lebt oft in der Nähe von Wasser.", "leicht"),
    ],

    "Gaming": [
        ("Minecraft", "Man kann dort eine Welt aus Blöcken bauen.", "leicht"),
        ("Fortnite", "Ein bekannter Battle-Royale-Titel.", "leicht"),
        ("PlayStation", "Eine bekannte Gaming-Plattform.", "leicht"),
        ("Controller", "Man benutzt ihn häufig zum Spielen.", "leicht"),
        ("Boss", "Er ist in vielen Spielen stärker als normale Gegner.", "mittel"),
        ("Skin", "Er verändert häufig das Aussehen einer Figur.", "mittel"),
        ("Server", "Viele Spieler können sich dort treffen.", "mittel"),
        ("Lobby", "Dort warten Spieler häufig vor einer Runde.", "mittel"),
        ("Respawn", "Danach ist eine Spielfigur wieder da.", "schwer"),
        ("Level", "Davon gibt es in vielen Spielen mehrere.", "leicht"),
    ],

    "Sport": [
        ("Fußball", "Man spielt ihn meistens mit einem Ball.", "leicht"),
        ("Basketball", "Ein Ball soll in ein Ziel gelangen.", "leicht"),
        ("Tennis", "Ein Netz befindet sich zwischen den Spielern.", "leicht"),
        ("Torwart", "Er darf im Fußball normalerweise den Ball mit den Händen spielen.", "mittel"),
        ("Schiedsrichter", "Er entscheidet über Regelverstöße.", "mittel"),
        ("Stadion", "Viele Zuschauer können dort Sport sehen.", "leicht"),
        ("Elfmeter", "Eine Standardsituation im Fußball.", "mittel"),
        ("Training", "Sportler machen es regelmäßig.", "leicht"),
        ("Pokal", "Man kann ihn nach einem Wettbewerb gewinnen.", "leicht"),
        ("Trikot", "Sportler tragen es häufig während eines Spiels.", "leicht"),
    ],

    "Schule": [
        ("Tafel", "Darauf kann ein Lehrer etwas schreiben.", "leicht"),
        ("Hausaufgaben", "Sie werden häufig nach dem Unterricht gemacht.", "leicht"),
        ("Klausur", "Dabei wird Wissen geprüft.", "mittel"),
        ("Pausenhof", "Schüler halten sich dort oft in den Pausen auf.", "leicht"),
        ("Rucksack", "Viele Schüler nehmen ihn mit zur Schule.", "leicht"),
        ("Lehrer", "Er unterrichtet Schüler.", "leicht"),
        ("Klassenarbeit", "Sie findet während des Unterrichts statt.", "mittel"),
        ("Stundenplan", "Er zeigt, was wann stattfindet.", "leicht"),
        ("Kreide", "Damit kann man auf bestimmten Tafeln schreiben.", "mittel"),
        ("Schulbus", "Er bringt Schüler zur Schule oder nach Hause.", "leicht"),
    ],

    "Unterhaltung": [
        ("Film", "Man schaut ihn meistens auf einem Bildschirm.", "leicht"),
        ("Kino", "Dort schaut man Filme auf großer Leinwand.", "leicht"),
        ("Serie", "Sie besteht normalerweise aus mehreren Folgen.", "leicht"),
        ("YouTube", "Dort kann man viele Videos anschauen.", "leicht"),
        ("Streamer", "Er überträgt häufig live.", "mittel"),
        ("Schauspieler", "Er spielt eine Rolle.", "mittel"),
        ("Musik", "Man kann sie hören.", "leicht"),
        ("Konzert", "Dort treten Musiker vor Publikum auf.", "leicht"),
        ("Anime", "Eine Form animierter Unterhaltung.", "mittel"),
        ("Superheld", "Er besitzt häufig besondere Fähigkeiten.", "leicht"),
    ],

    "Welt": [
        ("Paris", "Eine bekannte europäische Stadt.", "mittel"),
        ("New York", "Eine sehr bekannte Großstadt.", "mittel"),
        ("London", "Eine europäische Hauptstadt.", "mittel"),
        ("Deutschland", "Ein Land in Europa.", "leicht"),
        ("Japan", "Ein Inselstaat in Asien.", "mittel"),
        ("Wüste", "Dort fällt häufig sehr wenig Regen.", "leicht"),
        ("Ozean", "Davon gibt es mehrere auf der Erde.", "leicht"),
        ("Berg", "Er kann sehr hoch sein.", "leicht"),
        ("Flughafen", "Dort starten und landen Flugzeuge.", "leicht"),
        ("Hotel", "Dort können Menschen übernachten.", "leicht"),
    ],

    "Chaos": [
        ("Meme", "Im Internet wird es häufig geteilt.", "leicht"),
        ("Kopfhörer", "Man trägt sie häufig am Kopf.", "leicht"),
        ("Roboter", "Er kann Aufgaben automatisch erledigen.", "leicht"),
        ("Alien", "Eine mögliche Lebensform außerhalb der Erde.", "mittel"),
        ("Zeitmaschine", "Damit könnte man theoretisch durch die Zeit reisen.", "schwer"),
        ("Ninja", "Eine Figur, die oft mit Heimlichkeit verbunden wird.", "mittel"),
        ("Drache", "Ein bekanntes Fabelwesen.", "leicht"),
        ("Geist", "Ein übernatürliches Wesen.", "leicht"),
        ("Rakete", "Sie kann ins Weltall fliegen.", "leicht"),
        ("Detektiv", "Er versucht häufig Geheimnisse zu lösen.", "leicht"),
    ]
}


IMPOSTER_CATEGORIES = list(
    IMPOSTER_WORDS.keys()
)


# ------------------------------------------------------------
# HILFSFUNKTIONEN
# ------------------------------------------------------------

def imposter_game(guild_id: int) -> Optional[dict]:
    return IMPOSTER_GAMES.get(guild_id)


def imposter_player_count(game: dict) -> int:
    return len(game.get("players", {}))


def imposter_allowed_count(player_count: int) -> list[int]:

    if player_count < 3:
        return []

    if player_count == 3:
        return [1]

    if player_count == 4:
        return [1, 2]

    return [1, 2, 3]


def imposter_random_word(
    category: str,
    difficulty: str
):

    words = IMPOSTER_WORDS.get(
        category,
        []
    )

    if not words:
        words = [
            item
            for values in IMPOSTER_WORDS.values()
            for item in values
        ]

    matching = [
        item
        for item in words
        if item[2] == difficulty
    ]

    if not matching:
        matching = words

    return random.choice(
        matching
    )


def imposter_embed(
    game: dict
) -> discord.Embed:

    players = game.get(
        "players",
        {}
    )

    player_lines = []

    for user_id, data in players.items():

        status = (
            "🟢"
            if data.get("accepted")
            else "🟡"
        )

        member = game["guild"].get_member(
            int(user_id)
        )

        if member:
            player_lines.append(
                f"{status} {member.mention}"
            )

    if not player_lines:
        player_lines.append(
            "Noch niemand beigetreten."
        )

    allowed = imposter_allowed_count(
        len(players)
    )

    imposter_text = (
        ", ".join(
            str(x)
            for x in allowed
        )
        if allowed
        else "—"
    )

    embed = discord.Embed(
        title="🎭 WER IST DER IMPOSTER?",
        description=(
            "Eine neue Runde wurde erstellt!\n\n"
            "Alle Spieler müssen die Einladung "
            "annehmen, bevor die Runde gestartet "
            "werden kann."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="👥 Spieler",
        value=(
            f"**{len(players)}** Spieler\n\n"
            + "\n".join(player_lines)
        )[:1024],
        inline=False
    )

    embed.add_field(
        name="🎯 Kategorie",
        value=game.get(
            "category",
            "Alltag"
        ),
        inline=True
    )

    embed.add_field(
        name="🔴 Imposter",
        value=imposter_text,
        inline=True
    )

    embed.add_field(
        name="⏱️ Diskussion",
        value=f"{game.get('discussion_time', 60)} Sekunden",
        inline=True
    )

    embed.add_field(
        name="🗳️ Abstimmung",
        value=f"{game.get('voting_time', 45)} Sekunden",
        inline=True
    )

    embed.add_field(
        name="💡 Hinweise",
        value=(
            "✅ Aktiv"
            if game.get("hints", True)
            else "❌ Aus"
        ),
        inline=True
    )

    embed.add_field(
        name="⚡ Status",
        value=game.get(
            "status",
            "Lobby"
        ),
        inline=True
    )

    embed.set_footer(
        text="ReVu • Imposter Game"
    )

    return embed


# ------------------------------------------------------------
# EINLADUNG
# ------------------------------------------------------------

class ImposterInviteView(
    discord.ui.View
):

    def __init__(
        self,
        guild_id: int,
        user_id: int
    ):

        super().__init__(
            timeout=120
        )

        self.guild_id = guild_id
        self.user_id = user_id
        self.finished = False

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ):

        if interaction.user.id != self.user_id:

            await interaction.response.send_message(
                "❌ Diese Einladung gehört nicht dir.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="Annehmen",
        emoji="🎮",
        style=discord.ButtonStyle.success
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        game = imposter_game(
            self.guild_id
        )

        if not game:

            await interaction.response.edit_message(
                content="❌ Die Lobby existiert nicht mehr.",
                embed=None,
                view=None
            )

            return

        player = game["players"].get(
            str(self.user_id)
        )

        if not player:

            await interaction.response.edit_message(
                content="❌ Du bist nicht mehr für diese Runde vorgesehen.",
                embed=None,
                view=None
            )

            return

        player["accepted"] = True

        await interaction.response.edit_message(
            content=(
                "✅ **Du bist dabei!**\n\n"
                "Warte, bis der Ersteller die Runde startet."
            ),
            embed=None,
            view=None
        )

        await imposter_update_lobby(
            game
        )

    @discord.ui.button(
        label="Ablehnen",
        emoji="❌",
        style=discord.ButtonStyle.danger
    )
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        game = imposter_game(
            self.guild_id
        )

        if game:

            game["players"].pop(
                str(self.user_id),
                None
            )

            await imposter_update_lobby(
                game
            )

        await interaction.response.edit_message(
            content="❌ **Einladung abgelehnt.**",
            embed=None,
            view=None
        )


# ------------------------------------------------------------
# EINSTELLUNGEN
# ------------------------------------------------------------

class ImposterSettingsView(
    discord.ui.View
):

    def __init__(
        self,
        game: dict,
        owner_id: int
    ):

        super().__init__(
            timeout=300
        )

        self.game = game
        self.owner_id = owner_id

        self.category_select = discord.ui.Select(
            placeholder="🎯 Kategorie auswählen",
            options=[
                discord.SelectOption(
                    label=category,
                    value=category,
                    default=(
                        category
                        == game.get(
                            "category",
                            "Alltag"
                        )
                    )
                )
                for category in IMPOSTER_CATEGORIES
            ]
        )

        self.category_select.callback = (
            self.category_callback
        )

        self.add_item(
            self.category_select
        )

        self.imposter_select = discord.ui.Select(
            placeholder="🔴 Anzahl Imposter",
            options=[
                discord.SelectOption(
                    label="Automatisch",
                    value="auto"
                ),
                discord.SelectOption(
                    label="1 Imposter",
                    value="1"
                ),
                discord.SelectOption(
                    label="2 Imposter",
                    value="2"
                ),
                discord.SelectOption(
                    label="3 Imposter",
                    value="3"
                )
            ]
        )

        self.imposter_select.callback = (
            self.imposter_callback
        )

        self.add_item(
            self.imposter_select
        )

        self.difficulty_select = discord.ui.Select(
            placeholder="🧠 Schwierigkeit",
            options=[
                discord.SelectOption(
                    label="Leicht",
                    value="leicht"
                ),
                discord.SelectOption(
                    label="Mittel",
                    value="mittel"
                ),
                discord.SelectOption(
                    label="Schwer",
                    value="schwer"
                )
            ]
        )

        self.difficulty_select.callback = (
            self.difficulty_callback
        )

        self.add_item(
            self.difficulty_select
        )

    async def interaction_check(
        self,
        interaction
    ):

        if interaction.user.id != self.owner_id:

            await interaction.response.send_message(
                "❌ Nur der Ersteller kann die Einstellungen ändern.",
                ephemeral=True
            )

            return False

        return True

    async def category_callback(
        self,
        interaction
    ):

        self.game["category"] = (
            self.category_select.values[0]
        )

        await interaction.response.edit_message(
            embed=imposter_settings_embed(
                self.game
            ),
            view=self
        )

    async def imposter_callback(
        self,
        interaction
    ):

        value = self.imposter_select.values[0]

        self.game["imposter_setting"] = value

        await interaction.response.edit_message(
            embed=imposter_settings_embed(
                self.game
            ),
            view=self
        )

    async def difficulty_callback(
        self,
        interaction
    ):

        self.game["difficulty"] = (
            self.difficulty_select.values[0]
        )

        await interaction.response.edit_message(
            embed=imposter_settings_embed(
                self.game
            ),
            view=self
        )

    @discord.ui.button(
        label="💡 Hinweise AN/AUS",
        style=discord.ButtonStyle.secondary
    )
    async def hints(
        self,
        interaction,
        button
    ):

        self.game["hints"] = not self.game.get(
            "hints",
            True
        )

        await interaction.response.edit_message(
            embed=imposter_settings_embed(
                self.game
            ),
            view=self
        )

    @discord.ui.button(
        label="❌ Schließen",
        style=discord.ButtonStyle.danger
    )
    async def close(
        self,
        interaction,
        button
    ):

        await interaction.response.edit_message(
            content="⚙️ Einstellungen gespeichert.",
            embed=None,
            view=None
        )


def imposter_settings_embed(
    game: dict
) -> discord.Embed:

    embed = discord.Embed(
        title="⚙️ Imposter-Einstellungen",
        description=(
            "Passe die Runde an, bevor sie gestartet wird."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🎯 Kategorie",
        value=game.get(
            "category",
            "Alltag"
        ),
        inline=True
    )

    imposter_setting = game.get(
        "imposter_setting",
        "auto"
    )

    embed.add_field(
        name="🔴 Imposter",
        value=(
            "Automatisch"
            if imposter_setting == "auto"
            else f"{imposter_setting} Imposter"
        ),
        inline=True
    )

    embed.add_field(
        name="🧠 Schwierigkeit",
        value=game.get(
            "difficulty",
            "mittel"
        ).capitalize(),
        inline=True
    )

    embed.add_field(
        name="💡 Hinweise",
        value=(
            "✅ Aktiv"
            if game.get("hints", True)
            else "❌ Aus"
        ),
        inline=True
    )

    return embed


# ------------------------------------------------------------
# LOBBY VIEW
# ------------------------------------------------------------

class ImposterLobbyView(
    discord.ui.View
):

    def __init__(
        self,
        game: dict
    ):

        super().__init__(
            timeout=600
        )

        self.game = game

    async def interaction_check(
        self,
        interaction
    ):

        if interaction.user.id != self.game["owner_id"]:

            await interaction.response.send_message(
                "❌ Nur der Ersteller kann die Lobby verwalten.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="⚙️ Einstellungen",
        style=discord.ButtonStyle.secondary
    )
    async def settings(
        self,
        interaction,
        button
    ):

        await interaction.response.send_message(
            embed=imposter_settings_embed(
                self.game
            ),
            view=ImposterSettingsView(
                self.game,
                self.game["owner_id"]
            ),
            ephemeral=True
        )

    @discord.ui.button(
        label="▶️ Starten",
        style=discord.ButtonStyle.success
    )
    async def start(
        self,
        interaction,
        button
    ):

        players = [
            data
            for data in self.game["players"].values()
            if data.get("accepted")
        ]

        if len(players) < 3:

            await interaction.response.send_message(
                "❌ Du brauchst mindestens **3 angenommene Spieler**.",
                ephemeral=True
            )

            return

        await interaction.response.defer()

        await imposter_start_game(
            self.game
        )

        try:
            await interaction.edit_original_response(
                embed=imposter_embed(
                    self.game
                ),
                view=None
            )
        except Exception:
            pass

    @discord.ui.button(
        label="🛑 Abbrechen",
        style=discord.ButtonStyle.danger
    )
    async def cancel(
        self,
        interaction,
        button
    ):

        await interaction.response.edit_message(
            content="🛑 **Imposter-Spiel abgebrochen.**",
            embed=None,
            view=None
        )

        await imposter_cleanup(
            self.game
        )


# ------------------------------------------------------------
# LOBBY AKTUALISIEREN
# ------------------------------------------------------------

async def imposter_update_lobby(
    game: dict
):

    message = game.get(
        "lobby_message"
    )

    if not message:
        return

    try:

        await message.edit(
            embed=imposter_embed(
                game
            ),
            view=ImposterLobbyView(
                game
            )
        )

    except Exception as error:

        print(
            f"[IMPOSTER] Lobby konnte nicht aktualisiert werden: "
            f"{error!r}"
        )


# ------------------------------------------------------------
# VC CHANNEL ERSTELLEN
# ------------------------------------------------------------

async def imposter_create_voice_channel(
    game: dict
):

    guild = game["guild"]

    overwrites = {}

    # Jeder darf den Channel sehen.
    overwrites[
        guild.default_role
    ] = discord.PermissionOverwrite(
        view_channel=True,
        connect=False
    )

    # Bot darf alles Notwendige.
    bot_member = guild.me

    if bot_member:

        overwrites[
            bot_member
        ] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
            manage_channels=True
        )

    # Admin/Ersteller darf immer rein.
    owner = guild.get_member(
        game["owner_id"]
    )

    if owner:

        overwrites[
            owner
        ] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True
        )

    # Teilnehmer dürfen rein.
    for user_id, player in game["players"].items():

        if not player.get("accepted"):
            continue

        member = guild.get_member(
            int(user_id)
        )

        if not member:
            continue

        overwrites[
            member
        ] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True
        )

    category = None

    try:

        category = guild.get_channel(
            int(
                game["voice_category_id"]
            )
        ) if game.get("voice_category_id") else None

    except Exception:
        category = None

    channel = await guild.create_voice_channel(
        "🔴・WER IST DER IMPOSTER",
        category=category,
        overwrites=overwrites,
        reason="Imposter Game"
    )

    game[
        "voice_channel_id"
    ] = channel.id

    return channel


# ------------------------------------------------------------
# SPIEL STARTEN
# ------------------------------------------------------------

async def imposter_start_game(
    game: dict
):

    if game.get("status") == "running":
        return

    guild = game["guild"]

    accepted_players = [
        data
        for data in game["players"].values()
        if data.get("accepted")
    ]

    if len(accepted_players) < 3:
        return

    game["status"] = "running"

    # --------------------------------------------------------
    # IMPOSTER ANZAHL
    # --------------------------------------------------------

    allowed = imposter_allowed_count(
        len(accepted_players)
    )

    setting = game.get(
        "imposter_setting",
        "auto"
    )

    if setting == "auto":

        imposter_count = random.choice(
            allowed
        )

    else:

        try:
            imposter_count = int(
                setting
            )
        except Exception:
            imposter_count = 1

        if imposter_count not in allowed:

            imposter_count = max(
                allowed
            )

    game[
        "imposter_count"
    ] = imposter_count

    # --------------------------------------------------------
    # WORT
    # --------------------------------------------------------

    category = game.get(
        "category",
        "Alltag"
    )

    difficulty = game.get(
        "difficulty",
        "mittel"
    )

    word, hint, word_difficulty = (
        imposter_random_word(
            category,
            difficulty
        )
    )

    game[
        "word"
    ] = word

    game[
        "hint"
    ] = hint

    game[
        "word_difficulty"
    ] = word_difficulty

    # --------------------------------------------------------
    # SPIELER
    # --------------------------------------------------------

    player_ids = [
        int(data["user_id"])
        for data in accepted_players
    ]

    imposters = set(
        random.sample(
            player_ids,
            imposter_count
        )
    )

    game[
        "imposters"
    ] = imposters

    # --------------------------------------------------------
    # VOICE CHANNEL
    # --------------------------------------------------------

    voice_channel = await imposter_create_voice_channel(
        game
    )

    # Originale Voice-Channels merken.
    for user_id in player_ids:

        member = guild.get_member(
            user_id
        )

        if not member:
            continue

        game["players"][
            str(user_id)
        ][
            "original_voice_id"
        ] = (
            member.voice.channel.id
            if member.voice
            and member.voice.channel
            else None
        )

    # --------------------------------------------------------
    # SPIELER VERSCHIEBEN
    # --------------------------------------------------------

    for user_id in player_ids:

        member = guild.get_member(
            user_id
        )

        if not member:
            continue

        try:

            if member.voice:

                await member.move_to(
                    voice_channel,
                    reason="Imposter Game gestartet"
                )

        except Exception as error:

            print(
                f"[IMPOSTER] Voice Move Fehler "
                f"{member}: {error!r}"
            )

    # --------------------------------------------------------
    # GEHEIME ROLLEN SENDEN
    # --------------------------------------------------------

    for user_id in player_ids:

        member = guild.get_member(
            user_id
        )

        if not member:
            continue

        is_imposter = user_id in imposters

        if is_imposter:

            other_imposters = [
                guild.get_member(
                    other_id
                )
                for other_id in imposters
                if other_id != user_id
            ]

            other_names = [
                member_.display_name
                for member_ in other_imposters
                if member_
            ]

            others_text = (
                ", ".join(other_names)
                if other_names
                else "Du bist der einzige Imposter."
            )

            embed = discord.Embed(
                title="🕵️ DEINE ROLLE",
                description=(
                    "## 🔴 DU BIST DER IMPOSTER\n\n"
                    "Du kennst das geheime Wort **nicht**.\n\n"
                    "Versuche anhand der Hinweise herauszufinden, "
                    "welches Wort die anderen kennen.\n\n"
                    "### 🤝 Deine Mit-Imposter\n"
                    f"{others_text}\n\n"
                    "💡 **Dein Hinweis:**\n"
                    f"{hint}"
                ),
                color=discord.Color.red()
            )

        else:

            embed = discord.Embed(
                title="🔐 DEINE ROLLE",
                description=(
                    "## 👤 DU BIST SPIELER\n\n"
                    "### 🔑 Dein geheimes Wort\n"
                    f"## **{word}**\n\n"
                    "⚠️ Sag das Wort niemals direkt!\n"
                    "Gib stattdessen clevere Hinweise."
                ),
                color=discord.Color.green()
            )

        embed.add_field(
            name="🎯 Kategorie",
            value=category,
            inline=True
        )

        embed.add_field(
            name="🧠 Schwierigkeit",
            value=difficulty.capitalize(),
            inline=True
        )

        embed.set_footer(
            text="ReVu • Imposter Game"
        )

        try:

            await member.send(
                embed=embed
            )

        except discord.Forbidden:

            print(
                f"[IMPOSTER] DMs deaktiviert bei {member}"
            )

        except Exception as error:

            print(
                f"[IMPOSTER] DM Fehler bei {member}: {error!r}"
            )

    # --------------------------------------------------------
    # STARTNACHRICHT
    # --------------------------------------------------------

    text_channel = game.get(
        "text_channel"
    )

    if text_channel:

        embed = discord.Embed(
            title="🎭 DIE RUNDE BEGINNT!",
            description=(
                "Alle Spieler wurden in den "
                f"Voice-Channel {voice_channel.mention} verschoben.\n\n"
                "🔐 **Die Rollen wurden per DM verschickt.**\n\n"
                "🎙️ Sprecht euch jetzt ab und gebt Hinweise, "
                "ohne das geheime Wort direkt zu nennen.\n\n"
                f"⏱️ Diskussionszeit: "
                f"**{game.get('discussion_time', 60)} Sekunden**"
            ),
            color=discord.Color.red()
        )

        embed.set_footer(
            text="ReVu • Imposter Game"
        )

        game[
            "game_message"
        ] = await text_channel.send(
            embed=embed,
            view=ImposterGameControlView(
                game
            )
        )

    # Diskussion starten.
    asyncio.create_task(
        imposter_discussion_timer(
            game
        )
    )


# ------------------------------------------------------------
# SPIEL-CONTROL
# ------------------------------------------------------------

class ImposterGameControlView(
    discord.ui.View
):

    def __init__(
        self,
        game: dict
    ):

        super().__init__(
            timeout=None
        )

        self.game = game

    async def interaction_check(
        self,
        interaction
    ):

        participant_ids = {
            int(user_id)
            for user_id, data
            in self.game["players"].items()
            if data.get("accepted")
        }

        if interaction.user.id not in participant_ids:

            await interaction.response.send_message(
                "❌ Du bist kein Teilnehmer dieser Runde.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.button(
        label="🗳️ Abstimmung starten",
        style=discord.ButtonStyle.primary
    )
    async def vote(
        self,
        interaction,
        button
    ):

        if self.game.get("phase") != "discussion":

            await interaction.response.send_message(
                "❌ Die Abstimmung ist momentan nicht verfügbar.",
                ephemeral=True
            )

            return

        await imposter_begin_voting(
            self.game
        )

        await interaction.response.send_message(
            "🗳️ **Die geheime Abstimmung wurde gestartet.**",
            ephemeral=True
        )

    @discord.ui.button(
        label="🛑 Spiel abbrechen",
        style=discord.ButtonStyle.danger
    )
    async def stop(
        self,
        interaction,
        button
    ):

        if interaction.user.id != self.game["owner_id"]:

            await interaction.response.send_message(
                "❌ Nur der Ersteller kann das Spiel abbrechen.",
                ephemeral=True
            )

            return

        await interaction.response.send_message(
            "🛑 Spiel wird beendet.",
            ephemeral=True
        )

        await imposter_cleanup(
            self.game
        )


# ------------------------------------------------------------
# DISKUSSIONS-TIMER
# ------------------------------------------------------------

async def imposter_discussion_timer(
    game: dict
):

    game[
        "phase"
    ] = "discussion"

    seconds = int(
        game.get(
            "discussion_time",
            60
        )
    )

    await asyncio.sleep(
        seconds
    )

    if game.get(
        "status"
    ) != "running":

        return

    if game.get(
        "phase"
    ) != "discussion":

        return

    await imposter_begin_voting(
        game
    )


# ------------------------------------------------------------
# ABSTIMMUNG
# ------------------------------------------------------------

class ImposterVoteSelect(
    discord.ui.Select
):

    def __init__(
        self,
        game: dict
    ):

        self.game = game

        options = []

        for user_id, data in game["players"].items():

            if not data.get("accepted"):
                continue

            member = game["guild"].get_member(
                int(user_id)
            )

            if not member:
                continue

            options.append(
                discord.SelectOption(
                    label=member.display_name[:100],
                    value=str(member.id)
                )
            )

        super().__init__(
            placeholder="🗳️ Wähle den Imposter...",
            options=options[:25],
            min_values=1,
            max_values=1
        )

    async def callback(
        self,
        interaction
    ):

        user_id = interaction.user.id

        if user_id not in self.game["votes"]:

            self.game["votes"][
                user_id
            ] = int(
                self.values[0]
            )

            await interaction.response.send_message(
                "✅ Deine Stimme wurde geheim gespeichert.",
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                "ℹ️ Du hast bereits abgestimmt.",
                ephemeral=True
            )

        participants = [
            int(user_id)
            for user_id, data
            in self.game["players"].items()
            if data.get("accepted")
        ]

        if len(
            self.game["votes"]
        ) >= len(participants):

            await imposter_finish_voting(
                self.game
            )


class ImposterVoteView(
    discord.ui.View
):

    def __init__(
        self,
        game: dict
    ):

        super().__init__(
            timeout=game.get(
                "voting_time",
                45
            )
        )

        self.game = game

        self.add_item(
            ImposterVoteSelect(
                game
            )
        )

    async def on_timeout(
        self
    ):

        if self.game.get(
            "phase"
        ) != "voting":

            return

        await imposter_finish_voting(
            self.game
        )


async def imposter_begin_voting(
    game: dict
):

    if game.get(
        "phase"
    ) == "voting":

        return

    game[
        "phase"
    ] = "voting"

    game[
        "votes"
    ] = {}

    text_channel = game.get(
        "text_channel"
    )

    if not text_channel:
        return

    embed = discord.Embed(
        title="🗳️ ABSTIMMUNG",
        description=(
            "## Wer ist der Imposter?\n\n"
            "Wähle **heimlich** die Person aus, "
            "die du für den Imposter hältst.\n\n"
            "🔒 Deine Stimme wird niemandem angezeigt.\n\n"
            f"⏱️ Zeit: **{game.get('voting_time', 45)} Sekunden**"
        ),
        color=discord.Color.blurple()
    )

    message = await text_channel.send(
        embed=embed
    )

    # Jeder Teilnehmer bekommt seine eigene geheime Abstimmung.
    for user_id, data in game["players"].items():

        if not data.get("accepted"):
            continue

        member = game["guild"].get_member(
            int(user_id)
        )

        if not member:
            continue

        try:

            await member.send(
                embed=embed,
                view=ImposterVoteView(
                    game
                )
            )

        except Exception as error:

            print(
                f"[IMPOSTER] Vote-DM Fehler: {error!r}"
            )

    # Zusätzlich öffentlich erklären.
    try:

        await message.edit(
            embed=embed
        )

    except Exception:
        pass


# ------------------------------------------------------------
# ABSTIMMUNG AUSWERTEN
# ------------------------------------------------------------

async def imposter_finish_voting(
    game: dict
):

    if game.get(
        "_voting_finished"
    ):
        return

    game[
        "_voting_finished"
    ] = True

    game[
        "phase"
    ] = "reveal"

    votes = game.get(
        "votes",
        {}
    )

    if not votes:

        await imposter_reveal(
            game,
            None,
            0
        )

        return

    counts = {}

    for target_id in votes.values():

        counts[target_id] = (
            counts.get(
                target_id,
                0
            )
            + 1
        )

    highest = max(
        counts.values()
    )

    winners = [
        user_id
        for user_id, count
        in counts.items()
        if count == highest
    ]

    # Gleichstand.
    if len(winners) != 1:

        game[
            "tie"
        ] = True

        await imposter_reveal(
            game,
            None,
            highest
        )

        return

    voted_user_id = winners[0]

    await imposter_reveal(
        game,
        voted_user_id,
        highest
    )


# ------------------------------------------------------------
# LETZTE CHANCE
# ------------------------------------------------------------

class ImposterFinalGuessModal(
    discord.ui.Modal
):

    def __init__(
        self,
        game: dict,
        imposter_id: int
    ):

        super().__init__(
            title="🧠 Letzte Chance"
        )

        self.game = game
        self.imposter_id = imposter_id

        self.answer = discord.ui.TextInput(
            label="Wie lautet das geheime Wort?",
            placeholder="Deine Vermutung...",
            max_length=100,
            required=True
        )

        self.add_item(
            self.answer
        )

    async def on_submit(
        self,
        interaction
    ):

        guess = str(
            self.answer.value
        ).strip()

        word = str(
            self.game.get(
                "word",
                ""
            )
        ).strip()

        if guess.casefold() == word.casefold():

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🎯 RICHTIG!",
                    description=(
                        "Du hast das geheime Wort erraten!\n\n"
                        f"🔑 Das Wort war: **{word}**\n\n"
                        "## 🔴 DIE IMPOSTER GEWINNEN!"
                    ),
                    color=discord.Color.red()
                )
            )

            self.game[
                "winner"
            ] = "imposter"

        else:

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ FALSCH!",
                    description=(
                        f"Deine Antwort **{guess}** war falsch.\n\n"
                        f"🔑 Das Wort war: **{word}**\n\n"
                        "## 🟢 DIE SPIELER GEWINNEN!"
                    ),
                    color=discord.Color.green()
                )
            )

            self.game[
                "winner"
            ] = "players"

        await imposter_finish_game(
            self.game
        )


class ImposterFinalGuessView(
    discord.ui.View
):

    def __init__(
        self,
        game: dict,
        imposter_id: int
    ):

        super().__init__(
            timeout=45
        )

        self.game = game
        self.imposter_id = imposter_id

    @discord.ui.button(
        label="🧠 Wort erraten",
        style=discord.ButtonStyle.danger
    )
    async def guess(
        self,
        interaction,
        button
    ):

        if interaction.user.id != self.imposter_id:

            await interaction.response.send_message(
                "❌ Nur der erwischte Imposter darf raten.",
                ephemeral=True
            )

            return

        await interaction.response.send_modal(
            ImposterFinalGuessModal(
                self.game,
                self.imposter_id
            )
        )


# ------------------------------------------------------------
# AUFLÖSUNG
# ------------------------------------------------------------

async def imposter_reveal(
    game: dict,
    voted_user_id: Optional[int],
    votes: int
):

    guild = game["guild"]

    voted_member = (
        guild.get_member(
            voted_user_id
        )
        if voted_user_id
        else None
    )

    if game.get("tie"):

        description = (
            "## ⚖️ UNENTSCHIEDEN\n\n"
            "Die höchste Stimmenanzahl war geteilt.\n\n"
            "Niemand wurde eindeutig ausgewählt.\n\n"
            "🔴 **Die Imposter gewinnen diese Runde!**"
        )

        winner = "imposter"

    elif not voted_member:

        description = (
            "## ❌ NIEMAND WURDE AUSGEWÄHLT\n\n"
            "Es wurde keine gültige Stimme abgegeben.\n\n"
            "🔴 **Die Imposter gewinnen!**"
        )

        winner = "imposter"

    else:

        is_imposter = (
            voted_user_id
            in game.get(
                "imposters",
                set()
            )
        )

        if is_imposter:

            description = (
                f"## 🎯 {voted_member.mention} WURDE ENTLARVT!\n\n"
                f"**{votes}** Stimmen.\n\n"
                "🔴 Die Person war tatsächlich ein Imposter!"
            )

            winner = None

        else:

            description = (
                f"## ❌ FALSCHER VERDACHT!\n\n"
                f"{voted_member.mention} wurde mit "
                f"**{votes}** Stimmen gewählt.\n\n"
                "Die Person war **kein Imposter**."
            )

            winner = "imposter"

    embed = discord.Embed(
        title="🎭 AUFLÖSUNG",
        description=description,
        color=(
            discord.Color.red()
            if winner == "imposter"
            else discord.Color.orange()
        )
    )

    embed.add_field(
        name="🔑 Geheimes Wort",
        value=f"**{game.get('word', 'Unbekannt')}**",
        inline=True
    )

    if voted_member:

        embed.add_field(
            name="🗳️ Gewählt",
            value=voted_member.mention,
            inline=True
        )

    embed.add_field(
        name="🔴 Imposter",
        value="\n".join(
            (
                guild.get_member(
                    int(user_id)
                ).mention
                if guild.get_member(
                    int(user_id)
                )
                else f"`{user_id}`"
            )
            for user_id in game.get(
                "imposters",
                set()
            )
        ),
        inline=False
    )

    text_channel = game.get(
        "text_channel"
    )

    if text_channel:

        await text_channel.send(
            embed=embed
        )

    # Wenn ein Imposter erwischt wurde:
    if (
        voted_member
        and voted_user_id in game.get(
            "imposters",
            set()
        )
    ):

        game[
            "caught_imposters"
        ] = game.get(
            "caught_imposters",
            []
        )

        if voted_user_id not in game[
            "caught_imposters"
        ]:

            game[
                "caught_imposters"
            ].append(
                voted_user_id
            )

        # Letzte Chance per DM.
        try:

            await voted_member.send(
                embed=discord.Embed(
                    title="🧠 LETZTE CHANCE!",
                    description=(
                        "Du wurdest als Imposter entlarvt.\n\n"
                        "Aber das Spiel ist noch nicht vorbei.\n\n"
                        "Wenn du das geheime Wort errätst, "
                        "gewinnen die Imposter trotzdem."
                    ),
                    color=discord.Color.red()
                ),
                view=ImposterFinalGuessView(
                    game,
                    voted_user_id
                )
            )

            return

        except Exception as error:

            print(
                f"[IMPOSTER] Final Guess DM Fehler: {error!r}"
            )

    if winner:

        game[
            "winner"
        ] = winner

        await imposter_finish_game(
            game
        )


# ------------------------------------------------------------
# SPIEL BEENDEN
# ------------------------------------------------------------

async def imposter_finish_game(
    game: dict
):

    if game.get(
        "_finished"
    ):
        return

    game[
        "_finished"
    ] = True

    winner = game.get(
        "winner",
        "players"
    )

    text_channel = game.get(
        "text_channel"
    )

    if text_channel:

        if winner == "imposter":

            embed = discord.Embed(
                title="🔴 DIE IMPOSTER GEWINNEN!",
                description=(
                    "Die Imposter konnten die Runde für sich entscheiden.\n\n"
                    f"🔑 Das Wort war: **{game.get('word')}**"
                ),
                color=discord.Color.red()
            )

        else:

            embed = discord.Embed(
                title="🟢 DIE SPIELER GEWINNEN!",
                description=(
                    "Die Imposter wurden erfolgreich entlarvt.\n\n"
                    f"🔑 Das Wort war: **{game.get('word')}**"
                ),
                color=discord.Color.green()
            )

        await text_channel.send(
            embed=embed
        )

    await asyncio.sleep(
        3
    )

    await imposter_cleanup(
        game
    )


# ------------------------------------------------------------
# AUFRÄUMEN
# ------------------------------------------------------------

async def imposter_cleanup(
    game: dict
):

    guild = game.get(
        "guild"
    )

    if not guild:
        return

    # Spieler zurück in ursprüngliche VCs.
    for user_id, player in game.get(
        "players",
        {}
    ).items():

        if not player.get(
            "accepted"
        ):
            continue

        member = guild.get_member(
            int(user_id)
        )

        if not member:
            continue

        original_id = player.get(
            "original_voice_id"
        )

        if not original_id:
            continue

        original_channel = guild.get_channel(
            int(original_id)
        )

        if not isinstance(
            original_channel,
            discord.VoiceChannel
        ):
            continue

        try:

            await member.move_to(
                original_channel,
                reason="Imposter Game beendet"
            )

        except Exception as error:

            print(
                f"[IMPOSTER] Rücktransfer Fehler "
                f"{member}: {error!r}"
            )

    # Temporären VC löschen.
    voice_id = game.get(
        "voice_channel_id"
    )

    if voice_id:

        channel = guild.get_channel(
            int(voice_id)
        )

        if channel:

            try:

                await channel.delete(
                    reason="Imposter Game beendet"
                )

            except Exception as error:

                print(
                    f"[IMPOSTER] VC konnte nicht gelöscht werden: "
                    f"{error!r}"
                )

    # Spiel aus Speicher entfernen.
    if IMPOSTER_GAMES.get(
        guild.id
    ) is game:

        IMPOSTER_GAMES.pop(
            guild.id,
            None
        )

    print(
        f"[IMPOSTER] Spiel beendet auf {guild.name}"
    )


# ------------------------------------------------------------
# /IMPOSTER
# ------------------------------------------------------------

@bot.tree.command(
    name="imposter",
    description="Startet eine Runde Wer ist der Imposter?"
)
async def imposter(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:

        await interaction.response.send_message(
            "❌ Dieser Befehl funktioniert nur auf einem Server.",
            ephemeral=True
        )

        return

    
    # --------------------------------------------------------
    # VOICE CHECK
    # --------------------------------------------------------

    if not isinstance(
        interaction.user,
        discord.Member
    ):

        await interaction.response.send_message(
            "❌ Benutzer konnte nicht erkannt werden.",
            ephemeral=True
        )

        return

    if not interaction.user.voice:

        await interaction.response.send_message(
            "❌ Du musst in einem Voice-Channel sein, "
            "um ein Imposter-Spiel zu starten.",
            ephemeral=True
        )

        return

    source_voice = interaction.user.voice.channel

    if not isinstance(
        source_voice,
        discord.VoiceChannel
    ):

        await interaction.response.send_message(
            "❌ Dieser Voice-Channel wird nicht unterstützt.",
            ephemeral=True
        )

        return

    # --------------------------------------------------------
    # GAME STATE
    # --------------------------------------------------------

    game = {

        "guild": guild,

        "owner_id": interaction.user.id,

        "text_channel": interaction.channel,

        "source_voice_id": source_voice.id,

        "voice_category_id": (
            source_voice.category.id
            if source_voice.category
            else None
        ),

        "players": {},

        "status": "Lobby",

        "phase": "lobby",

        "category": "Alltag",

        "difficulty": "mittel",

        "imposter_setting": "auto",

        "imposter_count": 1,

        "discussion_time": 60,

        "voting_time": 45,

        "hints": True,

        "imposters": set(),

        "votes": {},

        "caught_imposters": [],

        "word": None,

        "hint": None,

        "voice_channel_id": None,

        "lobby_message": None,

        "game_message": None
    }

    # --------------------------------------------------------
    # ALLE IM VOICE AUTOMATISCH EINLADEN
    # --------------------------------------------------------

    for member in source_voice.members:

        if member.bot:
            continue

        game["players"][
            str(member.id)
        ] = {

            "user_id": member.id,

            "accepted": (
                member.id
                == interaction.user.id
            ),

            "original_voice_id": (
                source_voice.id
            )
        }

    IMPOSTER_GAMES[
        guild.id
    ] = game

    # --------------------------------------------------------
    # LOBBY
    # --------------------------------------------------------

    await interaction.response.send_message(
        embed=imposter_embed(
            game
        ),
        view=ImposterLobbyView(
            game
        )
    )

    game[
        "lobby_message"
    ] = await interaction.original_response()

    # --------------------------------------------------------
    # DMS
    # --------------------------------------------------------

    for user_id, player in list(
        game["players"].items()
    ):

        if player.get(
            "accepted"
        ):
            continue

        member = guild.get_member(
            int(user_id)
        )

        if not member:
            continue

        try:

            embed = discord.Embed(
                title="🎭 IMPOSTER-EINLADUNG",
                description=(
                    f"**{interaction.user.display_name}** "
                    "startet eine Runde **Wer ist der Imposter?**\n\n"
                    "Du bist aktuell im selben Voice-Channel "
                    "und wurdest zur Runde eingeladen.\n\n"
                    "Willst du mitspielen?"
                ),
                color=discord.Color.blurple()
            )

            embed.add_field(
                name="👥 Mindestspieler",
                value="3",
                inline=True
            )

            embed.add_field(
                name="🎯 Kategorie",
                value=game["category"],
                inline=True
            )

            await member.send(
                embed=embed,
                view=ImposterInviteView(
                    guild.id,
                    member.id
                )
            )

        except discord.Forbidden:

            print(
                f"[IMPOSTER] DMs deaktiviert: {member}"
            )

        except Exception as error:

            print(
                f"[IMPOSTER] Einladung Fehler: "
                f"{member} | {error!r}"
            )

    await imposter_update_lobby(
        game
    )
# ============================================================
# START
# ============================================================

print("=" * 70)
print("🚀 ReVu Ticket Manager startet...")
print(f"📁 BASE_DIR: {BASE_DIR}")
print(f"🤖 Gemini: {GEMINI_MODEL}")
print(f"📜 Rules: {RULES_FILE}")
print(f"🧠 Brain: {brain.__file__}")
print(
    f"🧠 Brain-Version: "
    f"{getattr(brain, 'VERSION', 'UNBEKANNT')}"
)
print("=" * 70)


bot.run(
    DISCORD_TOKEN
)