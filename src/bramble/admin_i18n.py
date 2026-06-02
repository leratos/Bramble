"""Translation catalog for the Bramble admin UI.

The admin UI is English-first with German as an option, selected once at
startup via :class:`bramble.admin_config.AdminConfig.language` (CLI
``--language``, env ``BRAMBLE_ADMIN_LANGUAGE``, default ``en``). A
:func:`make_translator` callable is exposed to Jinja as the ``t`` global so
templates use ``{{ t("key") }}`` instead of hardcoded text; missing keys
fall back to English and then to the key itself, so a typo renders visibly
rather than crashing.

The status values (``in_arbeit``/``abgeschlossen``/``notiz``/``bugfix``),
tag names and link relations are part of the data contract and are NOT in
this catalog — they are displayed verbatim.
"""

from __future__ import annotations

from collections.abc import Callable

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "de")

# key -> {language -> text}. English is authoritative; German is the option.
_STRINGS: dict[str, dict[str, str]] = {
    # Navigation / chrome
    "nav_main": {"en": "Main navigation", "de": "Hauptnavigation"},
    "nav_dashboard": {"en": "Dashboard", "de": "Dashboard"},
    "nav_projects": {"en": "Projects", "de": "Projekte"},
    "nav_search": {"en": "Search", "de": "Suche"},
    "nav_help": {"en": "Help", "de": "Hilfe"},
    "nav_tokens": {"en": "Tokens", "de": "Token"},
    "action_logout": {"en": "Log out", "de": "Logout"},
    "aria_project_list": {"en": "Project list", "de": "Projektliste"},
    "entries": {"en": "entries", "de": "Einträge"},
    "empty_no_projects": {"en": "No projects yet.", "de": "Noch keine Projekte."},
    # Dashboard
    "eyebrow_operations": {"en": "Operations", "de": "Betrieb"},
    "aria_statistics": {"en": "Statistics", "de": "Statistik"},
    "metric_projects": {"en": "Projects", "de": "Projekte"},
    "metric_entries": {"en": "Entries", "de": "Einträge"},
    "metric_24h": {"en": "24 hours", "de": "24 Stunden"},
    "metric_7d": {"en": "7 days", "de": "7 Tage"},
    "metric_30d": {"en": "30 days", "de": "30 Tage"},
    "aria_context_snapshot": {"en": "Context snapshot", "de": "Kontext-Snapshot"},
    "metric_open_items": {"en": "Open items", "de": "Offene Punkte"},
    "metric_7d_bugfixes": {"en": "Bugfixes (7 days)", "de": "7 Tage Bugfixes"},
    "metric_7d_decisions": {"en": "Decisions (7 days)", "de": "7 Tage Entscheidungen"},
    "metric_active_projects_7d": {
        "en": "Active projects (7 days)",
        "de": "Aktive Projekte 7 Tage",
    },
    "heading_recent_open": {
        "en": "Newest open work items",
        "de": "Neueste offene Arbeitspunkte",
    },
    "empty_no_open": {"en": "No open items.", "de": "Keine offenen Punkte."},
    "heading_recent_entries": {"en": "Latest entries", "de": "Letzte Einträge"},
    "aria_tags": {"en": "Tags", "de": "Tags"},
    "aria_links": {"en": "Links", "de": "Links"},
    "aria_backlinks": {"en": "Backlinks", "de": "Backlinks"},
    "empty_no_entries": {
        "en": "No journal entries yet.",
        "de": "Noch keine Journal-Einträge.",
    },
    # Project page
    "eyebrow_project": {"en": "Project", "de": "Projekt"},
    "last_entry": {"en": "last entry", "de": "letzter Eintrag"},
    "no_entries_yet": {"en": "no entries yet", "de": "noch keine Einträge"},
    "label_lifecycle": {"en": "Lifecycle", "de": "Lifecycle"},
    "heading_project_lifecycle": {"en": "Project lifecycle", "de": "Projekt-Lifecycle"},
    "text_lifecycle_help": {
        "en": "Toggle the project status. This affects only the registry, not the journal entries.",
        "de": "Status des Projekts umschalten. Dies betrifft nur die Registry, nicht die Journal-Einträge.",
    },
    "aria_current_status": {"en": "Current status", "de": "Aktueller Status"},
    "unknown": {"en": "unknown", "de": "unbekannt"},
    "aria_status_change": {"en": "Status change", "de": "Statuswechsel"},
    "label_search": {"en": "Search", "de": "Suche"},
    "placeholder_fts": {"en": "FTS5 search", "de": "FTS5-Suche"},
    "action_search": {"en": "Search", "de": "Suchen"},
    "action_reset": {"en": "Reset", "de": "Zurücksetzen"},
    "aria_project_context": {"en": "Project context", "de": "Projektkontext"},
    "metric_recent_bugfixes": {"en": "Recent bugfixes", "de": "Letzte Bugfixes"},
    "metric_recent_decisions": {"en": "Recent decisions", "de": "Letzte Entscheidungen"},
    "heading_recent_open_items": {"en": "Newest open items", "de": "Neueste offene Punkte"},
    "heading_workflow_completion": {
        "en": "Entry completion workflow",
        "de": "Workflow für Eintragsabschluss",
    },
    "text_workflow_help": {
        "en": "Recommended status values and DoD for this project.",
        "de": "Empfohlene Statuswerte und DoD für dieses Projekt.",
    },
    "aria_status_recommendation": {
        "en": "Status recommendation",
        "de": "Status-Empfehlung",
    },
    "aria_tag_suggestions": {"en": "Tag suggestions", "de": "Tag-Vorschläge"},
    "heading_correction_assistant": {
        "en": "Correction assistant",
        "de": "Korrektur-Assistent",
    },
    "text_correction_help": {
        "en": "Create an append-only journal entry.",
        "de": "Append-only Journal-Eintrag erstellen.",
    },
    "action_create_note": {"en": "Create note", "de": "Nachtrag erstellen"},
    "action_create_bugfix": {"en": "Create bugfix", "de": "Bugfix erstellen"},
    "msg_entry_created": {
        "en": "Journal entry created:",
        "de": "Journal-Eintrag erstellt:",
    },
    "label_status": {"en": "Status", "de": "Status"},
    "label_title": {"en": "Title", "de": "Titel"},
    "label_phase": {"en": "Phase", "de": "Phase"},
    "label_content": {"en": "Content", "de": "Inhalt"},
    "label_tags": {"en": "Tags", "de": "Tags"},
    "label_reference_id": {"en": "Reference entry id", "de": "Bezugs-Eintrags-ID"},
    "text_relation_adds_context": {
        "en": "Relation for the id: adds_context_to",
        "de": "Relation bei ID: adds_context_to",
    },
    "action_create_entry": {"en": "Create journal entry", "de": "Journal-Eintrag erstellen"},
    "label_corrected_id": {"en": "Corrected entry id", "de": "Korrigierte Eintrags-ID"},
    "text_relation_corrects": {
        "en": "Relation for the id: corrects",
        "de": "Relation bei ID: corrects",
    },
    "action_submit_bugfix": {"en": "Submit bugfix", "de": "Bugfix eintragen"},
    "aria_correction_actions": {"en": "Correction actions", "de": "Korrekturaktionen"},
    "action_note": {"en": "Note", "de": "Nachtrag"},
    "action_bugfix": {"en": "Bugfix", "de": "Bugfix"},
    "heading_search_results": {"en": "Search results", "de": "Suchergebnisse"},
    "heading_journal": {"en": "Journal", "de": "Journal"},
    "empty_no_matches": {"en": "No matching entries.", "de": "Keine passenden Einträge."},
    # Global search
    "page_global_search": {"en": "Global search", "de": "Globale Suche"},
    "eyebrow_search": {"en": "Search", "de": "Suche"},
    "text_search_help": {
        "en": "Cross-project full-text search with status and time-range filters.",
        "de": "Projektübergreifende Volltextsuche mit Status- und Zeitraum-Filter.",
    },
    "label_query": {"en": "Query", "de": "Query"},
    "label_project": {"en": "Project", "de": "Projekt"},
    "option_all": {"en": "all", "de": "alle"},
    "label_timerange": {"en": "Time range", "de": "Zeitraum"},
    "aria_active_tag_filters": {"en": "Active tag filters", "de": "Aktive Tagfilter"},
    "heading_hits": {"en": "Hits", "de": "Treffer"},
    "heading_no_search": {
        "en": "No search started yet",
        "de": "Noch keine Suche gestartet",
    },
    "text_start_search": {
        "en": "Start a search with a query and optional filters.",
        "de": "Starte eine Suche mit Query und optionalen Filtern.",
    },
    # Tokens
    "page_tokens": {"en": "Tokens", "de": "Token"},
    "eyebrow_project_access": {"en": "Project access", "de": "Projektzugriff"},
    "text_tokens_never_shown": {
        "en": "Existing token values are never shown.",
        "de": "Bestehende Tokenwerte werden nie angezeigt.",
    },
    "token_action_created": {"en": "created", "de": "erzeugt"},
    "token_action_rotated": {"en": "rotated", "de": "rotiert"},
    "token_action_revoked": {"en": "removed", "de": "entfernt"},
    "label_for": {"en": "for", "de": "für"},
    "text_new_token_once": {
        "en": "New token, shown once",
        "de": "Neuer Token, einmalig sichtbar",
    },
    "text_restart_required": {
        "en": "The change takes effect only after restarting the MCP service.",
        "de": "Aenderung wird erst nach einem Neustart des MCP-Service aktiv.",
    },
    "aria_token_management": {"en": "Token management", "de": "Tokenverwaltung"},
    "heading_new_token": {"en": "New project token", "de": "Neues Projekt-Token"},
    "action_create_token": {"en": "Create token", "de": "Token erzeugen"},
    "heading_active_tokens": {"en": "Active project tokens", "de": "Aktive Projekt-Token"},
    "text_token_present": {"en": "Token present", "de": "Token vorhanden"},
    "action_rotate": {"en": "Rotate", "de": "Rotieren"},
    "action_remove": {"en": "Remove", "de": "Entfernen"},
    "empty_no_tokens": {"en": "No project tokens yet.", "de": "Noch keine Projekt-Token."},
    "heading_projects_without_token": {
        "en": "Journal projects without a token",
        "de": "Journal-Projekte ohne Token",
    },
    # Login
    "text_internal_access": {"en": "Internal access", "de": "Interner Zugriff"},
    "label_user": {"en": "User", "de": "User"},
    "label_password": {"en": "Password", "de": "Passwort"},
    "action_login": {"en": "Log in", "de": "Einloggen"},
    # Help
    "page_help": {"en": "Help", "de": "Hilfe"},
    "text_help_intro": {
        "en": "A short guide to journal work and the admin UI.",
        "de": "Kurzer Leitfaden für Journal-Arbeit und Admin-UI.",
    },
    "heading_workflow_notes": {"en": "Workflow notes", "de": "Workflow-Hinweise"},
    "text_help_appendonly": {
        "en": "Journal entries are append-only. Existing entries are never edited or deleted; corrections are added as a new note or bugfix.",
        "de": "Journal-Einträge bleiben append-only. Bestehende Einträge werden nicht editiert oder gelöscht; Korrekturen werden als neuer Nachtrag oder Bugfix ergänzt.",
    },
    "label_status_recommendation": {
        "en": "Status recommendation:",
        "de": "Status-Empfehlung:",
    },
    "label_tag_suggestions": {"en": "Tag suggestions:", "de": "Tag-Vorschläge:"},
    "label_completion_checklist": {
        "en": "Completion checklist:",
        "de": "Abschluss-Checkliste:",
    },
    "heading_create_entries": {"en": "Creating entries", "de": "Einträge erstellen"},
    "text_help_assistant": {
        "en": "The correction assistant on every project page creates new journal entries directly in that project.",
        "de": "Der Korrektur-Assistent auf jeder Projektseite erstellt neue Journal-Einträge direkt in diesem Projekt.",
    },
    "text_help_note": {
        "en": "Note: uses the status notiz and can add context to an existing entry via a reference id.",
        "de": "Nachtrag: nutzt den Status notiz und kann per Bezugs-ID Kontext zu einem bestehenden Eintrag ergänzen.",
    },
    "text_help_bugfix": {
        "en": "Bugfix: uses the status bugfix and can set a corrects relation via a corrected entry id.",
        "de": "Bugfix: nutzt den Status bugfix und kann per korrigierter Eintrags-ID eine corrects-Relation setzen.",
    },
    "text_help_action_links": {
        "en": "Action links on entries pre-fill the matching reference id automatically.",
        "de": "Aktionslinks an Einträgen füllen die passende Bezugs-ID automatisch vor.",
    },
    "heading_help_open_items": {"en": "Open items", "de": "Offene Punkte"},
    "text_help_open_1": {
        "en": "The metric shows all effectively open items. The lists are deliberately limited previews: the dashboard shows the newest 10, project pages the newest 5.",
        "de": "Die Kennzahl zeigt alle effektiv offenen Punkte. Die Listen sind bewusst begrenzte Vorschauen: Dashboard zeigt die neuesten 10, Projektseiten zeigen die neuesten 5.",
    },
    "text_help_open_2": {
        "en": "An open item counts as closed when a newer completion, bugfix or note clearly covers it via link, text reference, phase, title or base title.",
        "de": "Ein offener Punkt gilt als geschlossen, wenn ein neuerer Abschluss, Bugfix oder Nachtrag ihn eindeutig per Link, Textverweis, Phase, Titel oder Basis-Titel abdeckt.",
    },
    "heading_search_filter": {"en": "Search and filters", "de": "Suche und Filter"},
    "text_help_search_1": {
        "en": "Global search uses SQLite FTS5. Status, project, time range and tags narrow the hits; multiple tags are applied together.",
        "de": "Die globale Suche nutzt SQLite FTS5. Status, Projekt, Zeitraum und Tags schränken Treffer ein; mehrere Tags werden gemeinsam angewendet.",
    },
    "text_help_search_2": {
        "en": "Plain words are enough for a simple search. For exact phrases, put terms in quotes.",
        "de": "Für einfache Suche reichen normale Wörter. Für exakte Formulierungen können Begriffe in Anführungszeichen gesetzt werden.",
    },
    # Definition-of-Done checklist (produced by admin_read_model as keys)
    "dod_committed": {"en": "Code/config committed", "de": "Code/Config committed"},
    "dod_tests": {
        "en": "Relevant tests or smoke checks run",
        "de": "Relevante Tests oder Smoke-Checks gelaufen",
    },
    "dod_journal": {
        "en": "Append-only journal entry written",
        "de": "Append-only Journal-Eintrag geschrieben",
    },
    "dod_next_step": {
        "en": "Next step explicitly documented",
        "de": "Naechster Schritt explizit dokumentiert",
    },
}


def normalize_language(value: str | None) -> str:
    """Return a supported language code, defaulting to English."""

    if value is None:
        return DEFAULT_LANGUAGE
    candidate = value.strip().lower()
    return candidate if candidate in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def make_translator(language: str) -> Callable[[str], str]:
    """Return a ``t(key)`` callable for ``language`` with English fallback."""

    lang = normalize_language(language)

    def translate(key: str) -> str:
        entry = _STRINGS.get(key)
        if entry is None:
            return key
        return entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or key

    return translate
