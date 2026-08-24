"""Configuration loading and validation using Pydantic."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class ModemConfig(BaseModel):
    """Huawei E3372 HiLink modem configuration."""

    base_url: str = Field("http://192.168.8.1", description="HiLink web API base URL")
    request_timeout_seconds: int = Field(10, ge=1, le=120, description="HTTP request timeout")


class PollConfig(BaseModel):
    """SMS inbox polling configuration."""

    interval_seconds: int = Field(
        30, ge=5, le=3600, description="Interval between inbox polls (seconds)"
    )


class FilterConfig(BaseModel):
    """SMS filtering configuration."""

    exclude_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns matched against SMS content; matching messages are discarded without a Trello card",
    )


class TrelloConfig(BaseModel):
    """Trello card creation configuration."""

    key: str = Field(..., min_length=1, description="Trello API key")
    token: str = Field(..., min_length=1, description="Trello API token")
    list_id: str = Field(..., min_length=1, description="Destination Trello list ID for new cards")
    card_name_template: str = Field(
        "SMS from {phone}",
        description="Template for the Trello card title, supports {phone} and {date}. "
        "{phone} must appear before any other numeric placeholder for the reply feature "
        "to reliably recover the phone number from the card title.",
    )
    card_desc_template: str = Field(
        "{content}",
        description="Template for the Trello card description, supports {phone}, {date}, {content}",
    )
    card_comment_template: str = Field(
        "{content}",
        description="Template for comments added to an existing card for the same phone number, "
        "supports {phone}, {date}, {content}",
    )


class MmsConfig(BaseModel):
    """MMS auto-reply configuration.

    The E3372 HiLink modem cannot retrieve MMS content; an incoming MMS
    surfaces in the inbox as a message with an empty Content but a valid
    sender Phone. When detected, we reply with a message telling the sender
    to resend as plain text instead.
    """

    enabled: bool = Field(True, description="Whether to auto-reply to detected MMS")
    reply_text: str = Field(
        "Ce numéro ne lit pas les MMS (photos, pièces jointes, ou textes trop "
        "longs que le téléphone envoie en MMS). Merci de renvoyer en SMS texte, "
        "au besoin en plusieurs messages, ou par email à "
        "contact@caenfalaiseplaneurs.fr",
        description="Auto-reply SMS sent to the sender of a detected MMS",
    )


class ReplyConfig(BaseModel):
    """Trello-comment-driven SMS reply configuration.

    A team member replies to an SMS conversation by writing a comment on the
    card containing the trigger marker; everything after the marker (on the
    same or following lines) is sent as the SMS body, and anything before it
    is left untouched as free-form attribution/notes. Trello only lets the
    original author of a comment edit it, and replies may come from any team
    member, so after a send attempt the daemon posts a *new* comment (always
    postable by its own token) referencing the trigger comment's ID, instead
    of editing the trigger comment itself.
    """

    enabled: bool = Field(True, description="Whether to poll Trello comments for SMS replies")
    trigger: str = Field(
        ">>RE:",
        min_length=1,
        description="Marker in a comment; text after the first occurrence is the SMS body",
    )
    case_insensitive: bool = Field(True, description="Match the trigger case-insensitively")
    poll_interval_seconds: int = Field(
        30, ge=5, le=3600, description="Interval between reply comment polls (seconds)"
    )
    sent_marker: str = Field(
        "[Réponse envoyée",
        description="Substring identifying a status comment recording a sent reply",
    )
    sent_tag_template: str = Field(
        "[Réponse envoyée le {date} a {time}]",
        description="Posted as a new comment once a reply SMS is sent, supports {date}, {time}",
    )
    failure_marker: str = Field(
        "[Echec d'envoi",
        description="Substring identifying a status comment recording a send failure",
    )
    failure_tag_template: str = Field(
        "[Echec d'envoi, nouvelle tentative en cours]",
        description="Posted as a new comment (once) when a reply SMS fails to send",
    )
    sqlite_path: str = Field(
        "/var/lib/pi-sms/reply.sqlite",
        min_length=1,
        description="SQLite file for the reply comment cursor and send state",
    )


class DebugConfig(BaseModel):
    """Debug configuration for development/testing.

    Debug mode is enabled via the DEBUG_MODE environment variable.
    This config only provides interval overrides when debug mode is active.
    """

    poll_interval_seconds: int = Field(5, ge=1, le=3600, description="Poll interval in debug mode")


class Config(BaseModel):
    """Root configuration model."""

    modem: ModemConfig = Field(default_factory=ModemConfig)
    poll: PollConfig = Field(default_factory=PollConfig)
    filter: FilterConfig = Field(default_factory=FilterConfig)
    mms: MmsConfig = Field(default_factory=MmsConfig)
    trello: TrelloConfig
    reply: ReplyConfig = Field(default_factory=ReplyConfig)
    debug: DebugConfig | None = Field(None, description="Optional debug configuration")


def format_validation_errors(error: ValidationError) -> str:
    """Format Pydantic validation errors into user-friendly messages.

    Args:
        error: Pydantic ValidationError instance

    Returns:
        Formatted error message string
    """
    lines = ["Configuration validation failed:"]
    for err in error.errors():
        field_path = " -> ".join(str(loc) for loc in err["loc"])
        error_msg = err.get("msg", "")
        if field_path:
            lines.append(f"  - {field_path}: {error_msg}")
        else:
            lines.append(f"  - {error_msg}")

    lines.append("")
    lines.append("For help configuring your config.yaml file:")
    lines.append("  - See config.example.yaml for a complete example configuration")
    lines.append("  - See README.md 'Configuration' section for detailed documentation")

    return "\n".join(lines)


def load_config(config_path: str | None = None) -> Config:
    """Load and validate configuration from YAML file.

    Args:
        config_path: Path to configuration file. If None, uses CONFIG_PATH env var or default.

    Returns:
        Validated Config instance

    Raises:
        FileNotFoundError: If config file does not exist
        ValidationError: If config validation fails (formatted error message is printed)
    """
    if config_path is None:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    print("Validating configuration...")

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config_obj = Config.model_validate(data)
        print("Configuration validated successfully")
        return config_obj
    except ValidationError as e:
        error_msg = format_validation_errors(e)
        print(error_msg)
        raise


def validate_config(config: dict) -> Config:
    """Validate configuration dictionary."""
    return Config.model_validate(config)
