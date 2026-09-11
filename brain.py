import json
import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional


# ============================================================
# RE:VU BRAIN
# Freie Gesprächsanalyse – keine Keyword-Gates
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
RULES_FILE = BASE_DIR / "rules.json"

VERSION = "6.0-free-context"
BRAIN_VERSION = VERSION


# Gemini callback:
# await ai_call(prompt) -> dict
AI_CALL = Callable[[str], Awaitable[Any]]


# ============================================================
# ZEIT
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# TEXT
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                default=str
            )
        except Exception:
            text = str(value)

    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\x00", " ")
    return text.strip()


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0

    if number < 0:
        return 0.0

    if number > 1:
        return 1.0

    return number


# ============================================================
# JSON
# ============================================================

def safe_json_load(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback

        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return fallback


# ============================================================
# RULES
# ============================================================

def _find_rule_text(data: Dict[str, Any]) -> str:
    possible = (
        "regel",
        "rule",
        "text",
        "description",
        "beschreibung",
        "content",
        "inhalt",
    )

    for key in possible:
        value = data.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _find_consequence(data: Dict[str, Any]) -> str:
    possible = (
        "consequence",
        "konsequenz",
        "folge",
        "strafe",
        "punishment",
        "action",
    )

    for key in possible:
        value = data.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _find_id(data: Dict[str, Any]) -> str:
    possible = (
        "id",
        "rule_id",
        "regel_id",
        "nummer",
        "nr",
    )

    for key in possible:
        value = data.get(key)

        if value is not None:
            text = clean_text(value)

            if text:
                return text

    return ""


def _normalize_rule(rule: Any, fallback_id: str = "") -> Optional[Dict[str, Any]]:
    if isinstance(rule, str):
        text = rule.strip()

        if not text:
            return None

        return {
            "id": fallback_id,
            "text": text,
            "consequence": "",
            "raw": rule,
        }

    if not isinstance(rule, dict):
        return None

    rule_id = _find_id(rule) or fallback_id
    text = _find_rule_text(rule)
    consequence = _find_consequence(rule)

    if not text:
        # Manche rules.json benutzen verschachtelte Strukturen.
        for key, value in rule.items():
            if isinstance(value, str) and value.strip():
                if key.lower() not in {
                    "id",
                    "rule_id",
                    "regel_id",
                    "nummer",
                    "nr",
                }:
                    text = value.strip()
                    break

    if not text and not rule_id:
        return None

    return {
        "id": rule_id,
        "text": text,
        "consequence": consequence,
        "raw": deepcopy(rule),
    }


def _collect_rules(data: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []

    if isinstance(data, list):
        for index, item in enumerate(data, start=1):
            normalized = _normalize_rule(
                item,
                fallback_id=str(index)
            )

            if normalized:
                result.append(normalized)

        return result

    if isinstance(data, dict):

        # Direkte Regelobjekte
        direct = _normalize_rule(data)

        if direct and (
            direct.get("text")
            or direct.get("id")
        ):
            result.append(direct)

        # Typische Container
        for key in (
            "rules",
            "regel",
            "regeln",
            "items",
            "entries",
            "data",
        ):
            value = data.get(key)

            if isinstance(value, (list, dict)):
                nested = _collect_rules(value)

                for rule in nested:
                    if rule not in result:
                        result.append(rule)

        # Dictionary mit Rule-IDs als Keys
        if not result:
            for key, value in data.items():

                if isinstance(value, dict):
                    normalized = _normalize_rule(
                        value,
                        fallback_id=str(key)
                    )

                    if normalized:
                        result.append(normalized)

                elif isinstance(value, str):
                    result.append({
                        "id": str(key),
                        "text": value.strip(),
                        "consequence": "",
                        "raw": value,
                    })

    return result


def load_rules() -> List[Dict[str, Any]]:
    data = safe_json_load(RULES_FILE, [])

    rules = _collect_rules(data)

    # IDs eindeutig machen
    seen = set()
    clean_rules = []

    for index, rule in enumerate(rules, start=1):
        rule_id = clean_text(rule.get("id"))

        if not rule_id:
            rule_id = str(index)

        if rule_id in seen:
            continue

        seen.add(rule_id)

        rule["id"] = rule_id

        clean_rules.append(rule)

    return clean_rules


def get_rules_for_display() -> List[Dict[str, Any]]:
    return load_rules()


def get_rule_by_id(rule_id: Any) -> Optional[Dict[str, Any]]:
    wanted = clean_text(rule_id)

    if not wanted:
        return None

    for rule in load_rules():
        if clean_text(rule.get("id")) == wanted:
            return rule

    return None


def valid_rule_id(rule_id: Any) -> bool:
    return get_rule_by_id(rule_id) is not None


def rules_to_prompt(rules: List[Dict[str, Any]]) -> str:
    if not rules:
        return "ES SIND KEINE REGELN GELADEN."

    lines = []

    for rule in rules:
        rule_id = clean_text(rule.get("id"))
        text = clean_text(rule.get("text"))
        consequence = clean_text(rule.get("consequence"))

        line = f"REGEL-ID: {rule_id}\nREGEL: {text}"

        if consequence:
            line += f"\nKONSEQUENZ: {consequence}"

        lines.append(line)

    return "\n\n".join(lines)


# ============================================================
# STATE
# ============================================================

def new_case_state() -> Dict[str, Any]:
    timestamp = now_iso()

    return {
        "version": 6,

        "phase": "collecting",
        "overall_status": "investigation",
        "next_action": "listen",

        "accused_user_id": None,
        "accused_user_name": None,

        "suggested_rule_id": None,
        "confirmed_rule_id": None,
        "final_rule_id": None,

        "rule_confirmation_pending": False,
        "evidence_confirmation_pending": False,

        "confidence": 0.0,
        "explanation": "",

        "consequence": "",

        "analysis_count": 0,
        "analysis_status": "waiting",
        "analysis_summary": "",

        "last_user_message": "",
        "last_conversation": "",

        "_last_ui_action": None,

        "created_at": timestamp,
        "updated_at": timestamp,
    }


# ============================================================
# RECOVERY
# ============================================================

def apply_recovery(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return new_case_state()

    base = new_case_state()

    for key, value in state.items():
        base[key] = value

    # Niemals automatisch abgeschlossen lassen,
    # außer finish_case() wurde ausdrücklich benutzt.
    if base.get("phase") == "done":
        return base

    if not base.get("phase"):
        base["phase"] = "collecting"

    if not base.get("overall_status"):
        base["overall_status"] = "investigation"

    if not base.get("next_action"):
        base["next_action"] = "listen"

    return base


# ============================================================
# AI RESULT
# ============================================================

def clean_explanation(value: Any) -> str:
    text = clean_text(value)

    if not text:
        return ""

    # Keine unnötig riesigen AI-Ausgaben im State.
    if len(text) > 2000:
        text = text[:2000].rstrip() + "…"

    return text


def normalize_ai_result(result: Any) -> Dict[str, Any]:

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {
                "status": "needs_context",
                "explanation": result,
            }

    if not isinstance(result, dict):
        return {
            "status": "needs_context",
            "rule_id": None,
            "confidence": 0.0,
            "accused_user": None,
            "explanation": "",
        }

    rule_id = result.get("rule_id")

    if rule_id is not None:
        rule_id = clean_text(rule_id)

    accused = result.get("accused_user")

    if isinstance(accused, dict):
        accused = (
            accused.get("id")
            or accused.get("user_id")
            or accused.get("name")
            or accused.get("username")
        )

    if accused is not None:
        accused = clean_text(accused)

    status = clean_text(
        result.get("status")
        or "needs_context"
    ).lower()

    allowed_statuses = {
        "needs_context",
        "possible_violation",
        "violation",
        "no_violation",
        "uncertain",
        "manual_review",
        "investigation",
    }

    if status not in allowed_statuses:
        status = "needs_context"

    return {
        "status": status,
        "rule_id": rule_id,
        "confidence": clamp_confidence(
            result.get("confidence", 0)
        ),
        "accused_user": accused,
        "explanation": clean_explanation(
            result.get("explanation", "")
        ),
        "consequence": clean_text(
            result.get("consequence", "")
        ),
    }


# ============================================================
# PROMPT
# ============================================================

def build_context_prompt(
    conversation: str,
    rules: List[Dict[str, Any]],
    accused_user_id: Optional[str] = None,
    evidence: Any = None,
) -> str:

    conversation = clean_text(conversation)

    if not conversation:
        conversation = "(Noch keine Gesprächsinhalte.)"

    evidence_text = clean_text(evidence)

    rules_text = rules_to_prompt(rules)

    accused_hint = clean_text(accused_user_id)

    prompt = f"""
Du bist die KI-Analyse für das ReVu Clan-Beschwerdesystem.

DEINE WICHTIGSTE AUFGABE:
Verstehe den gesamten Gesprächsinhalt und entscheide danach,
ob anhand der tatsächlich vorhandenen Informationen eine Regel
aus der bereitgestellten rules.json betroffen sein könnte.

WICHTIG:
- Es gibt KEINE Keyword-Prüfung.
- Es gibt KEINE Keyword-Liste.
- Es gibt KEINE Mindestlänge.
- Eine Nachricht muss nicht das Wort "Beschwerde" enthalten.
- Eine Nachricht muss nicht mit einem bestimmten Satz beginnen.
- Der Nutzer muss den Beschuldigten nicht in der ersten Nachricht nennen.
- Kurze Nachrichten sind gültiger Kontext.
- Umgangssprache ist gültiger Kontext.
- Tippfehler sind gültiger Kontext.
- Pronomen und Verweise müssen anhand des bisherigen Gesprächs verstanden werden.
- Jede Nachricht kann für sich oder zusammen mit früheren Nachrichten wichtig sein.
- Behandle den gesamten Verlauf als EINE Unterhaltung.
- Neue Nachrichten erweitern den bisherigen Kontext.
- Erfinde niemals Personen.
- Erfinde niemals Ereignisse.
- Erfinde niemals Beweise.
- Erfinde niemals Regeln.
- Erfinde niemals Regel-IDs.
- Erfinde niemals Konsequenzen.
- Verwende ausschließlich Regel-IDs aus der unten angegebenen rules.json.
- Wenn der Kontext nicht ausreicht, sage das ehrlich.
- "Nicht genug Informationen" bedeutet NICHT, dass der Fall beendet wird.
- Schließe niemals automatisch einen Fall.
- Eine finale Entscheidung darf nur durch das Bot-System bzw. eine manuelle Aktion erfolgen.

BEISPIELE FÜR GÜLTIGE EINGABEN:
"lisa hat 30m gescammt"
"revolutt hat die base gesprengt"
"ja"
"nein"
"er hat mir das geld noch nicht gegeben"
"das war gestern"
"ich meinte den anderen"
"hab noch nen screenshot"
"ok"

Diese Beispiele sind NUR Beispiele für freie Sprache.
Sie stellen KEINE Keywords oder Trigger dar.

AKTUELLER GESAMTER VERLAUF:
----------------------------
{conversation}
----------------------------

OPTIONALE ZUSATZINFORMATIONEN:
{evidence_text if evidence_text else "(keine separat übergebenen Informationen)"}

OPTIONALE BEKANNTEN-BESCHULDIGTEN-ID:
{accused_hint if accused_hint else "(nicht bekannt)"}

AUTORITATIVE REGELN AUS rules.json:
============================
{rules_text}
============================

AUFGABE:

1. Verstehe zuerst den gesamten Verlauf.
2. Bestimme, was tatsächlich behauptet wird.
3. Bestimme, wer beschuldigt wird, aber nur wenn dies aus dem Verlauf hervorgeht.
4. Prüfe danach semantisch, ob eine der vorhandenen Regeln passt.
5. Wenn eine Regel plausibel betroffen ist, gib deren EXAKTE rule_id zurück.
6. Wenn keine Regel sicher bestimmt werden kann, gib null zurück.
7. Gib keine erfundene Regel-ID zurück.
8. Gib eine realistische Confidence zwischen 0 und 1.
9. Erkläre kurz, warum die Regel passt oder warum weitere Informationen nötig sind.

ANTWORT AUSSCHLIESSLICH ALS JSON:

{{
  "status": "possible_violation",
  "rule_id": "EXAKTE_ID_ODER_NULL",
  "confidence": 0.0,
  "accused_user": "NAME_ODER_ID_ODER_NULL",
  "explanation": "Kurze sachliche Begründung",
  "consequence": "Nur wenn sie exakt aus der Regel hervorgeht, sonst leer"
}}

KEINE Markdown-Codeblöcke.
KEINE zusätzlichen Texte außerhalb des JSON.
"""

    return prompt


# ============================================================
# RULE CONFIRMATION
# ============================================================

def confirm_rule(
    state: Dict[str, Any],
    rule_id: Any,
) -> Dict[str, Any]:

    state = apply_recovery(state)

    rule = get_rule_by_id(rule_id)

    if not rule:
        return state

    state["suggested_rule_id"] = rule["id"]
    state["confirmed_rule_id"] = rule["id"]
    state["final_rule_id"] = rule["id"]

    state["rule_confirmation_pending"] = False

    state["phase"] = "collecting"
    state["overall_status"] = "investigation"
    state["next_action"] = "listen"

    state["consequence"] = clean_text(
        rule.get("consequence")
    )

    state["updated_at"] = now_iso()

    return state


def reject_rule(
    state: Dict[str, Any],
) -> Dict[str, Any]:

    state = apply_recovery(state)

    state["suggested_rule_id"] = None
    state["rule_confirmation_pending"] = False

    state["phase"] = "collecting"
    state["overall_status"] = "investigation"
    state["next_action"] = "listen"

    state["updated_at"] = now_iso()

    return state


# ============================================================
# EVIDENCE
# ============================================================

def set_insufficient_evidence(
    state: Dict[str, Any],
) -> Dict[str, Any]:

    state = apply_recovery(state)

    # WICHTIG:
    # Nicht abschließen.
    state["phase"] = "collecting"
    state["overall_status"] = "investigation"
    state["next_action"] = "listen"

    state["evidence_confirmation_pending"] = False

    state["analysis_status"] = "insufficient_evidence"

    state["updated_at"] = now_iso()

    return state


# ============================================================
# BRAIN TICK
# ============================================================

async def brain_tick(
    state: Dict[str, Any],
    conversation: Optional[str] = None,
    user_message: Optional[str] = None,
    accused_user_id: Optional[str] = None,
    ai_call: Optional[AI_CALL] = None,
    rules_text: str = "",
    evidence: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Any]:

    print("[BRAIN] brain_tick startet")
    print(f"[BRAIN] Version: {VERSION}")
    print("=" * 70)

    state = apply_recovery(state)

    # --------------------------------------------------------
    # KOMPLETTE CONVERSATION BEVORZUGEN
    # --------------------------------------------------------

    if conversation is None:
        conversation = user_message

    conversation = clean_text(conversation)

    if not conversation:
        print("[BRAIN] Keine Conversation vorhanden.")
        state["next_action"] = "listen"
        state["updated_at"] = now_iso()
        return state

    # --------------------------------------------------------
    # OPTIONAL EVIDENCE
    # --------------------------------------------------------

    evidence_text = clean_text(evidence)

    # Nur separat anhängen, wenn es wirklich vorhanden ist.
    # Die normale Conversation bleibt die Hauptquelle.
    analysis_conversation = conversation

    if evidence_text:
        analysis_conversation += (
            "\n\n[SEPARAT ÜBERGEBENE INFORMATIONEN]\n"
            + evidence_text
        )

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    state["last_conversation"] = conversation

    if user_message:
        state["last_user_message"] = clean_text(user_message)

    if accused_user_id:
        state["accused_user_id"] = clean_text(
            accused_user_id
        )

    state["analysis_count"] = (
        int(state.get("analysis_count", 0)) + 1
    )

    state["analysis_status"] = "analyzing"
    state["updated_at"] = now_iso()

    # --------------------------------------------------------
    # RULES
    # --------------------------------------------------------

    rules = load_rules()

    if not rules:
        print("[BRAIN] Keine Regeln gefunden.")

        state["analysis_status"] = "no_rules"
        state["overall_status"] = "investigation"
        state["next_action"] = "listen"
        state["updated_at"] = now_iso()

        return state

    # --------------------------------------------------------
    # AI FEHLT
    # --------------------------------------------------------

    if ai_call is None:
        print("[BRAIN] Keine AI-Funktion übergeben.")

        state["analysis_status"] = "ai_unavailable"
        state["overall_status"] = "investigation"
        state["next_action"] = "listen"
        state["updated_at"] = now_iso()

        return state

    # --------------------------------------------------------
    # PROMPT
    # --------------------------------------------------------

    prompt = build_context_prompt(
        conversation=analysis_conversation,
        rules=rules,
        accused_user_id=accused_user_id
        or state.get("accused_user_id"),
        evidence=evidence,
    )

    # --------------------------------------------------------
    # AI CALL
    # --------------------------------------------------------

    try:
        raw_result = await ai_call(prompt)

    except Exception as exc:
        print(
            "[BRAIN] AI-Fehler:",
            repr(exc)
        )

        # Niemals Case löschen.
        # Niemals Case schließen.
        state["analysis_status"] = "ai_error"
        state["overall_status"] = "investigation"
        state["next_action"] = "listen"
        state["updated_at"] = now_iso()

        return state

    # --------------------------------------------------------
    # NORMALIZE
    # --------------------------------------------------------

    result = normalize_ai_result(raw_result)

    print(
        "[BRAIN] AI result:",
        json.dumps(
            result,
            ensure_ascii=False
        )
    )

    rule_id = result.get("rule_id")
    confidence = result.get("confidence", 0.0)
    status = result.get("status", "needs_context")

    accused = result.get("accused_user")

    explanation = result.get("explanation", "")
    consequence = result.get("consequence", "")

    # --------------------------------------------------------
    # ACCUSED
    # --------------------------------------------------------

    if accused:
        state["accused_user_name"] = accused

    # --------------------------------------------------------
    # ANALYSE
    # --------------------------------------------------------

    state["confidence"] = confidence
    state["explanation"] = explanation
    state["analysis_summary"] = explanation
    state["analysis_status"] = status

    if consequence:
        state["consequence"] = consequence

    # --------------------------------------------------------
    # REGEL VALIDIEREN
    # --------------------------------------------------------

    rule = None

    if rule_id:
        rule = get_rule_by_id(rule_id)

        if rule is None:
            print(
                "[BRAIN] AI hat ungültige Regel-ID geliefert:",
                rule_id
            )

            rule_id = None

    # --------------------------------------------------------
    # BEREITS BESTÄTIGTE REGEL
    # --------------------------------------------------------

    confirmed_rule = state.get("confirmed_rule_id")

    if confirmed_rule:
        existing_rule = get_rule_by_id(
            confirmed_rule
        )

        if existing_rule:
            state["final_rule_id"] = existing_rule["id"]
            state["consequence"] = clean_text(
                existing_rule.get("consequence")
            )

            # Eine bestätigte Regel bleibt bestehen,
            # aber neue Nachrichten werden weiter analysiert.
            state["phase"] = "collecting"
            state["overall_status"] = "investigation"
            state["next_action"] = "listen"

            state["rule_confirmation_pending"] = False

            state["updated_at"] = now_iso()

            return state

    # --------------------------------------------------------
    # KEINE REGEL ERKANNT
    # --------------------------------------------------------

    if not rule:
        state["suggested_rule_id"] = None
        state["rule_confirmation_pending"] = False

        state["phase"] = "collecting"
        state["overall_status"] = "investigation"
        state["next_action"] = "listen"

        state["updated_at"] = now_iso()

        return state

    # --------------------------------------------------------
    # REGEL GEFUNDEN
    # --------------------------------------------------------

    state["suggested_rule_id"] = rule["id"]

    state["consequence"] = clean_text(
        rule.get("consequence")
    )

    state["phase"] = "collecting"
    state["overall_status"] = "investigation"

    # Nur bei einer sinnvollen semantischen Trefferqualität
    # eine Bestätigung anzeigen.
    #
    # Das ist KEIN Keyword-Gate.
    if confidence >= 0.55:

        already_pending = (
            state.get("rule_confirmation_pending")
            and state.get("suggested_rule_id") == rule["id"]
        )

        if not already_pending:
            state["rule_confirmation_pending"] = True
            state["next_action"] = "confirm_rule"
        else:
            state["next_action"] = "listen"

    else:
        state["rule_confirmation_pending"] = False
        state["next_action"] = "listen"

    state["updated_at"] = now_iso()

    return state


# ============================================================
# MANUELLES ABSCHLIESSEN
# ============================================================

def finish_case(
    state: Dict[str, Any],
    final_status: str = "resolved",
) -> Dict[str, Any]:

    state = apply_recovery(state)

    state["phase"] = "done"
    state["overall_status"] = final_status
    state["next_action"] = "done"

    state["rule_confirmation_pending"] = False
    state["evidence_confirmation_pending"] = False

    state["updated_at"] = now_iso()

    return state


# ============================================================
# SUMMARY
# ============================================================

def get_state_summary(
    state: Dict[str, Any]
) -> Dict[str, Any]:

    state = apply_recovery(state)

    rule = get_rule_by_id(
        state.get("confirmed_rule_id")
        or state.get("suggested_rule_id")
    )

    return {
        "phase": state.get("phase"),
        "status": state.get("overall_status"),
        "next_action": state.get("next_action"),
        "accused_user_id": state.get("accused_user_id"),
        "accused_user_name": state.get("accused_user_name"),
        "suggested_rule_id": state.get("suggested_rule_id"),
        "confirmed_rule_id": state.get("confirmed_rule_id"),
        "confidence": state.get("confidence", 0.0),
        "explanation": state.get("explanation", ""),
        "consequence": (
            clean_text(rule.get("consequence"))
            if rule
            else state.get("consequence", "")
        ),
        "analysis_count": state.get("analysis_count", 0),
        "analysis_status": state.get("analysis_status"),
    }