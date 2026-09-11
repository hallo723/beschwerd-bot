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