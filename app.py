from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import queue
import random
import re
import shutil
import sqlite3
import subprocess
import struct
import sys
import tempfile
import threading
import webbrowser
import time
import uuid
import weakref
import zipfile
import secrets
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from companion import CompanionBridge, ensure_extension_files


APP_NAME = "Vórtice"
APP_VERSION = "4.4.0"
APP_EDITION = "Public Beta"
REPO_URL = "https://github.com/joao-araujoo/vortice"
PUBLIC_RELEASE = True

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)).resolve()
else:
    APP_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = APP_DIR

# User state lives outside the release folder so upgrading Vórtice never loses
# projects, branches, prompts or context ZIPs. Portable fallback is kept for non-Windows.
if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
    STATE_DIR = Path(os.environ["LOCALAPPDATA"]) / "Vortice"
else:
    STATE_DIR = APP_DIR
DATA_DIR = STATE_DIR / "data"
OUTPUT_DIR = STATE_DIR / "outputs"
BACKUP_DIR = STATE_DIR / "backups"
TMP_DIR = STATE_DIR / "tmp"
PROJECTS_FILE = DATA_DIR / "projects.json"
HISTORY_FILE = DATA_DIR / "history.json"
DRAFTS_FILE = DATA_DIR / "drafts.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
AGENT_CHATS_FILE = DATA_DIR / "agent-chats.json"
CHECKLIST_FILE = DATA_DIR / "project-checklists.json"
PROJECT_META_FILE = DATA_DIR / "project-meta.json"
SKILLS_DIR = STATE_DIR / "skills"
GLOBAL_SKILLS_DIR = SKILLS_DIR / "global"
PROJECT_SKILLS_DIR = SKILLS_DIR / "projects"
CHECKLIST_ASSETS_DIR = STATE_DIR / "checklist-assets"
COMPANION_EXTENSION_DIR = STATE_DIR / "companion-extension"


def _read_json_list(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    except Exception:
        return []


def migrate_legacy_state() -> None:
    if STATE_DIR.resolve() == APP_DIR.resolve():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    marker = STATE_DIR / ".legacy-import-v2.done"
    if marker.exists():
        return

    candidates: list[Path] = [APP_DIR]
    try:
        parent = APP_DIR.parent
        siblings = list(parent.glob("Vortice-Desktop-v*")) + list(parent.glob("AI-Dev-Bridge-Desktop-v*"))
        siblings = [item for item in siblings if item.is_dir() and item.resolve() != APP_DIR.resolve()]
        siblings.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        candidates.extend(siblings[:12])
    except Exception:
        pass

    all_projects: dict[str, dict] = {}
    all_history: dict[str, dict] = {}
    # Old releases first; any already-persisted state wins last.
    for root in reversed(candidates):
        for item in _read_json_list(root / "data" / "projects.json"):
            key = str(item.get("id") or "").strip()
            if key:
                all_projects[key] = item
        for item in _read_json_list(root / "data" / "history.json"):
            key = str(item.get("id") or "").strip() or hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            all_history[key] = item
        legacy_outputs = root / "outputs"
        if legacy_outputs.is_dir():
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            for source in legacy_outputs.glob("*"):
                if source.is_file() and source.suffix.lower() in {".zip", ".txt"}:
                    target = OUTPUT_DIR / source.name
                    if not target.exists():
                        try:
                            shutil.copy2(source, target)
                        except Exception:
                            pass

    for item in _read_json_list(PROJECTS_FILE):
        key = str(item.get("id") or "").strip()
        if key:
            all_projects[key] = item
    for item in _read_json_list(HISTORY_FILE):
        key = str(item.get("id") or "").strip() or hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        all_history[key] = item

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if all_projects:
        PROJECTS_FILE.write_text(json.dumps(list(all_projects.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if all_history:
        ordered = sorted(all_history.values(), key=lambda item: str(item.get("createdAt") or ""))
        HISTORY_FILE.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        marker.write_text(now_iso() if "now_iso" in globals() else datetime.now().astimezone().isoformat(timespec="seconds"), encoding="utf-8")
    except Exception:
        pass


migrate_legacy_state()
for directory in (DATA_DIR, OUTPUT_DIR, BACKUP_DIR, TMP_DIR, SKILLS_DIR, GLOBAL_SKILLS_DIR, PROJECT_SKILLS_DIR, CHECKLIST_ASSETS_DIR, COMPANION_EXTENSION_DIR):
    directory.mkdir(parents=True, exist_ok=True)
if not PROJECTS_FILE.exists():
    PROJECTS_FILE.write_text("[]\n", encoding="utf-8")
if not HISTORY_FILE.exists():
    HISTORY_FILE.write_text("[]\n", encoding="utf-8")
if not DRAFTS_FILE.exists():
    DRAFTS_FILE.write_text("{}\n", encoding="utf-8")
if not SETTINGS_FILE.exists():
    SETTINGS_FILE.write_text(json.dumps({"agentName": "Vórtex", "autoRollback": True, "agentModel": "", "notificationsEnabled": True, "soundsEnabled": True, "automationMode": "assist", "companionToken": secrets.token_hex(16), "autopilotRetry": True}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if not AGENT_CHATS_FILE.exists():
    AGENT_CHATS_FILE.write_text("{}\n", encoding="utf-8")
if not CHECKLIST_FILE.exists():
    CHECKLIST_FILE.write_text("{}\n", encoding="utf-8")
if not PROJECT_META_FILE.exists():
    PROJECT_META_FILE.write_text("{}\n", encoding="utf-8")

MAX_CONTEXT_FILE = 15 * 1024 * 1024
MAX_RESULT_ZIP = 300 * 1024 * 1024
MAX_AGENT_BACKUP_FILE = 40 * 1024 * 1024
MAX_AGENT_BACKUP_TOTAL = 900 * 1024 * 1024
MAX_SKILL_CONTEXT = 120_000

BLOCKED_SEGMENTS = {
    ".git", "node_modules", ".next", ".nuxt", "dist", "build", "coverage",
    ".cache", ".turbo", ".parcel-cache", ".idea", ".vscode-test", "vendor",
    "__pycache__", ".venv", "venv", "target", "bin", "obj"
}
BLOCKED_BASENAME_PATTERNS = [
    re.compile(r"^\.env(?:\..+)?$", re.I),
    re.compile(r"^credentials?(?:\..+)?$", re.I),
    re.compile(r"^secrets?(?:\..+)?$", re.I),
    re.compile(r"^auth\.json$", re.I),
    re.compile(r"^id_rsa(?:\.pub)?$", re.I),
    re.compile(r"^id_ed25519(?:\.pub)?$", re.I),
    re.compile(r"\.(?:pem|p12|pfx|key|keystore|jks)$", re.I),
]

# Visual identity retained from v1, translated to a native desktop UI.
C = {
    "paper": "#F7F3EC",
    "card": "#FFFDFC",
    "ink": "#1D1C20",
    "muted": "#77736C",
    "line": "#E2DDD4",
    "line_dark": "#C9C2B7",
    "lime": "#D8FF75",
    "lime_dark": "#B6E653",
    "violet": "#7563F6",
    "violet_soft": "#EFECFF",
    "peach": "#FFB18F",
    "cyan": "#91E7F7",
    "red": "#C94C4C",
    "green": "#2B8A61",
    "dark": "#252329",
    "dark2": "#302E35",
    "white": "#FFFFFF",
}

FONT = "Segoe UI"
MONO = "Cascadia Mono"


@dataclass
class Project:
    id: str
    name: str
    paths: list[str]
    created_at: str = ""
    last_used_at: str = ""
    logo_path: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name") or "Projeto"),
            paths=[str(p) for p in data.get("paths", []) if str(p).strip()],
            created_at=str(data.get("createdAt") or data.get("created_at") or ""),
            last_used_at=str(data.get("lastUsedAt") or data.get("last_used_at") or ""),
            logo_path=str(data.get("logoPath") or data.get("logo_path") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "paths": self.paths,
            "createdAt": self.created_at,
            "lastUsedAt": self.last_used_at,
            "logoPath": self.logo_path,
        }


@dataclass
class RootAlias:
    alias: str
    root: Path


@dataclass
class PrepareResult:
    zip_path: Path
    prompt: str
    file_count: int
    summary: str
    notes: list[str]
    usage: Optional[dict] = None
    session_id: str = ""
    prompt_path: Optional[Path] = None
    attachment_count: int = 0
    attachment_paths: list[Path] | None = None
    round_id: str = ""
    round_number: int = 1
    task_title: str = ""
    project_id: str = ""
    result_token: str = ""
    expected_result_name: str = ""


@dataclass
class ApplyResult:
    changed: list[str]
    skipped: list[str]
    backup_path: Path
    checks: list[dict]
    manifest_path: Path


@dataclass
class AgentRunResult:
    response: str
    backup_path: Path
    usage: Optional[dict] = None
    rolled_back: bool = False


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def slug(value: str) -> str:
    value = str(value or "projeto")
    try:
        import unicodedata
        value = unicodedata.normalize("NFD", value)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    except Exception:
        pass
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return value or "projeto"


def load_projects() -> list[Project]:
    try:
        data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [Project.from_dict(item) for item in data if isinstance(item, dict)]
    except Exception:
        return []


def save_projects(projects: list[Project]) -> None:
    temp = PROJECTS_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps([p.to_dict() for p in projects], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(PROJECTS_FILE)


def load_drafts() -> dict[str, dict]:
    try:
        data = json.loads(DRAFTS_FILE.read_text(encoding="utf-8"))
        return {str(key): value for key, value in data.items() if isinstance(value, dict)} if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_drafts(drafts: dict[str, dict]) -> None:
    temp = DRAFTS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(drafts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(DRAFTS_FILE)


def load_settings() -> dict:
    defaults = {"agentName": "Vórtex", "autoRollback": True, "agentModel": "", "notificationsEnabled": True, "soundsEnabled": True, "automationMode": "assist", "companionToken": "", "autopilotRetry": False, "publicWelcomeShown": False}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            defaults.update(data)
    except Exception:
        pass
    if str(defaults.get("agentName") or "").strip().lower() == "nexo":
        defaults["agentName"] = "Vórtex"
    if str(defaults.get("automationMode") or "") not in {"assist", "send", "autopilot"}:
        defaults["automationMode"] = "assist"
    if PUBLIC_RELEASE:
        defaults["automationMode"] = "assist"
        defaults["autopilotRetry"] = False
    if not str(defaults.get("companionToken") or "").strip():
        defaults["companionToken"] = secrets.token_hex(16)
        try:
            save_settings(defaults)
        except Exception:
            pass
    return defaults


def save_settings(settings: dict) -> None:
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(SETTINGS_FILE)


def play_vortex_sound(kind: str = "ready") -> None:
    """Play a tiny recognizable Vórtex jingle without shipping audio files."""
    if os.name != "nt":
        return
    sequences = {
        "ready": [(740, 80), (988, 90), (1318, 120), (988, 70), (1568, 190)],
        "agent": [(660, 75), (784, 75), (988, 80), (1175, 90), (1568, 180)],
        "apply": [(523, 75), (659, 75), (784, 75), (1047, 170), (1318, 170)],
        "error": [(440, 110), (370, 120), (294, 170)],
        "reminder": [(988, 75), (988, 75), (1318, 120), (1568, 160)],
    }
    melody = sequences.get(str(kind or "ready"), sequences["ready"])

    def worker() -> None:
        try:
            import winsound
            for frequency, duration in melody:
                winsound.Beep(int(frequency), int(duration))
                time.sleep(0.025)
        except Exception:
            try:
                import winsound
                winsound.MessageBeep()
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()


def show_windows_balloon_notification(title: str, message: str, *, error: bool = False) -> None:
    """Best-effort Windows tray balloon. The in-app toast remains the guaranteed fallback."""
    if os.name != "nt":
        return
    title = re.sub(r"\s+", " ", str(title or APP_NAME)).strip()[:80]
    message = re.sub(r"\s+", " ", str(message or "")).strip()[:230]
    if not message:
        return
    title_ps = title.replace("'", "''")
    message_ps = message.replace("'", "''")
    icon_path = RESOURCE_DIR / "assets" / "app.ico"
    icon_ps = str(icon_path).replace("'", "''")
    balloon_icon = "Error" if error else "Info"
    script = f"""
Add-Type -AssemblyName System.Windows.Forms;
Add-Type -AssemblyName System.Drawing;
$n = New-Object System.Windows.Forms.NotifyIcon;
if (Test-Path '{icon_ps}') {{ $n.Icon = New-Object System.Drawing.Icon('{icon_ps}') }} else {{ $n.Icon = [System.Drawing.SystemIcons]::Information }};
$n.Visible = $true;
$n.BalloonTipTitle = '{title_ps}';
$n.BalloonTipText = '{message_ps}';
$n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::{balloon_icon};
$n.ShowBalloonTip(7000);
Start-Sleep -Seconds 8;
$n.Dispose();
"""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
        )
    except Exception:
        pass


def load_agent_chats() -> dict[str, list[dict]]:
    try:
        data = json.loads(AGENT_CHATS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        result: dict[str, list[dict]] = {}
        for key, value in data.items():
            if isinstance(value, list):
                normalized = [item for item in value if isinstance(item, dict)][-160:]
                key_text = str(key)
                if "::" not in key_text:
                    key_text = agent_chat_key(key_text, "project")
                result[key_text] = normalized
        return result
    except Exception:
        return {}


def save_agent_chats(chats: dict[str, list[dict]]) -> None:
    compact = {str(key): list(value)[-160:] for key, value in chats.items() if isinstance(value, list)}
    temp = AGENT_CHATS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(AGENT_CHATS_FILE)


def load_checklists() -> dict[str, list[dict]]:
    try:
        data = json.loads(CHECKLIST_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        result: dict[str, list[dict]] = {}
        for key, value in data.items():
            if isinstance(value, list):
                result[str(key)] = [item for item in value if isinstance(item, dict)][-300:]
        return result
    except Exception:
        return {}


def save_checklists(checklists: dict[str, list[dict]]) -> None:
    compact = {str(key): list(value)[-300:] for key, value in checklists.items() if isinstance(value, list)}
    temp = CHECKLIST_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(compact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(CHECKLIST_FILE)


def load_project_meta() -> dict[str, dict]:
    try:
        data = json.loads(PROJECT_META_FILE.read_text(encoding="utf-8"))
        return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_project_meta(meta: dict[str, dict]) -> None:
    temp = PROJECT_META_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(PROJECT_META_FILE)


def _project_meta_entry(project_id: str) -> dict:
    meta = load_project_meta()
    entry = meta.get(str(project_id))
    return dict(entry) if isinstance(entry, dict) else {}


def scan_project_memory(project: Project) -> dict:
    """Build tiny deterministic project memory without spending any model tokens."""
    facts: list[str] = []
    roots = root_aliases(project.paths)
    for alias in roots:
        root = alias.root
        facts.append(f"Raiz {alias.alias}: {root.name}")
        checks = [
            ("package.json", "Node/JavaScript"), ("composer.json", "PHP/Composer"),
            ("pyproject.toml", "Python"), ("requirements.txt", "Python"),
            ("Cargo.toml", "Rust"), ("go.mod", "Go"), ("pom.xml", "Java/Maven"),
        ]
        for filename, label in checks:
            if (root / filename).is_file() and label not in facts:
                facts.append(label)
        package = root / "package.json"
        if package.is_file():
            try:
                data = json.loads(package.read_text(encoding="utf-8", errors="replace"))
                deps = {}
                for key in ("dependencies", "devDependencies"):
                    value = data.get(key)
                    if isinstance(value, dict):
                        deps.update({str(k).lower(): v for k, v in value.items()})
                known = [
                    ("framework7", "Framework7"), ("next", "Next.js"), ("react", "React"),
                    ("vue", "Vue"), ("@angular/core", "Angular"), ("vite", "Vite"),
                    ("typescript", "TypeScript"), ("electron", "Electron"),
                ]
                for key, label in known:
                    if key in deps and label not in facts:
                        facts.append(label)
            except Exception:
                pass
        for marker, label in [
            ("README.md", "README disponível"),
            ("docs", "Pasta docs disponível"),
            (".git", "Git detectado"),
        ]:
            if (root / marker).exists() and label not in facts:
                facts.append(label)
    for path in list_skill_files(project.id):
        facts.append(f"Skill do projeto: {path.name}")
    meta = load_project_meta()
    entry = dict(meta.get(project.id) or {})
    entry["memoryFacts"] = facts[:60]
    entry["memoryUpdatedAt"] = now_iso()
    entry.setdefault("notes", "")
    entry.setdefault("glossary", {})
    meta[project.id] = entry
    save_project_meta(meta)
    return entry


def project_memory(project: Project, *, refresh: bool = False) -> dict:
    entry = _project_meta_entry(project.id)
    if refresh or not entry.get("memoryFacts"):
        entry = scan_project_memory(project)
    return entry


def project_memory_text(project: Project) -> str:
    # Refresh on each context cook: this is a tiny filesystem scan and spends zero AI/Codex tokens.
    entry = project_memory(project, refresh=True)
    facts = [str(x).strip() for x in entry.get("memoryFacts") or [] if str(x).strip()]
    notes = str(entry.get("notes") or "").strip()
    lines = [f"- {x}" for x in facts[:40]]
    if notes:
        lines.append(f"- Notas persistentes: {notes[:4000]}")
    return "\n".join(lines)


def update_project_meta(project_id: str, *, notes: str | None = None, glossary: dict | None = None) -> dict:
    meta = load_project_meta()
    entry = dict(meta.get(project_id) or {})
    if notes is not None:
        entry["notes"] = str(notes)
    if glossary is not None:
        entry["glossary"] = {str(k): str(v) for k, v in glossary.items() if str(k).strip() and str(v).strip()}
    entry["updatedAt"] = now_iso()
    meta[project_id] = entry
    save_project_meta(meta)
    return entry


def local_polish_dictation(project: Project | None, text: str) -> str:
    """Cheap/local cleanup: glossary + deterministic punctuation. No Codex/API call."""
    value = str(text or "")
    if not value.strip():
        return value
    replacements = {
        " underline ": "_",
        " arroba ": "@",
        " barra invertida ": "\\",
        " barra ": "/",
        " ponto json": ".json",
        " ponto js": ".js",
        " ponto ts": ".ts",
        " ponto php": ".php",
        " ponto f sete": ".f7",
        " framework sete": "Framework7",
        " f sete": "Framework7",
    }
    padded = f" {value} "
    for spoken, correct in replacements.items():
        padded = re.sub(re.escape(spoken), lambda _m, c=correct: c, padded, flags=re.I)
    value = padded.strip()
    glossary: dict[str, str] = {}
    if project:
        entry = project_memory(project)
        raw = entry.get("glossary")
        if isinstance(raw, dict):
            glossary.update({str(k): str(v) for k, v in raw.items()})
        # Canonical project name is always safe to normalize when the words match.
        normalized_name = re.sub(r"[-_]+", " ", project.name).strip()
        if normalized_name and normalized_name.lower() != project.name.lower():
            glossary.setdefault(normalized_name, project.name)
    for spoken, correct in sorted(glossary.items(), key=lambda item: len(item[0]), reverse=True):
        if not spoken.strip():
            continue
        value = re.sub(rf"(?<!\\w){re.escape(spoken.strip())}(?!\\w)", correct, value, flags=re.I)
    value = re.sub(r"[ \\t]+", " ", value)
    value = re.sub(r" ?\\n ?", "\n", value)
    value = re.sub(r"\\n{3,}", "\n\n", value)
    return value.strip()


def round_result_identity(project_id: str, task_id: str, round_id: str, round_number: int) -> tuple[str, str]:
    secret = str(load_settings().get("companionToken") or "vortice")
    raw = f"{project_id}|{task_id}|{round_id}|{int(round_number or 1)}|{secret}"
    token = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    filename = f"VORTICE-RESULT-{token}.zip"
    return token, filename


def stamp_prepare_identity(project: Project, result: PrepareResult, request_text: str) -> PrepareResult:
    token, filename = round_result_identity(project.id, result.session_id, result.round_id, result.round_number)
    result.result_token = token
    result.expected_result_name = filename
    identity = {
        "projectId": project.id,
        "projectName": project.name,
        "taskId": result.session_id,
        "roundId": result.round_id,
        "roundNumber": int(result.round_number or 1),
        "resultToken": token,
        "expectedResultName": filename,
    }
    # Rewrite the context manifest so the package itself carries the round identity.
    try:
        stage = TMP_DIR / f"identity-{uuid.uuid4().hex}"
        safe_extract_zip(result.zip_path, stage)
        manifest_path = stage / "AI-BRIDGE-MANIFEST.json"
        manifest = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(manifest, dict):
            manifest = {}
        manifest["vortice"] = identity
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_zip = result.zip_path.with_suffix(".identity.tmp.zip")
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as zf:
            for file in stage.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(stage).as_posix())
        temp_zip.replace(result.zip_path)
        shutil.rmtree(stage, ignore_errors=True)
    except Exception:
        pass
    memory = project_memory_text(project)
    memory_block = f"\n\nMEMÓRIA LOCAL DO PROJETO (detectada pelo Vórtice, sem IA)\n{memory}\n" if memory else ""
    protocol = f"""

PROTOCOLO VÓRTICE — OBRIGATÓRIO
Esta é uma execução automatizada e o resultado precisa voltar exatamente para a Task/Rodada correta.
- PROJECT_ID: {project.id}
- TASK_ID: {result.session_id}
- ROUND_ID: {result.round_id}
- ROUND_NUMBER: {int(result.round_number or 1)}
- RESULT_TOKEN: {token}
- Nome preferencial do ZIP final: {filename}

Ao entregar o código, gere UM ZIP final com somente os arquivos modificados, preservando as raízes do CONTEXTO.zip.
Dentro desse ZIP inclua também um arquivo `_vortice-result.json` na raiz com EXATAMENTE estas chaves/valores:
{json.dumps(identity, ensure_ascii=False, indent=2)}
Não use esse arquivo como substituto dos arquivos alterados; ele é apenas a etiqueta de retorno da rodada.
"""
    result.prompt = result.prompt.rstrip() + memory_block + protocol
    return result


def validate_vortice_result_zip(project: Project, zip_path: Path, task_id: str, round_id: str, round_number: int) -> tuple[bool, str, dict]:
    expected_token, _expected_name = round_result_identity(project.id, task_id, round_id, round_number)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = {name.replace("\\", "/").strip("/"): name for name in zf.namelist()}
            raw_name = names.get("_vortice-result.json") or names.get("VORTICE-RESULT.json") or names.get("vortice-result.json")
            if not raw_name:
                return False, "O ZIP não trouxe _vortice-result.json; não consigo provar que pertence a esta rodada.", {}
            data = json.loads(zf.read(raw_name).decode("utf-8", errors="replace"))
            if not isinstance(data, dict):
                return False, "O manifesto de retorno não é válido.", {}
    except Exception as exc:
        return False, f"Não consegui validar o manifesto do ZIP: {exc}", {}
    checks = {
        "projectId": project.id,
        "taskId": task_id,
        "roundId": round_id,
        "roundNumber": int(round_number or 1),
        "resultToken": expected_token,
        "expectedResultName": _expected_name,
    }
    for key, expected in checks.items():
        actual = data.get(key)
        if key == "roundNumber":
            try: actual = int(actual)
            except Exception: actual = -1
        if str(actual) != str(expected):
            return False, f"Manifesto de retorno não bate em {key}. ZIP bloqueado para não cair na Task errada.", data
    return True, "Identidade da rodada confirmada.", data


def semantic_validate_result(project: Project, zip_path: Path, request_text: str) -> tuple[list[str], list[str]]:
    """Conservative local validation. Blocks only obvious scope violations; never calls Codex."""
    warnings: list[str] = []
    blockers: list[str] = []
    try:
        rows = preview_result_zip(project, zip_path)
    except Exception as exc:
        return warnings, [str(exc)]
    changed = [str(row.get("path") or "") for row in rows if str(row.get("status") or "") in {"added", "modified", "same"}]
    lowered = str(request_text or "").lower()
    if any(str(row.get("status") or "") == "blocked" for row in rows):
        blockers.append("O pacote contém arquivo bloqueado pela política de segurança.")
    frontend_only = any(term in lowered for term in ["somente frontend", "apenas frontend", "não altere o backend", "nao altere o backend", "não mexa no backend", "nao mexa no backend", "api somente para consulta"])
    backend_only = any(term in lowered for term in ["somente backend", "apenas backend", "não altere o frontend", "nao altere o frontend", "não mexa no frontend", "nao mexa no frontend"])
    if frontend_only:
        bad = [path for path in changed if re.search(r"(^|/)(?:[^/]*(?:api|backend|server)[^/]*)/", path, flags=re.I)]
        if bad:
            blockers.append("A Task limita a mudança ao frontend, mas o ZIP toca possível backend/API: " + ", ".join(bad[:4]))
    if backend_only:
        bad = [path for path in changed if re.search(r"(^|/)(?:[^/]*(?:web|front|frontend|client)[^/]*)/", path, flags=re.I)]
        if bad:
            blockers.append("A Task limita a mudança ao backend, mas o ZIP toca possível frontend: " + ", ".join(bad[:4]))
    if len(changed) > 80:
        warnings.append(f"Mudança ampla: {len(changed)} arquivos no pacote.")
    return warnings, blockers


def checklist_asset_dir(project_id: str, item_id: str) -> Path:
    directory = CHECKLIST_ASSETS_DIR / slug(project_id) / slug(item_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def persist_checklist_attachments(project_id: str, item_id: str, sources: Iterable[str | Path]) -> list[str]:
    directory = checklist_asset_dir(project_id, item_id)
    saved: list[str] = []
    for source in normalize_attachment_selection(sources):
        target = directory / source.name
        if target.exists():
            stem, suffix = target.stem, target.suffix
            index = 2
            while target.exists():
                target = directory / f"{stem}-{index}{suffix}"
                index += 1
        try:
            shutil.copy2(source, target)
            saved.append(str(target.resolve()))
        except Exception:
            continue
    return saved


def checklist_attachment_paths(item: dict) -> list[Path]:
    return normalize_attachment_selection(item.get("attachments") or []) if isinstance(item, dict) else []


def agent_chat_key(project_id: str, task_id: str = "") -> str:
    task = str(task_id or "project").strip() or "project"
    return f"{project_id}::{task}"


def project_skill_dir(project_id: str) -> Path:
    directory = PROJECT_SKILLS_DIR / slug(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_skill_files(project_id: str | None = None) -> list[Path]:
    directory = project_skill_dir(project_id) if project_id else GLOBAL_SKILLS_DIR
    files: list[Path] = []
    for pattern in ("*.md", "*.markdown", "*.txt"):
        files.extend(path for path in directory.glob(pattern) if path.is_file())
    files.sort(key=lambda path: path.name.lower())
    return files


def copy_skill_files(sources: Iterable[str | Path], project_id: str | None = None) -> list[Path]:
    directory = project_skill_dir(project_id) if project_id else GLOBAL_SKILLS_DIR
    saved: list[Path] = []
    for raw in sources:
        source = Path(str(raw)).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in {".md", ".markdown", ".txt"}:
            continue
        target = directory / source.name
        if target.exists():
            base, suffix = target.stem, target.suffix
            idx = 2
            while target.exists():
                target = directory / f"{base}-{idx}{suffix}"
                idx += 1
        shutil.copy2(source, target)
        saved.append(target.resolve())
    return saved


def build_skills_context(project_id: str | None = None, max_chars: int = MAX_SKILL_CONTEXT) -> str:
    chunks: list[str] = []
    used = 0
    groups = [("GLOBAL", list_skill_files(None))]
    if project_id:
        groups.append(("PROJETO", list_skill_files(project_id)))
    for scope, files in groups:
        for path in files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                continue
            if not content:
                continue
            header = f"\n### SKILL {scope}: {path.name}\n"
            remaining = max_chars - used - len(header)
            if remaining <= 0:
                return "".join(chunks)
            piece = content[:remaining]
            chunks.append(header + piece + "\n")
            used += len(header) + len(piece) + 1
            if used >= max_chars:
                break
    return "".join(chunks).strip()


def load_history() -> list[dict]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list[dict]) -> None:
    # Keep the local history compact. 500 entries is plenty for a small personal tool.
    history = history[-500:]
    temp = HISTORY_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(HISTORY_FILE)


def append_history(project_id: str, event_type: str, **data) -> dict:
    history = load_history()
    item = {
        "id": str(uuid.uuid4()),
        "projectId": project_id,
        "type": event_type,
        "createdAt": now_iso(),
        **data,
    }
    history.append(item)
    save_history(history)
    return item


def project_history(project_id: str, limit: int = 50) -> list[dict]:
    items = [item for item in load_history() if item.get("projectId") == project_id]
    items.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    return items[:limit]


def app_relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(APP_DIR.resolve()).as_posix()
    except Exception:
        return str(path.resolve())


def resolve_saved_path(value: str | None) -> Optional[Path]:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = APP_DIR / path
    return path.resolve()


def history_zip_path(item: dict) -> Optional[Path]:
    item = item if isinstance(item, dict) else {}
    explicit = resolve_saved_path(item.get("zipPath"))
    if explicit and explicit.is_file():
        return explicit
    name = str(item.get("zipName") or "").strip()
    if name:
        candidate = OUTPUT_DIR / name
        if candidate.is_file():
            return candidate.resolve()
    return explicit


def _derived_prompt_path(zip_path: Path) -> Path:
    stem = zip_path.stem
    if stem.upper().startswith("CONTEXTO-"):
        stem = "PROMPT-" + stem[len("CONTEXTO-"):]
    else:
        stem = "PROMPT-" + stem
    return zip_path.with_name(stem + ".txt")


def history_prompt_path(item: dict) -> Optional[Path]:
    item = item if isinstance(item, dict) else {}
    path = resolve_saved_path(item.get("promptPath"))
    if path and path.is_file():
        return path
    zip_path = history_zip_path(item)
    if zip_path:
        derived = _derived_prompt_path(zip_path)
        if derived.is_file():
            return derived.resolve()
    return path


def recover_prompt_from_context_zip(project: Project, item: dict) -> Optional[Path]:
    item = item if isinstance(item, dict) else {}
    existing = history_prompt_path(item)
    if existing and existing.is_file():
        return existing
    zip_path = history_zip_path(item)
    if not zip_path or not zip_path.is_file():
        return None
    request = str(item.get("request") or "").strip()
    files: list[str] = []
    roots: list[str] = []
    attachments: list[str] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            raw = zf.read("AI-BRIDGE-MANIFEST.json").decode("utf-8", errors="replace")
            manifest = json.loads(raw)
            request = request or str(manifest.get("request") or "").strip()
            files = [f"{entry.get('root')}/{entry.get('path')}" for entry in manifest.get("files", []) if isinstance(entry, dict)]
            roots = [str(entry.get("alias") or "") for entry in manifest.get("roots", []) if isinstance(entry, dict)]
            attachments = [str(entry.get("name") or "") for entry in manifest.get("attachments", []) if isinstance(entry, dict)]
    except Exception:
        pass
    file_preview = "\n".join(f"- {name}" for name in files[:80]) or "- Use os arquivos anexados como fonte da verdade."
    attachment_block = ""
    if attachments:
        attachment_preview = "\n".join(f"- {name}" for name in attachments[:40])
        attachment_block = f"""
ANEXOS EXTRAS DO USUÁRIO
{attachment_preview}

Use imagens, documentos e prints enviados separadamente como contexto adicional quando forem relevantes.
"""
    prompt = f"""Você recebeu um CONTEXTO.zip do projeto {project.name}.

OBJETIVO
{request or 'Analise o contexto anexado e implemente a alteração solicitada com segurança.'}

REGRAS
- O estado atual dos arquivos anexados é a fonte da verdade.
- Preserve funcionalidades existentes e alterações locais corretas.
- Não reimplemente módulos sem necessidade.
- Analise dependências e impactos antes de editar.
- Não invente arquivos, endpoints, migrations ou contratos que não estejam sustentados pelo contexto.
- Corrija problemas relacionados que sejam necessários para a alteração funcionar de ponta a ponta.
- Valide sintaxe, tipos, build/testes disponíveis e regressões relevantes.
- Não inclua segredos, .env, node_modules, builds ou arquivos temporários.
- Ao final, devolva um ZIP contendo somente os arquivos modificados, preservando exatamente a estrutura de pastas do CONTEXTO.zip.
- Resuma de forma objetiva o que foi alterado e qualquer passo manual realmente necessário.

ARQUIVOS DE CONTEXTO
{file_preview}
{attachment_block}"""
    prompt_path = _derived_prompt_path(zip_path)
    try:
        prompt_path.write_text(prompt.rstrip() + "\n", encoding="utf-8")
        return prompt_path.resolve()
    except Exception:
        return None


def save_prepare_prompt(project: Project, result: PrepareResult) -> Path:
    prompt_path = _derived_prompt_path(result.zip_path)
    prompt_path.write_text(result.prompt.rstrip() + "\n", encoding="utf-8")
    return prompt_path.resolve()


def human_size(size: int) -> str:
    size = max(0, int(size or 0))
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size} B"


def format_attachment_caption(path: Path) -> str:
    try:
        return f"{path.name} · {human_size(path.stat().st_size)}"
    except Exception:
        return path.name


def normalize_attachment_selection(paths: Iterable[str | Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        try:
            path = Path(str(raw)).expanduser().resolve()
        except Exception:
            continue
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if path in seen:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0 or size > MAX_RESULT_ZIP:
            continue
        seen.add(path)
        result.append(path)
    return result


def history_attachment_paths(item: dict) -> list[Path]:
    item = item if isinstance(item, dict) else {}
    values = item.get("attachmentPaths") or []
    paths: list[Path] = []
    seen: set[Path] = set()
    if isinstance(values, list):
        for raw in values:
            path = resolve_saved_path(raw)
            if path and path.is_file() and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def session_title(value: str, fallback: str = "Novo chat") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return fallback
    return text if len(text) <= 58 else text[:55].rstrip() + "..."


def project_sessions(project_id: str, limit: int = 80) -> list[dict]:
    """Return project tasks, each with persistent rounds and the latest task state.

    Legacy history used sessionId as the task identifier. v4 keeps that contract while
    adding taskId/roundId/roundNumber so one task can have many correction rounds.
    """
    events = [item for item in load_history() if item.get("projectId") == project_id]
    tasks: dict[str, dict] = {}

    def task_for(event: dict) -> dict:
        task_id = str(event.get("taskId") or event.get("sessionId") or event.get("id") or uuid.uuid4())
        obj = tasks.get(task_id)
        if obj is None:
            obj = {
                "id": task_id,
                "createdAt": event.get("createdAt") or now_iso(),
                "request": event.get("taskTitle") or event.get("request") or event.get("summary") or "",
                "latestRequest": event.get("request") or "",
                "status": "doing",
                "rounds": [],
                "prepare": None,
                "handoff": None,
                "chat": None,
                "result": None,
                "apply": None,
            }
            tasks[task_id] = obj
        return obj

    def round_for(task: dict, event: dict) -> dict:
        legacy = str(event.get("sessionId") or task.get("id") or "")
        round_id = str(event.get("roundId") or legacy or event.get("id") or uuid.uuid4())
        rounds = task["rounds"]
        found = next((item for item in rounds if str(item.get("id") or "") == round_id), None)
        if found:
            return found
        number = int(event.get("roundNumber") or 0)
        if number <= 0:
            number = max([int(item.get("number") or 0) for item in rounds] + [0]) + 1
        found = {
            "id": round_id,
            "number": number,
            "createdAt": event.get("createdAt") or now_iso(),
            "request": event.get("request") or "",
            "prepare": None,
            "handoff": None,
            "chat": None,
            "result": None,
            "apply": None,
            "events": [],
        }
        rounds.append(found)
        return found

    relevant_kinds = {"prepare", "handoff", "chat_link", "result_received", "apply", "rollback", "nexo_action", "nexo_rollback", "followup", "autopilot_retry", "autopilot_rollback", "task_status", "homologation", "result_rejected"}
    for event in events:
        kind = str(event.get("type") or "")
        if kind not in relevant_kinds:
            continue
        task_key = str(event.get("taskId") or event.get("sessionId") or "").strip()
        if not task_key and kind != "prepare":
            # Old project-level events such as rollback/checkpoints must not become fake tasks.
            continue
        task = task_for(event)
        if event.get("taskTitle") and not task.get("request"):
            task["request"] = event.get("taskTitle")
        if kind == "task_status":
            task["status"] = str(event.get("status") or "doing")
        if kind == "homologation":
            task["status"] = str(event.get("status") or "review")
            continue
        if kind == "chat_link" and not event.get("roundId"):
            task["chat"] = event
        rnd = round_for(task, event)
        rnd["events"].append(event)
        if event.get("request"):
            rnd["request"] = event.get("request")
            task["latestRequest"] = event.get("request")
            if not task.get("request"):
                task["request"] = event.get("request")
        if kind == "prepare":
            rnd["prepare"] = event
            task["prepare"] = event
        elif kind == "handoff":
            rnd["handoff"] = event
            task["handoff"] = event
        elif kind == "chat_link":
            rnd["chat"] = event
            task["chat"] = event
        elif kind == "result_received":
            rnd["result"] = event
            task["result"] = event
        elif kind == "apply":
            rnd["apply"] = event
            task["apply"] = event
        if str(event.get("createdAt") or "") < str(task.get("createdAt") or ""):
            task["createdAt"] = event.get("createdAt")

    result = list(tasks.values())
    for task in result:
        task["rounds"].sort(key=lambda item: (int(item.get("number") or 0), str(item.get("createdAt") or "")), reverse=True)
        if task["rounds"]:
            latest = task["rounds"][0]
            for key in ("prepare", "handoff", "result", "apply"):
                if latest.get(key):
                    task[key] = latest.get(key)
            if latest.get("request"):
                task["latestRequest"] = latest.get("request")
        # A chat belongs to the task and is deliberately reused across rounds.
        chats = [event for event in events if str(event.get("taskId") or event.get("sessionId") or "") == str(task["id"]) and event.get("type") == "chat_link"]
        if chats:
            chats.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
            task["chat"] = chats[0]
    result.sort(key=lambda item: max([str(r.get("createdAt") or "") for r in item.get("rounds", [])] + [str(item.get("createdAt") or "")]), reverse=True)
    return result[:limit]


def task_next_round_number(project_id: str, task_id: str) -> int:
    task = next((item for item in project_sessions(project_id, 500) if str(item.get("id") or "") == str(task_id)), None)
    rounds = task.get("rounds") if isinstance(task, dict) else []
    return max([int(item.get("number") or 0) for item in (rounds or [])] + [0]) + 1


def task_round(project_id: str, task_id: str, round_id: str = "") -> Optional[dict]:
    task = next((item for item in project_sessions(project_id, 500) if str(item.get("id") or "") == str(task_id)), None)
    if not task:
        return None
    rounds = task.get("rounds") or []
    if round_id:
        found = next((item for item in rounds if str(item.get("id") or "") == str(round_id)), None)
        if found:
            return found
    return rounds[0] if rounds else None


def session_prepare_event(project_id: str, session_id: str) -> Optional[dict]:
    for session in project_sessions(project_id, 120):
        if str(session.get("id")) == str(session_id):
            item = session.get("prepare")
            return item if isinstance(item, dict) else None
    return None


def format_tokens(value: int | float | None) -> str:
    try:
        value = int(value or 0)
    except Exception:
        value = 0
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def format_bytes(value: int | float | None) -> str:
    try:
        size = float(value or 0)
    except Exception:
        size = 0.0
    units = ["B", "KB", "MB", "GB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"


def normalize_usage(usage: Optional[dict]) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "reasoning_output_tokens": int(usage.get("reasoning_output_tokens") or 0),
    }


def merge_usage(total: dict, usage: Optional[dict]) -> dict:
    item = normalize_usage(usage)
    for key, value in item.items():
        total[key] = int(total.get(key) or 0) + value
    return total


def usage_total(usage: Optional[dict]) -> int:
    item = normalize_usage(usage)
    return item["input_tokens"] + item["output_tokens"]


def usage_text(usage: Optional[dict]) -> str:
    item = normalize_usage(usage)
    if not usage_total(item):
        return "sem uso nesta rodada"
    return (
        f"{format_tokens(item['input_tokens'])} entrada  ·  "
        f"{format_tokens(item['cached_input_tokens'])} cache  ·  "
        f"{format_tokens(item['output_tokens'])} saída"
    )


def find_project_logo(project: Project) -> str:
    explicit = Path(project.logo_path).expanduser() if project.logo_path else None
    if explicit and explicit.is_file() and explicit.suffix.lower() in {".png", ".gif"}:
        return str(explicit.resolve())
    names = (
        "logo.png", "icon.png", "app-logo.png", "brand.png",
        "public/logo.png", "public/icon.png", "public/favicon.png",
        "src/assets/logo.png", "assets/logo.png", "static/logo.png",
    )
    for root_text in project.paths:
        root = Path(root_text)
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return str(candidate.resolve())
    return ""


def root_aliases(paths: Iterable[str | Path]) -> list[RootAlias]:
    used: set[str] = set()
    aliases: list[RootAlias] = []
    for index, item in enumerate(paths, start=1):
        root = Path(item).expanduser().resolve()
        base = f"{index:02d}-{slug(root.name or 'raiz')}"
        alias = base
        suffix = 2
        while alias in used:
            alias = f"{base}-{suffix}"
            suffix += 1
        used.add(alias)
        aliases.append(RootAlias(alias, root))
    return aliases


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child = child.resolve()
        parent = parent.resolve()
        child.relative_to(parent)
        return True
    except Exception:
        return False


def is_blocked_file(file_path: Path) -> bool:
    try:
        resolved = file_path.resolve()
    except Exception:
        resolved = file_path.absolute()
    parts = [part.lower() for part in resolved.parts]
    if any(segment.lower() in BLOCKED_SEGMENTS for segment in parts):
        return True
    name = resolved.name
    return any(rx.search(name) for rx in BLOCKED_BASENAME_PATTERNS)


def find_owner(file_path: Path, roots: Iterable[Path]) -> Optional[Path]:
    candidates = [root for root in roots if is_inside(file_path, root)]
    if not candidates:
        return None
    # Prefer the most specific root when roots are nested.
    return max(candidates, key=lambda p: len(p.parts))


def resolve_selected_file(candidate: str, roots: list[Path]) -> Optional[tuple[Path, Path, int]]:
    raw = str(candidate or "").strip().strip('"')
    if not raw:
        return None
    attempts: list[Path] = []
    path_obj = Path(raw).expanduser()
    if path_obj.is_absolute():
        attempts.append(path_obj)
    else:
        attempts.extend(root / path_obj for root in roots)

    for attempt in attempts:
        try:
            resolved = attempt.resolve()
            owner = find_owner(resolved, roots)
            if owner is None or is_blocked_file(resolved) or not resolved.is_file():
                continue
            size = resolved.stat().st_size
            if size > MAX_CONTEXT_FILE:
                continue
            return resolved, owner, size
        except OSError:
            continue
    return None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def run_hidden(command: list[str], cwd: Optional[Path] = None, timeout: int = 30) -> subprocess.CompletedProcess:
    kwargs: dict = {
        "cwd": str(cwd) if cwd else None,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "capture_output": True,
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, **kwargs)


def open_windows_voice_typing() -> bool:
    """Open Windows Voice Typing (Win+H) without imposing any Vórtice-side time/text limit."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        vk_lwin = 0x5B
        vk_h = 0x48
        keyup = 0x0002
        user32.keybd_event(vk_lwin, 0, 0, 0)
        user32.keybd_event(vk_h, 0, 0, 0)
        time.sleep(0.035)
        user32.keybd_event(vk_h, 0, keyup, 0)
        user32.keybd_event(vk_lwin, 0, keyup, 0)
        return True
    except Exception:
        return False


def polish_dictation_with_codex(project: Project, task_title: str, text: str) -> str:
    """Read-only Codex pass that fixes likely dictation mistakes without changing the user's intent."""
    value = str(text or "").strip()
    if not value:
        return value
    status = codex_status()
    if not status.get("installed"):
        raise RuntimeError("Codex CLI não encontrado para lapidar o ditado.")
    if not status.get("authenticated"):
        raise RuntimeError("Codex está sem login. Abra um terminal, rode codex e faça login.")

    roots = [Path(item).expanduser().resolve() for item in project.paths if str(item).strip()]
    roots = [item for item in roots if item.is_dir()]
    cwd = roots[0] if roots else APP_DIR
    job_dir = TMP_DIR / f"dictation-{uuid.uuid4().hex}"
    job_dir.mkdir(parents=True, exist_ok=True)
    last_path = job_dir / "result.txt"
    prompt = f"""Você é o revisor de ditado do Vórtice.

O usuário falou em português do Brasil e o texto abaixo veio de reconhecimento de voz.
Sua única tarefa é devolver o MESMO pedido, mais claro e com prováveis erros de transcrição corrigidos.

CONTEXTO MÍNIMO
- projeto: {project.name}
- task atual: {task_title or 'Task atual'}

REGRAS OBRIGATÓRIAS
- Preserve integralmente a intenção, requisitos, números, caminhos, nomes de arquivos, comandos e trechos de código.
- Corrija pontuação, concordância e palavras que claramente foram reconhecidas errado.
- Seja conservador com nomes técnicos. Se houver ambiguidade, mantenha o termo original em vez de inventar.
- Pode consultar o projeto SOMENTE em leitura para confirmar um nome técnico óbvio, se realmente necessário.
- Não transforme o pedido em especificação gigante e não acrescente requisitos.
- Não explique o que corrigiu.
- Não use markdown de apresentação.
- Responda SOMENTE com o texto final pronto para ficar no textarea.

TEXTO DITADO
{value}
"""
    args = [
        "exec", "--sandbox", "read-only", "--skip-git-repo-check",
        "--cd", str(cwd), "--config", 'approval_policy="never"',
        "--output-last-message", str(last_path),
    ]
    settings = load_settings()
    model = str(settings.get("agentModel") or "").strip()
    if model:
        args.extend(["--model", model])
    for extra in roots[1:]:
        args.extend(["--add-dir", str(extra)])
    args.append("-")
    command = codex_command(str(status.get("source")), args)
    kwargs: dict = {
        "cwd": str(cwd),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, **kwargs)
    _stdout, stderr = process.communicate(prompt)
    if process.returncode != 0:
        tail = "\n".join(str(stderr or "").splitlines()[-8:]).strip()
        raise RuntimeError(tail or f"Codex encerrou com código {process.returncode}.")
    if not last_path.is_file():
        raise RuntimeError("O Codex terminou sem devolver o texto lapidado.")
    result = last_path.read_text(encoding="utf-8", errors="replace").strip()
    return result or value


def resolve_git_source() -> Optional[str]:
    return shutil.which("git")


def git_repositories(project: Project) -> list[dict]:
    git = resolve_git_source()
    if not git:
        return []
    repos: dict[str, dict] = {}
    for root_text in project.paths:
        root = Path(root_text).expanduser()
        if not root.exists():
            continue
        try:
            top = run_hidden([git, "-C", str(root), "rev-parse", "--show-toplevel"], timeout=12)
            if top.returncode != 0:
                continue
            repo = Path(top.stdout.strip()).resolve()
            key = os.path.normcase(str(repo))
            if key not in repos:
                repos[key] = {"path": repo, "roots": []}
            repos[key]["roots"].append(root.resolve())
        except Exception:
            continue
    return list(repos.values())


def git_repo_snapshot(repo: Path) -> dict:
    git = resolve_git_source()
    if not git:
        return {"path": str(repo), "ok": False, "error": "Git não encontrado."}
    try:
        branch_cmd = run_hidden([git, "-C", str(repo), "branch", "--show-current"], timeout=12)
        branch = branch_cmd.stdout.strip() if branch_cmd.returncode == 0 else ""
        if not branch:
            detached = run_hidden([git, "-C", str(repo), "rev-parse", "--short", "HEAD"], timeout=12)
            branch = f"detached@{detached.stdout.strip()}" if detached.returncode == 0 else "sem branch"

        status_cmd = run_hidden([git, "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=normal"], timeout=20)
        changes = [line for line in status_cmd.stdout.splitlines() if line.strip()]

        log_cmd = run_hidden([
            git, "-C", str(repo), "log", "-n", "6",
            "--pretty=format:%h%x09%ad%x09%s", "--date=format:%d/%m %H:%M"
        ], timeout=20)
        commits = []
        if log_cmd.returncode == 0:
            for line in log_cmd.stdout.splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    commits.append({"hash": parts[0], "when": parts[1], "message": parts[2]})

        ahead = behind = 0
        upstream = ""
        upstream_cmd = run_hidden([git, "-C", str(repo), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], timeout=10)
        if upstream_cmd.returncode == 0:
            upstream = upstream_cmd.stdout.strip()
            counts = run_hidden([git, "-C", str(repo), "rev-list", "--left-right", "--count", "HEAD...@{u}"], timeout=12)
            if counts.returncode == 0:
                parts = counts.stdout.strip().split()
                if len(parts) == 2:
                    ahead, behind = int(parts[0]), int(parts[1])

        return {
            "path": str(repo),
            "ok": True,
            "branch": branch,
            "changes": changes,
            "dirty": len(changes),
            "commits": commits,
            "upstream": upstream,
            "ahead": ahead,
            "behind": behind,
        }
    except Exception as exc:
        return {"path": str(repo), "ok": False, "error": str(exc)}


def project_git_snapshot(project: Project) -> list[dict]:
    return [git_repo_snapshot(item["path"]) for item in git_repositories(project)]


def git_project_badge(project: Project) -> str:
    if not resolve_git_source():
        return "GIT OFF"
    snapshots = project_git_snapshot(project)
    if not snapshots:
        return "SEM GIT"
    dirty = sum(int(item.get("dirty") or 0) for item in snapshots if item.get("ok"))
    branches = [str(item.get("branch") or "") for item in snapshots if item.get("ok")]
    branch = branches[0] if len(set(branches)) == 1 and branches else f"{len(snapshots)} repos"
    return f"{branch} · {dirty} alteração" + ("" if dirty == 1 else "ões")


def _git_unstage_paths(git: str, repo: Path, paths: list[str]) -> None:
    if not paths:
        return
    for index in range(0, len(paths), 80):
        chunk = paths[index:index + 80]
        restore = run_hidden([git, "-C", str(repo), "restore", "--staged", "--", *chunk], timeout=30)
        if restore.returncode != 0:
            reset = run_hidden([git, "-C", str(repo), "reset", "HEAD", "--", *chunk], timeout=30)
            if reset.returncode != 0:
                run_hidden([git, "-C", str(repo), "rm", "--cached", "-f", "--ignore-unmatch", "--", *chunk], timeout=30)


def create_git_checkpoint(project: Project, message: str) -> list[dict]:
    git = resolve_git_source()
    if not git:
        raise RuntimeError("Git não foi encontrado neste computador.")
    repositories = git_repositories(project)
    if not repositories:
        raise RuntimeError("Nenhuma das pastas deste projeto está dentro de um repositório Git.")

    message = str(message or "").strip() or f"chore: checkpoint Vórtice {datetime.now().strftime('%d/%m %H:%M')}"
    results: list[dict] = []
    for item in repositories:
        repo: Path = item["path"]
        # Tracked modifications/deletions first.
        add_tracked = run_hidden([git, "-C", str(repo), "add", "-u"], timeout=60)
        if add_tracked.returncode != 0:
            raise RuntimeError((add_tracked.stderr or add_tracked.stdout).strip() or f"Falha ao preparar {repo.name}.")

        # Add safe untracked files without ever staging blocked secrets/build folders.
        untracked = run_hidden([git, "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"], timeout=30)
        safe_new: list[str] = []
        if untracked.returncode == 0:
            for rel in [x for x in untracked.stdout.split("\0") if x]:
                candidate = (repo / rel).resolve()
                if is_inside(candidate, repo) and not is_blocked_file(candidate):
                    safe_new.append(rel)
        for index in range(0, len(safe_new), 80):
            chunk = safe_new[index:index + 80]
            staged = run_hidden([git, "-C", str(repo), "add", "--", *chunk], timeout=60)
            if staged.returncode != 0:
                raise RuntimeError((staged.stderr or staged.stdout).strip() or f"Falha ao adicionar arquivos novos em {repo.name}.")

        staged_names = run_hidden([git, "-C", str(repo), "diff", "--cached", "--name-only", "-z"], timeout=30)
        blocked_staged: list[str] = []
        if staged_names.returncode == 0:
            for rel in [x for x in staged_names.stdout.split("\0") if x]:
                if is_blocked_file((repo / rel).resolve()):
                    blocked_staged.append(rel)
        _git_unstage_paths(git, repo, blocked_staged)

        has_changes = run_hidden([git, "-C", str(repo), "diff", "--cached", "--quiet"], timeout=20)
        if has_changes.returncode == 0:
            results.append({"repo": str(repo), "committed": False, "reason": "Nada novo para checkpoint."})
            continue

        commit = run_hidden([git, "-C", str(repo), "commit", "-m", message], timeout=120)
        if commit.returncode != 0:
            detail = (commit.stderr or commit.stdout).strip()
            raise RuntimeError(detail or f"Git não conseguiu criar o checkpoint em {repo.name}.")
        head = run_hidden([git, "-C", str(repo), "rev-parse", "--short", "HEAD"], timeout=15)
        results.append({
            "repo": str(repo),
            "committed": True,
            "hash": head.stdout.strip() if head.returncode == 0 else "",
            "message": message,
        })
    return results


def reveal_in_file_manager(path: Path) -> None:
    """Open the real containing folder and select the file, never navigate into a ZIP."""
    path = path.expanduser().resolve()
    if os.name == "nt":
        try:
            if path.is_file():
                # explorer.exe expects /select, as its own argument. Keeping the path as a
                # separate argv item handles spaces correctly and avoids treating .zip as a folder.
                subprocess.Popen(["explorer.exe", "/select,", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:
            # If Explorer refuses /select for any reason, opening the exact parent folder is
            # still better than sending the user somewhere unrelated or inside the ZIP.
            target = path.parent if path.is_file() else path
            os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path.is_file() else path)])


def open_project_folders(project: Project) -> int:
    roots = [Path(value).expanduser().resolve() for value in project.paths]
    roots = [root for root in roots if root.is_dir()]
    if not roots:
        raise RuntimeError("Nenhuma pasta válida foi encontrada neste projeto.")
    for root in roots:
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", str(root)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(root)])
        else:
            subprocess.Popen(["xdg-open", str(root)])
    return len(roots)


def resolve_vscode_source() -> Optional[Path]:
    candidates: list[Path] = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        for base in [local, program_files, program_files_x86]:
            if not base:
                continue
            root = Path(base)
            candidates.extend([
                root / "Programs" / "Microsoft VS Code" / "Code.exe",
                root / "Microsoft VS Code" / "Code.exe",
                root / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
                root / "Microsoft VS Code" / "bin" / "code.cmd",
            ])
    for command in ("code.exe", "code", "code.cmd"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def open_project_vscode(project: Project) -> int:
    roots = [Path(value).expanduser().resolve() for value in project.paths]
    roots = [root for root in roots if root.is_dir()]
    if not roots:
        raise RuntimeError("Nenhuma pasta válida foi encontrada neste projeto.")
    source = resolve_vscode_source()
    if not source:
        raise RuntimeError("VS Code não foi encontrado. Instale o VS Code ou habilite o comando 'code'.")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for root in roots:
        if os.name == "nt" and source.suffix.lower() in {".cmd", ".bat"}:
            command_line = f'""{source}" -n "{root}""'
            subprocess.Popen(
                ["cmd.exe", "/d", "/s", "/c", command_line],
                cwd=str(root),
                creationflags=flags,
            )
        else:
            subprocess.Popen([str(source), "-n", str(root)], cwd=str(root), creationflags=flags)
    return len(roots)


def copy_text_and_files_windows(text: str, file_paths: Iterable[Path]) -> bool:
    """Put prompt text and one or more file references on the Windows clipboard."""
    files = [Path(path).resolve() for path in file_paths if isinstance(path, Path) and path.is_file()]
    if os.name != "nt" or not files:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.restype = wintypes.LPVOID
        user32.SetClipboardData.restype = wintypes.HANDLE
        GMEM_MOVEABLE_ZEROINIT = 0x0042
        CF_UNICODETEXT = 13
        CF_HDROP = 15

        text_bytes = str(text).encode("utf-16-le") + b"\x00\x00"
        joined_paths = "\x00".join(str(path) for path in files)
        path_bytes = joined_paths.encode("utf-16-le") + b"\x00\x00\x00\x00"
        drop_header = struct.pack("<IiiII", 20, 0, 0, 0, 1)

        h_text = kernel32.GlobalAlloc(GMEM_MOVEABLE_ZEROINIT, len(text_bytes))
        h_drop = kernel32.GlobalAlloc(GMEM_MOVEABLE_ZEROINIT, len(drop_header) + len(path_bytes))
        if not h_text or not h_drop:
            return False

        p_text = kernel32.GlobalLock(h_text)
        p_drop = kernel32.GlobalLock(h_drop)
        if not p_text or not p_drop:
            return False
        ctypes.memmove(p_text, text_bytes, len(text_bytes))
        ctypes.memmove(p_drop, drop_header, len(drop_header))
        ctypes.memmove(p_drop + len(drop_header), path_bytes, len(path_bytes))
        kernel32.GlobalUnlock(h_text)
        kernel32.GlobalUnlock(h_drop)

        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            ok_text = bool(user32.SetClipboardData(CF_UNICODETEXT, h_text))
            ok_drop = bool(user32.SetClipboardData(CF_HDROP, h_drop))
            return ok_text and ok_drop
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def copy_text_and_file_windows(text: str, file_path: Path) -> bool:
    return copy_text_and_files_windows(text, [file_path])


def normalize_chatgpt_chat_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    match = re.search(r"^(https://chatgpt\.com/(?:g/[^/]+/)?c/[A-Za-z0-9_-]+)", text)
    return match.group(1) if match else ""


def is_previewable_image(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".gif", ".ppm", ".pgm"}


def attachment_badge(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "IMG"
    if suffix == ".pdf":
        return "PDF"
    if suffix in {".zip", ".rar", ".7z"}:
        return "ZIP"
    if suffix in {".doc", ".docx"}:
        return "DOC"
    if suffix in {".xls", ".xlsx", ".csv"}:
        return "PLAN"
    if suffix in {".txt", ".md"}:
        return "TXT"
    return "ARQ"


def attachment_color(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return C["violet"]
    if suffix == ".pdf":
        return C["peach"]
    if suffix in {".zip", ".rar", ".7z"}:
        return C["lime_dark"]
    if suffix in {".doc", ".docx", ".txt", ".md"}:
        return C["cyan"]
    return C["muted"]


def _powershell_command(script: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA", "-Command", script]


def clipboard_file_paths_windows() -> list[Path]:
    if os.name != "nt":
        return []
    script = "Add-Type -AssemblyName System.Windows.Forms; $list = [System.Windows.Forms.Clipboard]::GetFileDropList(); foreach ($item in $list) { [Console]::Out.WriteLine($item) }"
    try:
        result = run_hidden(_powershell_command(script), timeout=10)
        paths = [Path(line.strip()).resolve() for line in result.stdout.splitlines() if line.strip()]
        return normalize_attachment_selection(paths)
    except Exception:
        return []


def save_clipboard_image_windows() -> Optional[Path]:
    if os.name != "nt":
        return None
    target = (TMP_DIR / "clipboard").resolve()
    target.mkdir(parents=True, exist_ok=True)
    file_path = target / f"clipboard-{stamp()}-{uuid.uuid4().hex[:6]}.png"
    literal = str(file_path).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
        f"if ($img -ne $null) {{ $path = '{literal}'; $img.Save($path, [System.Drawing.Imaging.ImageFormat]::Png); [Console]::Out.WriteLine($path) }}"
    )
    try:
        result = run_hidden(_powershell_command(script), timeout=12)
        saved = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if saved:
            candidate = Path(saved[-1]).resolve()
            return candidate if candidate.is_file() else None
    except Exception:
        return None
    return None


def clipboard_attachments_windows() -> list[Path]:
    files = clipboard_file_paths_windows()
    if files:
        return files
    image = save_clipboard_image_windows()
    return [image] if image else []


def browser_history_databases() -> list[Path]:
    """Known Chromium History databases on Windows, newest profiles first."""
    if os.name != "nt":
        return []
    local_text = os.environ.get("LOCALAPPDATA")
    if not local_text:
        return []
    local = Path(local_text)
    roots = [
        local / "Google" / "Chrome" / "User Data",
        local / "Microsoft" / "Edge" / "User Data",
        local / "BraveSoftware" / "Brave-Browser" / "User Data",
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        profiles = [root / "Default"]
        try:
            profiles.extend(sorted((p for p in root.glob("Profile *") if p.is_dir()), key=lambda p: p.name))
        except Exception:
            pass
        for profile in profiles:
            history = profile / "History"
            if history.is_file():
                found.append(history)
    return found


def recent_chatgpt_chat_urls(limit: int = 20) -> list[str]:
    """Read recent ChatGPT conversation URLs from local Chromium history.

    The live browser may lock its DB, so the file is copied to TMP_DIR first. Failures are
    intentionally ignored: link capture can still fall back to clipboard/manual linking.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for source in browser_history_databases():
        temp = TMP_DIR / f"history-{uuid.uuid4().hex}.sqlite"
        try:
            shutil.copy2(source, temp)
            conn = sqlite3.connect(str(temp))
            try:
                rows = conn.execute(
                    "SELECT url FROM urls WHERE url LIKE 'https://chatgpt.com/%' "
                    "AND url LIKE '%/c/%' ORDER BY last_visit_time DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            finally:
                conn.close()
            for (raw_url,) in rows:
                url = normalize_chatgpt_chat_url(str(raw_url or ""))
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
                    if len(urls) >= limit:
                        return urls
        except Exception:
            continue
        finally:
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass
    return urls


def latest_project_chat(project_id: str) -> Optional[dict]:
    for item in project_history(project_id, 200):
        if item.get("type") == "chat_link" and normalize_chatgpt_chat_url(str(item.get("url") or "")):
            return item
    return None

def _start_jsonrpc_reader(proc: subprocess.Popen) -> queue.Queue:
    q: queue.Queue = queue.Queue()

    def reader():
        try:
            while True:
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    q.put(None)
                    return
                q.put(line)
        except Exception:
            q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    return q


def _wait_jsonrpc_response(q: queue.Queue, wanted_id: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = q.get(timeout=max(0.05, min(0.35, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if line is None:
            break
        try:
            payload = json.loads(str(line).strip())
        except Exception:
            continue
        if payload.get("id") == wanted_id:
            return payload
    raise TimeoutError("O Codex demorou para informar os limites da conta.")

def codex_rate_limits() -> dict:
    """Read current ChatGPT/Codex rolling limits from the local Codex app-server."""
    source = resolve_codex_source()
    if not source:
        return {"available": False, "error": "Codex não encontrado"}
    command = codex_command(source, ["app-server", "--stdio"])
    kwargs = {
        "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
        "text": True, "encoding": "utf-8", "errors": "replace", "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(command, **kwargs)
    responses = _start_jsonrpc_reader(proc)
    try:
        init = {
            "method": "initialize", "id": 1,
            "params": {
                "clientInfo": {"name": "vortice", "title": "Vórtice", "version": APP_VERSION},
                "capabilities": {"experimentalApi": True},
            },
        }
        proc.stdin.write(json.dumps(init, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        init_response = _wait_jsonrpc_response(responses, 1, 8)
        if init_response.get("error"):
            raise RuntimeError(str(init_response["error"]))
        proc.stdin.write(json.dumps({"method": "initialized", "params": {}}, ensure_ascii=False) + "\n")
        proc.stdin.write(json.dumps({"method": "account/rateLimits/read", "id": 2}, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        response = _wait_jsonrpc_response(responses, 2, 10)
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        by_id = result.get("rateLimitsByLimitId") if isinstance(result.get("rateLimitsByLimitId"), dict) else {}
        snapshot = by_id.get("codex") if isinstance(by_id.get("codex"), dict) else None
        if not snapshot:
            candidate = result.get("rateLimits")
            snapshot = candidate if isinstance(candidate, dict) else {}

        windows = []
        for key in ("primary", "secondary"):
            item = snapshot.get(key) if isinstance(snapshot, dict) else None
            if isinstance(item, dict):
                windows.append(item)

        parsed = {"available": True, "five_hour": None, "weekly": None, "plan": snapshot.get("planType") if isinstance(snapshot, dict) else None}
        for item in windows:
            try:
                duration = int(item.get("windowDurationMins") or 0)
                used = max(0, min(100, int(item.get("usedPercent") or 0)))
                row = {
                    "used": used, "remaining": 100 - used,
                    "duration": duration, "resets_at": item.get("resetsAt"),
                }
                if duration == 300:
                    parsed["five_hour"] = row
                elif duration == 10080:
                    parsed["weekly"] = row
            except Exception:
                continue
        return parsed
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def format_reset_time(timestamp) -> str:
    try:
        dt = datetime.fromtimestamp(int(timestamp)).astimezone()
        if dt.date() == datetime.now().astimezone().date():
            return dt.strftime("%H:%M")
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return ""


def resolve_codex_source() -> Optional[str]:
    source = shutil.which("codex")
    if source:
        return source
    if os.name == "nt":
        for name in ("codex.cmd", "codex.exe", "codex.ps1"):
            source = shutil.which(name)
            if source:
                return source
        # The official standalone installer keeps Codex under CODEX_HOME.
        codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        candidates = [
            codex_home / "packages" / "standalone" / "current" / "bin" / "codex.exe",
            codex_home / "bin" / "codex.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return None


def codex_command(source: str, args: list[str]) -> list[str]:
    suffix = Path(source).suffix.lower()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        line = subprocess.list2cmdline([source, *args])
        return ["cmd.exe", "/d", "/s", "/c", line]
    if os.name == "nt" and suffix == ".ps1":
        return [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", source, *args,
        ]
    return [source, *args]


def codex_status() -> dict:
    source = resolve_codex_source()
    if not source:
        return {"installed": False, "authenticated": False, "version": "", "source": None, "auth": ""}
    version = ""
    auth = ""
    try:
        result = run_hidden(codex_command(source, ["--version"]), timeout=15)
        version = (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else "Codex"
    except Exception:
        version = "Codex"
    try:
        result = run_hidden(codex_command(source, ["login", "status"]), timeout=20)
        auth = f"{result.stdout}\n{result.stderr}".strip()
    except Exception as exc:
        auth = str(exc)
    authenticated = bool(re.search(r"logged in", auth, re.I)) and not bool(re.search(r"not logged in", auth, re.I))
    return {
        "installed": True,
        "authenticated": authenticated,
        "version": version,
        "source": source,
        "auth": auth,
    }


def bridge_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "files", "prompt", "notes"],
        "properties": {
            "summary": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
            "prompt": {"type": "string"},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def build_collector_prompt(project: Project, request_text: str, aliases: list[RootAlias], attachment_names: Optional[list[str]] = None) -> str:
    roots_text = "\n".join(f"- {item.alias}: {item.root}" for item in aliases)
    alias_text = ", ".join(item.alias for item in aliases)
    attachment_block = ""
    if attachment_names:
        attachment_list = "\n".join(f"- {name}" for name in attachment_names[:30])
        attachment_block = f"""
ANEXOS MANUAIS DO USUÁRIO
Os seguintes arquivos externos também acompanharão o pacote final e devem ser considerados contexto adicional no prompt de handoff:
{attachment_list}

Esses anexos não fazem parte do código do projeto. São referências complementares, como prints, PDFs, imagens, documentos ou exemplos visuais.
"""
    skills_text = build_skills_context(project.id)
    skills_block = f"""
SKILLS / INSTRUÇÕES CONFIGURADAS NO VÓRTICE
{skills_text}

Use essas instruções para orientar a seleção e o prompt final.
""" if skills_text else ""
    return f"""Você atua SOMENTE como coletor inteligente de contexto para um desenvolvimento que será feito em outro ChatGPT.

PEDIDO DO USUÁRIO:
{request_text}

RAÍZES DO PROJETO:
{roots_text}
{attachment_block}{skills_block}
Sua missão é auditar o projeto local e selecionar APENAS os arquivos necessários para outra IA implementar o pedido corretamente.
Você pode ler e pesquisar livremente, mas NÃO pode editar, criar, apagar, formatar ou reorganizar arquivos do projeto.
Não faça implementação. Não faça commit. Não altere dependências.

REGRAS DE SELEÇÃO:
1. Inclua arquivos diretamente alteráveis e também contratos, tipos, rotas, serviços, componentes, configs e testes indispensáveis para entender o fluxo real.
2. Prefira contexto suficiente e preciso em vez de mandar o repositório inteiro.
3. Nunca selecione .env, chaves, credenciais, tokens, certificados, node_modules, builds, caches ou segredos.
4. Retorne caminhos ABSOLUTOS de arquivos existentes. Não retorne diretórios.
5. Considere o estado atual dos arquivos como fonte da verdade e preserve funcionalidades já existentes.
6. O prompt final deve ser profissional, específico para este projeto e pronto para o usuário copiar e colar no ChatGPT junto com o CONTEXTO.zip.
7. No prompt final, instrua a outra IA a devolver SOMENTE os arquivos modificados em ZIP, mantendo os mesmos aliases de raiz ({alias_text}), sem incluir segredos, dependências instaladas ou builds.
8. Se o pedido envolver risco de regressão, peça explicitamente auditoria ponta a ponta e validação dos fluxos relacionados.
9. Não peça ao usuário informações que possam ser descobertas nos arquivos disponíveis. Faça a melhor seleção possível.

Não exponha raciocínio interno. No summary, dê apenas um resumo curto do que foi inspecionado. No notes, registre somente observações objetivas úteis para a implementação.
"""


def fallback_handoff_prompt(project: Project, request_text: str, aliases: list[RootAlias], result: dict, attachment_names: Optional[list[str]] = None) -> str:
    alias_text = ", ".join(item.alias for item in aliases)
    summary = str(result.get("summary") or "").strip()
    summary_block = f"\nCONTEXTO COLETADO\n{summary}\n" if summary else ""
    attachment_block = ""
    if attachment_names:
        attachment_lines = "\n".join(f"- {name}" for name in attachment_names[:40])
        attachment_block = f"""
ANEXOS EXTRAS DO USUÁRIO
Além do CONTEXTO.zip, considere também estes anexos enviados separadamente pelo usuário:
{attachment_lines}

Use esses anexos como contexto adicional quando forem relevantes.
"""
    skill_count = len(list_skill_files(None)) + len(list_skill_files(project.id))
    skill_note = f"\nSKILLS DO VÓRTICE\nHá {skill_count} arquivo(s) de skill dentro de _vortice/skills no CONTEXTO.zip. Leia e siga essas instruções quando forem aplicáveis.\n" if skill_count else ""
    return f"""Você recebeu o arquivo CONTEXTO.zip do projeto {project.name}.

OBJETIVO
{request_text}

O estado atual dos arquivos anexados é a fonte da verdade. Audite o fluxo real antes de editar, preserve funcionalidades existentes e implemente a alteração de forma completa, sem reescrever partes corretas sem necessidade.
{summary_block}{attachment_block}{skill_note}
Ao finalizar, valide os fluxos relacionados e devolva SOMENTE os arquivos modificados em um ZIP. Preserve exatamente a estrutura por raiz usada no CONTEXTO.zip: {alias_text}. Não inclua .env, credenciais, node_modules, builds, caches ou outros arquivos gerados.
"""


def friendly_codex_event(event: dict) -> Optional[tuple[str, str]]:
    event_type = str(event.get("type") or "")
    if event_type == "thread.started":
        return "connected", "Codex conectado ao projeto."
    if event_type == "turn.started":
        return "analyzing", "Mapeando o fluxo e as dependências."
    if event_type == "turn.completed":
        return "organizing", "Análise concluída. Organizando o pacote."
    if event_type in {"turn.failed", "error"}:
        err = event.get("error") or {}
        message = err.get("message") if isinstance(err, dict) else str(err)
        return "error", str(message or event.get("message") or "O Codex encontrou um erro.")

    item = event.get("item")
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "")
    if item_type == "command_execution":
        raw = str(item.get("command") or item.get("aggregated_output") or "")
        raw = re.sub(r"\s+", " ", raw).strip()
        if len(raw) > 125:
            raw = raw[:122] + "..."
        return "reading", f"Inspecionando: {raw}" if raw else "Inspecionando arquivos e referências."
    if item_type == "file_change":
        return "reading", "Relacionando arquivos envolvidos no fluxo."
    if item_type == "agent_message":
        return "organizing", "Montando a seleção final e o prompt."
    return None


def parse_json_maybe(raw: str) -> dict:
    text = str(raw or "").strip()
    if not text:
        raise RuntimeError("O Codex não retornou o resultado final.")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if fenced:
        try:
            value = json.loads(fenced.group(1).strip())
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise RuntimeError("A resposta final do Codex não pôde ser interpretada.")


def create_context_zip(project: Project, request_text: str, result: dict, aliases: list[RootAlias], attachments: Optional[list[Path]] = None) -> PrepareResult:
    roots = [item.root for item in aliases]
    selected: list[tuple[Path, Path, int]] = []
    seen: set[Path] = set()
    for candidate in result.get("files") or []:
        resolved = resolve_selected_file(str(candidate), roots)
        if not resolved:
            continue
        absolute, owner, size = resolved
        if absolute in seen:
            continue
        seen.add(absolute)
        selected.append((absolute, owner, size))

    if not selected:
        raise RuntimeError("O Codex não selecionou nenhum arquivo válido para o pacote.")

    stage = TMP_DIR / f"context-{uuid.uuid4().hex}"
    stage.mkdir(parents=True, exist_ok=True)
    alias_by_root = {item.root: item.alias for item in aliases}
    manifest_files: list[dict] = []
    manifest_attachments: list[dict] = []
    manifest_skills: list[dict] = []
    saved_attachments: list[Path] = []

    try:
        for absolute, owner, size in selected:
            alias = alias_by_root[owner]
            relative = absolute.relative_to(owner)
            destination = stage / alias / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(absolute, destination)
            manifest_files.append({
                "root": alias,
                "path": relative.as_posix(),
                "size": size,
            })

        for scope, skill_paths in (("global", list_skill_files(None)), ("project", list_skill_files(project.id))):
            for skill in skill_paths:
                destination = stage / "_vortice" / "skills" / scope / skill.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(skill, destination)
                manifest_skills.append({"scope": scope, "name": skill.name, "path": destination.relative_to(stage).as_posix()})

        attachment_dir = OUTPUT_DIR / f"ANEXOS-{slug(project.name)}-{stamp()}"
        used_names: set[str] = set()
        for source in normalize_attachment_selection(attachments or []):
            name = source.name
            base_name = Path(name).stem
            suffix = Path(name).suffix
            candidate = name
            counter = 2
            while candidate.lower() in used_names:
                candidate = f"{base_name}-{counter}{suffix}"
                counter += 1
            used_names.add(candidate.lower())
            attachment_dir.mkdir(parents=True, exist_ok=True)
            target = attachment_dir / candidate
            shutil.copy2(source, target)
            saved_attachments.append(target.resolve())
            manifest_attachments.append({
                "name": source.name,
                "savedAs": candidate,
                "size": target.stat().st_size,
            })

        manifest = {
            "bridgeVersion": APP_VERSION,
            "project": project.name,
            "request": request_text,
            "roots": [{"alias": a.alias, "sourceName": a.root.name} for a in aliases],
            "files": manifest_files,
            "attachments": manifest_attachments,
            "skills": manifest_skills,
        }
        (stage / "AI-BRIDGE-MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        zip_name = f"CONTEXTO-{slug(project.name)}-{stamp()}.zip"
        zip_path = OUTPUT_DIR / zip_name
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as zf:
            for file in stage.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(stage).as_posix())

        prompt = str(result.get("prompt") or "").strip()
        attachment_names = [str(item.get("savedAs") or item.get("name") or "") for item in manifest_attachments]
        if not prompt:
            prompt = fallback_handoff_prompt(project, request_text, aliases, result, attachment_names or None)
        elif attachment_names:
            attachment_lines = "\n".join(f"- {name}" for name in attachment_names)
            prompt = prompt.rstrip() + f"""

ANEXOS EXTRAS DO USUÁRIO
Além do CONTEXTO.zip, o usuário também enviará estes anexos separadamente:
{attachment_lines}

Use esses anexos como contexto adicional quando forem relevantes.
"""
        if manifest_skills:
            prompt = prompt.rstrip() + f"""

SKILLS DO VÓRTICE
O CONTEXTO.zip contém {len(manifest_skills)} arquivo(s) em _vortice/skills. Leia e aplique essas instruções quando forem relevantes para o pedido.
"""

        notes = result.get("notes") if isinstance(result.get("notes"), list) else []
        summary = str(result.get("summary") or "Contexto técnico selecionado e validado.").strip()
        if manifest_attachments:
            summary += f" · {len(manifest_attachments)} anexo(s) extra"
        return PrepareResult(zip_path, prompt, len(selected), summary, [str(n) for n in notes], attachment_count=len(manifest_attachments), attachment_paths=saved_attachments)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            member = Path(name)
            if member.is_absolute() or ".." in member.parts:
                raise RuntimeError(f"ZIP contém caminho inseguro: {name}")
            target = (destination / member).resolve()
            if not is_inside(target, destination.resolve()):
                raise RuntimeError(f"ZIP contém caminho fora da área segura: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def detect_alias_base(extract_dir: Path, aliases: set[str]) -> Path:
    direct = {item.name for item in extract_dir.iterdir() if item.is_dir()}
    if direct.intersection(aliases):
        return extract_dir
    entries = [item for item in extract_dir.iterdir() if item.name not in {"__MACOSX", ".DS_Store"}]
    dirs = [item for item in entries if item.is_dir()]
    files = [item for item in entries if item.is_file() and item.name not in {"AI-BRIDGE-MANIFEST.json", "_vortice-result.json", "VORTICE-RESULT.json", "vortice-result.json"}]
    if len(dirs) == 1 and not files:
        nested = {item.name for item in dirs[0].iterdir() if item.is_dir()}
        if nested.intersection(aliases):
            return dirs[0]
    return extract_dir


def preview_result_zip(project: Project, zip_path: Path) -> list[dict]:
    if not zip_path.exists() or not zip_path.is_file():
        raise RuntimeError("O ZIP selecionado não existe.")
    if zip_path.stat().st_size > MAX_RESULT_ZIP:
        raise RuntimeError("Esse ZIP é grande demais para uma resposta de código.")

    aliases = root_aliases(project.paths)
    alias_map = {item.alias: item.root for item in aliases}
    alias_names = set(alias_map)
    rows: list[dict] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        parsed: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        for info in infos:
            name = info.filename.replace("\\", "/").strip("/")
            if not name:
                continue
            parts = tuple(part for part in name.split("/") if part)
            if not parts or ".." in parts:
                rows.append({"path": name or info.filename, "status": "blocked", "size": info.file_size, "detail": "caminho inseguro"})
                continue
            parsed.append((info, parts))

        prefix_len = 0
        direct = any(parts and parts[0] in alias_names for _info, parts in parsed)
        if not direct:
            wrappers = {parts[0] for _info, parts in parsed if len(parts) >= 2 and parts[1] in alias_names}
            if len(wrappers) == 1:
                prefix_len = 1

        for info, original_parts in parsed:
            parts = original_parts[prefix_len:]
            display = "/".join(parts) if parts else "/".join(original_parts)
            if not parts or display in {"AI-BRIDGE-MANIFEST.json", "_vortice-result.json", "VORTICE-RESULT.json", "vortice-result.json", ".DS_Store"} or "__MACOSX" in parts:
                continue
            if len(parts) < 2:
                rows.append({"path": display, "status": "invalid", "size": info.file_size, "detail": "sem raiz do projeto"})
                continue
            alias = parts[0]
            root = alias_map.get(alias)
            if root is None:
                rows.append({"path": display, "status": "invalid", "size": info.file_size, "detail": "fora das raízes"})
                continue
            inner = Path(*parts[1:])
            target = (root / inner).resolve()
            if not is_inside(target, root) or is_blocked_file(target):
                rows.append({"path": display, "status": "blocked", "size": info.file_size, "detail": "bloqueado por segurança"})
                continue
            if info.file_size > MAX_CONTEXT_FILE * 2:
                rows.append({"path": display, "status": "blocked", "size": info.file_size, "detail": "arquivo grande demais"})
                continue
            if target.exists() and target.is_dir():
                rows.append({"path": display, "status": "invalid", "size": info.file_size, "detail": "destino é uma pasta"})
                continue

            status = "added"
            detail = "arquivo novo"
            if target.is_file():
                try:
                    with zf.open(info, "r") as stream:
                        same = sha256_stream(stream) == sha256(target)
                    status = "same" if same else "modified"
                    detail = "sem alteração" if same else "arquivo existente"
                except OSError:
                    status = "modified"
                    detail = "arquivo existente"
            rows.append({
                "path": display,
                "status": status,
                "size": info.file_size,
                "detail": detail,
                "target": str(target),
            })

    order = {"added": 0, "modified": 1, "same": 2, "blocked": 3, "invalid": 4}
    return sorted(rows, key=lambda row: (order.get(str(row.get("status")), 9), str(row.get("path") or "").lower()))


def apply_result_zip(project: Project, zip_path: Path, emit: Callable[[str, str], None]) -> ApplyResult:
    if not zip_path.exists() or not zip_path.is_file():
        raise RuntimeError("O ZIP selecionado não existe.")
    if zip_path.stat().st_size > MAX_RESULT_ZIP:
        raise RuntimeError("Esse ZIP é grande demais para uma resposta de código. Revise o pacote antes de aplicar.")

    aliases = root_aliases(project.paths)
    alias_map = {item.alias: item.root for item in aliases}
    alias_names = set(alias_map)
    extract_dir = TMP_DIR / f"apply-{uuid.uuid4().hex}"
    backup_path = BACKUP_DIR / f"{stamp()}-{slug(project.name)}"
    extract_dir.mkdir(parents=True, exist_ok=True)
    backup_path.mkdir(parents=True, exist_ok=True)

    changed: list[str] = []
    skipped: list[str] = []
    manifest_entries: list[dict] = []
    manifest_path = backup_path / "rollback-manifest.json"

    try:
        emit("apply", "Abrindo o ZIP em uma área isolada.")
        safe_extract_zip(zip_path, extract_dir)
        base = detect_alias_base(extract_dir, alias_names)

        candidates = [file for file in base.rglob("*") if file.is_file()]
        if not candidates:
            raise RuntimeError("O ZIP não contém arquivos para aplicar.")

        emit("apply", "Validando caminhos e bloqueando arquivos sensíveis.")
        valid_items: list[tuple[Path, Path, str, str]] = []
        for source in candidates:
            relative = source.relative_to(base)
            if relative.name in {"AI-BRIDGE-MANIFEST.json", "_vortice-result.json", "VORTICE-RESULT.json", "vortice-result.json", ".DS_Store"} or "__MACOSX" in relative.parts:
                continue
            if len(relative.parts) < 2:
                skipped.append(relative.as_posix())
                continue
            alias = relative.parts[0]
            root = alias_map.get(alias)
            if root is None:
                skipped.append(relative.as_posix())
                continue
            inner = Path(*relative.parts[1:])
            target = (root / inner).resolve()
            if not is_inside(target, root) or is_blocked_file(target):
                skipped.append(relative.as_posix())
                continue
            if source.stat().st_size > MAX_CONTEXT_FILE * 2:
                skipped.append(relative.as_posix())
                continue
            valid_items.append((source, target, alias, inner.as_posix()))

        if not valid_items:
            raise RuntimeError(
                "Nenhum arquivo do ZIP corresponde às raízes deste projeto. "
                "A resposta precisa preservar as pastas de raiz do CONTEXTO.zip."
            )

        emit("apply", "Criando backup antes de tocar no projeto.")
        for source, target, alias, inner in valid_items:
            existed = target.exists()
            if existed and target.is_file():
                if sha256(source) == sha256(target):
                    skipped.append(f"{alias}/{inner} (sem alteração)")
                    continue
                backup_file = backup_path / alias / inner
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_file)
            elif existed and target.is_dir():
                skipped.append(f"{alias}/{inner} (destino é pasta)")
                continue

            entry = {
                "alias": alias,
                "relative": inner,
                "target": str(target),
                "existed": existed,
            }
            manifest_entries.append(entry)
            manifest_path.write_text(json.dumps({
                "bridgeVersion": APP_VERSION,
                "project": project.name,
                "createdAt": now_iso(),
                "entries": manifest_entries,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            changed.append(f"{alias}/{inner}")

        if not changed:
            raise RuntimeError("O ZIP não trouxe nenhuma alteração nova para aplicar.")

        manifest = {
            "bridgeVersion": APP_VERSION,
            "project": project.name,
            "createdAt": now_iso(),
            "entries": manifest_entries,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        emit("apply", "Rodando git diff --check nas raízes disponíveis.")
        checks: list[dict] = []
        git_source = shutil.which("git")
        for item in aliases:
            check = {"root": str(item.root), "ok": True, "detail": "Git não encontrado."}
            if git_source:
                try:
                    probe = run_hidden([git_source, "-C", str(item.root), "rev-parse", "--is-inside-work-tree"], timeout=15)
                    if probe.returncode == 0:
                        diff = run_hidden([git_source, "-C", str(item.root), "diff", "--check"], timeout=30)
                        check["ok"] = diff.returncode == 0
                        check["detail"] = (diff.stdout or diff.stderr).strip() or "git diff --check: ok"
                    else:
                        check["detail"] = "Raiz fora de um repositório Git."
                except Exception as exc:
                    check["ok"] = False
                    check["detail"] = str(exc)
            checks.append(check)

        return ApplyResult(changed, skipped, backup_path, checks, manifest_path)
    except Exception as exc:
        if manifest_entries and manifest_path.exists():
            try:
                rollback_backup(backup_path)
            except Exception as rollback_exc:
                raise RuntimeError(f"{exc} · Também falhou o rollback automático: {rollback_exc}") from exc
            raise RuntimeError(f"{exc} · O Vórtice reverteu automaticamente os arquivos já tocados.") from exc
        raise
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def rollback_backup(backup_path: Path) -> int:
    manifest_path = backup_path / "rollback-manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("Esse backup não possui manifesto de rollback.")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = data.get("entries") or []
    count = 0
    for entry in reversed(entries):
        target = Path(entry["target"])
        alias = entry["alias"]
        relative = entry["relative"]
        existed = bool(entry["existed"])
        backup_file = backup_path / alias / relative
        if existed:
            if not backup_file.exists():
                raise RuntimeError(f"Backup ausente para {alias}/{relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, target)
        else:
            try:
                if target.exists() and target.is_file():
                    target.unlink()
            except OSError:
                pass
        count += 1
    return count


def _iter_agent_backup_files(project: Project) -> tuple[list[RootAlias], list[tuple[RootAlias, Path, Path, int]]]:
    aliases = root_aliases(project.paths)
    items: list[tuple[RootAlias, Path, Path, int]] = []
    total = 0
    for alias in aliases:
        if not alias.root.is_dir():
            continue
        for file in alias.root.rglob("*"):
            try:
                if not file.is_file() or is_blocked_file(file):
                    continue
                size = file.stat().st_size
            except OSError:
                continue
            if size > MAX_AGENT_BACKUP_FILE:
                continue
            if total + size > MAX_AGENT_BACKUP_TOTAL:
                raise RuntimeError(
                    "O backup de segurança do Vórtex ficou grande demais. "
                    "Remova builds/arquivos pesados das raízes ou use uma raiz de projeto mais específica."
                )
            relative = file.resolve().relative_to(alias.root.resolve())
            items.append((alias, file.resolve(), relative, size))
            total += size
    return aliases, items


def create_agent_backup(project: Project) -> Path:
    aliases, items = _iter_agent_backup_files(project)
    backup_path = BACKUP_DIR / f"nexo-{slug(project.name)}-{stamp()}-{uuid.uuid4().hex[:6]}.zip"
    created_epoch = time.time()
    manifest_files: list[dict] = []
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for alias, file, relative, size in items:
            arcname = f"{alias.alias}/{relative.as_posix()}"
            zf.write(file, arcname)
            manifest_files.append({
                "root": alias.alias,
                "path": relative.as_posix(),
                "size": size,
            })
        manifest = {
            "version": APP_VERSION,
            "kind": "nexo-backup",
            "projectId": project.id,
            "project": project.name,
            "createdAt": now_iso(),
            "createdEpoch": created_epoch,
            "roots": [{"alias": item.alias, "path": str(item.root)} for item in aliases],
            "files": manifest_files,
        }
        zf.writestr("NEXO-BACKUP-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return backup_path.resolve()


def restore_agent_backup(project: Project, backup_path: Path) -> tuple[int, int]:
    if not backup_path.is_file():
        raise RuntimeError("Backup do Vórtex não foi encontrado.")
    with zipfile.ZipFile(backup_path, "r") as zf:
        try:
            manifest = json.loads(zf.read("NEXO-BACKUP-MANIFEST.json").decode("utf-8", errors="replace"))
        except Exception as exc:
            raise RuntimeError(f"Backup do Vórtex inválido: {exc}")
        if str(manifest.get("projectId") or "") != project.id:
            raise RuntimeError("Esse backup pertence a outro projeto.")
        aliases = root_aliases(project.paths)
        alias_map = {item.alias: item.root.resolve() for item in aliases}
        before = {(str(entry.get("root") or ""), str(entry.get("path") or "")) for entry in manifest.get("files", []) if isinstance(entry, dict)}
        created_epoch = float(manifest.get("createdEpoch") or 0.0)
        restored = 0
        temp = TMP_DIR / f"restore-nexo-{uuid.uuid4().hex}"
        temp.mkdir(parents=True, exist_ok=True)
        try:
            safe_extract_zip(backup_path, temp)
            for alias_name, relative_text in before:
                root = alias_map.get(alias_name)
                if not root or not relative_text:
                    continue
                source = temp / alias_name / Path(relative_text)
                target = (root / Path(relative_text)).resolve()
                if not source.is_file() or not is_inside(target, root) or is_blocked_file(target):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                restored += 1

            removed = 0
            # Clean up files created during the Nexo turn. The timestamp guard avoids
            # touching older user files that were intentionally outside the snapshot.
            for alias in aliases:
                root = alias.root.resolve()
                if not root.is_dir():
                    continue
                for file in root.rglob("*"):
                    try:
                        if not file.is_file() or is_blocked_file(file):
                            continue
                        relative = file.resolve().relative_to(root).as_posix()
                        key = (alias.alias, relative)
                        if key in before:
                            continue
                        if created_epoch and file.stat().st_mtime < created_epoch - 2:
                            continue
                        file.unlink()
                        removed += 1
                    except Exception:
                        continue
            return restored, removed
        finally:
            shutil.rmtree(temp, ignore_errors=True)


def friendly_agent_event(event: dict) -> Optional[str]:
    event_type = str(event.get("type") or "")
    if event_type == "thread.started":
        return "Vórtex entrou no projeto."
    if event_type == "turn.started":
        return "Entendendo o pedido e escolhendo a melhor ação."
    if event_type == "turn.completed":
        return "Finalizando e conferindo o que foi feito."
    if event_type in {"turn.failed", "error"}:
        err = event.get("error") or {}
        return str(err.get("message") if isinstance(err, dict) else err or event.get("message") or "Falha no agente.")
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    kind = str(item.get("type") or "")
    if kind == "command_execution":
        command = re.sub(r"\s+", " ", str(item.get("command") or "")).strip()
        if len(command) > 110:
            command = command[:107] + "..."
        return f"Executando: {command}" if command else "Executando validações no projeto."
    if kind == "file_change":
        return "Ajustando arquivos do projeto com backup já garantido."
    if kind == "agent_message":
        return "Organizando a resposta para você."
    return None


def run_nexo_agent(
    project: Project,
    user_text: str,
    chat_history: list[dict],
    emit: Callable[[str], None],
    raw_emit: Callable[[str], None],
    usage_emit: Optional[Callable[[dict], None]] = None,
    task_id: str = "",
    task_title: str = "",
) -> AgentRunResult:
    status = codex_status()
    if not status.get("installed"):
        raise RuntimeError("Codex CLI não encontrado. O Vórtex usa o Codex local para operar o projeto.")
    if not status.get("authenticated"):
        raise RuntimeError("Codex está instalado, mas sem login. Abra um terminal, rode codex e faça login.")

    roots = [Path(value).expanduser().resolve() for value in project.paths]
    if not roots:
        raise RuntimeError("Esse projeto não possui raízes configuradas.")
    for root in roots:
        if not root.is_dir():
            raise RuntimeError(f"Raiz do projeto não encontrada: {root}")

    emit("Criando backup de segurança antes de qualquer ação.")
    backup_path = create_agent_backup(project)
    settings = load_settings()
    auto_rollback = bool(settings.get("autoRollback", True))
    skills = build_skills_context(project.id)
    roots_text = "\n".join(f"- {item}" for item in roots)
    history_lines: list[str] = []
    for item in chat_history[-12:]:
        role = "USUÁRIO" if item.get("role") == "user" else "VÓRTEX"
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if content:
            history_lines.append(f"{role}: {content[:1800]}")
    history_text = "\n".join(history_lines) or "Sem conversa anterior relevante."
    skill_block = skills if skills else "Nenhuma skill adicional configurada."
    prompt = f"""Você é Vórtex, o agente operacional residente do Vórtice.

Você está autorizado pelo usuário a trabalhar no projeto selecionado para resolver o pedido de ponta a ponta.
Pode ler e editar arquivos dentro das raízes, executar comandos locais necessários, diagnosticar bugs, corrigir código, rodar testes/builds/typechecks e conferir o resultado.

REGRAS DE SEGURANÇA
- Trabalhe somente nas raízes abaixo.
- Preserve alterações locais existentes. Não use git reset, git clean, checkout destrutivo, force push ou comandos equivalentes.
- Nunca leia, mostre ou altere .env, credenciais, tokens, certificados ou segredos.
- Não faça commit nem push a menos que o usuário peça explicitamente nesta mensagem.
- Não instale software global nem altere configurações do sistema operacional.
- Prefira mudanças cirúrgicas e compatíveis com a arquitetura atual.
- Se encontrar um problema decorrente da própria alteração, corrija-o antes de terminar.
- Rode as validações relevantes disponíveis no projeto.
- Se não conseguir concluir com segurança, pare e explique objetivamente o bloqueio.
- Não exponha cadeia de raciocínio. Mostre apenas ações úteis, resultado, arquivos relevantes e próximos passos realmente necessários.

TASK ATUAL
- id: {task_id or 'sem-id'}
- título/pedido: {task_title or 'Task geral do projeto'}
Tudo que você fizer nesta conversa pertence a esta task. Não misture contexto de outras tasks do projeto.

RAÍZES DO PROJETO
{roots_text}

SKILLS / INSTRUÇÕES
{skill_block}

CONVERSA RECENTE
{history_text}

PEDIDO ATUAL
{user_text}

Ao terminar, responda em português do Brasil, de forma curta e operacional: o que você fez, o que validou e se ficou algo pendente.
"""

    job_dir = TMP_DIR / f"nexo-{uuid.uuid4().hex}"
    job_dir.mkdir(parents=True, exist_ok=True)
    last_path = job_dir / "result.txt"
    log_path = job_dir / "events.jsonl"
    args = [
        "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check",
        "--cd", str(roots[0]), "--config", 'approval_policy="never"',
        "--output-last-message", str(last_path),
    ]
    model = str(settings.get("agentModel") or "").strip()
    if model:
        args.extend(["--model", model])
    for extra in roots[1:]:
        args.extend(["--add-dir", str(extra)])
    args.append("-")
    command = codex_command(str(status["source"]), args)
    popen_kwargs: dict = {
        "cwd": str(roots[0]),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(command, **popen_kwargs)
    stderr_lines: list[str] = []

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            value = line.rstrip("\r\n")
            if value:
                stderr_lines.append(value)
                raw_emit(f"[stderr] {value}")

    threading.Thread(target=read_stderr, daemon=True).start()
    assert process.stdin is not None
    process.stdin.write(prompt)
    process.stdin.close()
    usage = normalize_usage(None)
    with log_path.open("w", encoding="utf-8") as log_file:
        assert process.stdout is not None
        for line in process.stdout:
            value = line.rstrip("\r\n")
            if not value:
                continue
            log_file.write(value + "\n")
            log_file.flush()
            raw_emit(value)
            try:
                event = json.loads(value)
            except json.JSONDecodeError:
                continue
            if str(event.get("type") or "") == "turn.completed":
                merge_usage(usage, event.get("usage"))
                if usage_emit:
                    usage_emit(dict(usage))
            friendly = friendly_agent_event(event)
            if friendly:
                emit(friendly)

    code = process.wait()
    if code != 0 or not last_path.exists():
        rolled_back = False
        rollback_note = ""
        if auto_rollback:
            try:
                restored, removed = restore_agent_backup(project, backup_path)
                rolled_back = True
                rollback_note = f" O backup foi restaurado automaticamente ({restored} restaurados, {removed} novos removidos)."
            except Exception as rollback_exc:
                rollback_note = f" A tentativa de rollback automático também falhou: {rollback_exc}"
        tail = "\n".join(stderr_lines[-8:]).strip()
        raise RuntimeError((tail or f"Vórtex encerrou com código {code}.") + rollback_note)

    response = last_path.read_text(encoding="utf-8", errors="replace").strip() or "Concluído."
    return AgentRunResult(response=response, backup_path=backup_path, usage=dict(usage), rolled_back=False)


def prepare_with_codex(
    project: Project,
    request_text: str,
    emit: Callable[[str, str], None],
    raw_emit: Callable[[str], None],
    usage_emit: Optional[Callable[[dict], None]] = None,
    attachments: Optional[list[Path]] = None,
) -> PrepareResult:
    roots = [Path(p).expanduser().resolve() for p in project.paths]
    if not roots:
        raise RuntimeError("Este projeto não possui pastas configuradas.")
    for root in roots:
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"Pasta do projeto não encontrada: {root}")

    emit("checking", "Conferindo projeto, Codex e autenticação.")
    status = codex_status()
    if not status["installed"]:
        raise RuntimeError("Codex CLI não encontrado. Instale o Codex e abra o Vórtice novamente.")
    if not status["authenticated"]:
        raise RuntimeError(
            "Codex CLI encontrado, mas você ainda não entrou com sua conta. "
            "Abra um terminal, execute codex e faça login com ChatGPT."
        )
    emit("ready", f"{status['version'] or 'Codex'} conectado.")

    aliases = root_aliases(roots)
    job_dir = TMP_DIR / f"job-{uuid.uuid4().hex}"
    job_dir.mkdir(parents=True, exist_ok=True)
    schema_path = job_dir / "schema.json"
    last_path = job_dir / "result.json"
    log_path = job_dir / "codex.jsonl"
    schema_path.write_text(json.dumps(bridge_schema(), ensure_ascii=False, indent=2), encoding="utf-8")

    attachment_names = [path.name for path in normalize_attachment_selection(attachments or [])]
    prompt = build_collector_prompt(project, request_text, aliases, attachment_names or None)
    args = [
        "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check",
        "--cd", str(roots[0]),
        "--config", 'approval_policy="never"',
        "--output-schema", str(schema_path),
        "--output-last-message", str(last_path),
    ]
    for extra in roots[1:]:
        args.extend(["--add-dir", str(extra)])
    args.append("-")

    command = codex_command(str(status["source"]), args)
    emit("analyzing", "Codex entrou em modo somente leitura. Agora começa a caça pelo contexto certo.")

    popen_kwargs: dict = {
        "cwd": str(roots[0]),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    process = subprocess.Popen(command, **popen_kwargs)
    stderr_lines: list[str] = []

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            text = line.rstrip("\r\n")
            if text:
                stderr_lines.append(text)
                raw_emit(f"[stderr] {text}")

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    assert process.stdin is not None
    process.stdin.write(prompt)
    process.stdin.close()

    total_usage = normalize_usage(None)
    with log_path.open("w", encoding="utf-8") as log_file:
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip("\r\n")
            if not text:
                continue
            log_file.write(text + "\n")
            log_file.flush()
            raw_emit(text)
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if str(event.get("type") or "") == "turn.completed":
                merge_usage(total_usage, event.get("usage"))
                if usage_emit:
                    usage_emit(dict(total_usage))
            friendly = friendly_codex_event(event)
            if friendly:
                emit(*friendly)

    exit_code = process.wait()
    stderr_thread.join(timeout=1.0)
    if exit_code != 0:
        tail = "\n".join(stderr_lines[-8:]).strip()
        raise RuntimeError(tail or f"Codex encerrou com código {exit_code}.")
    if not last_path.exists():
        raise RuntimeError("O Codex terminou sem gerar o resultado estruturado.")

    emit("selecting", "Validando a seleção e cortando segredos, builds e arquivos desnecessários.")
    result = parse_json_maybe(last_path.read_text(encoding="utf-8"))
    emit("packing", "Empacotando somente o que realmente precisa viajar para o ChatGPT.")
    prepared = create_context_zip(project, request_text, result, aliases, attachments)
    prepared.usage = dict(total_usage)
    emit("done", f"Pacote fechado com {prepared.file_count} arquivo(s).")
    return prepared


# ----------------------------- UI helpers -----------------------------


def set_windows_app_id() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenAI.Vortice.Desktop")
    except Exception:
        pass


def center_window(window: tk.Toplevel | tk.Tk, width: int, height: int) -> None:
    window.update_idletasks()
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x = max(0, (sw - width) // 2)
    y = max(0, (sh - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def clear_children(widget: tk.Misc) -> None:
    for child in widget.winfo_children():
        child.destroy()


def make_button(
    parent: tk.Misc,
    text: str,
    command: Callable,
    *,
    bg: str = C["ink"],
    fg: str = C["white"],
    active_bg: Optional[str] = None,
    font=(FONT, 10, "bold"),
    padx=16,
    pady=10,
    cursor="hand2",
    anchor="center",
) -> tk.Button:
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active_bg or bg,
        activeforeground=fg,
        bd=0,
        relief="flat",
        highlightthickness=0,
        font=font,
        padx=padx,
        pady=pady,
        cursor=cursor,
        anchor=anchor,
    )
    return button


class Reactor(tk.Canvas):
    def __init__(self, parent: tk.Misc, width=300, height=230, **kwargs):
        super().__init__(parent, width=width, height=height, bg=C["dark"], highlightthickness=0, **kwargs)
        self._angle = 0.0
        self._running = False
        self._phase = "idle"
        self._pulse = 0.0
        self._after: Optional[str] = None
        self.bind("<Configure>", lambda _e: self.draw())
        self.draw()

    def set_phase(self, phase: str) -> None:
        self._phase = phase
        if phase in {"checking", "ready", "analyzing", "connected", "reading", "organizing", "selecting", "packing", "apply"}:
            self.start()
        elif phase == "error":
            self.stop()
            self.draw()
        elif phase == "done":
            self.stop()
            self._phase = "done"
            self.draw()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.tick()

    def stop(self) -> None:
        self._running = False
        if self._after:
            try:
                self.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def tick(self) -> None:
        if not self._running:
            return
        self._angle = (self._angle + 0.075) % (math.pi * 2)
        self._pulse = (self._pulse + 0.09) % (math.pi * 2)
        self.draw()
        self._after = self.after(45, self.tick)

    def draw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 260)
        h = max(self.winfo_height(), 185)
        cx = w / 2
        cy = h / 2 + 8

        # Quiet dotted kitchen field — enough movement to feel alive, never noisy.
        for x in range(28, int(w), 38):
            for y in range(34, int(h) - 16, 38):
                self.create_oval(x, y, x + 2, y + 2, fill="#3B3940", outline="")

        color = C["red"] if self._phase == "error" else (C["lime"] if self._phase == "done" else C["violet"])
        halo = 54 + (math.sin(self._pulse) + 1) * 4
        self.create_oval(cx - halo, cy - halo, cx + halo, cy + halo, outline="#45424A", width=1)
        self.create_oval(cx - 38, cy - 38, cx + 38, cy + 38, fill="#18171B", outline=color, width=3)

        # Friendly little core. It becomes a tiny face while idle/done.
        self.create_oval(cx - 19, cy - 19, cx + 19, cy + 19, fill=color, outline="")
        if self._phase in {"idle", "done"}:
            eye = C["dark"]
            self.create_oval(cx - 9, cy - 5, cx - 5, cy - 1, fill=eye, outline="")
            self.create_oval(cx + 5, cy - 5, cx + 9, cy - 1, fill=eye, outline="")
            if self._phase == "done":
                self.create_arc(cx - 8, cy - 1, cx + 8, cy + 11, start=200, extent=140, style="arc", outline=eye, width=2)
            else:
                self.create_line(cx - 5, cy + 7, cx + 5, cy + 7, fill=eye, width=2)
        else:
            self.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=C["dark"], outline="")

        radii = [(76, 45, C["cyan"]), (101, 64, C["peach"]), (124, 82, C["lime"])]
        for index, (rx, ry, dot_color) in enumerate(radii):
            self.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, outline="#403E45", width=1)
            angle = self._angle * (1 + index * 0.38) + index * 2.15
            x = cx + math.cos(angle) * rx
            y = cy + math.sin(angle) * ry
            r = 5 if index != 1 else 4
            self.create_oval(x - r, y - r, x + r, y + r, fill=dot_color, outline="")

        if self._phase not in {"idle", "error", "done"}:
            random.seed(int(self._angle * 1000))
            for _ in range(6):
                x = random.randint(30, max(31, int(w - 74)))
                y = random.randint(38, max(39, int(h - 30)))
                length = random.choice((7, 11, 15))
                self.create_line(x, y, x + length, y, fill="#55525B", width=2)

        label = {"idle": "FORNO FRIO", "error": "PAUSADO", "done": "PRONTINHO", "apply": "APLICANDO"}.get(self._phase, "COZINHANDO")
        self.create_text(cx, 18, text=label, fill="#A9A5AF", font=(FONT, 8, "bold"))

class VortexAvatar(tk.Canvas):
    """Small animated Vórtex mascot drawn natively so it stays crisp inside the desktop UI."""

    def __init__(self, parent: tk.Misc, width: int = 86, height: int = 86, *, bg: str | None = None, show_label: bool = True):
        self.bg_color = bg or C["dark"]
        super().__init__(parent, width=width, height=height, bg=self.bg_color, highlightthickness=0, bd=0)
        self.w = width
        self.h = height
        self.angle = 0.0
        self.state = "ready"
        self.show_label = show_label
        self._after: Optional[str] = None
        self._ticks = 0
        self.bind("<Destroy>", self._on_destroy)
        self._tick()

    def set_state(self, state: str) -> None:
        self.state = str(state or "ready")

    def _on_destroy(self, _event=None) -> None:
        if self._after:
            try:
                self.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _face(self, cx: float, cy: float, state: str, scale: float) -> None:
        ink = "#19171F"
        eye_y = cy - 3 * scale
        eye_dx = 8 * scale
        blink = state == "ready" and (self._ticks % 62 in {0, 1, 2})

        if state == "done":
            self.create_arc(cx - eye_dx - 4 * scale, eye_y - 3 * scale, cx - eye_dx + 4 * scale, eye_y + 5 * scale,
                            start=200, extent=140, style="arc", outline=ink, width=max(2, int(2 * scale)))
            self.create_arc(cx + eye_dx - 4 * scale, eye_y - 3 * scale, cx + eye_dx + 4 * scale, eye_y + 5 * scale,
                            start=200, extent=140, style="arc", outline=ink, width=max(2, int(2 * scale)))
            self.create_arc(cx - 8 * scale, cy + 1 * scale, cx + 8 * scale, cy + 13 * scale,
                            start=200, extent=140, style="arc", outline=ink, width=max(2, int(2 * scale)))
            return

        if state == "error":
            self.create_line(cx - eye_dx - 4 * scale, eye_y - 2 * scale, cx - eye_dx + 3 * scale, eye_y + 1 * scale,
                             fill=ink, width=max(2, int(2 * scale)))
            self.create_line(cx + eye_dx - 3 * scale, eye_y + 1 * scale, cx + eye_dx + 4 * scale, eye_y - 2 * scale,
                             fill=ink, width=max(2, int(2 * scale)))
            self.create_arc(cx - 6 * scale, cy + 7 * scale, cx + 6 * scale, cy + 16 * scale,
                            start=20, extent=140, style="arc", outline=ink, width=max(2, int(2 * scale)))
            return

        if state == "working":
            self.create_oval(cx - eye_dx - 3 * scale, eye_y - 3 * scale, cx - eye_dx + 3 * scale, eye_y + 3 * scale, fill=ink, outline="")
            self.create_oval(cx + eye_dx - 3 * scale, eye_y - 3 * scale, cx + eye_dx + 3 * scale, eye_y + 3 * scale, fill=ink, outline="")
            self.create_line(cx - 5 * scale, cy + 8 * scale, cx + 5 * scale, cy + 8 * scale, fill=ink, width=max(2, int(2 * scale)))
            return

        if state == "alert":
            self.create_oval(cx - eye_dx - 3.5 * scale, eye_y - 4 * scale, cx - eye_dx + 3.5 * scale, eye_y + 4 * scale, fill=ink, outline="")
            self.create_oval(cx + eye_dx - 3.5 * scale, eye_y - 4 * scale, cx + eye_dx + 3.5 * scale, eye_y + 4 * scale, fill=ink, outline="")
            self.create_oval(cx - 2.5 * scale, cy + 6 * scale, cx + 2.5 * scale, cy + 11 * scale, fill=ink, outline="")
            return

        if blink:
            self.create_line(cx - eye_dx - 3 * scale, eye_y, cx - eye_dx + 3 * scale, eye_y, fill=ink, width=max(2, int(2 * scale)))
            self.create_line(cx + eye_dx - 3 * scale, eye_y, cx + eye_dx + 3 * scale, eye_y, fill=ink, width=max(2, int(2 * scale)))
        else:
            self.create_oval(cx - eye_dx - 3 * scale, eye_y - 3 * scale, cx - eye_dx + 3 * scale, eye_y + 3 * scale, fill=ink, outline="")
            self.create_oval(cx + eye_dx - 3 * scale, eye_y - 3 * scale, cx + eye_dx + 3 * scale, eye_y + 3 * scale, fill=ink, outline="")
        self.create_arc(cx - 6 * scale, cy + 4 * scale, cx + 6 * scale, cy + 11 * scale,
                        start=200, extent=140, style="arc", outline=ink, width=max(2, int(2 * scale)))

    def _tick(self) -> None:
        try:
            if not self.winfo_exists():
                return
            self.delete("all")
            self._ticks += 1
            state = self.state
            speed = 0.23 if state == "working" else 0.08
            if state == "error":
                speed = 0.035
            self.angle += speed

            label_space = 12 if self.show_label else 0
            cx = self.w / 2
            cy = (self.h - label_space) / 2 + math.sin(self.angle * 0.75) * (1.5 if state != "working" else 0.8)
            scale = min(self.w, self.h - label_space) / 86.0

            # A soft orbital silhouette makes the mascot readable even at tiny sizes.
            orbit = "#6555DA" if state not in {"error", "done"} else (C["peach"] if state == "error" else C["lime_dark"])
            ring_w = max(1, int(2 * scale))
            self.create_arc(cx - 34 * scale, cy - 20 * scale, cx + 34 * scale, cy + 20 * scale,
                            start=18 + math.sin(self.angle) * 5, extent=150, style="arc", outline=orbit, width=ring_w)
            self.create_arc(cx - 36 * scale, cy - 22 * scale, cx + 36 * scale, cy + 22 * scale,
                            start=198 + math.sin(self.angle) * 5, extent=150, style="arc", outline="#45398D", width=ring_w)

            # Cute asymmetric orbit nodes — the lime one is the Vórtice visual signature.
            node_angle = self.angle * (1.8 if state == "working" else 1.0)
            nx = cx + math.cos(node_angle) * 31 * scale
            ny = cy + math.sin(node_angle) * 17 * scale
            nr = 5.2 * scale
            self.create_oval(nx - nr, ny - nr, nx + nr, ny + nr, fill=C["lime"], outline="")
            px = cx + math.cos(node_angle + math.pi) * 30 * scale
            py = cy + math.sin(node_angle + math.pi) * 16 * scale
            pr = 3.7 * scale
            self.create_oval(px - pr, py - pr, px + pr, py + pr, fill="#A99BFF", outline="")

            # Body: warm, rounded floating core rather than a generic loading spinner.
            body_fill = "#8D7BFF"
            body_outline = "#C6BBFF"
            if state == "working":
                body_fill, body_outline = C["lime"], "#E8FFB6"
            elif state == "done":
                body_fill, body_outline = "#BFF768", "#E9FFBD"
            elif state == "error":
                body_fill, body_outline = C["peach"], "#FFD7C5"
            elif state == "alert":
                body_fill, body_outline = C["cyan"], "#D5F8FF"

            rx, ry = 20 * scale, 18 * scale
            self.create_oval(cx - rx - 3 * scale, cy - ry - 3 * scale, cx + rx + 3 * scale, cy + ry + 3 * scale,
                             fill="#282432", outline="")
            self.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, fill=body_fill, outline=body_outline, width=ring_w)
            self.create_oval(cx - 10 * scale, cy - 12 * scale, cx + 4 * scale, cy - 6 * scale, fill="#D9D2FF", outline="")
            self._face(cx, cy, state, scale)

            # Little celebratory / thinking details make its current state obvious.
            if state == "done":
                for dx, dy, color in [(-29, -22, C["lime"]), (27, -20, C["cyan"]), (30, 18, "#B2A5FF")]:
                    x, y = cx + dx * scale, cy + dy * scale
                    s = 3 * scale
                    self.create_line(x - s, y, x + s, y, fill=color, width=max(1, int(2 * scale)))
                    self.create_line(x, y - s, x, y + s, fill=color, width=max(1, int(2 * scale)))
            elif state == "working":
                dots = [(-27, -25), (-31, -17), (27, 23)]
                for i, (dx, dy) in enumerate(dots):
                    r = (1.5 + ((self._ticks + i) % 3) * .4) * scale
                    self.create_oval(cx + dx * scale - r, cy + dy * scale - r, cx + dx * scale + r, cy + dy * scale + r,
                                     fill=C["lime"] if i == 0 else C["violet_soft"], outline="")
            elif state == "alert":
                self.create_text(cx + 25 * scale, cy - 23 * scale, text="!", fill=C["lime"], font=(FONT, max(8, int(13 * scale)), "bold"))

            if self.show_label:
                label = {"ready": "VÓRTEX", "working": "TÔ NISSO", "done": "PRONTO!", "error": "OPA…", "alert": "EI!"}.get(state, "VÓRTEX")
                self.create_text(cx, self.h - 6, text=label, fill="#918B99", font=(FONT, 6, "bold"))
            self._after = self.after(75, self._tick)
        except tk.TclError:
            return


class QuotaMeter(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str):
        super().__init__(parent, bg="#F8F5EF")
        self.title_label = tk.Label(self, text=title, bg="#F8F5EF", fg=C["muted"], font=(FONT, 7, "bold"))
        self.title_label.pack(anchor="w")
        row = tk.Frame(self, bg="#F8F5EF")
        row.pack(fill="x", pady=(1, 0))
        self.value_label = tk.Label(row, text="—", bg="#F8F5EF", fg=C["ink"], font=(MONO, 11, "bold"))
        self.value_label.pack(side="left")
        self.reset_label = tk.Label(row, text="", bg="#F8F5EF", fg=C["muted"], font=(MONO, 6))
        self.reset_label.pack(side="left", padx=(6, 0), anchor="s")
        self.bar = tk.Canvas(self, height=4, width=100, bg="#E5E0D8", highlightthickness=0)
        self.bar.pack(fill="x", pady=(4, 0))
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, 4, fill=C["violet"], outline="")
        self.bar.bind("<Configure>", self._redraw)
        self.remaining: Optional[int] = None

    def _redraw(self, _event=None):
        width = max(1, self.bar.winfo_width())
        fraction = (self.remaining or 0) / 100 if self.remaining is not None else 0
        self.bar.coords(self.bar_fill, 0, 0, int(width * fraction), 4)
        color = C["lime_dark"] if self.remaining is not None and self.remaining >= 50 else (C["peach"] if self.remaining is not None and self.remaining >= 20 else C["red"])
        if self.remaining is None:
            color = C["line_dark"]
        self.bar.itemconfigure(self.bar_fill, fill=color)

    def set_value(self, row: Optional[dict]):
        if not isinstance(row, dict):
            self.remaining = None
            self.value_label.configure(text="—", fg=C["muted"])
            self.reset_label.configure(text="indisponível")
            self._redraw()
            return
        self.remaining = max(0, min(100, int(row.get("remaining") or 0)))
        self.value_label.configure(text=f"{self.remaining}%", fg=C["ink"])
        reset = format_reset_time(row.get("resets_at"))
        self.reset_label.configure(text=f"reset {reset}" if reset else "")
        self._redraw()


class ScrollFrame(tk.Frame):
    _instances = weakref.WeakSet()
    _dispatcher_bound = False

    def __init__(self, parent: tk.Misc, bg: str):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview, bd=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window_id, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        ScrollFrame._instances.add(self)
        if not ScrollFrame._dispatcher_bound:
            self.bind_all("<MouseWheel>", ScrollFrame._dispatch_mousewheel, add="+")
            self.bind_all("<Button-4>", ScrollFrame._dispatch_mousewheel, add="+")
            self.bind_all("<Button-5>", ScrollFrame._dispatch_mousewheel, add="+")
            ScrollFrame._dispatcher_bound = True

    def _contains_widget(self, widget: Optional[tk.Misc]) -> bool:
        try:
            current = widget
            while current is not None:
                if current is self:
                    return True
                current = getattr(current, "master", None)
            return False
        except tk.TclError:
            return False

    @classmethod
    def _dispatch_mousewheel(cls, event):
        try:
            target = event.widget.winfo_containing(event.x_root, event.y_root)
        except Exception:
            target = getattr(event, "widget", None)
        for frame in list(cls._instances):
            if not frame._contains_widget(target):
                continue
            try:
                if getattr(event, "num", None) == 4:
                    delta = -1
                elif getattr(event, "num", None) == 5:
                    delta = 1
                else:
                    delta = int(-1 * (event.delta / 120))
                if delta:
                    frame.canvas.yview_scroll(delta, "units")
                return "break"
            except Exception:
                return None
        return None


class AttachmentStrip(tk.Frame):
    def __init__(self, parent: tk.Misc, bg: str, *, height: int = 136):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, height=height)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.scrollbar = tk.Scrollbar(self, orient="horizontal", command=self.canvas.xview, bd=0)
        self.canvas.configure(xscrollcommand=self.scrollbar.set)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.pack(fill="x", expand=True)
        self.scrollbar.pack(fill="x")
        for widget in (self, self.canvas, self.inner):
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Shift-MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_mousewheel, add="+")
            widget.bind("<Button-5>", self._on_mousewheel, add="+")

    def _on_mousewheel(self, event):
        try:
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                raw = int(getattr(event, "delta", 0) or 0)
                delta = int(-1 * (raw / 120)) if raw else 0
            if delta:
                self.canvas.xview_scroll(delta, "units")
                return "break"
        except Exception:
            return None
        return None


class ProjectDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Optional[Project] = None):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.result: Optional[Project] = None
        self.paths = list(project.paths) if project else []
        self.logo_path = project.logo_path if project else ""

        self.title("Editar projeto" if project else "Novo projeto")
        self.configure(bg=C["paper"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        center_window(self, 650, 610)

        body = tk.Frame(self, bg=C["paper"], padx=28, pady=26)
        body.pack(fill="both", expand=True)

        tk.Label(body, text="PROJETO LOCAL", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(
            body,
            text="Dê um endereço para o Codex.",
            bg=C["paper"], fg=C["ink"], font=(FONT, 20, "bold"),
        ).pack(anchor="w", pady=(4, 22))

        tk.Label(body, text="Nome", bg=C["paper"], fg=C["muted"], font=(FONT, 9, "bold")).pack(anchor="w")
        input_frame = tk.Frame(body, bg=C["line"], padx=1, pady=1)
        input_frame.pack(fill="x", pady=(7, 18))
        self.name_entry = tk.Entry(
            input_frame, bg=C["card"], fg=C["ink"], insertbackground=C["ink"], relief="flat",
            bd=0, font=(FONT, 11),
        )
        self.name_entry.pack(fill="x", ipady=11, padx=1, pady=1)
        if project:
            self.name_entry.insert(0, project.name)

        logo_row = tk.Frame(body, bg=C["paper"])
        logo_row.pack(fill="x", pady=(0, 18))
        tk.Label(logo_row, text="Logo", bg=C["paper"], fg=C["muted"], font=(FONT, 9, "bold")).pack(side="left")
        self.logo_label = tk.Label(logo_row, text=Path(self.logo_path).name if self.logo_path else "automática", bg=C["paper"], fg=C["muted"], font=(MONO, 8))
        self.logo_label.pack(side="left", padx=(10, 0))
        make_button(
            logo_row, "Escolher PNG", self.choose_logo,
            bg=C["violet_soft"], fg=C["violet"], active_bg="#E1DCFF", font=(FONT, 8, "bold"), padx=10, pady=6,
        ).pack(side="right")

        title_row = tk.Frame(body, bg=C["paper"])
        title_row.pack(fill="x")
        tk.Label(title_row, text="Pastas do projeto", bg=C["paper"], fg=C["muted"], font=(FONT, 9, "bold")).pack(side="left")
        make_button(
            title_row, "+ adicionar pasta", self.add_folder,
            bg=C["violet_soft"], fg=C["violet"], active_bg="#E1DCFF", font=(FONT, 9, "bold"), padx=12, pady=7,
        ).pack(side="right")

        self.path_box = tk.Frame(body, bg=C["card"], highlightbackground=C["line"], highlightthickness=1)
        self.path_box.pack(fill="both", expand=True, pady=(8, 10))
        self.render_paths()
        tk.Label(
            body,
            text="Você pode colocar WEB + BACKEND + APP no mesmo projeto. O Vórtice cria uma raiz separada para cada pasta no ZIP.",
            bg=C["paper"], fg=C["muted"], font=(FONT, 8), justify="left", wraplength=575,
        ).pack(anchor="w", pady=(0, 18))

        actions = tk.Frame(body, bg=C["paper"])
        actions.pack(fill="x")
        make_button(actions, "Cancelar", self.destroy, bg=C["paper"], fg=C["muted"], active_bg="#E9E5DD", padx=14).pack(side="right")
        make_button(actions, "Salvar projeto", self.save, bg=C["ink"], fg=C["white"], active_bg="#303030", padx=20).pack(side="right", padx=(0, 8))

        self.name_entry.focus_set()
        self.bind("<Escape>", lambda _e: self.destroy())

    def choose_logo(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self, title="Escolha a logo do projeto",
            filetypes=[("Imagem PNG/GIF", "*.png *.gif"), ("PNG", "*.png"), ("GIF", "*.gif")],
        )
        if filename:
            self.logo_path = str(Path(filename).resolve())
            self.logo_label.configure(text=Path(self.logo_path).name)

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Escolha uma pasta do projeto", mustexist=True)
        if folder:
            normalized = str(Path(folder).resolve())
            if normalized not in self.paths:
                self.paths.append(normalized)
                self.render_paths()

    def remove_folder(self, index: int) -> None:
        if 0 <= index < len(self.paths):
            self.paths.pop(index)
            self.render_paths()

    def render_paths(self) -> None:
        clear_children(self.path_box)
        if not self.paths:
            tk.Label(
                self.path_box, text="Nenhuma pasta ainda. Adicione a raiz principal do projeto.",
                bg=C["card"], fg=C["muted"], font=(FONT, 9), padx=16, pady=26,
            ).pack(anchor="w")
            return
        for index, path in enumerate(self.paths):
            row = tk.Frame(self.path_box, bg=C["card"], padx=14, pady=10)
            row.pack(fill="x")
            dot = tk.Canvas(row, width=12, height=12, bg=C["card"], highlightthickness=0)
            dot.create_oval(2, 2, 10, 10, fill=[C["lime_dark"], C["violet"], C["peach"], C["cyan"]][index % 4], outline="")
            dot.pack(side="left", padx=(0, 10))
            tk.Label(row, text=path, bg=C["card"], fg=C["ink"], font=(MONO, 8), anchor="w").pack(side="left", fill="x", expand=True)
            make_button(
                row, "remover", lambda i=index: self.remove_folder(i),
                bg=C["card"], fg=C["red"], active_bg="#F7E8E8", font=(FONT, 8, "bold"), padx=8, pady=4,
            ).pack(side="right")
            if index < len(self.paths) - 1:
                tk.Frame(self.path_box, bg=C["line"], height=1).pack(fill="x", padx=14)

    def save(self) -> None:
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning(APP_NAME, "Dê um nome para o projeto.", parent=self)
            return
        if not self.paths:
            messagebox.showwarning(APP_NAME, "Adicione pelo menos uma pasta do projeto.", parent=self)
            return
        missing = [p for p in self.paths if not Path(p).is_dir()]
        if missing:
            messagebox.showerror(APP_NAME, f"Esta pasta não existe mais:\n\n{missing[0]}", parent=self)
            return
        now = now_iso()
        self.result = Project(
            id=self.project.id if self.project else str(uuid.uuid4()),
            name=name,
            paths=list(dict.fromkeys(self.paths)),
            created_at=self.project.created_at if self.project and self.project.created_at else now,
            last_used_at=self.project.last_used_at if self.project else "",
            logo_path=self.logo_path,
        )
        self.parent_app.upsert_project(self.result)
        self.destroy()


class HistoryDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Project):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.title(f"Histórico · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(780, 560)
        center_window(self, 860, 650)
        self.transient(parent)

        body = tk.Frame(self, bg=C["paper"], padx=24, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="TASKS & PACOTES", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text=project.name, bg=C["paper"], fg=C["ink"], font=(FONT, 20, "bold")).pack(anchor="w", pady=(4, 4))
        tk.Label(
            body,
            text="Cada trabalho vira uma task local. Prompt, ZIP, anexos e chat continuam acessíveis mesmo depois de fechar o Vórtice.",
            bg=C["paper"], fg=C["muted"], font=(FONT, 9), wraplength=780, justify="left",
        ).pack(anchor="w", pady=(0, 16))

        sessions = project_sessions(project.id, 150)
        total = normalize_usage(None)
        for session in sessions:
            prepare = session.get("prepare") if isinstance(session.get("prepare"), dict) else None
            if prepare:
                merge_usage(total, prepare.get("usage"))
        stats = tk.Frame(body, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=12, pady=10)
        stats.pack(fill="x", pady=(0, 14))
        tk.Label(stats, text=f"{len(sessions)} tasks", bg=C["card"], fg=C["ink"], font=(MONO, 8, "bold")).pack(side="left")
        tk.Label(stats, text=f"Codex · {usage_text(total)}", bg=C["card"], fg=C["violet"], font=(MONO, 8, "bold")).pack(side="right")

        scroll = ScrollFrame(body, C["paper"])
        scroll.pack(fill="both", expand=True)
        if not sessions:
            tk.Label(scroll.inner, text="Ainda não há tasks por aqui.", bg=C["paper"], fg=C["muted"], font=(FONT, 10)).pack(anchor="w", pady=20)

        for session in sessions:
            prepare = session.get("prepare") if isinstance(session.get("prepare"), dict) else None
            chat = session.get("chat") if isinstance(session.get("chat"), dict) else None
            prompt_path = recover_prompt_from_context_zip(project, prepare or {})
            zip_path = history_zip_path(prepare or {})
            url = normalize_chatgpt_chat_url(str((chat or {}).get("url") or ""))
            card = tk.Frame(scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=13, pady=11)
            card.pack(fill="x", pady=(0, 9))
            top = tk.Frame(card, bg=C["card"])
            top.pack(fill="x")
            try:
                dt = datetime.fromisoformat(str(session.get("createdAt") or "")).astimezone()
                when = dt.strftime("%d/%m/%Y  %H:%M:%S")
            except Exception:
                when = str(session.get("createdAt") or "")
            tk.Label(top, text="TASK", bg=C["card"], fg=C["violet"], font=(FONT, 8, "bold")).pack(side="left")
            tk.Label(top, text=when, bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(side="right")
            request = str(session.get("request") or "Contexto do projeto")
            tk.Label(card, text=request, bg=C["card"], fg=C["ink"], font=(FONT, 10, "bold"), justify="left", wraplength=760).pack(anchor="w", pady=(6, 5))

            meta = [f"{len(session.get('rounds') or [])} rodada" + ("" if len(session.get('rounds') or []) == 1 else "s")]
            if prepare:
                meta.append(f"{prepare.get('fileCount', 0)} arquivos")
                attachment_count = int(prepare.get("attachmentCount") or len(history_attachment_paths(prepare)))
                if attachment_count:
                    meta.append(f"{attachment_count} anexo(s)")
                if usage_total(prepare.get("usage")):
                    meta.append(usage_text(prepare.get("usage")))
            meta.append("PROMPT ✓" if prompt_path and prompt_path.is_file() else "prompt antigo —")
            meta.append("ZIP ✓" if zip_path and zip_path.is_file() else "ZIP ausente")
            meta.append("CHAT ✓" if url else "chat não vinculado")
            tk.Label(card, text="   ·   ".join(meta), bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(anchor="w")

            buttons = tk.Frame(card, bg=C["card"])
            buttons.pack(fill="x", pady=(9, 0))
            prompt_btn = make_button(buttons, "Copiar prompt", lambda p=prompt_path: self.copy_prompt(p), bg=C["violet_soft"], fg=C["violet"], active_bg="#E0DAFF", font=(FONT, 8, "bold"), padx=10, pady=6)
            prompt_btn.pack(side="left")
            if not prompt_path or not prompt_path.is_file():
                prompt_btn.configure(state="disabled")
            zip_btn = make_button(buttons, "ZIP na pasta", lambda z=zip_path: self.reveal_zip(z), bg=C["violet_soft"], fg=C["violet"], active_bg="#E0DAFF", font=(FONT, 8, "bold"), padx=10, pady=6)
            zip_btn.pack(side="left", padx=(6, 0))
            if not zip_path or not zip_path.is_file():
                zip_btn.configure(state="disabled")
            attachments = history_attachment_paths(prepare or {})
            bundle_btn = make_button(buttons, "Copiar pacote", lambda p=prompt_path, z=zip_path, a=attachments: self.copy_bundle(p, z, a), bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=10, pady=6)
            bundle_btn.pack(side="left", padx=(6, 0))
            if not prompt_path or not prompt_path.is_file() or not zip_path or not zip_path.is_file():
                bundle_btn.configure(state="disabled")
            make_button(buttons, "Timeline", lambda tid=str(session.get("id") or ""): TaskTimelineDialog(self.parent_app, self.project, tid), bg=C["card"], fg=C["violet"], active_bg="#F0ECFF", font=(FONT, 8, "bold"), padx=9, pady=6).pack(side="right")
            if url:
                make_button(buttons, "Abrir chat ↗", lambda u=url: webbrowser.open_new_tab(u), bg=C["card"], fg=C["violet"], active_bg="#F0ECFF", font=(FONT, 8, "bold"), padx=9, pady=6).pack(side="right", padx=(0, 5))

        make_button(body, "Fechar", self.destroy, bg=C["ink"], fg=C["white"], active_bg="#303030", font=(FONT, 8, "bold"), padx=16, pady=8).pack(anchor="e", pady=(12, 0))
        self.bind("<Escape>", lambda _e: self.destroy())

    def copy_prompt(self, prompt_path: Optional[Path]) -> None:
        if not prompt_path or not prompt_path.is_file():
            return
        text = prompt_path.read_text(encoding="utf-8")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def reveal_zip(self, zip_path: Optional[Path]) -> None:
        if zip_path and zip_path.is_file():
            reveal_in_file_manager(zip_path)

    def copy_bundle(self, prompt_path: Optional[Path], zip_path: Optional[Path], attachments: Optional[list[Path]] = None) -> None:
        if not prompt_path or not prompt_path.is_file() or not zip_path or not zip_path.is_file():
            return
        prompt = prompt_path.read_text(encoding="utf-8")
        files = [zip_path, *(attachments or [])]
        if not copy_text_and_files_windows(prompt, files):
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.update()
        reveal_in_file_manager(zip_path)



class ChatDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Project):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.title(f"Chats · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(720, 500)
        center_window(self, 780, 570)
        self.transient(parent)

        body = tk.Frame(self, bg=C["paper"], padx=24, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="CHATS VINCULADOS", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text=project.name, bg=C["paper"], fg=C["ink"], font=(FONT, 20, "bold")).pack(anchor="w", pady=(4, 4))
        tk.Label(
            body,
            text="O Vórtice guarda só o link da conversa e o pedido local. Nenhuma sessão, cookie ou senha é salva.",
            bg=C["paper"], fg=C["muted"], font=(FONT, 9), wraplength=690, justify="left",
        ).pack(anchor="w", pady=(0, 16))

        items = [item for item in project_history(project.id, 200) if item.get("type") == "chat_link"]
        scroll = ScrollFrame(body, C["paper"])
        scroll.pack(fill="both", expand=True)
        if not items:
            tk.Label(scroll.inner, text="Nenhum chat vinculado ainda.", bg=C["paper"], fg=C["muted"], font=(FONT, 10)).pack(anchor="w", pady=20)

        for item in items:
            url = normalize_chatgpt_chat_url(str(item.get("url") or ""))
            if not url:
                continue
            card = tk.Frame(scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=12, pady=10)
            card.pack(fill="x", pady=(0, 8))
            top = tk.Frame(card, bg=C["card"])
            top.pack(fill="x")
            try:
                dt = datetime.fromisoformat(str(item.get("createdAt"))).astimezone()
                when = dt.strftime("%d/%m/%Y  %H:%M:%S")
            except Exception:
                when = str(item.get("createdAt") or "")
            tk.Label(top, text="CHATGPT", bg=C["card"], fg=C["violet"], font=(FONT, 8, "bold")).pack(side="left")
            tk.Label(top, text=when, bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(side="right")
            detail = str(item.get("request") or item.get("summary") or "Conversa do projeto")
            if len(detail) > 180:
                detail = detail[:177] + "..."
            tk.Label(card, text=detail, bg=C["card"], fg=C["ink"], font=(FONT, 9), justify="left", wraplength=650).pack(anchor="w", pady=(6, 6))
            link_label = tk.Label(card, text=url, bg=C["card"], fg=C["muted"], font=(MONO, 7), anchor="w")
            link_label.pack(fill="x")
            buttons = tk.Frame(card, bg=C["card"])
            buttons.pack(fill="x", pady=(8, 0))
            make_button(
                buttons, "Abrir", lambda u=url: webbrowser.open_new_tab(u),
                bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=12, pady=6,
            ).pack(side="left")
            make_button(
                buttons, "Copiar link", lambda u=url: self.copy_url(u),
                bg=C["violet_soft"], fg=C["violet"], active_bg="#E0DAFF", font=(FONT, 8, "bold"), padx=10, pady=6,
            ).pack(side="left", padx=(6, 0))

        footer = tk.Frame(body, bg=C["paper"])
        footer.pack(fill="x", pady=(12, 0))
        make_button(
            footer, "Vincular link copiado", self.link_clipboard,
            bg=C["violet_soft"], fg=C["violet"], active_bg="#E0DAFF", font=(FONT, 8, "bold"), padx=12, pady=7,
        ).pack(side="left")
        make_button(footer, "Fechar", self.destroy, bg=C["ink"], fg=C["white"], active_bg="#303030", font=(FONT, 8, "bold"), padx=16, pady=8).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())

    def copy_url(self, url: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(url)
        self.update()

    def link_clipboard(self) -> None:
        if self.parent_app.save_chat_link_from_clipboard():
            self.destroy()
            ChatDialog(self.parent_app, self.project)

class GitDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Project):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.title(f"Git · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(760, 560)
        center_window(self, 820, 640)
        self.transient(parent)

        body = tk.Frame(self, bg=C["paper"], padx=24, pady=22)
        body.pack(fill="both", expand=True)
        head = tk.Frame(body, bg=C["paper"])
        head.pack(fill="x")
        copy = tk.Frame(head, bg=C["paper"])
        copy.pack(side="left", fill="x", expand=True)
        tk.Label(copy, text="REDE DE SEGURANÇA", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(copy, text=f"Git · {project.name}", bg=C["paper"], fg=C["ink"], font=(FONT, 20, "bold")).pack(anchor="w", pady=(4, 3))
        tk.Label(copy, text="Branch, alterações e últimos commits. Nada faz push.", bg=C["paper"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w")
        make_button(
            head, "Atualizar", self.refresh,
            bg=C["paper"], fg=C["muted"], active_bg="#E7E2D8", font=(FONT, 8, "bold"), padx=11, pady=7,
        ).pack(side="right", anchor="n")

        actions = tk.Frame(body, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=12, pady=10)
        actions.pack(fill="x", pady=(16, 12))
        action_copy = tk.Frame(actions, bg=C["card"])
        action_copy.pack(side="left", fill="x", expand=True)
        tk.Label(action_copy, text="Checkpoint local", bg=C["card"], fg=C["ink"], font=(FONT, 9, "bold")).pack(anchor="w")
        tk.Label(action_copy, text="Cria commit das mudanças seguras atuais. Segredos/builds ficam de fora.", bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", pady=(2, 0))
        make_button(
            actions, "CRIAR CHECKPOINT", self.create_checkpoint,
            bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=13, pady=8,
        ).pack(side="right")

        self.scroll = ScrollFrame(body, C["paper"])
        self.scroll.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.refresh()

    def refresh(self) -> None:
        clear_children(self.scroll.inner)
        snapshots = project_git_snapshot(self.project)
        if not resolve_git_source():
            tk.Label(self.scroll.inner, text="Git não foi encontrado no PATH.", bg=C["paper"], fg=C["red"], font=(FONT, 10, "bold")).pack(anchor="w", pady=18)
            return
        if not snapshots:
            tk.Label(self.scroll.inner, text="Esse projeto ainda não está dentro de um repositório Git.", bg=C["paper"], fg=C["muted"], font=(FONT, 10)).pack(anchor="w", pady=18)
            return

        for snap in snapshots:
            card = tk.Frame(self.scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=14, pady=12)
            card.pack(fill="x", pady=(0, 10))
            if not snap.get("ok"):
                tk.Label(card, text=Path(str(snap.get("path") or "repo")).name, bg=C["card"], fg=C["ink"], font=(FONT, 10, "bold")).pack(anchor="w")
                tk.Label(card, text=str(snap.get("error") or "Falha ao ler repositório."), bg=C["card"], fg=C["red"], font=(FONT, 8), wraplength=700, justify="left").pack(anchor="w", pady=(4, 0))
                continue

            top = tk.Frame(card, bg=C["card"])
            top.pack(fill="x")
            repo_path = Path(str(snap["path"]))
            tk.Label(top, text=repo_path.name or str(repo_path), bg=C["card"], fg=C["ink"], font=(FONT, 11, "bold")).pack(side="left")
            dirty = int(snap.get("dirty") or 0)
            badge_text = "LIMPO" if dirty == 0 else f"{dirty} ALTERAÇÃO" + ("" if dirty == 1 else "ÕES")
            badge_fg = C["green"] if dirty == 0 else C["peach"]
            tk.Label(top, text=badge_text, bg=C["card"], fg=badge_fg, font=(MONO, 7, "bold")).pack(side="right")

            branch_bits = [str(snap.get("branch") or "sem branch")]
            if snap.get("upstream"):
                branch_bits.append(str(snap["upstream"]))
                if snap.get("ahead") or snap.get("behind"):
                    branch_bits.append(f"↑{snap.get('ahead', 0)} ↓{snap.get('behind', 0)}")
            tk.Label(card, text="  ·  ".join(branch_bits), bg=C["card"], fg=C["violet"], font=(MONO, 8, "bold")).pack(anchor="w", pady=(4, 2))
            tk.Label(card, text=str(repo_path), bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(anchor="w", pady=(0, 8))

            changes = snap.get("changes") or []
            if changes:
                tk.Label(card, text="AGORA", bg=C["card"], fg=C["muted"], font=(FONT, 7, "bold")).pack(anchor="w", pady=(2, 3))
                for line in changes[:8]:
                    tk.Label(card, text=line, bg=C["card"], fg=C["ink"], font=(MONO, 7), anchor="w", justify="left").pack(fill="x")
                if len(changes) > 8:
                    tk.Label(card, text=f"+ {len(changes) - 8} outras alterações", bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", pady=(2, 0))

            commits = snap.get("commits") or []
            if commits:
                tk.Label(card, text="ÚLTIMOS COMMITS", bg=C["card"], fg=C["muted"], font=(FONT, 7, "bold")).pack(anchor="w", pady=(10, 4))
                for commit in commits:
                    row = tk.Frame(card, bg=C["card"])
                    row.pack(fill="x", pady=1)
                    tk.Label(row, text=commit["hash"], bg=C["card"], fg=C["violet"], font=(MONO, 7, "bold"), width=9, anchor="w").pack(side="left")
                    tk.Label(row, text=commit["message"], bg=C["card"], fg=C["ink"], font=(FONT, 8), anchor="w").pack(side="left", fill="x", expand=True)
                    tk.Label(row, text=commit["when"], bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(side="right")

    def create_checkpoint(self) -> None:
        message = simpledialog.askstring(
            APP_NAME,
            "Mensagem do checkpoint (pode deixar vazio para usar a automática):",
            parent=self,
        )
        if message is None:
            return
        if not message.strip():
            message = f"chore: checkpoint Vórtice {datetime.now().strftime('%d/%m %H:%M')}"
        if not messagebox.askyesno(
            APP_NAME,
            "Criar checkpoint local agora?\n\nNada será enviado para remoto e arquivos sensíveis continuam bloqueados.",
            parent=self,
        ):
            return
        try:
            results = create_git_checkpoint(self.project, message)
            committed = [item for item in results if item.get("committed")]
            if committed:
                append_history(
                    self.project.id,
                    "git_checkpoint",
                    summary=message,
                    commits=[{"repo": item.get("repo"), "hash": item.get("hash")} for item in committed],
                )
                hashes = ", ".join(str(item.get("hash") or "commit") for item in committed)
                messagebox.showinfo(APP_NAME, f"Checkpoint criado: {hashes}", parent=self)
            else:
                messagebox.showinfo(APP_NAME, "Nada novo para criar checkpoint.", parent=self)
            self.parent_app.render_projects()
            self.parent_app.render_selected_project()
            self.refresh()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)




class ProjectChecklistDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Project):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.title(f"Checklist · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(860, 620)
        center_window(self, 980, 720)
        self.transient(parent)

        body = tk.Frame(self, bg=C["paper"], padx=22, pady=20)
        body.pack(fill="both", expand=True)
        head = tk.Frame(body, bg=C["paper"])
        head.pack(fill="x")
        copy = tk.Frame(head, bg=C["paper"])
        copy.pack(side="left", fill="x", expand=True)
        tk.Label(copy, text="CHECKLIST DO PROJETO", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(copy, text=project.name, bg=C["paper"], fg=C["ink"], font=(FONT, 20, "bold")).pack(anchor="w", pady=(3, 2))
        tk.Label(copy, text="Um backlog simples: texto, status e prints. Quando chegar a hora, vira uma Task com um clique.", bg=C["paper"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w")
        self.progress_label = tk.Label(head, text="", bg=C["paper"], fg=C["green"], font=(MONO, 9, "bold"))
        self.progress_label.pack(side="right", anchor="n", pady=6)

        add = tk.Frame(body, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=12, pady=10)
        add.pack(fill="x", pady=(14, 12))
        self.entry = tk.Entry(add, bg=C["white"], fg=C["ink"], insertbackground=C["ink"], relief="flat", highlightbackground=C["line"], highlightthickness=1, font=(FONT, 9))
        self.entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry.bind("<Return>", lambda _e: self.add_item())
        make_button(add, "+ Adicionar", self.add_item, bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=12, pady=8).pack(side="left", padx=(8, 0))

        self.scroll = ScrollFrame(body, C["paper"])
        self.scroll.pack(fill="both", expand=True)
        footer = tk.Frame(body, bg=C["paper"])
        footer.pack(fill="x", pady=(10, 0))
        tk.Label(footer, text="○ fazer  ·  ◐ andamento  ·  ✓ concluído", bg=C["paper"], fg=C["muted"], font=(FONT, 8)).pack(side="left")
        make_button(footer, "Fechar", self.destroy, bg=C["ink"], fg=C["white"], active_bg="#303030", font=(FONT, 8, "bold"), padx=16, pady=8).pack(side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.refresh()

    def add_item(self) -> None:
        title = self.entry.get().strip()
        if not title:
            return
        self.parent_app.create_checklist_item(self.project.id, title)
        self.entry.delete(0, "end")
        self.refresh()

    def refresh(self) -> None:
        clear_children(self.scroll.inner)
        items = self.parent_app.project_checklist_items(self.project.id)
        done = len([item for item in items if str(item.get("status") or "") == "done"])
        doing = len([item for item in items if str(item.get("status") or "") == "doing"])
        self.progress_label.configure(text=f"{done}/{len(items)} concluídos · {doing} em andamento")
        if not items:
            card = tk.Frame(self.scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=18, pady=18)
            card.pack(fill="x", pady=6)
            tk.Label(card, text="Checklist vazio por enquanto.", bg=C["card"], fg=C["ink"], font=(FONT, 11, "bold")).pack(anchor="w")
            tk.Label(card, text="Joga aqui tudo que ainda precisa entrar no projeto. Não precisa virar Task antes da hora.", bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", pady=(4, 0))
            return
        labels = {"todo": "○ FAZER", "doing": "◐ ANDAMENTO", "done": "✓ CONCLUÍDO"}
        colors = {"todo": C["muted"], "doing": "#B66D43", "done": C["green"]}
        for item in items:
            status = str(item.get("status") or "todo")
            card = tk.Frame(self.scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=13, pady=11)
            card.pack(fill="x", pady=(0, 9))
            top = tk.Frame(card, bg=C["card"])
            top.pack(fill="x")
            make_button(top, labels.get(status, "○ FAZER"), lambda iid=str(item.get("id") or ""): self.cycle(iid), bg="#F4F0E9", fg=colors.get(status, C["muted"]), active_bg="#ECE7DE", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left")
            title = tk.Label(top, text=str(item.get("title") or ""), bg=C["card"], fg=C["ink"] if status != "done" else C["muted"], font=(FONT, 10, "bold"), anchor="w", justify="left", wraplength=610)
            title.pack(side="left", fill="x", expand=True, padx=(10, 8))
            task_id = str(item.get("taskId") or "")
            if task_id:
                tk.Label(top, text="TASK ✓", bg="#EDF7E8", fg=C["green"], font=(MONO, 6, "bold"), padx=6, pady=3).pack(side="right", padx=(0, 6))
            make_button(top, "×", lambda iid=str(item.get("id") or ""): self.remove(iid), bg=C["card"], fg=C["muted"], active_bg="#F3EEE7", font=(FONT, 8, "bold"), padx=5, pady=3).pack(side="right")

            note = str(item.get("note") or "").strip()
            if note:
                tk.Label(card, text=note, bg=C["card"], fg=C["muted"], font=(FONT, 8), justify="left", wraplength=700).pack(anchor="w", pady=(7, 0))

            files = checklist_attachment_paths(item)
            if files:
                file_row = tk.Frame(card, bg=C["card"])
                file_row.pack(fill="x", pady=(8, 0))
                for path in files[:5]:
                    label = path.name if len(path.name) <= 22 else path.name[:19] + "..."
                    make_button(file_row, f"▣ {label}", lambda p=path: self.parent_app.open_attachment(p), bg="#F6F2EB", fg=C["violet"], active_bg="#ECE6FA", font=(FONT, 7), padx=7, pady=5).pack(side="left", padx=(0, 5))
                if len(files) > 5:
                    tk.Label(file_row, text=f"+{len(files)-5}", bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(side="left")

            actions = tk.Frame(card, bg=C["card"])
            actions.pack(fill="x", pady=(9, 0))
            make_button(actions, "+ arquivo", lambda iid=str(item.get("id") or ""): self.add_files(iid), bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left")
            make_button(actions, "Colar print", lambda iid=str(item.get("id") or ""): self.paste_print(iid), bg="#EDF6FF", fg=C["violet"], active_bg="#DAEEFF", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left", padx=(5, 0))
            make_button(actions, "nota", lambda iid=str(item.get("id") or ""): self.edit_note(iid), bg="#F2EEE7", fg=C["muted"], active_bg="#E8E1D7", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left", padx=(5, 0))
            make_button(actions, "Começar Task →" if not task_id else "Abrir Task →", lambda iid=str(item.get("id") or ""): self.open_task(iid), bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 7, "bold"), padx=9, pady=5).pack(side="right")

    def cycle(self, item_id: str) -> None:
        self.parent_app.cycle_checklist_item(item_id, self.project.id)
        self.refresh()

    def remove(self, item_id: str) -> None:
        if messagebox.askyesno(APP_NAME, "Remover este item do checklist?", parent=self):
            self.parent_app.remove_checklist_item(item_id, self.project.id)
            self.refresh()

    def add_files(self, item_id: str) -> None:
        selected = filedialog.askopenfilenames(parent=self, title="Adicionar prints/arquivos ao checklist")
        if selected:
            self.parent_app.add_checklist_attachments(item_id, selected, self.project.id)
            self.refresh()

    def paste_print(self, item_id: str) -> None:
        files = clipboard_attachments_windows()
        if not files:
            messagebox.showinfo(APP_NAME, "Não encontrei imagem ou arquivo no clipboard agora.", parent=self)
            return
        self.parent_app.add_checklist_attachments(item_id, files, self.project.id)
        self.refresh()

    def edit_note(self, item_id: str) -> None:
        item = next((entry for entry in self.parent_app.checklists.get(self.project.id, []) if str(entry.get("id") or "") == str(item_id)), None)
        if not item:
            return
        value = simpledialog.askstring(APP_NAME, "Nota rápida deste item", initialvalue=str(item.get("note") or ""), parent=self)
        if value is None:
            return
        item["note"] = value.strip()
        item["updatedAt"] = now_iso()
        save_checklists(self.parent_app.checklists)
        self.refresh()

    def open_task(self, item_id: str) -> None:
        self.parent_app.activate_checklist_item(item_id, self.project.id)
        self.destroy()


class ProjectHomeDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Project):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.title(f"Visão do projeto · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(720, 520)
        center_window(self, 820, 600)
        self.transient(parent)
        self.body = tk.Frame(self, bg=C["paper"], padx=24, pady=22)
        self.body.pack(fill="both", expand=True)
        self.render()
        self.bind("<Escape>", lambda _e: self.destroy())

    def render(self) -> None:
        clear_children(self.body)
        sessions = project_sessions(self.project.id, 500)
        checklist = self.parent_app.project_checklist_items(self.project.id)
        doing = [t for t in sessions if str(t.get("status") or "doing") == "doing"]
        review = [t for t in sessions if str(t.get("status") or "doing") == "review"]
        done = [t for t in sessions if str(t.get("status") or "doing") == "done"]
        todo_items = [x for x in checklist if str(x.get("status") or "todo") == "todo"]
        doing_items = [x for x in checklist if str(x.get("status") or "todo") == "doing"]
        done_items = [x for x in checklist if str(x.get("status") or "todo") == "done"]
        events = project_history(self.project.id, 120)
        attention = next((e for e in events if e.get("type") in {"result_rejected", "autopilot_rollback"}), None)

        tk.Label(self.body, text="VISÃO DO PROJETO", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(self.body, text=self.project.name, bg=C["paper"], fg=C["ink"], font=(FONT, 21, "bold")).pack(anchor="w", pady=(4, 3))
        health = "atenção recente" if attention else "fluxo tranquilo ✓"
        tk.Label(self.body, text=f"{len(doing)} task em andamento · {len(review)} aguardando homologação · {len(done)} concluídas · {health}", bg=C["paper"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w")

        stats = tk.Frame(self.body, bg=C["paper"])
        stats.pack(fill="x", pady=(16, 12))
        for title, value, detail in [
            ("CHECKLIST", f"{len(todo_items)} pendentes", f"{len(doing_items)} em andamento · {len(done_items)} concluídos"),
            ("TASKS", f"{len(doing)+len(review)} abertas", f"{len(review)} para homologar"),
            ("MEMÓRIA", f"{len(project_memory(self.project).get('memoryFacts') or [])} fatos", "local · zero tokens"),
        ]:
            card = tk.Frame(stats, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=13, pady=11)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            tk.Label(card, text=title, bg=C["card"], fg=C["violet"], font=(FONT, 7, "bold")).pack(anchor="w")
            tk.Label(card, text=value, bg=C["card"], fg=C["ink"], font=(FONT, 12, "bold")).pack(anchor="w", pady=(4, 1))
            tk.Label(card, text=detail, bg=C["card"], fg=C["muted"], font=(FONT, 7)).pack(anchor="w")

        latest = sessions[0] if sessions else None
        card = tk.Frame(self.body, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=15, pady=13)
        card.pack(fill="x", pady=(0, 12))
        tk.Label(card, text="ONDE VOCÊ PAROU", bg=C["card"], fg=C["violet"], font=(FONT, 7, "bold")).pack(anchor="w")
        if latest:
            title = session_title(str(latest.get("request") or ""), "Task")
            status = str(latest.get("status") or "doing")
            human = {"doing":"em andamento", "review":"aguardando homologação", "done":"concluída"}.get(status, status)
            tk.Label(card, text=title, bg=C["card"], fg=C["ink"], font=(FONT, 11, "bold"), wraplength=710, justify="left").pack(anchor="w", pady=(5, 2))
            tk.Label(card, text=f"{human} · {len(latest.get('rounds') or [])} rodada(s)", bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(anchor="w")
            make_button(card, "Continuar Task →", lambda tid=str(latest.get("id") or ""): self._continue(tid), bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=11, pady=7).pack(anchor="w", pady=(10, 0))
        else:
            tk.Label(card, text="Ainda não há Task rodada neste projeto.", bg=C["card"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", pady=(5, 0))

        actions = tk.Frame(self.body, bg=C["paper"])
        actions.pack(fill="x", pady=(2, 0))
        make_button(actions, "+ Nova Task", self._new_task, bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=12, pady=8).pack(side="left")
        make_button(actions, "Checklist", self._checklist, bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 8, "bold"), padx=12, pady=8).pack(side="left", padx=(7, 0))
        make_button(actions, "VS Code", self._vscode, bg="#F2EEE7", fg=C["ink"], active_bg="#E8E1D7", font=(FONT, 8, "bold"), padx=12, pady=8).pack(side="left", padx=(7, 0))
        make_button(actions, "Fechar", self.destroy, bg=C["paper"], fg=C["muted"], active_bg="#ECE7DF", font=(FONT, 8, "bold"), padx=12, pady=8).pack(side="right")

    def _continue(self, task_id: str) -> None:
        self.parent_app.select_session(self.project.id, task_id)
        self.parent_app.set_mode("prepare")
        self.destroy()

    def _new_task(self) -> None:
        if self.parent_app.selected_project_id != self.project.id:
            self.parent_app.select_project(self.project.id)
        self.parent_app.new_request()
        self.parent_app.set_mode("prepare")
        self.destroy()

    def _checklist(self) -> None:
        ProjectChecklistDialog(self.parent_app, self.project)

    def _vscode(self) -> None:
        if self.parent_app.selected_project_id != self.project.id:
            self.parent_app.select_project(self.project.id)
        self.parent_app.open_selected_project_vscode()


class RoundComparisonDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Project, task_id: str):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.task_id = task_id
        self.title(f"Comparar rodadas · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(760, 520)
        center_window(self, 860, 610)
        self.transient(parent)
        body = tk.Frame(self, bg=C["paper"], padx=22, pady=20)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="COMPARAÇÃO DE RODADAS", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text="O que realmente mudou de uma tentativa para a outra.", bg=C["paper"], fg=C["ink"], font=(FONT, 18, "bold")).pack(anchor="w", pady=(3, 3))
        tk.Label(body, text="Comparação local por caminhos do ZIP/aplicação. Zero tokens do Codex.", bg=C["paper"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", pady=(0, 12))
        scroll = ScrollFrame(body, C["paper"])
        scroll.pack(fill="both", expand=True)
        task = next((x for x in project_sessions(project.id, 500) if str(x.get("id") or "") == task_id), None)
        rounds = sorted((task or {}).get("rounds") or [], key=lambda r: int(r.get("number") or 0))
        if len(rounds) < 2:
            tk.Label(scroll.inner, text="Precisa de pelo menos duas rodadas para comparar.", bg=C["paper"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", pady=20)
        else:
            for previous, current in zip(rounds[:-1], rounds[1:]):
                prev_paths = self._round_paths(previous)
                cur_paths = self._round_paths(current)
                added = sorted(cur_paths - prev_paths)
                removed = sorted(prev_paths - cur_paths)
                common = sorted(cur_paths & prev_paths)
                card = tk.Frame(scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=13, pady=11)
                card.pack(fill="x", pady=(0, 9))
                tk.Label(card, text=f"R{int(previous.get('number') or 0)} → R{int(current.get('number') or 0)}", bg=C["card"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
                tk.Label(card, text=f"{len(added)} novo(s) · {len(removed)} saiu(ram) · {len(common)} continuam sendo tocados", bg=C["card"], fg=C["ink"], font=(FONT, 9, "bold")).pack(anchor="w", pady=(5, 5))
                lines = []
                lines += [f"+ {x}" for x in added[:8]]
                lines += [f"− {x}" for x in removed[:8]]
                if not lines and common:
                    lines = [f"= {x}" for x in common[:8]]
                if lines:
                    tk.Label(card, text="\n".join(lines), bg=C["card"], fg=C["muted"], font=(MONO, 7), justify="left", wraplength=740).pack(anchor="w")
        make_button(body, "Fechar", self.destroy, bg=C["ink"], fg=C["white"], active_bg="#303030", font=(FONT, 8, "bold"), padx=15, pady=8).pack(anchor="e", pady=(10,0))
        self.bind("<Escape>", lambda _e: self.destroy())

    def _round_paths(self, rnd: dict) -> set[str]:
        apply = rnd.get("apply") if isinstance(rnd.get("apply"), dict) else None
        if apply and isinstance(apply.get("changed"), list):
            return {str(x) for x in apply.get("changed") if str(x).strip()}
        result = rnd.get("result") if isinstance(rnd.get("result"), dict) else None
        path = resolve_saved_path((result or {}).get("resultZipPath") or (result or {}).get("zipPath")) if result else None
        if path and path.is_file():
            try:
                return {str(row.get("path") or "") for row in preview_result_zip(self.project, path) if str(row.get("status") or "") in {"added","modified"}}
            except Exception:
                return set()
        return set()


class RecoveryCenterDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp", project: Project, task_id: str = ""):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.task_id = task_id
        self.title(f"Recuperação · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(760, 520)
        center_window(self, 850, 600)
        self.transient(parent)
        body = tk.Frame(self, bg=C["paper"], padx=22, pady=20)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="CENTRO DE RECUPERAÇÃO", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text="Pontos seguros do código.", bg=C["paper"], fg=C["ink"], font=(FONT, 18, "bold")).pack(anchor="w", pady=(3, 3))
        tk.Label(body, text="Fica escondidinho até dar ruim. Cada aplicação com backup pode voltar daqui.", bg=C["paper"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", pady=(0, 12))
        scroll = ScrollFrame(body, C["paper"])
        scroll.pack(fill="both", expand=True)
        events = [e for e in project_history(project.id, 500) if resolve_saved_path(e.get("backup")) and (not task_id or str(e.get("taskId") or e.get("sessionId") or "") == task_id)]
        if not events:
            tk.Label(scroll.inner, text="Nenhum ponto de restauração disponível para este recorte.", bg=C["paper"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", pady=18)
        for e in events:
            backup = resolve_saved_path(e.get("backup"))
            if not backup or not backup.exists():
                continue
            card = tk.Frame(scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=12, pady=10)
            card.pack(fill="x", pady=(0,8))
            try: when = datetime.fromisoformat(str(e.get("createdAt") or "")).astimezone().strftime("%d/%m/%Y %H:%M")
            except Exception: when = str(e.get("createdAt") or "")
            rn = int(e.get("roundNumber") or 0)
            tk.Label(card, text=f"{when}" + (f" · R{rn}" if rn else ""), bg=C["card"], fg=C["violet"], font=(MONO, 7, "bold")).pack(anchor="w")
            tk.Label(card, text=str(e.get("summary") or "Backup do código"), bg=C["card"], fg=C["ink"], font=(FONT, 9, "bold"), wraplength=690, justify="left").pack(anchor="w", pady=(4,6))
            make_button(card, "Restaurar este ponto", lambda ev=e: self._restore(ev), bg="#FFF0E8", fg="#A65E36", active_bg="#F8E4D8", font=(FONT, 7, "bold"), padx=9, pady=6).pack(anchor="w")
        make_button(body, "Fechar", self.destroy, bg=C["ink"], fg=C["white"], active_bg="#303030", font=(FONT, 8, "bold"), padx=15, pady=8).pack(anchor="e", pady=(10,0))
        self.bind("<Escape>", lambda _e: self.destroy())

    def _restore(self, event: dict) -> None:
        self.parent_app.restore_history_backup(event)


class TaskTimelineDialog(tk.Toplevel):
    IMPORTANT = {"prepare", "result_received", "result_rejected", "apply", "rollback", "followup", "autopilot_rollback", "task_status", "homologation"}
    NAMES = {
        "prepare":"Contexto preparado", "handoff":"Enviado ao ChatGPT", "chat_link":"Chat vinculado",
        "result_received":"ZIP recebido", "result_rejected":"ZIP bloqueado", "apply":"Código aplicado",
        "rollback":"Rollback", "nexo_action":"Vórtex agiu", "nexo_rollback":"Rollback do Vórtex",
        "followup":"Ajuste iniciado", "autopilot_retry":"Correção automática pedida",
        "autopilot_rollback":"Autopilot reverteu", "task_status":"Status alterado", "homologation":"Homologação",
    }
    def __init__(self, parent: "BridgeApp", project: Project, task_id: str):
        super().__init__(parent)
        self.parent_app = parent
        self.project = project
        self.task_id = task_id
        self.show_technical = False
        self.title(f"Histórico da Task · {project.name}")
        self.configure(bg=C["paper"])
        self.minsize(820, 600)
        center_window(self, 920, 690)
        self.transient(parent)
        self.body = tk.Frame(self, bg=C["paper"], padx=22, pady=20)
        self.body.pack(fill="both", expand=True)
        self.render()
        self.bind("<Escape>", lambda _e: self.destroy())

    def render(self) -> None:
        clear_children(self.body)
        task = next((item for item in project_sessions(self.project.id, 500) if str(item.get("id") or "") == self.task_id), None)
        if not task:
            tk.Label(self.body, text="Essa Task não está mais no histórico.", bg=C["paper"], fg=C["muted"], font=(FONT, 10)).pack(anchor="w")
            return
        top = tk.Frame(self.body, bg=C["paper"]); top.pack(fill="x")
        copy = tk.Frame(top, bg=C["paper"]); copy.pack(side="left", fill="x", expand=True)
        tk.Label(copy, text="HISTÓRICO DA TASK", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(copy, text=session_title(str(task.get("request") or ""), "Task"), bg=C["paper"], fg=C["ink"], font=(FONT, 18, "bold"), wraplength=630, justify="left").pack(anchor="w", pady=(3, 3))
        rounds = task.get("rounds") or []
        status = str(task.get("status") or "doing")
        human = {"doing":"em andamento", "review":"aguardando homologação", "done":"concluída"}.get(status,status)
        tk.Label(copy, text=f"{len(rounds)} rodada" + ("" if len(rounds)==1 else "s") + f" · {human}", bg=C["paper"], fg=C["muted"], font=(MONO, 8)).pack(anchor="w")
        actions = tk.Frame(top, bg=C["paper"]); actions.pack(side="right", anchor="n")
        if status == "done":
            make_button(actions, "Reabrir", lambda: self._set_status("doing"), bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 8, "bold"), padx=10, pady=7).pack(side="left")
        else:
            make_button(actions, "Reprovar / ajustar", self._reject, bg="#FFF0E8", fg="#A65E36", active_bg="#F8E4D8", font=(FONT, 8, "bold"), padx=10, pady=7).pack(side="left", padx=(0,5))
            make_button(actions, "Aprovar ✓", self._approve, bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=10, pady=7).pack(side="left")

        toolrow = tk.Frame(self.body, bg=C["paper"]); toolrow.pack(fill="x", pady=(12, 4))
        make_button(toolrow, "Comparar rodadas", lambda: RoundComparisonDialog(self.parent_app, self.project, self.task_id), bg="#F2EEE7", fg=C["violet"], active_bg="#E7E0F5", font=(FONT, 7, "bold"), padx=9, pady=5).pack(side="left")
        make_button(toolrow, "Recuperação", lambda: RecoveryCenterDialog(self.parent_app, self.project, self.task_id), bg="#F2EEE7", fg=C["muted"], active_bg="#E7E0F5", font=(FONT, 7, "bold"), padx=9, pady=5).pack(side="left", padx=(5,0))
        make_button(toolrow, "Esconder detalhes técnicos" if self.show_technical else "Ver detalhes técnicos", self._toggle_details, bg=C["paper"], fg=C["muted"], active_bg="#ECE7DF", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="right")

        scroll = ScrollFrame(self.body, C["paper"]); scroll.pack(fill="both", expand=True, pady=(6, 10))
        events = [item for item in load_history() if item.get("projectId") == self.project.id and str(item.get("taskId") or item.get("sessionId") or "") == self.task_id]
        events.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        if not self.show_technical:
            events = [e for e in events if str(e.get("type") or "") in self.IMPORTANT]
        if not events:
            tk.Label(scroll.inner, text="Ainda não há eventos importantes nesta Task.", bg=C["paper"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", pady=12)
        for event in events:
            kind = str(event.get("type") or "")
            card = tk.Frame(scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=12, pady=10); card.pack(fill="x", pady=(0,8))
            head = tk.Frame(card, bg=C["card"]); head.pack(fill="x")
            rn = int(event.get("roundNumber") or 0)
            tk.Label(head, text=(self.NAMES.get(kind, kind or "Evento") + (f" · R{rn}" if rn else "")).upper(), bg=C["card"], fg=C["red"] if kind in {"result_rejected","autopilot_rollback"} else C["violet"], font=(FONT, 7, "bold")).pack(side="left")
            try: when = datetime.fromisoformat(str(event.get("createdAt") or "")).astimezone().strftime("%d/%m %H:%M")
            except Exception: when = str(event.get("createdAt") or "")
            tk.Label(head, text=when, bg=C["card"], fg=C["muted"], font=(MONO, 7)).pack(side="right")
            detail = str(event.get("summary") or event.get("request") or event.get("status") or "").strip()
            if detail:
                tk.Label(card, text=detail, bg=C["card"], fg=C["ink"], font=(FONT, 8), wraplength=760, justify="left").pack(anchor="w", pady=(5,0))
            zip_path = history_zip_path(event) or resolve_saved_path(event.get("resultZipPath") or event.get("zipPath"))
            if zip_path and zip_path.is_file():
                make_button(card, "Abrir ZIP", lambda p=zip_path: reveal_in_file_manager(p), bg="#F2EEE7", fg=C["violet"], active_bg="#E7E0F5", font=(FONT, 7, "bold"), padx=8, pady=5).pack(anchor="w", pady=(7,0))
        footer = tk.Frame(self.body, bg=C["paper"]); footer.pack(fill="x")
        chat = task.get("chat") if isinstance(task.get("chat"), dict) else None
        url = normalize_chatgpt_chat_url(str((chat or {}).get("url") or ""))
        if url:
            make_button(footer, "Abrir chat ↗", lambda: webbrowser.open_new_tab(url), bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 8, "bold"), padx=10, pady=7).pack(side="left")
        make_button(footer, "Fechar", self.destroy, bg=C["ink"], fg=C["white"], active_bg="#303030", font=(FONT, 8, "bold"), padx=16, pady=8).pack(side="right")

    def _toggle_details(self) -> None:
        self.show_technical = not self.show_technical
        self.render()
    def _approve(self) -> None:
        self.parent_app.approve_task(self.task_id)
        self.render()
    def _reject(self) -> None:
        self.parent_app.reject_task(self.task_id)
        self.destroy()
    def _set_status(self, value: str) -> None:
        self.parent_app.set_task_status(self.task_id, value)
        self.render()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "BridgeApp"):
        super().__init__(parent)
        self.parent_app = parent
        self.title("Configurações · Vórtice")
        self.configure(bg=C["paper"])
        self.minsize(780, 620)
        center_window(self, 850, 700)
        self.transient(parent)
        self.settings = load_settings()
        self.project = parent.selected_project()
        self.auto_rollback = tk.BooleanVar(value=bool(self.settings.get("autoRollback", True)))
        self.notifications_var = tk.BooleanVar(value=bool(self.settings.get("notificationsEnabled", True)))
        self.sounds_var = tk.BooleanVar(value=bool(self.settings.get("soundsEnabled", True)))
        self.model_var = tk.StringVar(value=str(self.settings.get("agentModel") or ""))
        self.automation_var = tk.StringVar(value=str(self.settings.get("automationMode") or "assist"))
        self.autopilot_retry_var = tk.BooleanVar(value=bool(self.settings.get("autopilotRetry", True)))
        meta = project_memory(self.project) if self.project else {}
        self.project_glossary = dict(meta.get("glossary") or {}) if isinstance(meta.get("glossary"), dict) else {}

        body = tk.Frame(self, bg=C["paper"], padx=22, pady=20); body.pack(fill="both", expand=True)
        tk.Label(body, text="CONFIGURAÇÕES", bg=C["paper"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text="Só o que importa fica na frente.", bg=C["paper"], fg=C["ink"], font=(FONT, 19, "bold")).pack(anchor="w", pady=(4,2))
        nav = tk.Frame(body, bg=C["paper"]); nav.pack(fill="x", pady=(12,10))
        self.nav_buttons = {}
        for key, label in [("general","Geral"),("automation","Automação"),("skills","Skills"),("project","Projeto"),("advanced","Avançado")]:
            btn = make_button(nav, label, lambda k=key:self.show_page(k), bg="#EEEAE2", fg=C["muted"], active_bg=C["violet_soft"], font=(FONT, 8, "bold"), padx=10, pady=6)
            btn.pack(side="left", padx=(0,5)); self.nav_buttons[key]=btn
        self.host = tk.Frame(body, bg=C["paper"]); self.host.pack(fill="both", expand=True)
        footer = tk.Frame(body, bg=C["paper"]); footer.pack(fill="x", pady=(10,0))
        make_button(footer, "Fechar", self.close_and_save, bg=C["ink"], fg=C["white"], active_bg="#303030", font=(FONT, 8, "bold"), padx=16, pady=8).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close_and_save)
        self.bind("<Escape>", lambda _e:self.close_and_save())
        self.show_page("general")

    def show_page(self, page: str) -> None:
        self._save_project_page_if_present()
        clear_children(self.host)
        for key, btn in self.nav_buttons.items():
            btn.configure(bg=C["violet_soft"] if key==page else "#EEEAE2", fg=C["violet"] if key==page else C["muted"])
        scroll=ScrollFrame(self.host,C["paper"]); scroll.pack(fill="both",expand=True)
        if page=="general": self._render_general(scroll.inner)
        elif page=="automation": self._render_automation(scroll.inner)
        elif page=="skills": self._render_skills(scroll.inner)
        elif page=="project": self._render_project(scroll.inner)
        else: self._render_advanced(scroll.inner)

    def _card(self,parent,title,desc=""):
        card=tk.Frame(parent,bg=C["card"],highlightbackground=C["line"],highlightthickness=1,padx=15,pady=14); card.pack(fill="x",pady=(0,12))
        tk.Label(card,text=title,bg=C["card"],fg=C["violet"],font=(FONT,8,"bold")).pack(anchor="w")
        if desc: tk.Label(card,text=desc,bg=C["card"],fg=C["muted"],font=(FONT,8),wraplength=710,justify="left").pack(anchor="w",pady=(3,9))
        return card

    def _render_general(self,parent):
        c=self._card(parent,"VÓRTEX · LEMBRETES","O mascote e os sons continuam engraçadinhos; aqui você só decide se eles podem te cutucar.")
        tk.Checkbutton(c,text="Mostrar notificações quando alguma coisa terminar",variable=self.notifications_var,bg=C["card"],fg=C["ink"],activebackground=C["card"],selectcolor=C["white"],font=(FONT,8)).pack(anchor="w",pady=3)
        tk.Checkbutton(c,text="Tocar os sons marcantes do Vórtex",variable=self.sounds_var,bg=C["card"],fg=C["ink"],activebackground=C["card"],selectcolor=C["white"],font=(FONT,8)).pack(anchor="w",pady=3)
        make_button(c,"Testar lembrete",self.test_vortex_alert,bg=C["violet_soft"],fg=C["violet"],active_bg="#DDD6FF",font=(FONT,8,"bold"),padx=10,pady=7).pack(anchor="w",pady=(8,0))
        c2=self._card(parent,"DITADO LOCAL","A arrumação do ditado agora usa só regras locais + dicionário do projeto. Não gasta Codex.")
        tk.Label(c2,text="Edite as palavras do projeto na aba Projeto. Ex.: ‘select móvel’ → ‘SelectMovel’.",bg=C["card"],fg=C["ink"],font=(FONT,8)).pack(anchor="w")

    def _render_automation(self,parent):
        c=self._card(parent,"CHATGPT · FLUXO ASSISTIDO","A edição pública prepara prompt, CONTEXTO.zip e anexos, abre o ChatGPT e mantém Task/Rodada/histórico. Você confirma o envio e devolve o ZIP ao Vórtice.")
        self.automation_var.set("assist")
        row=tk.Frame(c,bg=C["card"]); row.pack(fill="x",pady=2)
        tk.Radiobutton(row,text="Assistido",variable=self.automation_var,value="assist",bg=C["card"],fg=C["ink"],activebackground=C["card"],selectcolor=C["white"],font=(FONT,8,"bold")).pack(side="left")
        tk.Label(row,text="prepara tudo; você confere/envia e devolve o ZIP",bg=C["card"],fg=C["muted"],font=(FONT,7)).pack(side="left",padx=(8,0))
        tk.Label(c,text="O protocolo RESULT_TOKEN continua protegendo Task/Rodada quando você aplica o resultado.",bg=C["card"],fg=C["muted"],font=(FONT,8),wraplength=710,justify="left").pack(anchor="w",pady=(10,0))

    def _render_skills(self,parent):
        self.global_box=self._skill_section(parent,"SKILLS GLOBAIS","Regras que entram em qualquer projeto.",None)
        pid=self.project.id if self.project else ""
        self.project_box=self._skill_section(parent,f"SKILLS · {self.project.name}" if self.project else "SKILLS DO PROJETO","Só entram no projeto ativo." if self.project else "Selecione um projeto primeiro.",pid)
        self.refresh_skill_lists()

    def _render_project(self,parent):
        if not self.project:
            self._card(parent,"PROJETO","Selecione um projeto para ver memória e dicionário.")
            return
        meta=project_memory(self.project)
        c=self._card(parent,"MEMÓRIA AUTOMÁTICA","Lida de arquivos e metadados locais; não chama Codex nem outra IA.")
        self.memory_box=tk.Frame(c,bg="#FAF7F1",highlightbackground=C["line"],highlightthickness=1,padx=9,pady=8); self.memory_box.pack(fill="x")
        self._render_memory_facts(meta)
        make_button(c,"Atualizar leitura local",self.refresh_project_memory,bg=C["violet_soft"],fg=C["violet"],active_bg="#DDD6FF",font=(FONT,7,"bold"),padx=9,pady=6).pack(anchor="w",pady=(8,0))
        notes=self._card(parent,"NOTAS PERSISTENTES","Coisas que você quer que o Vórtice lembre neste projeto e mande junto para o ChatGPT.")
        self.project_notes=tk.Text(notes,height=4,bg=C["white"],fg=C["ink"],insertbackground=C["ink"],relief="flat",highlightbackground=C["line"],highlightthickness=1,font=(FONT,8),wrap="word",padx=8,pady=7)
        self.project_notes.pack(fill="x"); self.project_notes.insert("1.0",str(meta.get("notes") or ""))
        g=self._card(parent,"DICIONÁRIO DO DITADO","Correções locais por projeto. Ex.: ‘track agá ci i’ → ‘TrackHCI’. Zero tokens.")
        self.glossary_box=tk.Frame(g,bg="#FAF7F1",highlightbackground=C["line"],highlightthickness=1,padx=8,pady=7); self.glossary_box.pack(fill="x")
        self._render_glossary()
        make_button(g,"+ Palavra",self.add_glossary,bg=C["violet_soft"],fg=C["violet"],active_bg="#DDD6FF",font=(FONT,7,"bold"),padx=9,pady=6).pack(anchor="w",pady=(8,0))

    def _render_advanced(self,parent):
        c=self._card(parent,"VÓRTEX · AGENTE","Só aqui ficam as opções que gastam Codex. O resto desta atualização é local.")
        tk.Checkbutton(c,text="Rollback automático se o agente falhar",variable=self.auto_rollback,bg=C["card"],fg=C["ink"],activebackground=C["card"],selectcolor=C["white"],font=(FONT,8)).pack(anchor="w",pady=3)
        row=tk.Frame(c,bg=C["card"]); row.pack(fill="x",pady=(6,0))
        tk.Label(row,text="Modelo Codex (opcional)",bg=C["card"],fg=C["muted"],font=(FONT,8,"bold")).pack(side="left")
        tk.Entry(row,textvariable=self.model_var,bg=C["white"],fg=C["ink"],relief="flat",highlightbackground=C["line"],highlightthickness=1,font=(MONO,8)).pack(side="right",fill="x",expand=True,padx=(12,0),ipady=5)
        if not PUBLIC_RELEASE:
            c2=self._card(parent,"AUTOPILOT · FALHA","Correção automática ainda usa o mesmo ChatGPT da Task; não chama Codex.")
            tk.Checkbutton(c2,text="Se precisar reverter, abrir automaticamente uma rodada de correção",variable=self.autopilot_retry_var,bg=C["card"],fg=C["ink"],activebackground=C["card"],selectcolor=C["white"],font=(FONT,8)).pack(anchor="w",pady=3)

    def _render_memory_facts(self,meta):
        clear_children(self.memory_box)
        facts=[str(x) for x in meta.get("memoryFacts") or []]
        if not facts: tk.Label(self.memory_box,text="Nada detectado ainda.",bg="#FAF7F1",fg=C["muted"],font=(FONT,8)).pack(anchor="w")
        for fact in facts[:30]: tk.Label(self.memory_box,text=f"• {fact}",bg="#FAF7F1",fg=C["ink"],font=(FONT,8),anchor="w",justify="left").pack(fill="x",pady=1)

    def refresh_project_memory(self):
        if not self.project:return
        meta=scan_project_memory(self.project); self._render_memory_facts(meta)

    def _render_glossary(self):
        clear_children(self.glossary_box)
        if not self.project_glossary:
            tk.Label(self.glossary_box,text="Nenhuma correção ainda.",bg="#FAF7F1",fg=C["muted"],font=(FONT,8)).pack(anchor="w")
            return
        for spoken,correct in sorted(self.project_glossary.items()):
            row=tk.Frame(self.glossary_box,bg="#FAF7F1"); row.pack(fill="x",pady=2)
            tk.Label(row,text=f"{spoken}  →  {correct}",bg="#FAF7F1",fg=C["ink"],font=(MONO,7),anchor="w").pack(side="left",fill="x",expand=True)
            make_button(row,"×",lambda k=spoken:self.remove_glossary(k),bg="#FAF7F1",fg=C["red"],active_bg="#F9EDED",font=(FONT,7,"bold"),padx=5,pady=3).pack(side="right")

    def add_glossary(self):
        spoken=simpledialog.askstring(APP_NAME,"Como o Windows costuma escrever/falar isso?",parent=self)
        if not spoken:return
        correct=simpledialog.askstring(APP_NAME,"Qual é a forma correta?",parent=self)
        if not correct:return
        self.project_glossary[spoken.strip()]=correct.strip(); self._render_glossary()

    def remove_glossary(self,key):
        self.project_glossary.pop(key,None); self._render_glossary()

    def _save_project_page_if_present(self):
        if not self.project:return
        notes=None
        if hasattr(self,"project_notes") and self.project_notes.winfo_exists():
            notes=self.project_notes.get("1.0","end-1c")
        if notes is not None or self.project_glossary:
            update_project_meta(self.project.id,notes=notes,glossary=self.project_glossary)

    def _skill_section(self,parent,title,description,project_id):
        card=self._card(parent,title,description)
        actions=tk.Frame(card,bg=C["card"]); actions.pack(fill="x")
        add=make_button(actions,"+ Adicionar markdown",lambda pid=project_id:self.add_skills(pid),bg=C["violet_soft"],fg=C["violet"],active_bg="#E0DAFF",font=(FONT,8,"bold"),padx=10,pady=7); add.pack(side="left")
        if project_id=="":add.configure(state="disabled")
        make_button(actions,"Abrir pasta",lambda pid=project_id:self.open_skill_folder(pid),bg="#F2EEE7",fg=C["muted"],active_bg="#E9E4DA",font=(FONT,8,"bold"),padx=10,pady=7).pack(side="left",padx=(6,0))
        box=tk.Frame(card,bg="#FAF7F1",highlightbackground=C["line"],highlightthickness=1,padx=8,pady=7); box.pack(fill="x",pady=(9,0)); box.project_id=project_id
        return box

    def refresh_skill_lists(self):
        for box in (getattr(self,"global_box",None),getattr(self,"project_box",None)):
            if not box or not box.winfo_exists():continue
            clear_children(box); pid=getattr(box,"project_id",None)
            if pid=="": tk.Label(box,text="Selecione um projeto primeiro.",bg="#FAF7F1",fg=C["muted"],font=(FONT,8)).pack(anchor="w",pady=5); continue
            files=list_skill_files(pid or None)
            if not files: tk.Label(box,text="Nenhuma skill ainda.",bg="#FAF7F1",fg=C["muted"],font=(FONT,8)).pack(anchor="w",pady=5); continue
            for path in files:
                row=tk.Frame(box,bg="#FAF7F1"); row.pack(fill="x",pady=2)
                tk.Label(row,text=path.name,bg="#FAF7F1",fg=C["ink"],font=(MONO,8),anchor="w").pack(side="left",fill="x",expand=True)
                make_button(row,"abrir",lambda p=path:self.open_file(p),bg="#FAF7F1",fg=C["violet"],active_bg="#EFE9FF",font=(FONT,7,"bold"),padx=6,pady=3).pack(side="right")
                make_button(row,"remover",lambda p=path:self.remove_skill(p),bg="#FAF7F1",fg=C["red"],active_bg="#F9EDED",font=(FONT,7,"bold"),padx=6,pady=3).pack(side="right",padx=(0,4))

    def add_skills(self,project_id):
        selected=filedialog.askopenfilenames(parent=self,title="Adicionar skills",filetypes=[("Markdown / texto","*.md *.markdown *.txt"),("Todos","*.*")])
        if selected: copy_skill_files(selected,project_id or None); self.refresh_skill_lists()
    def remove_skill(self,path):
        if messagebox.askyesno(APP_NAME,f"Remover a skill {path.name}?",parent=self):
            try:path.unlink(missing_ok=True)
            except Exception as exc:messagebox.showerror(APP_NAME,str(exc),parent=self)
            self.refresh_skill_lists()
    def open_skill_folder(self,project_id):
        directory=project_skill_dir(project_id) if project_id else GLOBAL_SKILLS_DIR; directory.mkdir(parents=True,exist_ok=True)
        try:
            if os.name=="nt":os.startfile(str(directory))
            elif sys.platform=="darwin":subprocess.Popen(["open",str(directory)])
            else:subprocess.Popen(["xdg-open",str(directory)])
        except Exception as exc:messagebox.showerror(APP_NAME,str(exc),parent=self)
    def open_file(self,path):
        try:
            if os.name=="nt":os.startfile(str(path))
            elif sys.platform=="darwin":subprocess.Popen(["open",str(path)])
            else:subprocess.Popen(["xdg-open",str(path)])
        except Exception as exc:messagebox.showerror(APP_NAME,str(exc),parent=self)
    def test_vortex_alert(self):
        if bool(self.sounds_var.get()):play_vortex_sound("reminder")
        if bool(self.notifications_var.get()):self.parent_app.show_vortex_toast("Vórtex tá te chamando","Ei! Terminei aquele negócio. Não me deixa mofando aqui 😭",kind="reminder",native=True,play_sound=False,force=True)
    def copy_companion_token(self):
        token=str(self.settings.get("companionToken") or ""); self.clipboard_clear(); self.clipboard_append(token); self.update(); messagebox.showinfo(APP_NAME,"Código copiado. Cole nas opções do Vórtice Companion.",parent=self)
    def open_companion_extension(self):
        directory=ensure_extension_files(COMPANION_EXTENSION_DIR)
        try:
            if os.name=="nt":os.startfile(str(directory))
            elif sys.platform=="darwin":subprocess.Popen(["open",str(directory)])
            else:subprocess.Popen(["xdg-open",str(directory)])
        except Exception as exc:messagebox.showerror(APP_NAME,str(exc),parent=self)
    def open_extensions_page(self):
        try:webbrowser.open_new_tab("chrome://extensions/")
        except Exception:pass
    def close_and_save(self):
        self._save_project_page_if_present()
        self.settings["agentName"]="Vórtex"; self.settings["autoRollback"]=bool(self.auto_rollback.get()); self.settings["notificationsEnabled"]=bool(self.notifications_var.get()); self.settings["soundsEnabled"]=bool(self.sounds_var.get()); self.settings["agentModel"]=self.model_var.get().strip(); self.settings["automationMode"]=("assist" if PUBLIC_RELEASE else (self.automation_var.get().strip() or "assist")); self.settings["autopilotRetry"]=(False if PUBLIC_RELEASE else bool(self.autopilot_retry_var.get()))
        save_settings(self.settings); self.parent_app.settings=load_settings(); self.parent_app.refresh_agent_header(); self.destroy()


class BridgeApp(tk.Tk):
    def __init__(self):
        set_windows_app_id()
        super().__init__()
        self.title(f"{APP_NAME} · {APP_EDITION}")
        try:
            icon_path = RESOURCE_DIR / "assets" / "app.ico"
            if icon_path.exists():
                self.iconbitmap(default=str(icon_path))
        except Exception:
            pass
        self.configure(bg=C["paper"])
        self.minsize(1080, 700)
        center_window(self, 1220, 790)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.projects = load_projects()
        if self.projects:
            most_recent = max(self.projects, key=lambda p: p.last_used_at or p.created_at or "")
            self.selected_project_id = most_recent.id
        else:
            self.selected_project_id = ""
        self.mode = "prepare"
        self.busy = False
        self.events: queue.Queue = queue.Queue()
        self.activity: list[str] = []
        self.raw_log: list[str] = []
        self.last_prepare: Optional[PrepareResult] = None
        self.selected_result_zip: Optional[Path] = None
        self.last_apply: Optional[ApplyResult] = None
        self.phase = "idle"
        self.elapsed_started: Optional[float] = None
        self.motivation_after: Optional[str] = None
        self.status_after: Optional[str] = None
        self.rate_limit_after: Optional[str] = None
        self.current_usage = normalize_usage(None)
        self.current_limits = {"available": False, "five_hour": None, "weekly": None}
        self._project_images: dict[str, tk.PhotoImage] = {}
        self._brand_images: dict[str, tk.PhotoImage] = {}
        self.chat_watch_generation = 0
        self.chat_watch_started_at: Optional[float] = None
        self.selected_session_id = ""
        self.apply_preview_generation = 0
        self.apply_preview_items: list[dict] = []
        self.git_overview_generation = 0
        self.request_attachments: list[Path] = []
        self._attachment_images: dict[str, tk.PhotoImage] = {}
        self.drafts = load_drafts()
        self._loading_draft = False
        self.composer_run_session_id = ""
        self.composer_run_at = ""
        self.composer_task_id = ""
        self.composer_checklist_id = ""
        self.composer_round_id = ""
        self.composer_round_number = 1
        self.composer_followup = False
        self.settings = load_settings()
        ensure_extension_files(COMPANION_EXTENSION_DIR)
        self.companion = CompanionBridge(str(self.settings.get("companionToken") or ""), self.events)
        self.companion_started = self.companion.start()
        self.agent_chats = load_agent_chats()
        self.checklists = load_checklists()
        self.last_agent_backup: Optional[Path] = None
        self.agent_status_text = "Pronto para ajudar."
        self._toast_window: Optional[tk.Toplevel] = None
        self.busy_kind = ""
        self.busy_feedback_after: Optional[str] = None
        self.busy_feedback_step = 0
        self.busy_started_at: Optional[float] = None
        self.busy_last_progress_at: Optional[float] = None
        self.busy_last_fallback_at: Optional[float] = None
        self.dictation_polishing = ""
        self._autopilot_handoffs: set[str] = set()
        self._autopilot_apply_waiting: set[str] = set()

        self._build_ui()
        self.load_project_draft(self.selected_project_id)
        self.bind_all("<Control-v>", self.handle_attachment_paste, add="+")
        self.bind_all("<Control-V>", self.handle_attachment_paste, add="+")
        self.bind_all("<Control-Shift-m>", self.handle_dictation_shortcut, add="+")
        self.bind_all("<Control-Shift-M>", self.handle_dictation_shortcut, add="+")
        self.render_projects()
        self.render_selected_project()
        self.render_request_attachments()
        self.set_mode("prepare")
        self.after(80, self.poll_events)
        self.after(250, self.refresh_codex_status_async)
        self.after(900, self.refresh_rate_limits_async)
        if PUBLIC_RELEASE:
            self.after(650, self.show_public_welcome_if_needed)

    def show_public_welcome_if_needed(self) -> None:
        if not PUBLIC_RELEASE or bool(self.settings.get("publicWelcomeShown", False)):
            return
        self.settings["publicWelcomeShown"] = True
        try:
            save_settings(self.settings)
        except Exception:
            pass
        messagebox.showinfo(
            f"{APP_NAME} · {APP_EDITION}",
            "Bem-vindo ao Vórtice! ✦\n\n"
            "1. Adicione a pasta do seu projeto.\n"
            "2. Crie uma Task e descreva o que quer fazer.\n"
            "3. COZINHAR CONTEXTO prepara só o necessário.\n"
            "4. MANDAR PRO GPT abre o handoff assistido.\n"
            "5. Traga o ZIP final e o Vórtice valida, cria backup e aplica.\n\n"
            "Tudo que é seu fica local em %LOCALAPPDATA%\\Vortice.\n"
            "Divirta-se — e o Vórtex avisa quando alguma coisa terminar 😭",
            parent=self,
        )

    # ----------------------------- layout -----------------------------

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg=C["paper"], height=72, padx=22, pady=14)
        header.pack(fill="x")
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=C["paper"])
        brand.pack(side="left")
        try:
            logo_img = tk.PhotoImage(file=RESOURCE_DIR / "assets" / "vortice-header.png")
            self._brand_images["header"] = logo_img
            tk.Label(brand, image=logo_img, bg=C["paper"]).pack(side="left", padx=(0, 12))
        except Exception:
            tk.Label(brand, text="VÓRTICE", bg=C["paper"], fg=C["ink"], font=(FONT, 13, "bold")).pack(side="left", padx=(0, 12))
        brand_copy = tk.Frame(brand, bg=C["paper"])
        brand_copy.pack(side="left")
        tk.Label(brand_copy, text=f"PUBLIC BETA {APP_VERSION}", bg=C["paper"], fg=C["violet"], font=(FONT, 7, "bold")).pack(anchor="w")
        tk.Label(brand_copy, text="contexto entra · código sai", bg=C["paper"], fg=C["muted"], font=(FONT, 7)).pack(anchor="w", pady=(2, 0))

        header_actions = tk.Frame(header, bg=C["paper"])
        header_actions.pack(side="right")
        self.settings_button = make_button(header_actions, "Config", self.open_settings, bg=C["paper"], fg=C["violet"], active_bg="#ECE7DE", font=(FONT, 8, "bold"), padx=11, pady=7)
        self.settings_button.pack(side="right", padx=(5, 0))
        self.git_button = make_button(header_actions, "Git", self.open_git, bg=C["paper"], fg=C["muted"], active_bg="#ECE7DE", font=(FONT, 8, "bold"), padx=11, pady=7)
        self.git_button.pack(side="right", padx=(5, 0))
        self.chats_button = make_button(header_actions, "Chats", self.open_chats, bg=C["paper"], fg=C["muted"], active_bg="#ECE7DE", font=(FONT, 8, "bold"), padx=11, pady=7)
        self.chats_button.pack(side="right", padx=(5, 0))
        make_button(header_actions, "Histórico", self.open_history, bg=C["paper"], fg=C["muted"], active_bg="#ECE7DE", font=(FONT, 8, "bold"), padx=11, pady=7).pack(side="right", padx=(5, 0))
        self.header_vscode_button = make_button(header_actions, "VS Code", self.open_selected_project_vscode, bg=C["paper"], fg=C["violet"], active_bg="#ECE7DE", font=(FONT, 8, "bold"), padx=11, pady=7)
        self.header_vscode_button.pack(side="right", padx=(5, 0))
        self.header_folder_button = make_button(header_actions, "Pasta", self.open_selected_project_folders, bg=C["paper"], fg=C["muted"], active_bg="#ECE7DE", font=(FONT, 8, "bold"), padx=11, pady=7)
        self.header_folder_button.pack(side="right", padx=(5, 0))

        status_frame = tk.Frame(header_actions, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=10, pady=7, cursor="hand2")
        status_frame.pack(side="right", padx=(0, 7))
        self.codex_dot = tk.Canvas(status_frame, width=12, height=12, bg=C["card"], highlightthickness=0)
        self.codex_dot.pack(side="left", padx=(0, 6))
        self.codex_dot_id = self.codex_dot.create_oval(2, 2, 10, 10, fill=C["muted"], outline="")
        self.codex_status_label = tk.Label(status_frame, text="verificando Codex", bg=C["card"], fg=C["muted"], font=(FONT, 8, "bold"))
        self.codex_status_label.pack(side="left")
        # Kept for internal updates; the visible token dashboard lives permanently in the main screen.
        self.codex_usage_label = tk.Label(status_frame, text="", bg=C["card"], fg=C["muted"], font=(MONO, 1))
        status_frame.bind("<Button-1>", lambda _e: self.refresh_codex_status_async())
        self.codex_status_label.bind("<Button-1>", lambda _e: self.refresh_codex_status_async())
        self.codex_dot.bind("<Button-1>", lambda _e: self.refresh_codex_status_async())

        body = tk.Frame(self, bg=C["paper"])
        body.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.sidebar = tk.Frame(body, bg=C["dark"], width=318, padx=12, pady=13)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        sidebar_head = tk.Frame(self.sidebar, bg=C["dark"])
        sidebar_head.pack(fill="x", pady=(4, 11))
        side_copy = tk.Frame(sidebar_head, bg=C["dark"])
        side_copy.pack(side="left", fill="x", expand=True)
        tk.Label(side_copy, text="MEUS PROJETOS", bg=C["dark"], fg="#A7A3AC", font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(side_copy, text="projetos + tasks", bg=C["dark"], fg="#6F6B74", font=(FONT, 7)).pack(anchor="w", pady=(2, 0))
        self.project_count_label = tk.Label(sidebar_head, text="0", bg=C["dark"], fg=C["lime"], font=(MONO, 8, "bold"))
        self.project_count_label.pack(side="right", padx=3)

        self.project_scroll = ScrollFrame(self.sidebar, C["dark"])
        self.project_scroll.pack(fill="both", expand=True)

        self.checklist_card = tk.Frame(self.sidebar, bg="#2C2A31", highlightbackground="#3D3944", highlightthickness=1, padx=9, pady=9)
        self.checklist_card.pack(fill="x", pady=(8, 0))
        checklist_head = tk.Frame(self.checklist_card, bg="#2C2A31")
        checklist_head.pack(fill="x")
        tk.Label(checklist_head, text="CHECKLIST DO PROJETO", bg="#2C2A31", fg=C["lime"], font=(FONT, 7, "bold")).pack(side="left")
        self.checklist_count_label = tk.Label(checklist_head, text="0/0", bg="#2C2A31", fg="#8D8994", font=(MONO, 7, "bold"))
        self.checklist_count_label.pack(side="right")
        self.checklist_summary_label = tk.Label(self.checklist_card, text="Backlog simples com prints e status.", bg="#2C2A31", fg="#8D8994", font=(FONT, 7), anchor="w", justify="left", wraplength=275)
        self.checklist_summary_label.pack(fill="x", pady=(5, 7))
        make_button(self.checklist_card, "ABRIR CHECKLIST  →", self.open_project_checklist, bg=C["violet"], fg=C["white"], active_bg="#6654E4", font=(FONT, 7, "bold"), padx=10, pady=7).pack(fill="x")

        sidebar_bottom = tk.Frame(self.sidebar, bg=C["dark"])
        sidebar_bottom.pack(fill="x", pady=(10, 0))
        make_button(sidebar_bottom, "+  NOVO PROJETO", self.new_project, bg=C["lime"], fg=C["ink"], active_bg=C["lime_dark"], font=(FONT, 8, "bold"), padx=10, pady=10).pack(fill="x")
        tool_row = tk.Frame(sidebar_bottom, bg=C["dark"])
        tool_row.pack(fill="x", pady=(6, 0))
        self.edit_project_button = make_button(tool_row, "Editar", self.edit_project, bg=C["dark2"], fg="#D7D4DB", active_bg="#3A3840", font=(FONT, 8, "bold"), padx=9, pady=7)
        self.edit_project_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.remove_project_button = make_button(tool_row, "Remover", self.remove_project, bg=C["dark2"], fg="#D7D4DB", active_bg="#3A3840", font=(FONT, 8, "bold"), padx=9, pady=7)
        self.remove_project_button.pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.main = tk.Frame(body, bg=C["card"], highlightbackground=C["line"], highlightthickness=1)
        self.main.pack(side="left", fill="both", expand=True, padx=(12, 0))

        top = tk.Frame(self.main, bg=C["card"], padx=24, pady=17)
        top.pack(fill="x")
        top_left = tk.Frame(top, bg=C["card"])
        top_left.pack(side="left", fill="x", expand=True)
        tk.Label(top_left, text="ESCOLHE · PEDE · COZINHA", bg=C["card"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        self.page_title = tk.Label(top_left, text="O que vamos construir hoje?", bg=C["card"], fg=C["ink"], font=(FONT, 18, "bold"))
        self.page_title.pack(anchor="w", pady=(3, 0))

        self.mode_frame = tk.Frame(top, bg="#F0ECE5", padx=3, pady=3)
        self.mode_frame.pack(side="right", padx=(16, 0))
        self.prepare_tab = make_button(self.mode_frame, "Preparar", lambda: self.set_mode("prepare"), bg=C["ink"], fg=C["white"], active_bg=C["ink"], font=(FONT, 8, "bold"), padx=14, pady=7)
        self.prepare_tab.pack(side="left")
        self.apply_tab = make_button(self.mode_frame, "Aplicar", lambda: self.set_mode("apply"), bg="#F0ECE5", fg=C["muted"], active_bg="#E5E0D8", font=(FONT, 8, "bold"), padx=14, pady=7)
        self.apply_tab.pack(side="left")
        self.agent_tab = make_button(self.mode_frame, "Vórtex", lambda: self.set_mode("agent"), bg="#F0ECE5", fg=C["violet"], active_bg="#E5E0D8", font=(FONT, 8, "bold"), padx=14, pady=7)
        self.agent_tab.pack(side="left")

        # Usage dashboard: quota percentages are the primary signal; token details stay compact.
        strip = tk.Frame(self.main, bg="#F8F5EF", padx=24, pady=9)
        strip.pack(fill="x")
        quota_side = tk.Frame(strip, bg="#F8F5EF")
        quota_side.pack(side="left")
        tk.Label(quota_side, text="LIMITES CODEX", bg="#F8F5EF", fg=C["violet"], font=(FONT, 7, "bold")).pack(anchor="w")
        quota_row = tk.Frame(quota_side, bg="#F8F5EF")
        quota_row.pack(anchor="w", pady=(3, 0))
        self.quota_5h = QuotaMeter(quota_row, "5 HORAS")
        self.quota_5h.pack(side="left", padx=(0, 18))
        self.quota_week = QuotaMeter(quota_row, "SEMANAL")
        self.quota_week.pack(side="left")

        tk.Frame(strip, bg=C["line"], width=1, height=48).pack(side="left", padx=22)
        token_side = tk.Frame(strip, bg="#F8F5EF")
        token_side.pack(side="left", fill="x", expand=True)
        token_head = tk.Frame(token_side, bg="#F8F5EF")
        token_head.pack(fill="x")
        tk.Label(token_head, text="TOKENS COLETADOS", bg="#F8F5EF", fg=C["muted"], font=(FONT, 7, "bold")).pack(side="left")
        self.token_total_value = tk.Label(token_head, text="0 tokens", bg="#F8F5EF", fg=C["ink"], font=(MONO, 9, "bold"))
        self.token_total_value.pack(side="left", padx=(8, 0))
        self.token_round_value = tk.Label(token_head, text="RODADA —", bg="#F8F5EF", fg=C["violet"], font=(MONO, 7, "bold"))
        self.token_round_value.pack(side="right")
        detail = tk.Frame(token_side, bg="#F8F5EF")
        detail.pack(fill="x", pady=(6, 0))
        self.token_input_value = tk.Label(detail, text="ENTRADA 0", bg="#F8F5EF", fg=C["muted"], font=(MONO, 7, "bold"))
        self.token_input_value.pack(side="left", padx=(0, 14))
        self.token_cache_value = tk.Label(detail, text="CACHE 0", bg="#F8F5EF", fg=C["muted"], font=(MONO, 7, "bold"))
        self.token_cache_value.pack(side="left", padx=(0, 14))
        self.token_output_value = tk.Label(detail, text="SAÍDA 0", bg="#F8F5EF", fg=C["muted"], font=(MONO, 7, "bold"))
        self.token_output_value.pack(side="left")
        self.limit_note = tk.Label(detail, text="", bg="#F8F5EF", fg=C["muted"], font=(FONT, 7))
        self.limit_note.pack(side="right")
        self.git_overview_label = tk.Label(detail, text="GIT —", bg="#F8F5EF", fg=C["muted"], font=(MONO, 7, "bold"), cursor="hand2")
        self.git_overview_label.pack(side="right", padx=(0, 14))
        self.git_overview_label.bind("<Button-1>", lambda _e: self.open_git())

        tk.Frame(self.main, bg=C["line"], height=1).pack(fill="x")
        self.content = tk.Frame(self.main, bg=C["card"])
        self.content.pack(fill="both", expand=True)
        self.prepare_page = tk.Frame(self.content, bg=C["card"])
        self.apply_page = tk.Frame(self.content, bg=C["card"])
        self.agent_page = tk.Frame(self.content, bg=C["card"])
        self._build_prepare_page()
        self._build_apply_page()
        self._build_agent_page()

    def _build_prepare_page(self) -> None:
        page = self.prepare_page
        page.columnconfigure(0, weight=5)
        page.columnconfigure(1, weight=3)
        page.rowconfigure(0, weight=1)

        left = tk.Frame(page, bg=C["card"], padx=18, pady=14)
        left.grid(row=0, column=0, sticky="nsew")

        self.active_project_bar = tk.Frame(left, bg="#F8F5EF", highlightbackground=C["line"], highlightthickness=1, padx=11, pady=8)
        self.active_project_bar.pack(fill="x")
        self.active_logo = tk.Label(self.active_project_bar, text="AI", bg=C["ink"], fg=C["lime"], width=6, height=3, font=(FONT, 9, "bold"))
        self.active_logo.pack(side="left", padx=(0, 11))
        project_copy = tk.Frame(self.active_project_bar, bg="#F8F5EF")
        project_copy.pack(side="left", fill="x", expand=True)
        self.active_project_kicker = tk.Label(project_copy, text="PROJETO ATIVO", bg="#F8F5EF", fg=C["violet"], font=(FONT, 7, "bold"))
        self.active_project_kicker.pack(anchor="w")
        self.active_project_name = tk.Label(project_copy, text="Nenhum projeto", bg="#F8F5EF", fg=C["ink"], font=(FONT, 12, "bold"))
        self.active_project_name.pack(anchor="w", pady=(1, 0))
        self.active_project_roots = tk.Label(project_copy, text="Escolha um projeto na lateral", bg="#F8F5EF", fg=C["muted"], font=(FONT, 8))
        self.active_project_roots.pack(anchor="w", pady=(3, 0))
        self.active_project_chat = tk.Label(project_copy, text="", bg="#F8F5EF", fg=C["violet"], font=(FONT, 7, "bold"), cursor="hand2")
        self.active_project_chat.pack(anchor="w", pady=(3, 0))
        self.active_project_chat.bind("<Button-1>", lambda _e: self.open_history())
        active_meta = tk.Frame(self.active_project_bar, bg="#F8F5EF")
        active_meta.pack(side="right")
        self.active_project_git = tk.Label(active_meta, text="GIT —", bg="#F8F5EF", fg=C["green"], font=(MONO, 7, "bold"), cursor="hand2")
        self.active_project_git.pack(anchor="e")
        self.active_project_git.bind("<Button-1>", lambda _e: self.open_git())
        self.active_project_usage = tk.Label(active_meta, text="", bg="#F8F5EF", fg=C["violet"], font=(MONO, 7, "bold"))
        self.active_project_usage.pack(anchor="e", pady=(4, 0))
        quick_row = tk.Frame(active_meta, bg="#F8F5EF")
        quick_row.pack(anchor="e", pady=(7, 0))
        self.project_home_button = make_button(quick_row, "Visão", self.open_project_home, bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 7, "bold"), padx=8, pady=5)
        self.project_home_button.pack(side="left", padx=(0, 4))
        self.open_folder_button = make_button(
            quick_row, "Pasta", self.open_selected_project_folders,
            bg="#ECE8E0", fg=C["ink"], active_bg="#E1DCD3", font=(FONT, 7, "bold"), padx=8, pady=5,
        )
        self.open_folder_button.pack(side="left", padx=(0, 4))
        self.open_vscode_button = make_button(
            quick_row, "VS Code", self.open_selected_project_vscode,
            bg=C["ink"], fg=C["lime"], active_bg="#313036", font=(FONT, 7, "bold"), padx=8, pady=5,
        )
        self.open_vscode_button.pack(side="left")

        # Selected branch: persists after restart and gives direct access to its prompt/ZIP/chat.
        self.branch_bar = tk.Frame(left, bg="#F1EEFF", highlightbackground="#D9D2FF", highlightthickness=1, padx=10, pady=7)
        self.branch_bar.pack(fill="x", pady=(8, 0))
        branch_copy = tk.Frame(self.branch_bar, bg="#F1EEFF")
        branch_copy.pack(fill="x")
        self.branch_kicker = tk.Label(branch_copy, text="TASK ATIVA", bg="#F1EEFF", fg=C["violet"], font=(FONT, 7, "bold"))
        self.branch_kicker.pack(anchor="w")
        self.branch_title = tk.Label(branch_copy, text="Nenhuma task selecionada", bg="#F1EEFF", fg=C["ink"], font=(FONT, 9, "bold"), anchor="w")
        self.branch_title.pack(fill="x", pady=(2, 0))
        self.branch_meta = tk.Label(branch_copy, text="Crie ou selecione uma task para continuar.", bg="#F1EEFF", fg=C["muted"], font=(MONO, 7), anchor="w")
        self.branch_meta.pack(fill="x", pady=(2, 0))
        branch_actions = tk.Frame(self.branch_bar, bg="#F1EEFF")
        branch_actions.pack(fill="x", pady=(7, 0))
        self.branch_chat_button = make_button(branch_actions, "Chat ↗", self.open_selected_session_chat, bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 7, "bold"), padx=8, pady=6)
        self.branch_chat_button.pack(side="left", padx=(0, 4))
        self.branch_timeline_button = make_button(branch_actions, "Histórico", self.open_task_timeline, bg="#E7E1FF", fg=C["violet"], active_bg="#DCD4FF", font=(FONT, 7, "bold"), padx=8, pady=6)
        self.branch_timeline_button.pack(side="left", padx=(0, 4))
        self.branch_followup_button = make_button(branch_actions, "Reprovar / ajuste", self.reject_current_task, bg="#FFF0E8", fg="#A65E36", active_bg="#F8E3D6", font=(FONT, 7, "bold"), padx=8, pady=6)
        self.branch_followup_button.pack(side="left", padx=(0, 4))
        self.branch_status_button = make_button(branch_actions, "Aprovar ✓", self.approve_current_task, bg="#F1EEFF", fg=C["green"], active_bg="#E5F5E8", font=(FONT, 7, "bold"), padx=8, pady=6)
        self.branch_status_button.pack(side="left")
        # Hidden compatibility handles used by older state methods.
        self.branch_bundle_button = tk.Button(branch_actions)
        self.branch_prompt_button = tk.Button(branch_actions)
        self.branch_zip_button = tk.Button(branch_actions)
        self.branch_reuse_button = tk.Button(branch_actions)
        make_button(branch_actions, "+ Nova task", self.new_request, bg=C["lime"], fg=C["ink"], active_bg=C["lime_dark"], font=(FONT, 7, "bold"), padx=9, pady=6).pack(side="right")
        self.task_next_button = make_button(branch_actions, "›", lambda: self.switch_task_relative(-1), bg="#E7E1FF", fg=C["violet"], active_bg="#DCD4FF", font=(FONT, 10, "bold"), padx=8, pady=4)
        self.task_next_button.pack(side="right", padx=(0, 4))
        self.task_prev_button = make_button(branch_actions, "‹", lambda: self.switch_task_relative(1), bg="#E7E1FF", fg=C["violet"], active_bg="#DCD4FF", font=(FONT, 10, "bold"), padx=8, pady=4)
        self.task_prev_button.pack(side="right", padx=(0, 4))

        prompt_head = tk.Frame(left, bg=C["card"])
        prompt_head.pack(fill="x", pady=(10, 6))
        copy = tk.Frame(prompt_head, bg=C["card"])
        copy.pack(side="left", fill="x", expand=True)
        tk.Label(copy, text="O que você quer mudar?", bg=C["card"], fg=C["ink"], font=(FONT, 12, "bold")).pack(anchor="w")
        tk.Label(copy, text="Fala normal. O Vórtice se vira com o resto.", bg=C["card"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", pady=(3, 0))
        prompt_meta = tk.Frame(prompt_head, bg=C["card"])
        prompt_meta.pack(side="right", anchor="s")
        self.composer_state_label = tk.Label(prompt_meta, text="NOVA TASK", bg="#EEF9D8", fg=C["green"], font=(FONT, 7, "bold"), padx=7, pady=3)
        self.composer_state_label.pack(side="right")
        self.request_count = tk.Label(prompt_meta, text="0 caracteres", bg=C["card"], fg=C["muted"], font=(MONO, 7))
        self.request_count.pack(side="right", padx=(0, 8))

        voice_row = tk.Frame(left, bg=C["card"])
        voice_row.pack(fill="x", pady=(0, 5))
        self.dictate_prompt_button = make_button(voice_row, "🎙 Ditar", lambda: self.start_dictation("request"), bg="#FFF3DE", fg="#8A5A16", active_bg="#F7E5C5", font=(FONT, 7, "bold"), padx=8, pady=4)
        self.dictate_prompt_button.pack(side="left")
        self.polish_prompt_button = make_button(voice_row, "✦ Arrumar ditado", lambda: self.start_polish_dictation("request"), bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 7, "bold"), padx=8, pady=4)
        self.polish_prompt_button.pack(side="left", padx=(5, 0))
        tk.Label(voice_row, text="Ctrl+Shift+M · fala quanto quiser", bg=C["card"], fg=C["muted"], font=(FONT, 7)).pack(side="left", padx=(8, 0))

        request_border = tk.Frame(left, bg=C["line_dark"], padx=1, pady=1)
        request_border.pack(fill="x")
        self.request_text = tk.Text(request_border, height=6, bg=C["white"], fg=C["ink"], insertbackground=C["ink"], relief="flat", bd=0, wrap="word", font=(FONT, 10), padx=13, pady=10, undo=True)
        self.request_text.pack(fill="x")
        self.request_text.bind("<KeyRelease>", self._request_count)

        action_row = tk.Frame(left, bg=C["card"])
        action_row.pack(fill="x", pady=(8, 0))
        attach_tools = tk.Frame(action_row, bg=C["card"])
        attach_tools.pack(side="left")
        make_button(attach_tools, "+ Arquivo", self.add_request_attachments, bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 7, "bold"), padx=9, pady=5).pack(side="left")
        make_button(attach_tools, "Colar", self.paste_attachments_from_clipboard, bg="#EDF6FF", fg=C["violet"], active_bg="#DAEEFF", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left", padx=(5, 0))
        self.clear_attachments_button = make_button(attach_tools, "Limpar", self.clear_request_attachments, bg="#F3EFE7", fg=C["muted"], active_bg="#ECE6DB", font=(FONT, 7, "bold"), padx=7, pady=5)
        self.clear_attachments_button.pack(side="left", padx=(5, 0))
        self.attachments_count = tk.Label(attach_tools, text="0 anexos", bg=C["card"], fg=C["muted"], font=(MONO, 7, "bold"))
        self.attachments_count.pack(side="left", padx=(8, 0))
        self.prepare_button = make_button(action_row, "COZINHAR CONTEXTO  →", self.start_prepare, bg=C["ink"], fg=C["lime"], active_bg="#313036", font=(FONT, 9, "bold"), padx=15, pady=8)
        self.prepare_button.pack(side="right")

        self.attach_panel = tk.Frame(left, bg=C["card"])
        attachment_shell = tk.Frame(self.attach_panel, bg=C["line"], padx=1, pady=1)
        attachment_shell.pack(fill="x")
        self.attachment_strip = AttachmentStrip(attachment_shell, "#FBF8F2", height=92)
        self.attachment_strip.pack(fill="x")

        right = tk.Frame(page, bg=C["dark"], padx=16, pady=16)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        reactor_shell = tk.Frame(right, bg="#2D2B31", highlightbackground="#3B3940", highlightthickness=1, padx=5, pady=5)
        reactor_shell.grid(row=0, column=0, sticky="ew")
        self.reactor = Reactor(reactor_shell, width=320, height=205)
        self.reactor.pack(fill="x")

        live = tk.Frame(right, bg=C["dark"])
        live.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        head = tk.Frame(live, bg=C["dark"])
        head.pack(fill="x")
        self.live_kicker = tk.Label(head, text="FORJA LOCAL", bg=C["dark"], fg=C["lime"], font=(FONT, 8, "bold"))
        self.live_kicker.pack(side="left")
        self.elapsed_label = tk.Label(head, text="00:00", bg=C["dark"], fg="#77737E", font=(MONO, 8, "bold"))
        self.elapsed_label.pack(side="right")
        self.live_title = tk.Label(live, text="Pronto para cozinhar.", bg=C["dark"], fg=C["white"], font=(FONT, 15, "bold"), justify="left", wraplength=330)
        self.live_title.pack(anchor="w", pady=(5, 3))
        self.live_subtitle = tk.Label(live, text="Escolhe um projeto, pede e deixa a parte chata comigo.", bg=C["dark"], fg="#AAA6B0", font=(FONT, 8), justify="left", wraplength=330)
        self.live_subtitle.pack(anchor="w")

        activity_head = tk.Frame(live, bg=C["dark"])
        activity_head.pack(fill="x", pady=(12, 6))
        tk.Label(activity_head, text="O QUE ESTÁ ROLANDO", bg=C["dark"], fg="#79757F", font=(FONT, 7, "bold")).pack(side="left")
        self.detail_toggle = make_button(activity_head, "detalhes", self.toggle_raw_log, bg=C["dark"], fg="#8F8B95", active_bg="#302E35", font=(FONT, 7, "bold"), padx=6, pady=3)
        self.detail_toggle.pack(side="right")
        self.activity_box = tk.Text(live, bg=C["dark2"], fg="#DDD9E1", relief="flat", bd=0, state="disabled", font=(FONT, 8), padx=10, pady=9, height=7, wrap="word")
        self.activity_box.pack(fill="both", expand=True)
        self.raw_log_box = tk.Text(live, bg="#18171B", fg="#B4AFBA", relief="flat", bd=0, state="disabled", font=(MONO, 7), padx=8, pady=8, height=7, wrap="word")

        self.result_panel = tk.Frame(left, bg=C["violet_soft"], highlightbackground="#D8D0FF", highlightthickness=1, padx=14, pady=12)

    def _build_apply_page(self) -> None:
        page = self.apply_page
        page.columnconfigure(0, weight=3)
        page.columnconfigure(1, weight=2)
        page.rowconfigure(0, weight=1)

        left = tk.Frame(page, bg=C["card"], padx=28, pady=24)
        left.grid(row=0, column=0, sticky="nsew")
        right = tk.Frame(page, bg="#F5F2EA", padx=22, pady=22)
        right.grid(row=0, column=1, sticky="nsew")

        tk.Label(left, text="TRAZER DE VOLTA", bg=C["card"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(left, text="Aplique o ZIP sem susto.", bg=C["card"], fg=C["ink"], font=(FONT, 20, "bold")).pack(anchor="w", pady=(4, 5))
        tk.Label(
            left,
            text="Escolha o ZIP, confira os arquivos reais e aplique com backup.",
            bg=C["card"], fg=C["muted"], font=(FONT, 9), justify="left", wraplength=520,
        ).pack(anchor="w", pady=(0, 18))

        self.apply_project_bar = tk.Frame(left, bg="#F6F3EC", highlightbackground=C["line"], highlightthickness=1, padx=12, pady=10)
        self.apply_project_bar.pack(fill="x", pady=(0, 16))
        tk.Label(self.apply_project_bar, text="Destino", bg="#F6F3EC", fg=C["muted"], font=(FONT, 8, "bold")).pack(side="left")
        self.apply_project_name = tk.Label(self.apply_project_bar, text="Nenhum projeto", bg="#F6F3EC", fg=C["ink"], font=(FONT, 9, "bold"))
        self.apply_project_name.pack(side="right")

        self.drop_card = tk.Frame(left, bg=C["white"], highlightbackground=C["line_dark"], highlightthickness=1, padx=18, pady=24)
        self.drop_card.pack(fill="both", expand=True)
        tk.Label(self.drop_card, text="ZIP DO CHATGPT", bg=C["white"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        self.zip_name_label = tk.Label(
            self.drop_card, text="Nenhum ZIP escolhido", bg=C["white"], fg=C["ink"], font=(FONT, 14, "bold"),
            justify="left", wraplength=500,
        )
        self.zip_name_label.pack(anchor="w", pady=(10, 4))
        self.zip_meta_label = tk.Label(self.drop_card, text="", bg=C["white"], fg=C["muted"], font=(MONO, 8))
        self.zip_meta_label.pack(anchor="w")
        make_button(
            self.drop_card, "Escolher ZIP", self.choose_result_zip,
            bg=C["violet_soft"], fg=C["violet"], active_bg="#E0DAFF", font=(FONT, 9, "bold"), padx=14, pady=9,
        ).pack(anchor="w", pady=(18, 0))

        self.apply_button = make_button(
            left, "APLICAR COM BACKUP   →", self.start_apply,
            bg=C["ink"], fg=C["lime"], active_bg="#2A2A2A", font=(FONT, 10, "bold"), padx=18, pady=13,
        )
        self.apply_button.pack(fill="x", pady=(16, 0))

        files_head = tk.Frame(right, bg="#F5F2EA")
        files_head.pack(fill="x")
        files_copy = tk.Frame(files_head, bg="#F5F2EA")
        files_copy.pack(side="left", fill="x", expand=True)
        tk.Label(files_copy, text="ARQUIVOS DO PACOTE", bg="#F5F2EA", fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(files_copy, text="O que vai entrar no projeto", bg="#F5F2EA", fg=C["ink"], font=(FONT, 15, "bold")).pack(anchor="w", pady=(4, 0))
        self.apply_files_count = tk.Label(files_head, text="—", bg="#F5F2EA", fg=C["muted"], font=(MONO, 8, "bold"))
        self.apply_files_count.pack(side="right", anchor="s", pady=(0, 2))

        self.apply_files_summary = tk.Label(
            right, text="Escolha um ZIP para ver cada arquivo antes de aplicar.",
            bg="#F5F2EA", fg=C["muted"], font=(FONT, 8), anchor="w", justify="left",
        )
        self.apply_files_summary.pack(fill="x", pady=(7, 10))

        self.apply_file_scroll = ScrollFrame(right, "#F5F2EA")
        self.apply_file_scroll.pack(fill="both", expand=True, pady=(0, 12))
        self.render_apply_preview([])

        self.apply_result_panel = tk.Frame(right, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=13, pady=13)
        self.apply_result_panel.pack(fill="x", side="bottom")
        self.apply_result_title = tk.Label(self.apply_result_panel, text="Nenhuma aplicação ainda.", bg=C["card"], fg=C["ink"], font=(FONT, 10, "bold"), justify="left", wraplength=340)
        self.apply_result_title.pack(anchor="w")
        self.apply_result_detail = tk.Label(self.apply_result_panel, text="Seu último backup aparecerá aqui.", bg=C["card"], fg=C["muted"], font=(FONT, 8), justify="left", wraplength=340)
        self.apply_result_detail.pack(anchor="w", pady=(4, 8))
        self.rollback_button = make_button(
            self.apply_result_panel, "Desfazer última aplicação", self.rollback_last,
            bg=C["card"], fg=C["red"], active_bg="#F9ECEC", font=(FONT, 8, "bold"), padx=0, pady=4, anchor="w",
        )
        self.rollback_button.pack(anchor="w")
        self.rollback_button.configure(state="disabled")

    def _build_agent_page(self) -> None:
        page = self.agent_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        head = tk.Frame(page, bg=C["dark"], padx=20, pady=15)
        head.grid(row=0, column=0, sticky="ew")
        left = tk.Frame(head, bg=C["dark"])
        left.pack(side="left", fill="x", expand=True)
        kicker = tk.Frame(left, bg=C["dark"])
        kicker.pack(fill="x")
        tk.Label(kicker, text="VÓRTEX · AGENTE RESIDENTE", bg=C["dark"], fg=C["lime"], font=(FONT, 8, "bold")).pack(side="left")
        self.agent_status_label = tk.Label(kicker, text="PRONTO", bg=C["dark"], fg="#9B96A2", font=(MONO, 7, "bold"))
        self.agent_status_label.pack(side="left", padx=(10, 0))
        self.agent_project_name = tk.Label(left, text="Escolha um projeto", bg=C["dark"], fg=C["white"], font=(FONT, 16, "bold"))
        self.agent_project_name.pack(anchor="w", pady=(4, 1))
        self.agent_meta = tk.Label(left, text="O Vórtex fica ligado à task atual, opera o projeto, valida e guarda backup antes de mexer.", bg=C["dark"], fg="#ABA6B0", font=(FONT, 8), justify="left")
        self.agent_meta.pack(anchor="w")
        right = tk.Frame(head, bg=C["dark"])
        right.pack(side="right", padx=(15, 0))
        self.agent_avatar = VortexAvatar(right, width=68, height=68)
        self.agent_avatar.pack(side="left", padx=(0, 10))
        right_meta = tk.Frame(right, bg=C["dark"])
        right_meta.pack(side="left")
        self.agent_backup_label = tk.Label(right_meta, text="SEM BACKUP RECENTE", bg=C["dark"], fg="#77727D", font=(MONO, 7, "bold"))
        self.agent_backup_label.pack(anchor="e")
        self.agent_rollback_button = make_button(right_meta, "Desfazer última ação", self.rollback_last_agent_action, bg=C["dark2"], fg=C["peach"], active_bg="#3A3740", font=(FONT, 7, "bold"), padx=9, pady=6)
        self.agent_rollback_button.pack(anchor="e", pady=(6, 0))
        self.agent_rollback_button.configure(state="disabled")

        chat_shell = tk.Frame(page, bg="#F8F5EF", padx=18, pady=14)
        chat_shell.grid(row=1, column=0, sticky="nsew")
        chat_shell.rowconfigure(0, weight=1)
        chat_shell.columnconfigure(0, weight=1)
        self.agent_chat_scroll = ScrollFrame(chat_shell, "#F8F5EF")
        self.agent_chat_scroll.grid(row=0, column=0, sticky="nsew")

        composer = tk.Frame(page, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=16, pady=12)
        composer.grid(row=2, column=0, sticky="ew")
        quick = tk.Frame(composer, bg=C["card"])
        quick.pack(fill="x", pady=(0, 8))
        tk.Label(quick, text="ATALHOS", bg=C["card"], fg=C["muted"], font=(FONT, 7, "bold")).pack(side="left", padx=(0, 8))
        make_button(quick, "Diagnosticar", lambda: self.agent_quick_action("Faça um diagnóstico do projeto agora. Procure erros, inconsistências e coisas quebradas. Corrija o que for seguro corrigir e valide o resultado."), bg="#F2EEE7", fg=C["ink"], active_bg="#E9E4DB", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left", padx=(0, 5))
        make_button(quick, "Corrigir o que quebrou", lambda: self.agent_quick_action("Descubra o que está quebrado no projeto e corrija de ponta a ponta. Preserve o que já funciona, rode as validações relevantes e me diga o resultado."), bg="#F2EEE7", fg=C["ink"], active_bg="#E9E4DB", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left", padx=(0, 5))
        make_button(quick, "Validar projeto", lambda: self.agent_quick_action("Valide o projeto atual sem fazer mudanças desnecessárias. Rode as verificações apropriadas e, se encontrar erro real, corrija e valide novamente."), bg="#F2EEE7", fg=C["ink"], active_bg="#E9E4DB", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="left")
        make_button(quick, "Configurar skills", self.open_settings, bg=C["violet_soft"], fg=C["violet"], active_bg="#E0DAFF", font=(FONT, 7, "bold"), padx=8, pady=5).pack(side="right")

        input_shell = tk.Frame(composer, bg=C["line_dark"], padx=1, pady=1)
        input_shell.pack(fill="x")
        self.agent_input = tk.Text(input_shell, height=4, bg=C["white"], fg=C["ink"], insertbackground=C["ink"], relief="flat", bd=0, wrap="word", font=(FONT, 10), padx=12, pady=10, undo=True)
        self.agent_input.pack(fill="x")
        self.agent_input.bind("<Control-Return>", lambda _e: self.start_agent_turn())
        bottom = tk.Frame(composer, bg=C["card"])
        bottom.pack(fill="x", pady=(8, 0))
        self.agent_hint = tk.Label(bottom, text="Ctrl+Enter envia · Ctrl+Shift+M dita · backup automático antes de agir", bg=C["card"], fg=C["muted"], font=(FONT, 7))
        self.agent_hint.pack(side="left")
        self.agent_send_button = make_button(bottom, "MANDAR PRO VÓRTEX  →", self.start_agent_turn, bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 9, "bold"), padx=14, pady=9)
        self.agent_send_button.pack(side="right")
        self.polish_agent_button = make_button(bottom, "✦ Arrumar local", lambda: self.start_polish_dictation("agent"), bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 7, "bold"), padx=8, pady=7)
        self.polish_agent_button.pack(side="right", padx=(0, 6))
        self.dictate_agent_button = make_button(bottom, "🎙 Ditar", lambda: self.start_dictation("agent"), bg="#FFF3DE", fg="#8A5A16", active_bg="#F7E5C5", font=(FONT, 7, "bold"), padx=8, pady=7)
        self.dictate_agent_button.pack(side="right", padx=(0, 6))
        self.render_agent_chat()

    # ----------------------------- dictation -----------------------------

    def _dictation_widget(self, target: str) -> Optional[tk.Text]:
        if target == "agent":
            return getattr(self, "agent_input", None)
        return getattr(self, "request_text", None)

    def handle_dictation_shortcut(self, _event=None):
        focus = self.focus_get()
        if focus is getattr(self, "agent_input", None) or self.mode == "agent":
            self.start_dictation("agent")
        else:
            self.start_dictation("request")
        return "break"

    def start_dictation(self, target: str = "request") -> None:
        widget = self._dictation_widget(target)
        if widget is None:
            return
        if os.name != "nt":
            messagebox.showinfo(APP_NAME, "O ditado integrado usa a Digitação por voz do Windows (Win+H). Esta máquina não está rodando Windows.", parent=self)
            return
        try:
            widget.configure(state="normal")
            widget.focus_force()
            widget.mark_set("insert", "end-1c")
            widget.see("insert")
        except Exception:
            pass

        def trigger() -> None:
            if not open_windows_voice_typing():
                messagebox.showerror(APP_NAME, "Não consegui abrir o ditado do Windows. Tente apertar Win+H manualmente com o cursor no campo de texto.", parent=self)
                return
            if target == "agent":
                self.agent_hint.configure(text="🎙 Ditado aberto · fala normal · Win+H fecha · depois use ✦ Lapidar se quiser")
                self.after(9000, lambda: self.agent_hint.configure(text="Ctrl+Enter envia · Ctrl+Shift+M dita · backup automático antes de agir") if self.agent_hint.winfo_exists() else None)
                if hasattr(self, "agent_avatar"):
                    self.agent_avatar.set_state("working")
                    self.after(1800, lambda: self.agent_avatar.set_state("ready") if hasattr(self, "agent_avatar") and self.agent_avatar.winfo_exists() else None)
            else:
                self.live_title.configure(text="🎙 Pode falar. Eu tô anotando.")
                self.live_subtitle.configure(text="Ditado do Windows aberto · sem limite imposto pelo Vórtice · Win+H fecha quando terminar.")
                self.after(9000, lambda: self.live_title.configure(text="Pronto para cozinhar.") if not self.busy and self.live_title.winfo_exists() else None)
            try:
                play_vortex_sound("reminder") if bool(self.settings.get("soundsEnabled", True)) else None
            except Exception:
                pass

        self.after(140, trigger)

    def start_polish_dictation(self, target: str = "request") -> None:
        if self.dictation_polishing:
            return
        project = self.selected_project()
        widget = self._dictation_widget(target)
        if widget is None:
            return
        value = widget.get("1.0", "end-1c").strip()
        if not value:
            return
        self.dictation_polishing = target
        button = self.polish_agent_button if target == "agent" else self.polish_prompt_button
        try:
            button.configure(text="✦ Arrumando local…", state="disabled")
        except Exception:
            pass

        def worker():
            try:
                cleaned = local_polish_dictation(project, value)
                self.events.put(("dictation_polished", {"target": target, "text": cleaned, "local": True}))
            except Exception as exc:
                self.events.put(("dictation_polish_error", {"target": target, "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def finish_dictation_polish_ui(self, target: str) -> None:
        widget = self._dictation_widget(target)
        if widget is not None:
            try:
                widget.configure(state="normal")
            except Exception:
                pass
        button = self.polish_agent_button if target == "agent" else self.polish_prompt_button
        try:
            button.configure(text="✦ Arrumar local" if target == "agent" else "✦ Arrumar ditado", state="normal")
        except Exception:
            pass
        self.dictation_polishing = ""

    # ----------------------------- checklist -----------------------------

    def project_checklist_items(self, project_id: str | None = None) -> list[dict]:
        pid = str(project_id or self.selected_project_id or "")
        if not pid:
            return []
        items = [item for item in self.checklists.get(pid, []) if isinstance(item, dict)]
        order = {"doing": 0, "todo": 1, "done": 2}
        items.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        items.sort(key=lambda item: order.get(str(item.get("status") or "todo"), 1))
        return items

    def create_checklist_item(self, project_id: str, title: str) -> Optional[dict]:
        title = str(title or "").strip()
        if not project_id or not title:
            return None
        item = {"id": str(uuid.uuid4()), "title": title, "status": "todo", "attachments": [], "createdAt": now_iso(), "updatedAt": now_iso(), "taskId": ""}
        items = self.checklists.setdefault(project_id, [])
        items.append(item)
        self.checklists[project_id] = items[-500:]
        save_checklists(self.checklists)
        self.render_checklist_widget()
        return item

    def add_checklist_item(self) -> None:
        # Kept for compatibility with old shortcuts. The large checklist is the canonical UI now.
        self.open_project_checklist()

    def cycle_checklist_item(self, item_id: str, project_id: str | None = None) -> None:
        pid = str(project_id or self.selected_project_id or "")
        if not pid:
            return
        cycle = {"todo": "doing", "doing": "done", "done": "todo"}
        for item in self.checklists.get(pid, []):
            if str(item.get("id") or "") == str(item_id):
                item["status"] = cycle.get(str(item.get("status") or "todo"), "doing")
                item["updatedAt"] = now_iso()
                break
        save_checklists(self.checklists)
        self.render_checklist_widget()

    def remove_checklist_item(self, item_id: str, project_id: str | None = None) -> None:
        pid = str(project_id or self.selected_project_id or "")
        if not pid:
            return
        self.checklists[pid] = [item for item in self.checklists.get(pid, []) if str(item.get("id") or "") != str(item_id)]
        save_checklists(self.checklists)
        self.render_checklist_widget()

    def add_checklist_attachments(self, item_id: str, sources: Iterable[str | Path], project_id: str | None = None) -> list[Path]:
        pid = str(project_id or self.selected_project_id or "")
        if not pid:
            return []
        item = next((entry for entry in self.checklists.get(pid, []) if str(entry.get("id") or "") == str(item_id)), None)
        if not item:
            return []
        saved = persist_checklist_attachments(pid, item_id, sources)
        current = [str(path) for path in checklist_attachment_paths(item)]
        for path in saved:
            if path not in current:
                current.append(path)
        item["attachments"] = current
        item["updatedAt"] = now_iso()
        save_checklists(self.checklists)
        self.render_checklist_widget()
        return normalize_attachment_selection(saved)

    def activate_checklist_item(self, item_id: str, project_id: str | None = None) -> None:
        pid = str(project_id or self.selected_project_id or "")
        project = next((p for p in self.projects if p.id == pid), None)
        if not project or self.busy:
            return
        if self.selected_project_id != pid:
            self.select_project(pid)
        item = next((entry for entry in self.checklists.get(pid, []) if str(entry.get("id") or "") == str(item_id)), None)
        if not item:
            return
        linked_task = str(item.get("taskId") or "")
        if linked_task and any(str(task.get("id") or "") == linked_task for task in project_sessions(pid, 500)):
            self.select_session(pid, linked_task)
            self.set_mode("prepare")
            return
        self.save_current_draft()
        self._loading_draft = True
        try:
            self.request_text.delete("1.0", "end")
            self.request_text.insert("1.0", str(item.get("title") or ""))
            self.request_attachments = checklist_attachment_paths(item)
            self._attachment_images.clear()
            task_id = str(uuid.uuid4())
            item["taskId"] = task_id
            self.composer_task_id = task_id
            self.composer_checklist_id = str(item_id)
            self.composer_run_session_id = ""
            self.composer_run_at = ""
            self.composer_round_id = str(uuid.uuid4())
            self.composer_round_number = 1
            self.composer_followup = False
            self.selected_session_id = ""
            if str(item.get("status") or "todo") == "todo":
                item["status"] = "doing"
            item["updatedAt"] = now_iso()
            save_checklists(self.checklists)
            self._request_count()
            self.render_request_attachments()
            self.render_composer_state()
        finally:
            self._loading_draft = False
        self.save_current_draft()
        self.render_checklist_widget()
        self.render_projects()
        self.render_selected_session()
        self.refresh_agent_header()
        self.render_agent_chat()
        self.set_mode("prepare")
        self.request_text.focus_set()

    def checklist_to_vortex(self, item_id: str) -> None:
        project = self.selected_project()
        if not project:
            return
        self.activate_checklist_item(item_id, project.id)
        item = next((entry for entry in self.checklists.get(project.id, []) if str(entry.get("id") or "") == str(item_id)), None)
        if not item:
            return
        self.set_mode("agent")
        self.agent_input.delete("1.0", "end")
        self.agent_input.insert("1.0", f"Me ajude a melhorar este item do checklist antes de eu desenvolver. Deixe o texto claro, específico e fácil de virar uma task, sem inventar requisitos:\n\n{item.get('title') or ''}")
        self.agent_input.focus_set()

    def open_project_home(self) -> None:
        project = self.selected_project()
        if not project:
            messagebox.showinfo(APP_NAME, "Selecione um projeto primeiro.", parent=self)
            return
        ProjectHomeDialog(self, project)

    def open_project_checklist(self) -> None:
        project = self.selected_project()
        if not project:
            messagebox.showinfo(APP_NAME, "Selecione um projeto primeiro.", parent=self)
            return
        ProjectChecklistDialog(self, project)

    def render_checklist_widget(self) -> None:
        if not hasattr(self, "checklist_count_label"):
            return
        project = self.selected_project()
        if not project:
            self.checklist_count_label.configure(text="—")
            if hasattr(self, "checklist_summary_label"):
                self.checklist_summary_label.configure(text="Selecione um projeto para abrir o checklist.")
            return
        items = self.project_checklist_items(project.id)
        done = len([item for item in items if str(item.get("status") or "") == "done"])
        doing = len([item for item in items if str(item.get("status") or "") == "doing"])
        todo = len(items) - done - doing
        self.checklist_count_label.configure(text=f"{done}/{len(items)}")
        if hasattr(self, "checklist_summary_label"):
            if items:
                self.checklist_summary_label.configure(text=f"{todo} a fazer · {doing} em andamento · {done} concluídos")
            else:
                self.checklist_summary_label.configure(text="Backlog vazio. Adicione ideias, ajustes e prints quando precisar.")

    # ----------------------------- projects -----------------------------

    def selected_project(self) -> Optional[Project]:
        return next((p for p in self.projects if p.id == self.selected_project_id), None)

    def _photo_for_project(self, project: Project, max_size: int = 42) -> Optional[tk.PhotoImage]:
        logo = find_project_logo(project)
        if not logo:
            return None
        key = f"{project.id}:{logo}:{max_size}"
        cached = self._project_images.get(key)
        if cached:
            return cached
        try:
            image = tk.PhotoImage(file=logo)
            factor = max(1, math.ceil(max(image.width(), image.height()) / max_size))
            if factor > 1:
                image = image.subsample(factor, factor)
            self._project_images[key] = image
            return image
        except Exception:
            return None

    def render_projects(self) -> None:
        clear_children(self.project_scroll.inner)
        self.project_count_label.configure(text=str(len(self.projects)))

        if not self.projects:
            empty = tk.Frame(self.project_scroll.inner, bg=C["dark2"], padx=13, pady=16)
            empty.pack(fill="x", pady=(0, 8))
            tk.Label(empty, text="Sua bancada está vazia", bg=C["dark2"], fg=C["white"], font=(FONT, 9, "bold")).pack(anchor="w")
            tk.Label(empty, text="Adicione um projeto e bora cozinhar.", bg=C["dark2"], fg="#9A96A0", font=(FONT, 8), wraplength=250, justify="left").pack(anchor="w", pady=(4, 0))
            return

        ordered = sorted(self.projects, key=lambda p: (p.id != self.selected_project_id, p.name.lower()))
        palette = [C["lime"], C["violet"], C["peach"], C["cyan"]]
        for index, project in enumerate(ordered):
            selected = project.id == self.selected_project_id
            bg = "#3B3842" if selected else C["dark2"]
            border = C["lime"] if selected else "#38363D"
            shell = tk.Frame(self.project_scroll.inner, bg=bg, highlightbackground=border, highlightthickness=2 if selected else 1)
            shell.pack(fill="x", pady=(0, 9))

            head = tk.Frame(shell, bg=bg, padx=9, pady=9, cursor="hand2")
            head.pack(fill="x")
            image = self._photo_for_project(project, 38)
            if image:
                mark = tk.Label(head, image=image, bg=bg, width=42, height=42, cursor="hand2")
            else:
                initials = "".join(part[0] for part in project.name.split()[:2] if part).upper()[:2] or "AI"
                mark = tk.Label(head, text=initials, bg=palette[index % len(palette)], fg=C["ink"], width=4, height=2, font=(FONT, 8, "bold"), cursor="hand2")
            mark.pack(side="left", padx=(0, 9))

            copy = tk.Frame(head, bg=bg, cursor="hand2")
            copy.pack(side="left", fill="x", expand=True)
            name = tk.Label(copy, text=project.name, bg=bg, fg=C["white"], font=(FONT, 9, "bold"), anchor="w", cursor="hand2")
            name.pack(fill="x")
            sessions = project_sessions(project.id, 80)
            branch_count = len(sessions)
            subtitle = f"{branch_count} task" + ("" if branch_count == 1 else "s")
            subtitle += f"  ·  {len(project.paths)} pasta" + ("" if len(project.paths) == 1 else "s")
            root_count = tk.Label(copy, text=subtitle, bg=bg, fg="#9D99A3", font=(FONT, 7), anchor="w", cursor="hand2")
            root_count.pack(fill="x", pady=(2, 0))
            arrow = tk.Label(head, text="ATIVO" if selected else "›", bg=bg, fg=C["lime"] if selected else "#77727D", font=(FONT, 7, "bold"), cursor="hand2")
            arrow.pack(side="right", padx=(5, 1))
            for widget in (head, mark, copy, name, root_count, arrow):
                widget.bind("<Button-1>", lambda _e, pid=project.id: self.select_project(pid))

            # Orca-like branch tree: chats/contexts live under their project.
            if sessions and selected:
                tree_bg = "#2B2930"
                tree = tk.Frame(shell, bg=tree_bg, padx=8, pady=6)
                tree.pack(fill="x")
                visible = sessions[:6]
                for pos, session in enumerate(visible):
                    sid = str(session.get("id") or "")
                    is_active = selected and sid == self.selected_session_id
                    row_bg = "#393445" if is_active else tree_bg
                    row = tk.Frame(tree, bg=row_bg, cursor="hand2", padx=4, pady=5)
                    row.pack(fill="x", pady=(0, 2))
                    branch = tk.Canvas(row, width=20, height=30, bg=row_bg, highlightthickness=0, cursor="hand2")
                    branch.pack(side="left", padx=(0, 3))
                    line = "#68636F"
                    branch.create_line(6, 0, 6, 15, fill=line, width=1)
                    branch.create_line(6, 15, 16, 15, fill=line, width=1)
                    branch.create_oval(13, 12, 18, 17, fill=C["lime"] if is_active else C["violet"], outline="")
                    text_box = tk.Frame(row, bg=row_bg, cursor="hand2")
                    text_box.pack(side="left", fill="x", expand=True)
                    title = session_title(str(session.get("request") or ""))
                    title_label = tk.Label(text_box, text=title, bg=row_bg, fg=C["white"] if is_active else "#D2CED6", font=(FONT, 7, "bold" if is_active else "normal"), anchor="w", cursor="hand2")
                    title_label.pack(fill="x")
                    try:
                        dt = datetime.fromisoformat(str(session.get("createdAt") or "")).astimezone()
                        when = dt.strftime("%d/%m · %H:%M")
                    except Exception:
                        when = "recente"
                    chat = session.get("chat") if isinstance(session.get("chat"), dict) else None
                    prepare = session.get("prepare") if isinstance(session.get("prepare"), dict) else None
                    applied = session.get("apply") if isinstance(session.get("apply"), dict) else None
                    zip_ok = bool(history_zip_path(prepare or {}))
                    attachment_count = int((prepare or {}).get("attachmentCount") or len(history_attachment_paths(prepare or {})))
                    task_status = str(session.get("status") or "doing")
                    status = "concluída ✓" if task_status == "done" else ("homologar" if task_status == "review" else ("aplicado ✓" if applied else ("chat salvo" if chat else ("contexto pronto" if zip_ok else "contexto"))))
                    vortex_count = len(self._agent_messages_for(project.id, sid))
                    rounds_count = len(session.get("rounds") or [])
                    meta_text = f"{when}  ·  {status}" + (f"  ·  R{rounds_count}" if rounds_count else "") + (f"  ·  Vórtex {vortex_count}" if vortex_count else "") + (f"  ·  {attachment_count} anexo(s)" if attachment_count else "")
                    meta = tk.Label(text_box, text=meta_text, bg=row_bg, fg="#817C87", font=(MONO, 6), anchor="w", cursor="hand2")
                    meta.pack(fill="x", pady=(1, 0))
                    for widget in (row, branch, text_box, title_label, meta):
                        widget.bind("<Button-1>", lambda _e, pid=project.id, session_id=sid: self.select_session(pid, session_id))
                        widget.bind("<Double-Button-1>", lambda _e, pid=project.id, session_id=sid: self.open_session_chat_by_id(pid, session_id))
                if len(sessions) > len(visible):
                    more = make_button(tree, f"+ {len(sessions) - len(visible)} tasks · ver histórico", lambda pid=project.id: self.open_history_for(pid), bg=tree_bg, fg="#8E8994", active_bg="#35323A", font=(FONT, 7, "bold"), padx=4, pady=5, anchor="w")
                    more.pack(fill="x", pady=(2, 0))
            elif selected:
                empty_branch = tk.Label(shell, text="   └  nenhuma task ainda · crie seu primeiro pedido", bg="#2A282E", fg="#817C87", font=(FONT, 7), anchor="w", padx=8, pady=7)
                empty_branch.pack(fill="x")

    def select_project(self, project_id: str) -> None:
        if self.busy:
            return
        if not any(p.id == project_id for p in self.projects):
            return
        if self.selected_project_id == project_id:
            return
        self.save_current_draft()
        self.selected_project_id = project_id
        project = self.selected_project()
        if project:
            project.last_used_at = now_iso()
            save_projects(self.projects)
            sessions = project_sessions(project.id, 80)
            self.selected_session_id = str(sessions[0].get("id") or "") if sessions else ""
        self.load_project_draft(project_id)
        self.last_prepare = None
        self.hide_prepare_result()
        self.selected_result_zip = None
        if hasattr(self, "zip_name_label"):
            self.zip_name_label.configure(text="Nenhum ZIP escolhido")
            self.zip_meta_label.configure(text="")
            self.render_apply_preview([])
        self.set_phase("idle", f"{project.name} está na bancada" if project else "Projeto trocado", "Seu rascunho e seus chats agora são só deste projeto.")
        self.render_projects()
        self.render_selected_project()
        self.render_agent_chat()
        if self.mode == "agent":
            self.agent_input.focus_set()
        else:
            self.request_text.focus_set()

    def selected_session(self) -> Optional[dict]:
        project = self.selected_project()
        if not project:
            return None
        sessions = project_sessions(project.id, 120)
        if not self.selected_session_id:
            return None
        return next((item for item in sessions if str(item.get("id") or "") == self.selected_session_id), None)

    def select_session(self, project_id: str, session_id: str) -> None:
        if self.busy:
            return
        self.save_current_draft()
        if self.selected_project_id != project_id:
            self.selected_project_id = project_id
            project = self.selected_project()
            if project:
                project.last_used_at = now_iso()
                save_projects(self.projects)
        self.last_prepare = None
        self.hide_prepare_result()
        self.selected_result_zip = None
        self.render_apply_preview([])
        self.zip_name_label.configure(text="Nenhum ZIP escolhido")
        self.zip_meta_label.configure(text="")

        self.selected_session_id = str(session_id or "")
        session = next((item for item in project_sessions(project_id, 500) if str(item.get("id") or "") == self.selected_session_id), None)
        if session and hasattr(self, "request_text"):
            self._loading_draft = True
            try:
                rounds = session.get("rounds") or []
                latest_round = rounds[0] if rounds else {}
                task_draft = self._draft_for_task(project_id, self.selected_session_id)
                latest_prepare = latest_round.get("prepare") if isinstance(latest_round.get("prepare"), dict) else None
                if task_draft and bool(task_draft.get("followup")) and str(task_draft.get("roundId") or "") == str(latest_round.get("id") or "") and not latest_prepare:
                    prepare = {}
                else:
                    prepare = latest_prepare or (session.get("prepare") if isinstance(session.get("prepare"), dict) else {})
                request = str(task_draft.get("text") or latest_round.get("request") or session.get("latestRequest") or session.get("request") or "")
                self.request_text.delete("1.0", "end")
                if request:
                    self.request_text.insert("1.0", request)
                self.request_attachments = normalize_attachment_selection(task_draft.get("attachments") or history_attachment_paths(prepare or {}))
                self.composer_task_id = self.selected_session_id
                self.composer_checklist_id = str(task_draft.get("checklistId") or (prepare or {}).get("checklistId") or "")
                self.composer_round_id = str(task_draft.get("roundId") or latest_round.get("id") or (prepare or {}).get("roundId") or uuid.uuid4())
                self.composer_round_number = max(1, int(task_draft.get("roundNumber") or latest_round.get("number") or (prepare or {}).get("roundNumber") or 1))
                self.composer_followup = bool(task_draft.get("followup", False))
                self.composer_run_session_id = str(task_draft.get("runSessionId") or "") if task_draft else self.selected_session_id
                self.composer_run_at = str(task_draft.get("ranAt") or (prepare or {}).get("createdAt") or latest_round.get("createdAt") or session.get("createdAt") or "")
                self._request_count()
                self.render_request_attachments()
                self.render_composer_state()

                project = self.selected_project()
                zip_path = history_zip_path(prepare or {})
                prompt_path = history_prompt_path(prepare or {})
                if project and zip_path and zip_path.is_file():
                    prompt = ""
                    if prompt_path and prompt_path.is_file():
                        try:
                            prompt = prompt_path.read_text(encoding="utf-8")
                        except Exception:
                            prompt = ""
                    self.last_prepare = PrepareResult(
                        zip_path=zip_path,
                        prompt=prompt,
                        file_count=int((prepare or {}).get("fileCount") or 0),
                        summary=str((prepare or {}).get("summary") or "Contexto desta rodada."),
                        notes=[],
                        usage=(prepare or {}).get("usage"),
                        session_id=self.selected_session_id,
                        prompt_path=prompt_path,
                        attachment_count=int((prepare or {}).get("attachmentCount") or len(history_attachment_paths(prepare or {}))),
                        attachment_paths=history_attachment_paths(prepare or {}),
                        round_id=str((prepare or {}).get("roundId") or latest_round.get("id") or self.composer_round_id or ""),
                        round_number=max(1, int((prepare or {}).get("roundNumber") or latest_round.get("number") or self.composer_round_number or 1)),
                        task_title=str((prepare or {}).get("taskTitle") or session.get("title") or session.get("request") or ""),
                        project_id=project.id,
                        result_token=str((prepare or {}).get("resultToken") or ""),
                        expected_result_name=str((prepare or {}).get("expectedResultName") or ""),
                    )
                    self.show_prepare_result(self.last_prepare)

                result_event = latest_round.get("result") if isinstance(latest_round.get("result"), dict) else session.get("result")
                result_path = resolve_saved_path((result_event or {}).get("resultZipPath") or (result_event or {}).get("zipPath")) if isinstance(result_event, dict) else None
                if result_path and result_path.is_file():
                    self.selected_result_zip = result_path
                    self.zip_name_label.configure(text=result_path.name)
                    try:
                        self.zip_meta_label.configure(text=f"{result_path.stat().st_size / (1024*1024):.2f} MB  ·  resposta da Task")
                    except OSError:
                        self.zip_meta_label.configure(text="Resposta da Task")
                    self.refresh_apply_preview_async()
            finally:
                self._loading_draft = False
            self.save_current_draft()
        self.render_projects()
        self.render_selected_project()
        self.render_agent_chat()

    def open_session_chat_by_id(self, project_id: str, session_id: str) -> None:
        project = next((p for p in self.projects if p.id == project_id), None)
        if not project:
            return
        session = next((item for item in project_sessions(project.id, 120) if str(item.get("id") or "") == str(session_id)), None)
        chat = session.get("chat") if isinstance(session, dict) else None
        url = normalize_chatgpt_chat_url(str(chat.get("url") or "")) if isinstance(chat, dict) else ""
        if url:
            webbrowser.open_new_tab(url)
        else:
            self.select_session(project_id, session_id)

    def open_history_for(self, project_id: str) -> None:
        project = next((p for p in self.projects if p.id == project_id), None)
        if project:
            HistoryDialog(self, project)

    def _selected_session_artifacts(self) -> tuple[Optional[dict], Optional[Path], Optional[Path], str]:
        session = self.selected_session()
        if not session:
            return None, None, None, ""
        prepare = session.get("prepare") if isinstance(session.get("prepare"), dict) else None
        prompt_path = recover_prompt_from_context_zip(self.selected_project(), prepare or {}) if self.selected_project() else history_prompt_path(prepare or {})
        zip_path = history_zip_path(prepare or {})
        prompt = ""
        if prompt_path and prompt_path.is_file():
            try:
                prompt = prompt_path.read_text(encoding="utf-8")
            except Exception:
                prompt = ""
        return prepare, prompt_path, zip_path, prompt

    def render_selected_session(self) -> None:
        session = self.selected_session()
        if not session:
            draft_text = self.request_text.get("1.0", "end-1c").strip() if hasattr(self, "request_text") else ""
            if draft_text:
                self.branch_title.configure(text=session_title(draft_text, "Nova task"))
                vortex_count = len(self._agent_messages_for(self.selected_project_id, self.current_task_id())) if self.selected_project_id else 0
                bits = ["rascunho", "ainda não rodada"]
                if vortex_count:
                    bits.append(f"Vórtex {vortex_count} msg")
                self.branch_meta.configure(text="  ·  ".join(bits))
            else:
                self.branch_title.configure(text="Nova task")
                self.branch_meta.configure(text="Escreva o pedido ou abra uma task existente.")
            self.branch_chat_button.configure(state="disabled")
            self.branch_bundle_button.configure(state="disabled")
            self.branch_prompt_button.configure(state="disabled")
            self.branch_zip_button.configure(state="disabled")
            self.branch_reuse_button.configure(state="disabled")
            if hasattr(self, "branch_timeline_button"):
                self.branch_timeline_button.configure(state="disabled")
                self.branch_followup_button.configure(state="disabled")
                self.branch_status_button.configure(state="disabled", text="Aprovar ✓")
            sessions = project_sessions(self.selected_project_id, 160) if self.selected_project_id else []
            if hasattr(self, "task_prev_button"):
                self.task_prev_button.configure(state="normal" if sessions else "disabled")
                self.task_next_button.configure(state="disabled")
            return
        self.selected_session_id = str(session.get("id") or self.selected_session_id)
        title = session_title(str(session.get("request") or ""), "Task do projeto")
        self.branch_title.configure(text=title)
        try:
            dt = datetime.fromisoformat(str(session.get("createdAt") or "")).astimezone()
            when = dt.strftime("%d/%m/%Y · %H:%M")
        except Exception:
            when = "atividade recente"
        prepare, prompt_path, zip_path, _prompt = self._selected_session_artifacts()
        chat = session.get("chat") if isinstance(session.get("chat"), dict) else None
        applied = session.get("apply") if isinstance(session.get("apply"), dict) else None
        bits = [when]
        if prepare:
            bits.append(f"{prepare.get('fileCount', 0)} arquivos")
            attachment_count = int(prepare.get("attachmentCount") or len(history_attachment_paths(prepare)))
            if attachment_count:
                bits.append(f"{attachment_count} anexo(s)")
        bits.append("chat salvo" if chat else "chat ainda não vinculado")
        if applied:
            bits.append("aplicado ✓")
        vortex_count = len(self._agent_messages_for(self.selected_project_id, self.selected_session_id))
        if vortex_count:
            bits.append(f"Vórtex {vortex_count} msg")
        rounds = session.get("rounds") or []
        status = str(session.get("status") or "doing")
        if status == "review":
            bits.append("homologar")
        elif status == "done":
            bits.append("concluída ✓")
        if rounds:
            bits.append(f"R{int(rounds[0].get('number') or 1)}/{len(rounds)}")
        self.branch_meta.configure(text="  ·  ".join(bits))
        if hasattr(self, "branch_timeline_button"):
            self.branch_timeline_button.configure(state="normal")
            self.branch_followup_button.configure(state="normal")
            status = str(session.get("status") or "doing")
            self.branch_status_button.configure(state="normal", text="Reabrir" if status == "done" else "Aprovar ✓", command=self.toggle_current_task_status if status == "done" else self.approve_current_task, fg=C["violet"] if status == "done" else C["green"])
        url = normalize_chatgpt_chat_url(str(chat.get("url") or "")) if chat else ""
        self.branch_chat_button.configure(state="normal" if url else "disabled")
        self.branch_bundle_button.configure(state="normal" if prompt_path and prompt_path.is_file() and zip_path and zip_path.is_file() else "disabled")
        self.branch_prompt_button.configure(state="normal" if prompt_path and prompt_path.is_file() else "disabled")
        self.branch_zip_button.configure(state="normal" if zip_path and zip_path.is_file() else "disabled")
        self.branch_reuse_button.configure(state="normal" if str(session.get("request") or "").strip() else "disabled")
        sessions = project_sessions(self.selected_project_id, 160)
        index = next((i for i, item in enumerate(sessions) if str(item.get("id") or "") == self.selected_session_id), -1)
        self.task_prev_button.configure(state="normal" if index >= 0 and index + 1 < len(sessions) else "disabled")
        self.task_next_button.configure(state="normal" if index > 0 else "disabled")

    def switch_task_relative(self, delta: int) -> None:
        project = self.selected_project()
        if not project or self.busy:
            return
        sessions = project_sessions(project.id, 160)
        if not sessions:
            return
        if not self.selected_session_id:
            self.select_session(project.id, str(sessions[0].get("id") or ""))
            return
        index = next((i for i, item in enumerate(sessions) if str(item.get("id") or "") == self.selected_session_id), 0)
        target = max(0, min(len(sessions) - 1, index + int(delta)))
        if target != index:
            self.select_session(project.id, str(sessions[target].get("id") or ""))

    def open_task_timeline(self) -> None:
        project = self.selected_project()
        task_id = self.current_task_id()
        if not project or not task_id or task_id == "project":
            return
        TaskTimelineDialog(self, project, task_id)

    def set_task_status(self, task_id: str, status: str) -> None:
        project = self.selected_project()
        if not project or not task_id:
            return
        status = status if status in {"doing", "review", "done"} else "doing"
        labels = {"done": "Task concluída", "review": "Aguardando homologação", "doing": "Task reaberta"}
        append_history(project.id, "task_status", sessionId=task_id, taskId=task_id, status=status, summary=labels.get(status, status))
        for item in self.checklists.get(project.id, []):
            if str(item.get("taskId") or "") == str(task_id):
                item["status"] = "done" if status == "done" else "doing"
                item["updatedAt"] = now_iso()
        save_checklists(self.checklists)
        self.render_projects()
        self.render_selected_project()
        self.render_checklist_widget()

    def approve_task(self, task_id: str) -> None:
        project = self.selected_project()
        if not project or not task_id:
            return
        append_history(project.id, "homologation", sessionId=task_id, taskId=task_id, status="done", summary="Homologado ✓")
        for item in self.checklists.get(project.id, []):
            if str(item.get("taskId") or "") == str(task_id):
                item["status"] = "done"
                item["updatedAt"] = now_iso()
        save_checklists(self.checklists)
        self.render_projects(); self.render_selected_project(); self.render_checklist_widget()
        self.notify_finished("ready", "Task homologada ✓", "Fechou bonito. Marquei a Task e o item do checklist como concluídos.")

    def reject_task(self, task_id: str) -> None:
        project = self.selected_project()
        if not project or not task_id:
            return
        append_history(project.id, "homologation", sessionId=task_id, taskId=task_id, status="doing", summary="Reprovado · nova rodada necessária")
        self.begin_followup(task_id)

    def approve_current_task(self) -> None:
        task_id = self.current_task_id()
        if task_id and task_id != "project":
            self.approve_task(task_id)

    def reject_current_task(self) -> None:
        task_id = self.current_task_id()
        if task_id and task_id != "project":
            self.reject_task(task_id)

    def toggle_current_task_status(self) -> None:
        session = self.selected_session()
        if not session:
            return
        current = str(session.get("status") or "doing")
        self.set_task_status(str(session.get("id") or ""), "doing" if current == "done" else "done")

    def begin_followup(self, task_id: str | None = None) -> None:
        project = self.selected_project()
        tid = str(task_id or self.current_task_id() or "")
        task = next((item for item in project_sessions(project.id, 500) if str(item.get("id") or "") == tid), None) if project else None
        if not project or not task:
            messagebox.showinfo(APP_NAME, "Selecione uma Task que já tenha sido rodada.", parent=self)
            return
        self.save_current_draft()
        self.selected_session_id = tid
        self.composer_task_id = tid
        self.composer_round_id = str(uuid.uuid4())
        self.composer_round_number = task_next_round_number(project.id, tid)
        self.composer_followup = True
        self.composer_run_session_id = ""
        self.composer_run_at = ""
        self.request_text.delete("1.0", "end")
        title = session_title(str(task.get("request") or ""), "Task")
        self.request_text.insert("1.0", f"Continue a mesma Task ({title}) sobre o estado atual já alterado.\n\nAJUSTE NECESSÁRIO:\n")
        self.request_attachments = []
        self._attachment_images.clear()
        self._request_count()
        self.render_request_attachments()
        self.render_composer_state()
        self.hide_prepare_result()
        self.last_prepare = None
        append_history(project.id, "followup", sessionId=tid, taskId=tid, roundId=self.composer_round_id, roundNumber=self.composer_round_number, request="Nova rodada de ajuste iniciada", summary=f"Rodada {self.composer_round_number} iniciada")
        self.save_current_draft()
        self.render_selected_session()
        self.set_mode("prepare")
        self.request_text.focus_set()
        self.add_activity(f"Rodada {self.composer_round_number} aberta na mesma Task. O chat anterior será reutilizado.")

    def restore_history_backup(self, event: dict) -> None:
        project = self.selected_project()
        if not project:
            return
        backup = resolve_saved_path(event.get("backup"))
        if not backup or not backup.exists():
            messagebox.showinfo(APP_NAME, "Esse ponto de restauração não está mais disponível.", parent=self)
            return
        if not messagebox.askyesno(APP_NAME, "Restaurar o código para este ponto?\n\nO Vórtice vai mexer somente no projeto e manter o histórico da Task.", parent=self):
            return
        try:
            if backup.is_file() and backup.suffix.lower() == ".zip":
                restored, removed = restore_agent_backup(project, backup)
                summary = f"{restored} restaurados · {removed} removidos"
            else:
                count = rollback_backup(backup)
                summary = f"{count} arquivo(s) restaurados"
            append_history(project.id, "rollback", sessionId=self.current_task_id(), taskId=self.current_task_id(), roundId=self.composer_round_id, roundNumber=self.composer_round_number, summary=f"Restauração manual: {summary}", backup=str(backup))
            self.notify_finished("apply", "Código restaurado", summary)
            self.refresh_git_overview_async()
            self.render_selected_project()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def open_selected_session_chat(self) -> None:
        session = self.selected_session()
        chat = session.get("chat") if isinstance(session, dict) and isinstance(session.get("chat"), dict) else None
        url = normalize_chatgpt_chat_url(str(chat.get("url") or "")) if chat else ""
        if url:
            webbrowser.open_new_tab(url)

    def copy_selected_session_prompt(self) -> None:
        _prepare, prompt_path, _zip_path, prompt = self._selected_session_artifacts()
        if not prompt_path or not prompt_path.is_file() or not prompt:
            messagebox.showinfo(APP_NAME, "Esse prompt não está salvo localmente. Contextos criados antes da v2.6 podem ter somente o ZIP.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(prompt)
        self.update()
        self.add_activity("Prompt da task copiado para o clipboard.")

    def reveal_selected_session_zip(self) -> None:
        _prepare, _prompt_path, zip_path, _prompt = self._selected_session_artifacts()
        if not zip_path or not zip_path.is_file():
            messagebox.showinfo(APP_NAME, "O ZIP dessa task não está mais na pasta de outputs.", parent=self)
            return
        reveal_in_file_manager(zip_path)

    def copy_selected_session_bundle(self) -> None:
        prepare, prompt_path, zip_path, prompt = self._selected_session_artifacts()
        if not zip_path or not zip_path.is_file():
            messagebox.showinfo(APP_NAME, "O ZIP dessa task não está disponível.", parent=self)
            return
        if not prompt and prompt_path and prompt_path.is_file():
            prompt = prompt_path.read_text(encoding="utf-8")
        files = [zip_path, *history_attachment_paths(prepare or {})]
        copied = copy_text_and_files_windows(prompt, files) if prompt else False
        if not copied and prompt:
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.update()
        reveal_in_file_manager(zip_path)
        self.add_activity("Pacote da task pronto de novo para o ChatGPT.")

    def current_attachment_paths(self) -> list[Path]:
        self.request_attachments = normalize_attachment_selection(self.request_attachments)
        return list(self.request_attachments)

    def _merge_attachment_paths(self, paths: Iterable[Path]) -> int:
        existing = self.current_attachment_paths()
        seen = {path.resolve() for path in existing}
        added = 0
        for path in normalize_attachment_selection(paths):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            existing.append(resolved)
            added += 1
        self.request_attachments = existing
        self.composer_run_session_id = ""
        self.composer_run_at = ""
        self.render_request_attachments()
        self.render_composer_state()
        self.save_current_draft()
        return added

    def add_request_attachments(self) -> None:
        selected = filedialog.askopenfilenames(title="Escolha imagens e arquivos de apoio", parent=self)
        if not selected:
            return
        added = self._merge_attachment_paths(Path(value) for value in selected)
        if added:
            self.add_activity(f"{added} anexo(s) extra adicionados ao pedido.")

    def clear_request_attachments(self) -> None:
        self.request_attachments = []
        self._attachment_images.clear()
        self.composer_run_session_id = ""
        self.composer_run_at = ""
        self.render_request_attachments()
        self.render_composer_state()
        self.save_current_draft()

    def handle_attachment_paste(self, _event=None):
        files = clipboard_attachments_windows()
        if not files:
            return None
        added = self._merge_attachment_paths(files)
        if added:
            self.add_activity(f"{added} anexo(s) colados do clipboard.")
        return "break"

    def paste_attachments_from_clipboard(self) -> None:
        result = self.handle_attachment_paste()
        if result != "break":
            messagebox.showinfo(APP_NAME, "Não encontrei imagem ou arquivo no clipboard agora.", parent=self)

    def open_attachment(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não consegui abrir o anexo.\n\n{exc}", parent=self)

    def remove_attachment(self, path: Path) -> None:
        self.request_attachments = [item for item in self.current_attachment_paths() if item != path]
        self._attachment_images.pop(str(path), None)
        self.composer_run_session_id = ""
        self.composer_run_at = ""
        self.render_request_attachments()
        self.render_composer_state()
        self.save_current_draft()

    def _attachment_preview_image(self, path: Path, max_px: int = 72) -> Optional[tk.PhotoImage]:
        key = f"{path.resolve()}::{max_px}"
        if key in self._attachment_images:
            return self._attachment_images[key]
        if not is_previewable_image(path):
            return None
        try:
            img = tk.PhotoImage(file=str(path))
            width = max(int(img.width()), 1)
            height = max(int(img.height()), 1)
            factor = max(math.ceil(width / max_px), math.ceil(height / max_px), 1)
            if factor > 1:
                img = img.subsample(factor, factor)
            self._attachment_images[key] = img
            return img
        except Exception:
            return None

    def render_request_attachments(self) -> None:
        files = self.current_attachment_paths()
        if hasattr(self, "attachment_strip"):
            clear_children(self.attachment_strip.inner)
            if files:
                if hasattr(self, "attach_panel") and not self.attach_panel.winfo_manager():
                    self.attach_panel.pack(fill="x", pady=(6, 0))
                for index, path in enumerate(files):
                    card = tk.Frame(self.attachment_strip.inner, bg="#FBF8F2", width=118, height=86, highlightbackground=C["line"], highlightthickness=1)
                    card.grid(row=0, column=index, padx=(0, 8), pady=6, sticky="ns")
                    card.grid_propagate(False)

                    top = tk.Frame(card, bg="#FBF8F2")
                    top.pack(fill="x", padx=7, pady=(5, 3))
                    badge = attachment_badge(path)
                    accent = attachment_color(path)
                    badge_bg = "#EEE8FF" if badge == "IMG" else ("#FFF0E8" if badge == "PDF" else ("#EFF8EE" if badge == "ZIP" else "#EDF6FF"))
                    tk.Label(top, text=badge, bg=badge_bg, fg=accent, font=(MONO, 6, "bold"), padx=6, pady=2).pack(side="left")
                    make_button(top, "×", lambda p=path: self.remove_attachment(p), bg="#FBF8F2", fg=C["muted"], active_bg="#F0ECE5", font=(FONT, 8, "bold"), padx=5, pady=1).pack(side="right")

                    preview_shell = tk.Frame(card, bg="#F4F0EA", width=100, height=34)
                    preview_shell.pack(fill="x", padx=7)
                    preview_shell.pack_propagate(False)
                    preview = self._attachment_preview_image(path, 52)
                    if preview:
                        widget = tk.Label(preview_shell, image=preview, bg="#F4F0EA", cursor="hand2")
                        widget.image = preview
                        widget.pack(expand=True)
                    else:
                        widget = tk.Label(preview_shell, text=badge, bg="#F4F0EA", fg=accent, font=(FONT, 15, "bold"), cursor="hand2")
                        widget.pack(expand=True)
                    widget.bind("<Button-1>", lambda _e, p=path: self.open_attachment(p))

                    name = path.name if len(path.name) <= 17 else path.name[:14] + "..."
                    tk.Label(card, text=name, bg="#FBF8F2", fg=C["ink"], font=(FONT, 8, "bold"), anchor="w").pack(fill="x", padx=7, pady=(3, 0))
                    tk.Label(card, text=human_size(path.stat().st_size), bg="#FBF8F2", fg=C["muted"], font=(MONO, 6), anchor="w").pack(fill="x", padx=7, pady=(2, 0))
            else:
                if hasattr(self, "attach_panel") and self.attach_panel.winfo_manager():
                    self.attach_panel.pack_forget()
        if hasattr(self, "attachments_count"):
            self.attachments_count.configure(text=f"{len(files)} anexo" + ("" if len(files) == 1 else "s"))
        if hasattr(self, "clear_attachments_button"):
            self.clear_attachments_button.configure(state="normal" if files else "disabled")

    def _task_draft_key(self, project_id: str, task_id: str) -> str:
        return f"{project_id}::{task_id or 'new'}"

    def save_current_draft(self) -> None:
        if self._loading_draft or not self.selected_project_id or not hasattr(self, "request_text"):
            return
        text = self.request_text.get("1.0", "end-1c")
        attachments = [str(path) for path in self.current_attachment_paths()]
        task_id = str(self.composer_task_id or "")
        payload = {
            "text": text,
            "attachments": attachments,
            "updatedAt": now_iso(),
            "taskId": task_id,
            "checklistId": self.composer_checklist_id,
            "roundId": self.composer_round_id,
            "roundNumber": self.composer_round_number,
            "followup": self.composer_followup,
            "runSessionId": self.composer_run_session_id,
            "ranAt": self.composer_run_at,
        }
        if text.strip() or attachments or self.composer_run_session_id or task_id:
            self.drafts[self._task_draft_key(self.selected_project_id, task_id)] = payload
            self.drafts[self.selected_project_id] = {"activeTaskId": task_id, "updatedAt": now_iso()}
        try:
            save_drafts(self.drafts)
        except Exception:
            pass

    def _draft_for_task(self, project_id: str, task_id: str) -> dict:
        if not project_id or not task_id:
            return {}
        item = self.drafts.get(self._task_draft_key(project_id, task_id), {})
        return item if isinstance(item, dict) else {}

    def load_project_draft(self, project_id: str) -> None:
        if not hasattr(self, "request_text"):
            return
        self._loading_draft = True
        try:
            pointer = self.drafts.get(project_id, {}) if project_id else {}
            draft: dict = {}
            active_task_id = ""
            if isinstance(pointer, dict):
                active_task_id = str(pointer.get("activeTaskId") or "")
                # Legacy v3 draft stored the whole payload directly under projectId.
                if pointer.get("text") is not None or pointer.get("runSessionId"):
                    draft = pointer
                    active_task_id = str(pointer.get("taskId") or pointer.get("runSessionId") or active_task_id)
            if active_task_id and not draft:
                draft = self._draft_for_task(project_id, active_task_id)
            if project_id and not draft:
                sessions = project_sessions(project_id, 160)
                if sessions:
                    latest = sessions[0]
                    latest_round = (latest.get("rounds") or [{}])[0]
                    prepare = latest_round.get("prepare") if isinstance(latest_round.get("prepare"), dict) else (latest.get("prepare") if isinstance(latest.get("prepare"), dict) else {})
                    draft = {
                        "text": str(latest_round.get("request") or latest.get("latestRequest") or latest.get("request") or ""),
                        "attachments": [str(path) for path in history_attachment_paths(prepare or {})],
                        "taskId": str(latest.get("id") or ""),
                        "roundId": str(latest_round.get("id") or (prepare or {}).get("roundId") or latest.get("id") or ""),
                        "roundNumber": int(latest_round.get("number") or (prepare or {}).get("roundNumber") or 1),
                        "followup": False,
                        "runSessionId": str(latest.get("id") or ""),
                        "ranAt": str((prepare or {}).get("createdAt") or latest_round.get("createdAt") or latest.get("createdAt") or ""),
                    }
            self.request_text.delete("1.0", "end")
            value = str(draft.get("text") or "")
            if value:
                self.request_text.insert("1.0", value)
            self.request_attachments = normalize_attachment_selection(draft.get("attachments") or [])
            self.composer_task_id = str(draft.get("taskId") or draft.get("runSessionId") or (str(uuid.uuid4()) if project_id else ""))
            self.composer_checklist_id = str(draft.get("checklistId") or "")
            self.composer_round_id = str(draft.get("roundId") or (str(uuid.uuid4()) if project_id else ""))
            self.composer_round_number = max(1, int(draft.get("roundNumber") or 1))
            self.composer_followup = bool(draft.get("followup", False))
            self.composer_run_session_id = str(draft.get("runSessionId") or "")
            self.composer_run_at = str(draft.get("ranAt") or "")
            existing_ids = {str(item.get("id") or "") for item in project_sessions(project_id, 500)} if project_id else set()
            self.selected_session_id = self.composer_task_id if self.composer_task_id in existing_ids else ""
            self._request_count()
            self.render_request_attachments()
            self.render_composer_state()
        finally:
            self._loading_draft = False

    def render_composer_state(self) -> None:
        if not hasattr(self, "composer_state_label"):
            return
        text = self.request_text.get("1.0", "end-1c").strip() if hasattr(self, "request_text") else ""
        attachments = self.current_attachment_paths() if hasattr(self, "request_attachments") else []
        if self.composer_followup and not self.composer_run_session_id:
            label, bg, fg = f"AJUSTE · R{self.composer_round_number}", "#FFF0E8", "#A65E36"
        elif self.composer_run_session_id:
            label, bg, fg = f"TASK · R{self.composer_round_number} ✓", "#E5F8EC", C["green"]
        elif text or attachments:
            label, bg, fg = "RASCUNHO", "#FFF0E8", "#A65E36"
        else:
            label, bg, fg = "NOVA TASK", "#EEF9D8", C["green"]
        self.composer_state_label.configure(text=label, bg=bg, fg=fg)

    def new_request(self) -> None:
        if self.busy:
            return
        self.request_text.delete("1.0", "end")
        self.request_attachments = []
        self._attachment_images.clear()
        self.composer_task_id = str(uuid.uuid4())
        self.composer_checklist_id = ""
        self.composer_round_id = str(uuid.uuid4())
        self.composer_round_number = 1
        self.composer_followup = False
        self.composer_run_session_id = ""
        self.composer_run_at = ""
        self.selected_session_id = ""
        self.hide_prepare_result()
        self._request_count()
        self.render_request_attachments()
        self.render_composer_state()
        self.save_current_draft()
        self.render_projects()
        self.render_selected_session()
        self.refresh_agent_header()
        self.render_agent_chat()
        self.request_text.focus_set()

    def reuse_selected_session_request(self) -> None:
        session = self.selected_session()
        if not session:
            return
        request = str(session.get("request") or "").strip()
        if not request:
            return
        self.request_text.delete("1.0", "end")
        self.request_text.insert("1.0", request)
        prepare = session.get("prepare") if isinstance(session.get("prepare"), dict) else {}
        self.request_attachments = history_attachment_paths(prepare or {})
        self.composer_task_id = str(uuid.uuid4())
        self.composer_checklist_id = ""
        self.composer_round_id = str(uuid.uuid4())
        self.composer_round_number = 1
        self.composer_followup = False
        self.composer_run_session_id = ""
        self.composer_run_at = ""
        self._request_count()
        self.render_request_attachments()
        self.render_composer_state()
        self.save_current_draft()
        self.render_selected_session()
        self.refresh_agent_header()
        self.render_agent_chat()
        self.request_text.focus_set()

    def _project_usage(self, project: Optional[Project]) -> dict:
        total = normalize_usage(None)
        if not project:
            return total
        for entry in project_history(project.id, 500):
            if entry.get("type") == "prepare":
                merge_usage(total, entry.get("usage"))
        return total

    def refresh_usage_dashboard(self) -> None:
        total = self._project_usage(self.selected_project())
        self.token_total_value.configure(text=f"{format_tokens(usage_total(total))} tokens")
        self.token_input_value.configure(text=f"ENTRADA {format_tokens(total['input_tokens'])}")
        self.token_cache_value.configure(text=f"CACHE {format_tokens(total['cached_input_tokens'])}")
        self.token_output_value.configure(text=f"SAÍDA {format_tokens(total['output_tokens'])}")
        round_total = usage_total(self.current_usage)
        self.token_round_value.configure(text=f"RODADA {format_tokens(round_total)}" if round_total else "RODADA —")

    def render_selected_project(self) -> None:
        project = self.selected_project()
        if project:
            roots = f"{len(project.paths)} pasta" + ("" if len(project.paths) == 1 else "s")
            self.active_project_name.configure(text=project.name)
            sessions = project_sessions(project.id, 120)
            latest_text = "nenhuma rodada ainda"
            if sessions:
                try:
                    latest_dt = datetime.fromisoformat(str(sessions[0].get("createdAt") or "")).astimezone()
                    latest_text = f"última rodada {latest_dt.strftime('%d/%m · %H:%M')}"
                except Exception:
                    latest_text = "atividade recente"
            self.active_project_roots.configure(text=f"{roots} · {len(sessions)} task" + ("" if len(sessions) == 1 else "s") + f" · {latest_text}")
            self.apply_project_name.configure(text=project.name)
            image = self._photo_for_project(project, 52)
            if image:
                self.active_logo.configure(image=image, text="", width=60, height=54, bg="#F8F5EF")
                self.active_logo.image = image
            else:
                initials = "".join(part[0] for part in project.name.split()[:2] if part).upper()[:2] or "AI"
                self.active_logo.configure(image="", text=initials, width=6, height=3, bg=C["ink"], fg=C["lime"])
                self.active_logo.image = None
            total = self._project_usage(project)
            self.active_project_usage.configure(text=f"{format_tokens(usage_total(total))} TOKENS" if usage_total(total) else "SEM USO AINDA")
            self.active_project_git.configure(text="GIT · lendo…", fg=C["muted"])
            self.git_overview_label.configure(text="GIT · lendo…", fg=C["muted"])
            valid_ids = {str(item.get("id") or "") for item in sessions}
            if self.selected_session_id not in valid_ids:
                self.selected_session_id = self.composer_run_session_id if self.composer_run_session_id in valid_ids else ""
            branch_count = len(sessions)
            if branch_count:
                self.active_project_chat.configure(text=f"{branch_count} TASK" + ("" if branch_count == 1 else "S") + " · VER HISTÓRICO  ↗")
            else:
                self.active_project_chat.configure(text="NENHUMA TASK AINDA")
            self.git_button.configure(state="normal")
            self.chats_button.configure(state="normal")
            self.edit_project_button.configure(state="normal")
            self.remove_project_button.configure(state="normal")
            self.open_folder_button.configure(state="normal")
            self.open_vscode_button.configure(state="normal")
            self.header_folder_button.configure(state="normal")
            self.header_vscode_button.configure(state="normal")
        else:
            self.active_project_name.configure(text="Nenhum projeto")
            self.active_project_roots.configure(text="Escolha um projeto na lateral")
            self.active_project_usage.configure(text="")
            self.active_project_git.configure(text="")
            self.git_overview_label.configure(text="GIT —", fg=C["muted"])
            self.active_project_chat.configure(text="")
            self.git_button.configure(state="disabled")
            self.chats_button.configure(state="disabled")
            self.active_logo.configure(image="", text="AI", width=6, height=3, bg=C["ink"], fg=C["lime"])
            self.active_logo.image = None
            self.apply_project_name.configure(text="Nenhum projeto")
            self.edit_project_button.configure(state="disabled")
            self.remove_project_button.configure(state="disabled")
            self.open_folder_button.configure(state="disabled")
            self.open_vscode_button.configure(state="disabled")
            self.header_folder_button.configure(state="disabled")
            self.header_vscode_button.configure(state="disabled")
        self.refresh_usage_dashboard()
        self.render_selected_session()
        self.render_checklist_widget()
        self.refresh_agent_header()
        self.render_agent_chat()
        self.refresh_git_overview_async()
        if self.selected_result_zip:
            self.refresh_apply_preview_async()

    def refresh_git_overview_async(self) -> None:
        project = self.selected_project()
        self.git_overview_generation += 1
        generation = self.git_overview_generation
        if not project:
            return
        project_id = project.id

        def worker() -> None:
            if not resolve_git_source():
                payload = {"generation": generation, "project_id": project_id, "text": "GIT OFF", "dirty": None, "ok": False}
                self.events.put(("git_overview", payload))
                return
            try:
                snapshots = project_git_snapshot(project)
                valid = [item for item in snapshots if item.get("ok")]
                if not valid:
                    payload = {"generation": generation, "project_id": project_id, "text": "SEM GIT", "dirty": 0, "ok": False}
                else:
                    dirty = sum(int(item.get("dirty") or 0) for item in valid)
                    branches = [str(item.get("branch") or "") for item in valid]
                    branch = branches[0] if branches and len(set(branches)) == 1 else f"{len(valid)} repos"
                    ahead = sum(int(item.get("ahead") or 0) for item in valid)
                    behind = sum(int(item.get("behind") or 0) for item in valid)
                    state = "limpo" if dirty == 0 else f"{dirty} alteração" + ("" if dirty == 1 else "ões")
                    sync = f" · ↑{ahead} ↓{behind}" if ahead or behind else ""
                    payload = {
                        "generation": generation, "project_id": project_id,
                        "text": f"GIT · {branch} · {state}{sync}", "dirty": dirty, "ok": True,
                    }
            except Exception:
                payload = {"generation": generation, "project_id": project_id, "text": "GIT · erro ao ler", "dirty": None, "ok": False}
            self.events.put(("git_overview", payload))

        threading.Thread(target=worker, daemon=True).start()

    def new_project(self) -> None:
        if self.busy:
            return
        ProjectDialog(self)

    def edit_project(self) -> None:
        if self.busy:
            return
        project = self.selected_project()
        if project:
            ProjectDialog(self, project)

    def upsert_project(self, project: Project) -> None:
        previous_id = self.selected_project_id
        if previous_id and previous_id != project.id:
            self.save_current_draft()
        existing_index = next((i for i, item in enumerate(self.projects) if item.id == project.id), None)
        is_new = existing_index is None
        if is_new:
            self.projects.append(project)
        else:
            self.projects[existing_index] = project
        self.selected_project_id = project.id
        save_projects(self.projects)
        if is_new or previous_id != project.id:
            self.selected_session_id = ""
            self.load_project_draft(project.id)
        self.render_projects()
        self.render_selected_project()

    def remove_project(self) -> None:
        if self.busy:
            return
        project = self.selected_project()
        if not project:
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Remover '{project.name}' da lista?\n\nNenhum arquivo do projeto será apagado.",
            parent=self,
        ):
            return
        self.projects = [p for p in self.projects if p.id != project.id]
        self.drafts.pop(project.id, None)
        for key in [key for key in list(self.drafts) if str(key).startswith(project.id + "::")]:
            self.drafts.pop(key, None)
        for key in list(self.agent_chats):
            if key == project.id or key.startswith(project.id + "::"):
                self.agent_chats.pop(key, None)
        self.checklists.pop(project.id, None)
        try:
            save_drafts(self.drafts)
            save_agent_chats(self.agent_chats)
            save_checklists(self.checklists)
            shutil.rmtree(project_skill_dir(project.id), ignore_errors=True)
        except Exception:
            pass
        self.selected_project_id = self.projects[0].id if self.projects else ""
        self.selected_session_id = ""
        save_projects(self.projects)
        self.load_project_draft(self.selected_project_id)
        self.last_prepare = None
        self.hide_prepare_result()
        self.render_projects()
        self.render_selected_project()

    def open_selected_project_folders(self) -> None:
        project = self.selected_project()
        if not project:
            messagebox.showinfo(APP_NAME, "Escolha um projeto primeiro.", parent=self)
            return
        try:
            count = open_project_folders(project)
            self.add_activity(f"Abri {count} pasta" + ("." if count == 1 else "s do projeto."))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def open_selected_project_vscode(self) -> None:
        project = self.selected_project()
        if not project:
            messagebox.showinfo(APP_NAME, "Escolha um projeto primeiro.", parent=self)
            return
        try:
            count = open_project_vscode(project)
            self.add_activity(f"VS Code aberto em {count} raiz" + ("." if count == 1 else "es do projeto."))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def open_git(self) -> None:
        project = self.selected_project()
        if not project:
            messagebox.showwarning(APP_NAME, "Escolha um projeto primeiro.", parent=self)
            return
        GitDialog(self, project)

    def open_history(self) -> None:
        project = self.selected_project()
        if not project:
            messagebox.showinfo(APP_NAME, "Escolha um projeto para ver o histórico.", parent=self)
            return
        HistoryDialog(self, project)

    def open_chats(self) -> None:
        project = self.selected_project()
        if not project:
            messagebox.showinfo(APP_NAME, "Escolha um projeto para ver os chats.", parent=self)
            return
        ChatDialog(self, project)

    def open_latest_chat(self) -> None:
        project = self.selected_project()
        if not project:
            return
        item = latest_project_chat(project.id)
        url = normalize_chatgpt_chat_url(str(item.get("url") or "")) if item else ""
        if not url:
            self.open_chats()
            return
        try:
            webbrowser.open_new_tab(url)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não consegui abrir o chat.\n\n{exc}", parent=self)

    def _save_chat_link(self, project: Project, url: str, source: str = "auto", session_id: str = "") -> bool:
        url = normalize_chatgpt_chat_url(url)
        if not url:
            return False
        existing = [item for item in project_history(project.id, 300) if item.get("type") == "chat_link" and normalize_chatgpt_chat_url(str(item.get("url") or "")) == url]
        if existing:
            return True
        session_id = str(session_id or self.selected_session_id or (self.last_prepare.session_id if self.last_prepare else ""))
        prepare = session_prepare_event(project.id, session_id) if session_id else None
        request = str((prepare or {}).get("request") or self.request_text.get("1.0", "end-1c").strip())
        append_history(
            project.id, "chat_link",
            sessionId=session_id,
            taskId=session_id,
            roundId=str((prepare or {}).get("roundId") or self.composer_round_id or ""),
            roundNumber=int((prepare or {}).get("roundNumber") or self.composer_round_number or 1),
            summary="ChatGPT vinculado", request=request, url=url, source=source,
            zipName=str((prepare or {}).get("zipName") or (self.last_prepare.zip_path.name if self.last_prepare else "")),
        )
        self.selected_session_id = session_id or self.selected_session_id
        self.add_activity("Link do chat salvo na task. Agora ele não some no multiverso.")
        self.render_projects()
        self.render_selected_project()
        return True

    def save_chat_link_from_clipboard(self) -> bool:
        project = self.selected_project()
        if not project:
            return False
        try:
            raw = self.clipboard_get()
        except Exception:
            raw = ""
        url = normalize_chatgpt_chat_url(str(raw or ""))
        if not url:
            messagebox.showinfo(
                APP_NAME,
                "Não achei um link de conversa do ChatGPT no clipboard.\n\nNo navegador, copie a URL do chat e tente novamente.",
                parent=self,
            )
            return False
        if self._save_chat_link(project, url, "clipboard", self.selected_session_id):
            messagebox.showinfo(APP_NAME, "Chat vinculado à task selecionada.", parent=self)
            return True
        return False

    def _start_chat_link_watcher(self, project: Project, baseline: set[str], session_id: str = "") -> None:
        self.chat_watch_generation += 1
        generation = self.chat_watch_generation
        self.chat_watch_started_at = time.monotonic()
        session_id = str(session_id or "")

        def worker():
            deadline = time.monotonic() + 15 * 60
            while time.monotonic() < deadline and generation == self.chat_watch_generation:
                try:
                    urls = recent_chatgpt_chat_urls(30)
                    for url in urls:
                        if url not in baseline:
                            self.events.put(("chat_link_detected", {"project_id": project.id, "url": url, "session_id": session_id}))
                            return
                except Exception:
                    pass
                time.sleep(2.0)
            if generation == self.chat_watch_generation:
                self.events.put(("chat_link_timeout", {"project_id": project.id, "session_id": session_id}))

        threading.Thread(target=worker, daemon=True).start()

    def open_settings(self) -> None:
        if self.busy:
            return
        SettingsDialog(self)

    def show_vortex_toast(
        self,
        title: str,
        message: str,
        *,
        kind: str = "ready",
        native: bool = True,
        play_sound: bool = True,
        force: bool = False,
    ) -> None:
        settings = self.settings if isinstance(self.settings, dict) else load_settings()
        notifications_enabled = bool(settings.get("notificationsEnabled", True))
        sounds_enabled = bool(settings.get("soundsEnabled", True))
        if force:
            notifications_enabled = True

        if play_sound and sounds_enabled:
            play_vortex_sound(kind)

        if not notifications_enabled:
            return

        if native:
            show_windows_balloon_notification(title, message, error=(kind == "error"))

        # Replace the previous little reminder instead of stacking a wall of popups.
        try:
            if self._toast_window and self._toast_window.winfo_exists():
                self._toast_window.destroy()
        except Exception:
            pass

        try:
            toast = tk.Toplevel(self)
            self._toast_window = toast
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            toast.configure(bg=C["dark"])
            width, height = 390, 112
            screen_w = toast.winfo_screenwidth()
            screen_h = toast.winfo_screenheight()
            x = max(12, screen_w - width - 24)
            y = max(12, screen_h - height - 58)
            toast.geometry(f"{width}x{height}+{x}+{y}")

            shell = tk.Frame(toast, bg=C["dark"], highlightbackground="#4A4550", highlightthickness=1, padx=12, pady=10)
            shell.pack(fill="both", expand=True)
            state = "error" if kind == "error" else ("done" if kind in {"ready", "agent", "apply"} else "alert")
            avatar = VortexAvatar(shell, width=70, height=70, bg=C["dark"], show_label=False)
            avatar.set_state(state)
            avatar.pack(side="left", padx=(0, 11))

            copy = tk.Frame(shell, bg=C["dark"])
            copy.pack(side="left", fill="both", expand=True)
            title_label = tk.Label(copy, text=str(title), bg=C["dark"], fg=C["white"], font=(FONT, 10, "bold"), anchor="w")
            title_label.pack(fill="x", pady=(2, 2))
            message_label = tk.Label(copy, text=str(message), bg=C["dark"], fg="#BBB6C0", font=(FONT, 8), anchor="w", justify="left", wraplength=260)
            message_label.pack(fill="x")
            hint = tk.Label(copy, text="clique para voltar pro Vórtice", bg=C["dark"], fg=C["lime"] if kind != "error" else C["peach"], font=(FONT, 7, "bold"), anchor="w")
            hint.pack(fill="x", pady=(6, 0))

            close = tk.Label(shell, text="×", bg=C["dark"], fg="#77727D", font=(FONT, 11, "bold"), cursor="hand2")
            close.pack(side="right", anchor="n")

            def focus_app(_event=None) -> None:
                try:
                    self.deiconify()
                    self.lift()
                    self.focus_force()
                except Exception:
                    pass
                try:
                    toast.destroy()
                except Exception:
                    pass

            def dismiss(_event=None) -> None:
                try:
                    toast.destroy()
                except Exception:
                    pass

            for widget in (shell, avatar, copy, title_label, message_label, hint):
                widget.bind("<Button-1>", focus_app, add="+")
            close.bind("<Button-1>", dismiss)
            toast.after(9000, dismiss)

            if os.name == "nt":
                try:
                    ctypes.windll.user32.FlashWindow(self.winfo_id(), True)
                except Exception:
                    pass
        except Exception:
            pass

    def notify_finished(self, kind: str, title: str, message: str) -> None:
        self.show_vortex_toast(title, message, kind=kind, native=True, play_sound=True)

    def current_task_id(self) -> str:
        return str(self.composer_task_id or self.selected_session_id or "project")

    def current_task_title(self) -> str:
        session = self.selected_session()
        if session and str(session.get("id") or "") == self.current_task_id():
            title = str(session.get("request") or "").strip()
            if title:
                return session_title(title, "Task atual")
        if hasattr(self, "request_text"):
            title = self.request_text.get("1.0", "end-1c").strip()
            if title:
                return session_title(title, "Task atual")
        return "Task geral do projeto"

    def _agent_messages_for(self, project_id: str, task_id: str | None = None) -> list[dict]:
        key = agent_chat_key(project_id, str(task_id or self.current_task_id()))
        return list(self.agent_chats.get(key, []))

    def _latest_agent_backup(self, project_id: str, task_id: str | None = None) -> Optional[Path]:
        rolled_back: set[str] = set()
        for item in reversed(self._agent_messages_for(project_id, task_id)):
            backup = str(item.get("backup") or "").strip()
            kind = str(item.get("kind") or "")
            if kind == "rollback" and backup:
                rolled_back.add(backup)
                continue
            if kind == "action" and backup and backup not in rolled_back:
                path = Path(backup)
                if path.is_file():
                    return path.resolve()
        return None

    def refresh_agent_header(self) -> None:
        project = self.selected_project()
        if not hasattr(self, "agent_project_name"):
            return
        if not project:
            self.agent_project_name.configure(text="Escolha um projeto")
            self.agent_meta.configure(text="O Vórtex precisa de um projeto ativo para poder operar arquivos com segurança.")
            self.agent_backup_label.configure(text="SEM BACKUP RECENTE")
            self.agent_rollback_button.configure(state="disabled")
            self.last_agent_backup = None
            self.agent_send_button.configure(state="disabled")
            return
        global_count = len(list_skill_files(None))
        project_count = len(list_skill_files(project.id))
        task_id = self.current_task_id()
        task_title = self.current_task_title()
        messages = len(self._agent_messages_for(project.id, task_id))
        self.agent_project_name.configure(text=f"{project.name} · {task_title}")
        self.agent_meta.configure(text=f"TASK ATUAL · {messages} mensagem(ns) · {global_count} skill(s) globais · {project_count} do projeto")
        self.last_agent_backup = self._latest_agent_backup(project.id, task_id)
        if self.last_agent_backup:
            self.agent_backup_label.configure(text=f"BACKUP · {self.last_agent_backup.name}")
            self.agent_rollback_button.configure(state="normal" if not self.busy else "disabled")
        else:
            self.agent_backup_label.configure(text="SEM BACKUP RECENTE")
            self.agent_rollback_button.configure(state="disabled")
        if not self.busy:
            self.agent_send_button.configure(state="normal")

    def render_agent_chat(self) -> None:
        if not hasattr(self, "agent_chat_scroll"):
            return
        clear_children(self.agent_chat_scroll.inner)
        project = self.selected_project()
        if not project:
            card = tk.Frame(self.agent_chat_scroll.inner, bg=C["violet_soft"], padx=14, pady=12)
            card.pack(fill="x", pady=(4, 8), padx=4)
            tk.Label(card, text="VÓRTEX", bg=C["violet_soft"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
            tk.Label(card, text="Escolha um projeto na lateral. Aí eu consigo entrar nele, investigar, corrigir, validar e guardar backup antes de agir.", bg=C["violet_soft"], fg=C["ink"], font=(FONT, 9), wraplength=760, justify="left").pack(anchor="w", pady=(4, 0))
            self.refresh_agent_header()
            return
        task_id = self.current_task_id()
        task_title = self.current_task_title()
        messages = self._agent_messages_for(project.id, task_id)[-100:]
        if not messages:
            card = tk.Frame(self.agent_chat_scroll.inner, bg=C["violet_soft"], padx=14, pady=12)
            card.pack(fill="x", pady=(4, 8), padx=4)
            tk.Label(card, text="VÓRTEX", bg=C["violet_soft"], fg=C["violet"], font=(FONT, 8, "bold")).pack(anchor="w")
            tk.Label(card, text=f"Estou na task ‘{task_title}’ do {project.name}. Pode falar normal: 'arruma isso', 'descobre por que quebrou', 'valida tudo'. Eu fico preso a esta task e faço backup antes de mexer.", bg=C["violet_soft"], fg=C["ink"], font=(FONT, 9), wraplength=760, justify="left").pack(anchor="w", pady=(4, 0))
        for message in messages:
            role = str(message.get("role") or "assistant")
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            is_user = role == "user"
            outer = tk.Frame(self.agent_chat_scroll.inner, bg="#F8F5EF")
            outer.pack(fill="x", pady=4, padx=4)
            bubble_bg = C["ink"] if is_user else C["card"]
            bubble_fg = C["white"] if is_user else C["ink"]
            bubble = tk.Frame(outer, bg=bubble_bg, highlightbackground=C["line"] if not is_user else C["ink"], highlightthickness=1, padx=13, pady=10)
            bubble.pack(side="right" if is_user else "left", fill="x", expand=False)
            title = "VOCÊ" if is_user else "VÓRTEX"
            tk.Label(bubble, text=title, bg=bubble_bg, fg=C["lime"] if is_user else C["violet"], font=(FONT, 7, "bold")).pack(anchor="w")
            tk.Label(bubble, text=content, bg=bubble_bg, fg=bubble_fg, font=(FONT, 9), wraplength=720, justify="left").pack(anchor="w", pady=(4, 0))
            meta_bits = []
            when = str(message.get("createdAt") or "")
            if when:
                try:
                    dt = datetime.fromisoformat(when).astimezone()
                    meta_bits.append(dt.strftime("%d/%m · %H:%M"))
                except Exception:
                    pass
            if message.get("backup"):
                meta_bits.append("backup ✓")
            if meta_bits:
                tk.Label(bubble, text=" · ".join(meta_bits), bg=bubble_bg, fg="#98939D" if is_user else C["muted"], font=(MONO, 6)).pack(anchor="w", pady=(6, 0))
        self.refresh_agent_header()
        self.after(60, lambda: self.agent_chat_scroll.canvas.yview_moveto(1.0) if hasattr(self, "agent_chat_scroll") else None)

    def _append_agent_message(self, project_id: str, role: str, content: str, task_id: str | None = None, **extra) -> None:
        tid = str(task_id or self.current_task_id())
        key = agent_chat_key(project_id, tid)
        items = self.agent_chats.setdefault(key, [])
        items.append({"id": str(uuid.uuid4()), "role": role, "content": content, "createdAt": now_iso(), "taskId": tid, **extra})
        self.agent_chats[key] = items[-160:]
        save_agent_chats(self.agent_chats)

    def agent_quick_action(self, text: str) -> None:
        if not self.selected_project() or self.busy:
            return
        self.agent_input.delete("1.0", "end")
        self.agent_input.insert("1.0", text)
        self.start_agent_turn()

    def start_agent_turn(self) -> None:
        if self.busy:
            return
        project = self.selected_project()
        if not project:
            messagebox.showwarning(APP_NAME, "Escolha um projeto para o Vórtex operar.", parent=self)
            return
        request = self.agent_input.get("1.0", "end-1c").strip()
        if not request:
            return
        task_id = self.current_task_id()
        task_title = self.current_task_title()
        history_before = self._agent_messages_for(project.id, task_id)
        self._append_agent_message(project.id, "user", request, task_id=task_id)
        self.agent_input.delete("1.0", "end")
        self.render_agent_chat()
        self.busy = True
        self.set_busy_controls(True)
        self.start_busy_feedback("agent")
        self.agent_status_label.configure(text="TRABALHANDO · comecei agora", fg=C["lime"])
        if hasattr(self, "agent_avatar"):
            self.agent_avatar.set_state("working")
        self.agent_meta.configure(text="Clique recebido ✓  Vórtex entrou na task. Backup primeiro, mudança depois.")

        def emit(text: str) -> None:
            self.events.put(("agent_progress", text))

        def raw_emit(text: str) -> None:
            self.events.put(("agent_raw", text))

        def usage_emit(usage: dict) -> None:
            self.events.put(("agent_usage", usage))

        def worker() -> None:
            try:
                result = run_nexo_agent(project, request, history_before, emit, raw_emit, usage_emit, task_id=task_id, task_title=task_title)
                self.events.put(("agent_done", {"project_id": project.id, "task_id": task_id, "request": request, "result": result}))
            except Exception as exc:
                self.events.put(("agent_error", {"project_id": project.id, "task_id": task_id, "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def rollback_last_agent_action(self) -> None:
        project = self.selected_project()
        backup = self.last_agent_backup
        if not project or not backup or not backup.is_file():
            messagebox.showinfo(APP_NAME, "Não há backup recente do Vórtex para esta task.", parent=self)
            return
        if not messagebox.askyesno(APP_NAME, "Voltar o projeto para antes da última ação do Vórtex?\n\nArquivos alterados serão restaurados e arquivos novos criados nessa ação serão removidos quando identificados.", parent=self):
            return
        try:
            restored, removed = restore_agent_backup(project, backup)
            task_id = self.current_task_id()
            self._append_agent_message(project.id, "assistant", f"Voltei o projeto para o backup anterior. Restaurei {restored} arquivo(s) e removi {removed} arquivo(s) novo(s) daquela ação.", task_id=task_id, backup=str(backup), kind="rollback")
            append_history(project.id, "nexo_rollback", sessionId=task_id, summary=f"Rollback Vórtex: {restored} restaurados, {removed} removidos", backup=str(backup))
            self.agent_status_label.configure(text="REVERTIDO", fg=C["peach"])
            if hasattr(self, "agent_avatar"):
                self.agent_avatar.set_state("ready")
            self.agent_backup_label.configure(text="BACKUP RESTAURADO")
            self.agent_rollback_button.configure(state="disabled")
            self.last_agent_backup = None
            self.render_agent_chat()
            self.render_projects()
            self.refresh_git_overview_async()
            self.notify_finished("ready", "Backup restaurado", f"Voltei {restored} arquivo(s) e removi {removed} novidade(s). Tá tudo de volta no lugar.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    # ----------------------------- modes -----------------------------

    def set_mode(self, mode: str) -> None:
        if self.busy:
            return
        self.mode = mode
        self.prepare_page.pack_forget()
        self.apply_page.pack_forget()
        self.agent_page.pack_forget()
        self.prepare_tab.configure(bg="#EEEAE1", fg=C["muted"])
        self.apply_tab.configure(bg="#EEEAE1", fg=C["muted"])
        self.agent_tab.configure(bg="#EEEAE1", fg=C["violet"])
        if mode == "prepare":
            self.prepare_page.pack(fill="both", expand=True)
            self.prepare_tab.configure(bg=C["ink"], fg=C["white"])
            self.page_title.configure(text="O que vamos construir hoje?")
        elif mode == "apply":
            self.apply_page.pack(fill="both", expand=True)
            self.apply_tab.configure(bg=C["ink"], fg=C["white"])
            self.page_title.configure(text="Traga o código de volta com rede de segurança.")
        else:
            self.agent_page.pack(fill="both", expand=True)
            self.agent_tab.configure(bg=C["ink"], fg=C["lime"])
            self.page_title.configure(text="Fala com o Vórtex. Ele cuida do resto.")
            self.render_agent_chat()
            self.agent_input.focus_set()

    # ----------------------------- Codex status -----------------------------

    def refresh_codex_status_async(self) -> None:
        self.codex_status_label.configure(text="verificando Codex", fg=C["muted"])
        self.codex_dot.itemconfigure(self.codex_dot_id, fill=C["muted"])

        def worker():
            try:
                status = codex_status()
                self.events.put(("codex_status", status))
            except Exception as exc:
                self.events.put(("codex_status", {"installed": False, "authenticated": False, "version": "", "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def apply_codex_status(self, status: dict) -> None:
        if status.get("installed") and status.get("authenticated"):
            self.codex_status_label.configure(text=status.get("version") or "Codex pronto", fg=C["green"])
            self.codex_dot.itemconfigure(self.codex_dot_id, fill=C["green"])
        elif status.get("installed"):
            self.codex_status_label.configure(text="Codex sem login", fg=C["peach"])
            self.codex_dot.itemconfigure(self.codex_dot_id, fill=C["peach"])
        else:
            self.codex_status_label.configure(text="Codex não encontrado", fg=C["red"])
            self.codex_dot.itemconfigure(self.codex_dot_id, fill=C["red"])

    def refresh_rate_limits_async(self) -> None:
        if self.rate_limit_after:
            try:
                self.after_cancel(self.rate_limit_after)
            except Exception:
                pass
            self.rate_limit_after = None
        def worker():
            self.events.put(("rate_limits", codex_rate_limits()))
        threading.Thread(target=worker, daemon=True).start()
        # Limits change much more slowly than the UI; two minutes keeps them fresh without noise.
        self.rate_limit_after = self.after(120000, self.refresh_rate_limits_async)

    def apply_rate_limits(self, limits: dict) -> None:
        self.current_limits = limits if isinstance(limits, dict) else {}
        self.quota_5h.set_value(self.current_limits.get("five_hour"))
        self.quota_week.set_value(self.current_limits.get("weekly"))
        if self.current_limits.get("available"):
            missing = []
            if not self.current_limits.get("five_hour"):
                missing.append("5h")
            if not self.current_limits.get("weekly"):
                missing.append("semanal")
            self.limit_note.configure(text=("Codex não informou " + " / ".join(missing)) if missing else "atualizado agora")
        else:
            self.limit_note.configure(text="limites indisponíveis")

    # ----------------------------- operation feedback -----------------------------

    def start_busy_feedback(self, kind: str) -> None:
        self.stop_busy_feedback(reset_buttons=False)
        self.busy_kind = str(kind or "work")
        self.busy_feedback_step = 0
        self.busy_started_at = time.monotonic()
        self.busy_last_progress_at = self.busy_started_at
        self.busy_last_fallback_at = self.busy_started_at
        self._tick_busy_feedback()

    def mark_busy_progress(self) -> None:
        if self.busy_kind:
            self.busy_last_progress_at = time.monotonic()

    def stop_busy_feedback(self, *, reset_buttons: bool = True) -> None:
        if self.busy_feedback_after:
            try:
                self.after_cancel(self.busy_feedback_after)
            except Exception:
                pass
            self.busy_feedback_after = None
        self.busy_kind = ""
        self.busy_started_at = None
        self.busy_last_progress_at = None
        self.busy_last_fallback_at = None
        self.busy_feedback_step = 0
        if reset_buttons:
            if hasattr(self, "prepare_button"):
                self.prepare_button.configure(text="COZINHAR CONTEXTO  →", cursor="hand2")
            if hasattr(self, "apply_button"):
                self.apply_button.configure(text="APLICAR COM BACKUP   →", cursor="hand2")
            if hasattr(self, "agent_send_button"):
                self.agent_send_button.configure(text="MANDAR PRO VÓRTEX  →", cursor="hand2")

    def _busy_fallback_text(self, kind: str, elapsed: int) -> str:
        if kind == "prepare":
            phrases = [
                "Tô aqui. O Codex só tá fuçando mais fundo do que o normal.",
                "Ainda cozinhando — não precisa clicar de novo, eu não esqueci de você.",
                "A marmita não queimou. Só estou separando o contexto sem levar a cozinha inteira.",
                "Demorou um pouquinho, mas continuo trabalhando. Pode deixar essa aba quietinha.",
            ]
        elif kind == "apply":
            phrases = [
                "Ainda aplicando. Primeiro o backup, depois a coragem.",
                "Tô conferindo caminho por caminho antes de encostar no seu projeto.",
                "Sem pressa e sem susto: validando o pacote antes de substituir qualquer coisa.",
                "Ainda vivo. Se eu parecer quieto, é porque estou sendo paranoico com seu código.",
            ]
        elif kind == "agent":
            phrases = [
                "Vórtex ainda está na task. Provavelmente discutindo com algum arquivo teimoso.",
                "Não dormi. Estou investigando antes de sair mexendo em tudo.",
                "Backup garantido; agora o Vórtex está fazendo a parte perigosa com capacete.",
                "Tá levando um pouco mais, mas continuo operando a task atual.",
            ]
        else:
            phrases = ["Ainda trabalhando. Não precisa clicar de novo."]
        index = max(0, (elapsed // 6) - 1) % len(phrases)
        if elapsed >= 45:
            return phrases[index] + " Já passou de 45s, mas o processo continua ativo."
        return phrases[index]

    def _tick_busy_feedback(self) -> None:
        if not self.busy or not self.busy_kind or self.busy_started_at is None:
            return
        now = time.monotonic()
        elapsed = max(0, int(now - self.busy_started_at))
        dots = "." * ((self.busy_feedback_step % 3) + 1)
        self.busy_feedback_step += 1

        if self.busy_kind == "prepare" and hasattr(self, "prepare_button"):
            self.prepare_button.configure(text=f"COZINHANDO{dots}  {elapsed}s", disabledforeground=C["lime"], cursor="watch")
        elif self.busy_kind == "apply" and hasattr(self, "apply_button"):
            self.apply_button.configure(text=f"APLICANDO{dots}  {elapsed}s", disabledforeground=C["lime"], cursor="watch")
        elif self.busy_kind == "agent" and hasattr(self, "agent_send_button"):
            self.agent_send_button.configure(text=f"VÓRTEX TRABALHANDO{dots}  {elapsed}s", disabledforeground=C["lime"], cursor="watch")

        last_progress = self.busy_last_progress_at or self.busy_started_at
        last_fallback = self.busy_last_fallback_at or self.busy_started_at
        stalled_for = now - last_progress
        if stalled_for >= 5.0 and now - last_fallback >= 4.5:
            fallback = self._busy_fallback_text(self.busy_kind, elapsed)
            self.busy_last_fallback_at = now
            if self.busy_kind == "prepare":
                self.live_subtitle.configure(text=fallback)
                self.add_activity(fallback)
            elif self.busy_kind == "apply":
                self.apply_result_detail.configure(text=fallback)
            elif self.busy_kind == "agent":
                self.agent_meta.configure(text=fallback)

        self.busy_feedback_after = self.after(650, self._tick_busy_feedback)

    # ----------------------------- preparation -----------------------------

    def _request_count(self, _event=None) -> None:
        value = self.request_text.get("1.0", "end-1c")
        self.request_count.configure(text=f"{len(value)} caracteres")
        if _event is not None and not self._loading_draft:
            self.composer_run_session_id = ""
            self.composer_run_at = ""
            self.render_composer_state()
            self.save_current_draft()

    def start_prepare(self) -> None:
        if self.busy:
            return
        project = self.selected_project()
        request = self.request_text.get("1.0", "end-1c").strip()
        attachments = self.current_attachment_paths()
        if not project:
            messagebox.showwarning(APP_NAME, "Escolha ou cadastre um projeto primeiro.", parent=self)
            return
        if not request:
            messagebox.showwarning(APP_NAME, "Escreva o que você quer alterar.", parent=self)
            return
        if not self.composer_task_id:
            self.composer_task_id = str(uuid.uuid4())
        task_id = self.composer_task_id
        if not self.composer_round_id:
            self.composer_round_id = str(uuid.uuid4())
        if self.composer_round_number <= 0:
            self.composer_round_number = task_next_round_number(project.id, task_id)
        round_id = self.composer_round_id
        round_number = self.composer_round_number
        existing_task = next((item for item in project_sessions(project.id, 500) if str(item.get("id") or "") == task_id), None)
        task_title = str((existing_task or {}).get("request") or request).strip()
        self.save_current_draft()

        self.busy = True
        self.last_prepare = None
        self.current_usage = normalize_usage(None)
        self.codex_usage_label.configure(text="0 entrada  ·  0 cache  ·  0 saída")
        self.refresh_usage_dashboard()
        self.raw_log.clear()
        self.activity.clear()
        self.hide_prepare_result()
        self.set_busy_controls(True)
        self.start_busy_feedback("prepare")
        self.elapsed_started = time.monotonic()
        self.update_elapsed()
        self.set_phase("checking", "Clique recebido ✓", "Ligando a cozinha e conferindo o projeto. Pode deixar comigo.")
        self.add_activity("Clique recebido. A coleta começou de verdade.")
        if attachments:
            self.add_activity(f"{len(attachments)} anexo(s) extra também vão junto para o ChatGPT.")
        self.start_motivation_loop()

        def emit(phase: str, text: str) -> None:
            self.events.put(("progress", {"phase": phase, "text": text}))

        def raw_emit(line: str) -> None:
            self.events.put(("raw", line))

        def usage_emit(usage: dict) -> None:
            self.events.put(("codex_usage", usage))

        def worker():
            try:
                result = prepare_with_codex(project, request, emit, raw_emit, usage_emit, attachments)
                result.session_id = task_id
                result.round_id = round_id
                result.round_number = round_number
                result.task_title = task_title
                result.project_id = project.id
                self.events.put(("prepare_done", result))
            except Exception as exc:
                self.events.put(("prepare_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def set_phase(self, phase: str, title: Optional[str] = None, subtitle: Optional[str] = None) -> None:
        self.phase = phase
        self.reactor.set_phase(phase)
        mapping = {
            "idle": ("Pronto para cozinhar.", "Nada acontece no seu código até você pedir."),
            "checking": ("Ligando a cozinha", "Conferindo projeto, Codex e autenticação."),
            "ready": ("Tudo conectado", "A bancada está limpa. Pode começar."),
            "analyzing": ("Entendendo o território", "O Codex está olhando o projeto sem poder escrever nele."),
            "connected": ("Entrou no projeto", "Agora ele começa a seguir o fluxo real do código."),
            "reading": ("Caçando dependências", "Rotas, tipos, componentes e serviços estão entrando no mapa."),
            "organizing": ("Ligando os pontos", "A parte chata está quase pronta."),
            "selecting": ("Tirando o excesso", "Segredos, builds e arquivos inúteis ficam de fora."),
            "packing": ("Fechando a marmita", "Só o contexto útil vai para o ZIP."),
            "done": ("Pacote no ponto", "Baixe o ZIP, copie o prompt e mande os dois no ChatGPT."),
            "error": ("A cozinha parou", "Nada foi aplicado no projeto. Veja o erro e tente novamente."),
        }
        default_title, default_subtitle = mapping.get(phase, mapping["analyzing"])
        self.live_title.configure(text=title or default_title)
        self.live_subtitle.configure(text=subtitle or default_subtitle)
        self.live_kicker.configure(text="ATENÇÃO" if phase == "error" else ("PRONTO" if phase == "done" else "FORJA LOCAL"))

    def add_activity(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        if self.activity and self.activity[-1] == text:
            return
        self.activity.append(text)
        self.activity = self.activity[-10:]
        self.activity_box.configure(state="normal")
        self.activity_box.delete("1.0", "end")
        for item in self.activity[-7:]:
            self.activity_box.insert("end", f"• {item}\n")
        self.activity_box.configure(state="disabled")
        self.activity_box.see("end")

    def append_raw(self, line: str) -> None:
        self.raw_log.append(str(line))
        self.raw_log = self.raw_log[-500:]
        if self.raw_log_box.winfo_ismapped():
            self.raw_log_box.configure(state="normal")
            self.raw_log_box.insert("end", str(line) + "\n")
            self.raw_log_box.configure(state="disabled")
            self.raw_log_box.see("end")

    def toggle_raw_log(self) -> None:
        if self.raw_log_box.winfo_ismapped():
            self.raw_log_box.pack_forget()
            self.activity_box.pack(fill="both", expand=True)
            self.detail_toggle.configure(text="detalhes")
        else:
            self.activity_box.pack_forget()
            self.raw_log_box.configure(state="normal")
            self.raw_log_box.delete("1.0", "end")
            self.raw_log_box.insert("1.0", "\n".join(self.raw_log))
            self.raw_log_box.configure(state="disabled")
            self.raw_log_box.pack(fill="both", expand=True)
            self.raw_log_box.see("end")
            self.detail_toggle.configure(text="atividade")

    def update_elapsed(self) -> None:
        if self.elapsed_started is None:
            self.elapsed_label.configure(text="00:00")
            return
        seconds = int(time.monotonic() - self.elapsed_started)
        self.elapsed_label.configure(text=f"{seconds // 60:02d}:{seconds % 60:02d}")
        if self.busy:
            self.after(1000, self.update_elapsed)

    def start_motivation_loop(self) -> None:
        if self.motivation_after:
            try:
                self.after_cancel(self.motivation_after)
            except Exception:
                pass
        phrases = [
            "Procurando o arquivo que estava três pastas abaixo.",
            "Separando contexto útil do entulho digital.",
            "Deixando o Codex fazer a parte monótona.",
            "Seguindo imports até a toca do coelho.",
            "Nada de mandar o repositório inteiro por preguiça.",
            "A máquina está quente. O projeto continua intacto.",
            "Cortando bytes sem cortar contexto.",
        ]

        def tick():
            if not self.busy or self.phase in {"error", "done"}:
                return
            if random.random() < 0.75:
                self.live_subtitle.configure(text=random.choice(phrases))
            self.motivation_after = self.after(3300, tick)

        self.motivation_after = self.after(3300, tick)

    def show_prepare_result(self, result: PrepareResult) -> None:
        self.last_prepare = result
        clear_children(self.result_panel)
        top = tk.Frame(self.result_panel, bg=C["violet_soft"])
        top.pack(fill="x")
        tk.Label(top, text="PACOTE PRONTO", bg=C["violet_soft"], fg=C["violet"], font=(FONT, 8, "bold")).pack(side="left")
        extra_meta = f"{result.file_count} arquivos" + (f" · {result.attachment_count} anexo(s)" if result.attachment_count else "")
        tk.Label(top, text=extra_meta, bg=C["violet_soft"], fg=C["muted"], font=(MONO, 8, "bold")).pack(side="right")
        tk.Label(
            self.result_panel, text=result.zip_path.name, bg=C["violet_soft"], fg=C["ink"], font=(FONT, 10, "bold"),
            justify="left", wraplength=500,
        ).pack(anchor="w", pady=(5, 2))
        tk.Label(
            self.result_panel, text=result.summary, bg=C["violet_soft"], fg=C["muted"], font=(FONT, 8),
            justify="left", wraplength=500,
        ).pack(anchor="w", pady=(0, 5))
        tk.Label(
            self.result_panel, text=f"Codex · {usage_text(result.usage)}", bg=C["violet_soft"], fg=C["violet"], font=(MONO, 7, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        buttons = tk.Frame(self.result_panel, bg=C["violet_soft"])
        buttons.pack(fill="x")
        make_button(buttons, "MANDAR PRO GPT  ✦", self.open_chatgpt_handoff, bg=C["ink"], fg=C["lime"], active_bg="#303030", font=(FONT, 8, "bold"), padx=12, pady=8).pack(side="left")
        make_button(buttons, "Copiar pacote", self.copy_bundle_for_chatgpt, bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 8, "bold"), padx=10, pady=7).pack(side="left", padx=(5, 0))
        make_button(buttons, "ZIP na pasta", self.open_last_zip, bg=C["violet_soft"], fg=C["violet"], active_bg="#DDD6FF", font=(FONT, 8, "bold"), padx=10, pady=7).pack(side="left", padx=(5, 0))
        make_button(buttons, "Prompt", self.copy_last_prompt, bg=C["violet_soft"], fg=C["muted"], active_bg="#E3DEFB", font=(FONT, 8, "bold"), padx=10, pady=7).pack(side="left", padx=(5, 0))
        note = "O Vórtice consegue guardar automaticamente o link do chat depois que a conversa nascer no navegador."
        if result.attachment_count:
            note = f"{result.attachment_count} anexo(s) extra também vão no clipboard junto com o ZIP. " + note
        self.chat_capture_label = tk.Label(
            self.result_panel, text=note,
            bg=C["violet_soft"], fg=C["muted"], font=(FONT, 7), justify="left", wraplength=500,
        )
        self.chat_capture_label.pack(anchor="w", pady=(8, 0))
        self.result_panel.pack(fill="x", pady=(12, 0))

    def hide_prepare_result(self) -> None:
        self.result_panel.pack_forget()

    def copy_bundle_for_chatgpt(self) -> None:
        if not self.last_prepare:
            return
        files = [self.last_prepare.zip_path, *(self.last_prepare.attachment_paths or [])]
        copied_both = copy_text_and_files_windows(self.last_prepare.prompt, files)
        if not copied_both:
            self.clipboard_clear()
            self.clipboard_append(self.last_prepare.prompt)
            self.update()
        try:
            reveal_in_file_manager(self.last_prepare.zip_path)
        except Exception:
            pass
        self.add_activity("Prompt copiado e ZIP selecionado na pasta. É só mandar no ChatGPT.")

    def _autopilot_handoff_after_prepare(self, session_id: str, round_id: str) -> None:
        if str(self.settings.get("automationMode") or "assist") != "autopilot":
            return
        result = self.last_prepare
        if not result or str(result.session_id or "") != str(session_id or "") or str(result.round_id or "") != str(round_id or ""):
            return
        key = f"{session_id}:{round_id}"
        if key in self._autopilot_handoffs:
            return
        if not getattr(self, "companion_started", False):
            self.notify_finished("error", "Autopilot sem Companion", "O contexto ficou pronto, mas a ponte local não iniciou. Não enviei o prompt sem os arquivos.")
            self.add_activity("Autopilot parou antes do envio: Companion local indisponível.")
            return
        self._autopilot_handoffs.add(key)
        self.add_activity("AUTOPILOT · contexto pronto; assumindo a ida ao ChatGPT sem outro clique.")
        self.open_chatgpt_handoff(automatic=True)

    def open_chatgpt_handoff(self, automatic: bool = False) -> None:
        if not self.last_prepare:
            return
        project = next((item for item in self.projects if item.id == str(self.last_prepare.project_id or "")), None) or self.selected_project()
        if not project:
            return

        task_id = str(self.last_prepare.session_id or self.current_task_id() or self.selected_session_id)
        round_id = str(self.last_prepare.round_id or self.composer_round_id or "")
        round_number = int(self.last_prepare.round_number or self.composer_round_number or 1)
        # Compatibility for contexts cooked before v4.3: stamp a strict return identity lazily
        # before handing anything to the browser. This keeps old Tasks usable without
        # weakening the Project/Task/Rodada isolation.
        if not self.last_prepare.result_token or not self.last_prepare.expected_result_name:
            self.last_prepare.session_id = task_id
            self.last_prepare.round_id = round_id or str(uuid.uuid4())
            self.last_prepare.round_number = round_number
            self.last_prepare.project_id = project.id
            stamp_prepare_identity(project, self.last_prepare, self.request_text.get("1.0", "end-1c").strip())
            round_id = self.last_prepare.round_id
            try:
                self.last_prepare.prompt_path = save_prepare_prompt(project, self.last_prepare)
            except Exception as exc:
                self.add_activity(f"Não consegui atualizar o protocolo desta rodada antiga: {exc}")
        task = next((item for item in project_sessions(project.id, 500) if str(item.get("id") or "") == task_id), None)
        chat = task.get("chat") if isinstance(task, dict) and isinstance(task.get("chat"), dict) else None
        chat_url = normalize_chatgpt_chat_url(str((chat or {}).get("url") or ""))
        mode = str(self.settings.get("automationMode") or "assist")
        files = [self.last_prepare.zip_path, *(self.last_prepare.attachment_paths or [])]

        append_history(
            project.id, "handoff",
            sessionId=task_id,
            taskId=task_id,
            taskTitle=self.last_prepare.task_title or str((task or {}).get("request") or ""),
            roundId=round_id,
            roundNumber=round_number,
            summary=(("Autopilot · " if automatic else "") + ("Follow-up enviado ao mesmo ChatGPT" if chat_url else "Pacote preparado para novo ChatGPT")),
            request=self.request_text.get("1.0", "end-1c").strip(),
            zipName=self.last_prepare.zip_path.name,
            zipPath=app_relative_path(self.last_prepare.zip_path),
            promptPath=app_relative_path(self.last_prepare.prompt_path) if self.last_prepare.prompt_path else "",
            attachmentCount=self.last_prepare.attachment_count,
            attachmentPaths=[app_relative_path(path) for path in (self.last_prepare.attachment_paths or [])],
            automationMode=mode, resultToken=self.last_prepare.result_token, expectedResultName=self.last_prepare.expected_result_name,
        )

        companion_job = ""
        if getattr(self, "companion_started", False):
            try:
                companion_job = self.companion.queue_handoff({
                    "projectId": project.id,
                    "projectName": project.name,
                    "taskId": task_id,
                    "taskTitle": self.last_prepare.task_title or str((task or {}).get("request") or "Task"),
                    "roundId": round_id,
                    "roundNumber": round_number,
                    "mode": mode,
                    "chatUrl": chat_url,
                    "prompt": self.last_prepare.prompt,
                    "resultToken": self.last_prepare.result_token,
                    "expectedFilename": self.last_prepare.expected_result_name,
                }, files)
            except Exception:
                companion_job = ""

        # Clipboard remains a dependable fallback even when Companion is enabled.
        copied_both = copy_text_and_files_windows(self.last_prepare.prompt, files)
        if not copied_both:
            self.clipboard_clear()
            self.clipboard_append(self.last_prepare.prompt)
            self.update()

        target = chat_url or "https://chatgpt.com/"
        # The marker makes exactly the tab opened for this handoff claim the queued job.
        # Other ChatGPT tabs can stay open without stealing another Task/Rodada.
        if companion_job:
            target = f"{target}?vortice-job={companion_job}#vortice-job={companion_job}"
        baseline = set(recent_chatgpt_chat_urls(40))
        try:
            webbrowser.open_new_tab(target)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não consegui abrir o navegador.\n\n{exc}", parent=self)
            return

        if not companion_job:
            try:
                reveal_in_file_manager(self.last_prepare.zip_path)
            except Exception:
                pass
            self._start_chat_link_watcher(project, baseline, task_id)
            status = "Companion não respondeu; deixei prompt no clipboard e o ZIP selecionado na pasta."
        else:
            status = {
                "assist": "Companion acordou: vou colocar prompt + arquivos no chat certo. Você só confere e envia.",
                "send": "Companion acordou: vou colocar tudo, enviar e trazer o ZIP de volta para esta Task.",
                "autopilot": "AUTOPILOT ligado: não precisa clicar em mais nada. Vou confirmar os anexos, enviar, esperar, baixar, validar e aplicar com backup.",
            }.get(mode, "Companion preparado.")
        if hasattr(self, "chat_capture_label") and self.chat_capture_label.winfo_exists():
            self.chat_capture_label.configure(text=status)
        self.add_activity(status)
        self.render_projects()

    def open_last_zip(self) -> None:
        if not self.last_prepare:
            return
        reveal_in_file_manager(self.last_prepare.zip_path)

    def open_outputs(self) -> None:
        if os.name == "nt":
            os.startfile(OUTPUT_DIR)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT_DIR)])

    def copy_last_prompt(self) -> None:
        if not self.last_prepare:
            return
        self.clipboard_clear()
        self.clipboard_append(self.last_prepare.prompt)
        self.update()
        self.add_activity("Prompt copiado. Agora é só colar no ChatGPT.")

    # ----------------------------- apply -----------------------------

    def render_apply_preview(self, items: list[dict], *, applied: Optional[set[str]] = None) -> None:
        if not hasattr(self, "apply_file_scroll"):
            return
        clear_children(self.apply_file_scroll.inner)
        self.apply_preview_items = list(items)
        applied = applied or set()

        if not items:
            empty = tk.Frame(self.apply_file_scroll.inner, bg="#EEEAE2", padx=13, pady=14)
            empty.pack(fill="x")
            tk.Label(empty, text="Nenhum pacote carregado", bg="#EEEAE2", fg=C["ink"], font=(FONT, 9, "bold")).pack(anchor="w")
            tk.Label(empty, text="Escolha o ZIP e eu mostro os arquivos reais aqui.", bg="#EEEAE2", fg=C["muted"], font=(FONT, 8), justify="left", wraplength=330).pack(anchor="w", pady=(3, 0))
            self.apply_files_count.configure(text="—")
            self.apply_files_summary.configure(text="Escolha um ZIP para ver cada arquivo antes de aplicar.", fg=C["muted"])
            return

        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status") or "invalid")
            counts[status] = counts.get(status, 0) + 1
        valid_count = counts.get("added", 0) + counts.get("modified", 0) + counts.get("same", 0)
        self.apply_files_count.configure(text=f"{len(items)} arquivo" + ("" if len(items) == 1 else "s"))
        summary_parts: list[str] = []
        if counts.get("added"):
            summary_parts.append(f"{counts['added']} novo" + ("" if counts['added'] == 1 else "s"))
        if counts.get("modified"):
            summary_parts.append(f"{counts['modified']} alterado" + ("" if counts['modified'] == 1 else "s"))
        if counts.get("same"):
            summary_parts.append(f"{counts['same']} " + ("igual" if counts['same'] == 1 else "iguais"))
        blocked_total = counts.get("blocked", 0) + counts.get("invalid", 0)
        if blocked_total:
            summary_parts.append(f"{blocked_total} ignorado" + ("" if blocked_total == 1 else "s"))
        self.apply_files_summary.configure(text="  ·  ".join(summary_parts) or f"{valid_count} arquivo(s) válido(s)", fg=C["muted"])

        palette = {
            "added": ("+", "NOVO", C["green"], "#E9F5EF"),
            "modified": ("~", "ALTERADO", C["violet"], C["violet_soft"]),
            "same": ("=", "IGUAL", C["muted"], "#ECE8E1"),
            "blocked": ("!", "BLOQUEADO", C["red"], "#F8E8E8"),
            "invalid": ("×", "FORA", C["red"], "#F8E8E8"),
            "applied": ("✓", "APLICADO", C["green"], "#E9F5EF"),
        }

        for item in items:
            path = str(item.get("path") or "arquivo")
            status = "applied" if path in applied else str(item.get("status") or "invalid")
            symbol, label, accent, badge_bg = palette.get(status, palette["invalid"])
            row = tk.Frame(self.apply_file_scroll.inner, bg=C["card"], highlightbackground=C["line"], highlightthickness=1, padx=9, pady=8)
            row.pack(fill="x", pady=(0, 6))

            badge = tk.Label(row, text=symbol, bg=badge_bg, fg=accent, width=3, height=1, font=(MONO, 9, "bold"))
            badge.pack(side="left", padx=(0, 8), anchor="n")
            copy = tk.Frame(row, bg=C["card"])
            copy.pack(side="left", fill="x", expand=True)
            filename = Path(path).name or path
            folder = str(Path(path).parent).replace("\\", "/")
            tk.Label(copy, text=filename, bg=C["card"], fg=C["ink"], font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
            if folder not in {".", ""}:
                tk.Label(copy, text=folder, bg=C["card"], fg=C["muted"], font=(MONO, 6), anchor="w").pack(fill="x", pady=(2, 0))

            meta = tk.Frame(row, bg=C["card"])
            meta.pack(side="right", padx=(8, 0), anchor="n")
            tk.Label(meta, text=label, bg=C["card"], fg=accent, font=(FONT, 6, "bold")).pack(anchor="e")
            tk.Label(meta, text=format_bytes(item.get("size")), bg=C["card"], fg=C["muted"], font=(MONO, 6)).pack(anchor="e", pady=(2, 0))

    def refresh_apply_preview_async(self) -> None:
        project = self.selected_project()
        zip_path = self.selected_result_zip
        self.apply_preview_generation += 1
        generation = self.apply_preview_generation
        if not project or not zip_path:
            self.render_apply_preview([])
            return
        self.apply_files_count.configure(text="lendo…")
        self.apply_files_summary.configure(text="Abrindo o pacote sem tocar no projeto.", fg=C["muted"])
        clear_children(self.apply_file_scroll.inner)
        loading = tk.Label(self.apply_file_scroll.inner, text="Lendo os arquivos reais do ZIP…", bg="#F5F2EA", fg=C["muted"], font=(FONT, 8))
        loading.pack(anchor="w", pady=8)

        def worker():
            try:
                items = preview_result_zip(project, zip_path)
                self.events.put(("apply_preview", {"generation": generation, "items": items}))
            except Exception as exc:
                self.events.put(("apply_preview_error", {"generation": generation, "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def register_result_zip(self, project: Project, source_path: Path, *, task_id: str, round_id: str = "", round_number: int = 1, chat_url: str = "", source: str = "manual") -> Optional[Path]:
        source_path = Path(source_path).expanduser().resolve()
        if not source_path.is_file() or source_path.suffix.lower() != ".zip":
            return None
        try:
            target = OUTPUT_DIR / f"RESULT-{slug(project.name)}-{slug(task_id)[:8]}-r{max(1, int(round_number or 1))}-{stamp()}.zip"
            if source_path != target.resolve():
                shutil.copy2(source_path, target)
            else:
                target = source_path
        except Exception:
            target = source_path
        append_history(
            project.id, "result_received",
            sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=max(1, int(round_number or 1)),
            summary=f"ZIP recebido ({source})", resultZipPath=app_relative_path(target), zipPath=app_relative_path(target), chatUrl=chat_url, source=source,
        )
        if self.selected_project_id == project.id and self.current_task_id() == task_id:
            self.selected_result_zip = target
            self.zip_name_label.configure(text=target.name)
            try:
                self.zip_meta_label.configure(text=f"{target.stat().st_size / (1024*1024):.2f} MB  ·  resposta da Task")
            except OSError:
                self.zip_meta_label.configure(text="Resposta da Task")
            self.refresh_apply_preview_async()
            self.render_selected_project()
        return target

    def _queue_autopilot_retry(self, project: Project, task_id: str, failed_round: int, chat_url: str, detail: str) -> None:
        if not bool(self.settings.get("autopilotRetry", True)) or not chat_url or not getattr(self, "companion_started", False):
            return
        # Never get trapped in an unattended retry loop. Count only the current automatic
        # correction streak; a fresh prepare/follow-up/success resets the allowance.
        retry_streak = 0
        for event in project_history(project.id, 200):
            if str(event.get("taskId") or event.get("sessionId") or "") != str(task_id):
                continue
            kind = str(event.get("type") or "")
            if kind == "autopilot_retry":
                retry_streak += 1
                continue
            if kind in {"prepare", "followup", "apply", "homologation", "task_status"}:
                break
        if retry_streak >= 2:
            append_history(project.id, "result_rejected", sessionId=task_id, taskId=task_id, roundNumber=failed_round, summary="Autopilot parou após duas autocorreções seguidas para evitar loop.")
            self.notify_finished("error", "Autopilot pediu ajuda", "Tentei duas correções sozinho e parei antes de entrar num looping eterno. A Task e os backups ficaram intactos.")
            self.add_activity("Autopilot: limite seguro de 2 autocorreções atingido; aguardando você.")
            return
        round_number = task_next_round_number(project.id, task_id)
        round_id = str(uuid.uuid4())
        retry_token, retry_name = round_result_identity(project.id, task_id, round_id, round_number)
        prompt = f"""Continue estritamente na mesma Task e corrija o ZIP anterior. O Vórtice tentou aplicar a rodada {failed_round}, mas reverteu o código automaticamente porque a validação falhou.

ERRO/VALIDAÇÃO REAL:
{detail}

Revise somente o necessário sobre o estado atual do projeto já restaurado e devolva novamente SOMENTE um ZIP com os arquivos modificados, preservando a mesma estrutura de raízes usada anteriormente. Não repita explicações longas; priorize a correção e o ZIP final.

PROTOCOLO VÓRTICE DE RETORNO
RESULT_TOKEN: {retry_token}
Nome preferencial: {retry_name}
Inclua `_vortice-result.json` na raiz do ZIP com projectId={project.id}, taskId={task_id}, roundId={round_id}, roundNumber={round_number}, resultToken={retry_token} e expectedResultName={retry_name}."""
        append_history(project.id, "autopilot_retry", sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=round_number, request=prompt, summary=f"Autopilot abriu correção R{round_number}")
        retry_job = self.companion.queue_handoff({
            "projectId": project.id, "projectName": project.name, "taskId": task_id,
            "taskTitle": self.current_task_title() if self.selected_project_id == project.id else "Task",
            "roundId": round_id, "roundNumber": round_number, "mode": "autopilot",
            "chatUrl": chat_url, "prompt": prompt, "resultToken": retry_token, "expectedFilename": retry_name,
        }, [])
        try:
            webbrowser.open_new_tab(f"{chat_url}?vortice-job={retry_job}#vortice-job={retry_job}")
        except Exception:
            pass
        self.notify_finished("reminder", "Autopilot pediu correção", f"A rodada {failed_round} voltou para o lugar. Já pedi uma R{round_number} corrigida no mesmo chat.")

    def _resume_autopilot_apply(self, project_id: str, zip_text: str, meta: dict, key: str) -> None:
        path = Path(zip_text)
        if not path.is_file():
            self._autopilot_apply_waiting.discard(key)
            return
        project = next((item for item in self.projects if item.id == project_id), None)
        if not project:
            self._autopilot_apply_waiting.discard(key)
            return
        if self.busy:
            self.after(1500, lambda: self._resume_autopilot_apply(project_id, zip_text, meta, key))
            return
        self._autopilot_apply_waiting.discard(key)
        self.start_companion_autopilot_apply(project, path, meta)

    def start_companion_autopilot_apply(self, project: Project, zip_path: Path, meta: dict) -> None:
        if self.busy:
            key = f"{project.id}|{zip_path}|{meta.get('roundId') or meta.get('roundNumber') or ''}"
            if key not in self._autopilot_apply_waiting:
                self._autopilot_apply_waiting.add(key)
                self.add_activity("Autopilot recebeu o ZIP enquanto eu estava ocupado. Ele ficou na fila e será aplicado sozinho assim que a bancada liberar.")
                self.after(1500, lambda: self._resume_autopilot_apply(project.id, str(zip_path), dict(meta), key))
            return
        task_id = str(meta.get("taskId") or "")
        round_id = str(meta.get("roundId") or "")
        round_number = max(1, int(meta.get("roundNumber") or 1))
        chat_url = normalize_chatgpt_chat_url(str(meta.get("chatUrl") or ""))
        try:
            preview = preview_result_zip(project, zip_path)
            actionable = [item for item in preview if item.get("status") in {"added", "modified"}]
            blocked = [item for item in preview if item.get("status") in {"blocked", "invalid"}]
            if not actionable or blocked:
                reason = "O ZIP não passou na pré-validação: " + (f"{len(blocked)} caminho(s) bloqueado(s)." if blocked else "nenhuma alteração aplicável.")
                append_history(project.id, "autopilot_rollback", sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=round_number, summary=reason)
                self._queue_autopilot_retry(project, task_id, round_number, chat_url, reason)
                return
        except Exception as exc:
            reason = f"Falha lendo o ZIP: {exc}"
            self._queue_autopilot_retry(project, task_id, round_number, chat_url, reason)
            return

        self.busy = True
        self.set_busy_controls(True)
        self.start_busy_feedback("apply")
        self.apply_result_title.configure(text="Autopilot aplicando com backup...")
        self.apply_result_detail.configure(text="ZIP chegou na Task certa. Validando e criando ponto de restauração.")

        def emit(_phase: str, detail: str) -> None:
            self.events.put(("companion_auto_progress", detail))

        def worker() -> None:
            try:
                result = apply_result_zip(project, zip_path, emit)
                self.events.put(("companion_auto_apply_done", {"project_id": project.id, "task_id": task_id, "round_id": round_id, "round_number": round_number, "chat_url": chat_url, "zip": str(zip_path), "result": result}))
            except Exception as exc:
                self.events.put(("companion_auto_apply_error", {"project_id": project.id, "task_id": task_id, "round_id": round_id, "round_number": round_number, "chat_url": chat_url, "zip": str(zip_path), "error": str(exc)}))
        threading.Thread(target=worker, daemon=True).start()

    def choose_result_zip(self) -> None:
        if self.busy:
            return
        filename = filedialog.askopenfilename(
            parent=self,
            title="Escolha o ZIP devolvido pelo ChatGPT",
            filetypes=[("Arquivos ZIP", "*.zip")],
        )
        if not filename:
            return
        path = Path(filename).resolve()
        project = self.selected_project()
        if project:
            path = self.register_result_zip(project, path, task_id=self.current_task_id(), round_id=self.composer_round_id, round_number=self.composer_round_number, source="manual") or path
        self.selected_result_zip = path
        self.zip_name_label.configure(text=path.name)
        try:
            size = path.stat().st_size / (1024 * 1024)
            self.zip_meta_label.configure(text=f"{size:.2f} MB  ·  {path.parent}")
        except OSError:
            self.zip_meta_label.configure(text=str(path.parent))
        self.refresh_apply_preview_async()

    def start_apply(self) -> None:
        if self.busy:
            return
        project = self.selected_project()
        if not project:
            messagebox.showwarning(APP_NAME, "Escolha o projeto de destino na lateral.", parent=self)
            return
        if not self.selected_result_zip:
            messagebox.showwarning(APP_NAME, "Escolha o ZIP devolvido pelo ChatGPT.", parent=self)
            return
        session = self.selected_session() or {}
        request_text = str(session.get("latestRequest") or session.get("request") or self.request_text.get("1.0", "end-1c")).strip()
        warnings, blockers = semantic_validate_result(project, self.selected_result_zip, request_text)
        if blockers:
            messagebox.showerror(APP_NAME, "Validação local bloqueou este ZIP antes de tocar no projeto:\n\n" + "\n".join(f"• {x}" for x in blockers), parent=self)
            return
        if warnings and not messagebox.askyesno(APP_NAME, "A validação local encontrou atenção:\n\n" + "\n".join(f"• {x}" for x in warnings) + "\n\nAplicar mesmo assim?", parent=self):
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Aplicar as alterações em '{project.name}'?\n\nO Vórtice criará um backup antes de substituir qualquer arquivo.",
            parent=self,
        ):
            return

        self.busy = True
        self.set_busy_controls(True)
        self.start_busy_feedback("apply")
        self.apply_result_title.configure(text="Aplicando com proteção...")
        self.apply_result_detail.configure(text="Clique recebido ✓  Validando o pacote antes de tocar no projeto.")

        def emit(_phase: str, text: str) -> None:
            self.events.put(("apply_progress", text))

        def worker():
            try:
                result = apply_result_zip(project, self.selected_result_zip, emit)
                self.events.put(("apply_done", result))
            except Exception as exc:
                self.events.put(("apply_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def rollback_last(self) -> None:
        if self.busy or not self.last_apply:
            return
        if not messagebox.askyesno(
            APP_NAME,
            "Desfazer a última aplicação?\n\nArquivos que já existiam serão restaurados e arquivos novos dessa aplicação serão removidos.",
            parent=self,
        ):
            return
        try:
            count = rollback_backup(self.last_apply.backup_path)
            messagebox.showinfo(APP_NAME, f"Rollback concluído em {count} arquivo(s).", parent=self)
            self.apply_result_title.configure(text="Última aplicação desfeita.")
            self.apply_result_detail.configure(text=str(self.last_apply.backup_path))
            self.rollback_button.configure(state="disabled")
            project = self.selected_project()
            if project:
                append_history(project.id, "rollback", summary=f"Rollback de {count} arquivo(s)")
            self.render_apply_preview(self.apply_preview_items)
            self.render_projects()
            self.refresh_git_overview_async()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    # ----------------------------- UI state -----------------------------

    def set_busy_controls(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.prepare_button.configure(state=state)
        self.apply_button.configure(state=state)
        self.edit_project_button.configure(state=state if self.selected_project() else "disabled")
        self.remove_project_button.configure(state=state if self.selected_project() else "disabled")
        self.git_button.configure(state=state if self.selected_project() else "disabled")
        self.chats_button.configure(state=state if self.selected_project() else "disabled")
        if hasattr(self, "project_home_button"):
            self.project_home_button.configure(state=state if self.selected_project() else "disabled")
        self.open_folder_button.configure(state=state if self.selected_project() else "disabled")
        self.open_vscode_button.configure(state=state if self.selected_project() else "disabled")
        self.header_folder_button.configure(state=state if self.selected_project() else "disabled")
        self.header_vscode_button.configure(state=state if self.selected_project() else "disabled")
        self.settings_button.configure(state=state)
        self.agent_send_button.configure(state=state if self.selected_project() else "disabled")
        if hasattr(self, "polish_prompt_button") and not self.dictation_polishing:
            self.polish_prompt_button.configure(state=state if self.selected_project() else "disabled")
        if hasattr(self, "polish_agent_button") and not self.dictation_polishing:
            self.polish_agent_button.configure(state=state if self.selected_project() else "disabled")
        self.prepare_tab.configure(state=state)
        self.apply_tab.configure(state=state)
        self.agent_tab.configure(state=state)

    def finish_busy(self) -> None:
        self.busy = False
        self.elapsed_started = None
        self.stop_busy_feedback(reset_buttons=True)
        self.set_busy_controls(False)

    def poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "codex_status":
                    self.apply_codex_status(payload)
                elif kind == "rate_limits":
                    self.apply_rate_limits(payload)
                elif kind == "companion_ack":
                    if str(payload.get("taskId") or "") == self.current_task_id():
                        stage = str(payload.get("stage") or "")
                        if stage == "files-ready":
                            self.add_activity("Companion confirmou que TODOS os anexos terminaram de subir ✓")
                        elif stage == "loaded":
                            self.add_activity("Companion colocou o prompt só depois dos arquivos ficarem prontos ✓")
                        elif stage == "sent":
                            self.add_activity("Companion enviou a rodada completa. Pode sair da aba; sigo de plantão no ZIP.")
                elif kind == "companion_chat_link":
                    project = next((p for p in self.projects if p.id == str(payload.get("projectId") or "")), None)
                    url = normalize_chatgpt_chat_url(str(payload.get("chatUrl") or ""))
                    task_id = str(payload.get("taskId") or "")
                    if project and url and task_id:
                        self._save_chat_link(project, url, "companion", task_id)
                elif kind == "companion_event":
                    event_name = str(payload.get("event") or "")
                    if event_name == "extension-error":
                        self.notify_finished("error", "Companion tropeçou", str(payload.get("error") or "A interface do ChatGPT mudou ou o pacote não pôde ser enviado."))
                    elif event_name == "files-selected":
                        self.add_activity("Companion entregou os arquivos ao ChatGPT; aguardando confirmação real do upload…")
                    elif event_name == "files-ready":
                        self.add_activity("Arquivos confirmados no ChatGPT. Agora sim o prompt pode sair.")
                    elif event_name == "waiting-response":
                        self.add_activity("Rodada enviada completa. Monitoramento segue mesmo com a aba em segundo plano.")
                    elif event_name == "zip-rescue-sent":
                        self.add_activity("A resposta terminou sem ZIP; o Companion pediu o arquivo automaticamente no mesmo chat.")
                    elif event_name == "zip-detected":
                        self.add_activity("Companion encontrou o ZIP novo da rodada.")
                    elif event_name in {"zip-clicked", "zip-direct-download"}:
                        self.add_activity("Companion puxou o ZIP. Esperando o download terminar…")
                    elif event_name == "download-fallback-caught":
                        self.add_activity("Fallback local capturou o ZIP na pasta Downloads ✓")
                    elif event_name in {"zip-timeout", "download-timeout"}:
                        self.notify_finished("reminder", "Autopilot ainda procurando", "Ainda não apareceu um ZIP detectável. O Companion já tentou pedir o arquivo novamente; só preciso de você se a interface do ChatGPT tiver mudado de verdade.")
                elif kind == "companion_download":
                    project = next((p for p in self.projects if p.id == str(payload.get("projectId") or "")), None)
                    source = Path(str(payload.get("path") or "")).expanduser()
                    if project and source.is_file():
                        task_id = str(payload.get("taskId") or "")
                        round_id = str(payload.get("roundId") or "")
                        round_number = max(1, int(payload.get("roundNumber") or 1))
                        chat_url = normalize_chatgpt_chat_url(str(payload.get("chatUrl") or ""))
                        if not chat_url:
                            task = next((item for item in project_sessions(project.id, 500) if str(item.get("id") or "") == task_id), None)
                            chat = task.get("chat") if isinstance(task, dict) and isinstance(task.get("chat"), dict) else None
                            chat_url = normalize_chatgpt_chat_url(str((chat or {}).get("url") or ""))
                        payload["chatUrl"] = chat_url
                        download_source = str(payload.get("source") or "companion")
                        identity_ok, identity_detail, _identity = validate_vortice_result_zip(project, source, task_id, round_id, round_number)
                        if not identity_ok:
                            append_history(project.id, "result_rejected", sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=round_number, summary=identity_detail, source=download_source)
                            self.notify_finished("error", "ZIP recusado — Task protegida", identity_detail)
                            self.add_activity(identity_detail)
                            if str(payload.get("mode") or "") == "autopilot" and chat_url:
                                self._queue_autopilot_retry(project, task_id, round_number, chat_url, "O ZIP retornou, mas a etiqueta de identidade da rodada está ausente ou incorreta. " + identity_detail)
                            continue
                        task_obj = next((item for item in project_sessions(project.id, 500) if str(item.get("id") or "") == task_id), None)
                        round_obj = task_round(project.id, task_id, round_id) or {}
                        request_text = str(round_obj.get("request") or (task_obj or {}).get("latestRequest") or (task_obj or {}).get("request") or "")
                        sem_warnings, sem_blockers = semantic_validate_result(project, source, request_text)
                        if sem_blockers:
                            detail = " ".join(sem_blockers)
                            append_history(project.id, "result_rejected", sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=round_number, summary=detail, source="local-semantic")
                            self.notify_finished("error", "ZIP segurado antes do projeto", detail)
                            self.add_activity(detail)
                            if str(payload.get("mode") or "") == "autopilot" and chat_url:
                                self._queue_autopilot_retry(project, task_id, round_number, chat_url, "A validação local, sem Codex, bloqueou o pacote antes de tocar no projeto: " + detail)
                            continue
                        if sem_warnings:
                            self.add_activity("Validação local: " + " · ".join(sem_warnings))
                        saved = self.register_result_zip(project, source, task_id=task_id, round_id=round_id, round_number=round_number, chat_url=chat_url, source=download_source)
                        if saved:
                            self.notify_finished("ready", "ZIP voltou para a Task", f"R{round_number} chegou e ficou presa à Task certa. {'Autopilot vai aplicar agora.' if str(payload.get('mode') or '') == 'autopilot' else 'Pode revisar/aplicar quando quiser.'}")
                            if str(payload.get("mode") or "") == "autopilot":
                                self.start_companion_autopilot_apply(project, saved, payload)
                            self.render_projects()
                            self.render_selected_project()
                elif kind == "companion_auto_progress":
                    self.mark_busy_progress()
                    self.apply_result_detail.configure(text=str(payload))
                elif kind == "companion_auto_apply_done":
                    self.finish_busy()
                    project = next((p for p in self.projects if p.id == str(payload.get("project_id") or "")), None)
                    result = payload.get("result")
                    if project and isinstance(result, ApplyResult):
                        task_id = str(payload.get("task_id") or "")
                        round_id = str(payload.get("round_id") or "")
                        round_number = max(1, int(payload.get("round_number") or 1))
                        failed = [check for check in result.checks if not check.get("ok")]
                        if failed:
                            detail = "\n".join(str(check.get("detail") or "") for check in failed)[:4000]
                            try:
                                rollback_backup(result.backup_path)
                                append_history(project.id, "autopilot_rollback", sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=round_number, summary="Autopilot reverteu porque git diff --check falhou", backup=str(result.backup_path))
                                self._queue_autopilot_retry(project, task_id, round_number, str(payload.get("chat_url") or ""), detail or "git diff --check falhou")
                            except Exception as exc:
                                self.notify_finished("error", "Autopilot precisa de você", f"A validação falhou e o rollback também pediu atenção: {exc}")
                        else:
                            append_history(project.id, "apply", sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=round_number, summary=f"Autopilot aplicou {len(result.changed)} arquivo(s)", changedCount=len(result.changed), changed=result.changed, skipped=result.skipped, backup=str(result.backup_path), zipPath=str(payload.get("zip") or ""), resultZipPath=str(payload.get("zip") or ""), autopilot=True)
                            append_history(project.id, "homologation", sessionId=task_id, taskId=task_id, roundId=round_id, roundNumber=round_number, status="review", summary="Código aplicado · aguardando sua homologação")
                            self.last_apply = result
                            if self.selected_project_id == project.id and self.current_task_id() == task_id:
                                self.apply_result_title.configure(text=f"Autopilot aplicou {len(result.changed)} arquivo(s).")
                                self.apply_result_detail.configure(text=f"Backup: {result.backup_path}\nValidação: ok")
                                self.rollback_button.configure(state="normal")
                            self.notify_finished("apply", "Autopilot terminou!", f"R{round_number} aplicada com backup e diff check limpo.")
                        self.render_projects()
                        self.render_selected_project()
                        self.refresh_git_overview_async()
                elif kind == "companion_auto_apply_error":
                    self.finish_busy()
                    project = next((p for p in self.projects if p.id == str(payload.get("project_id") or "")), None)
                    if project:
                        task_id = str(payload.get("task_id") or "")
                        round_number = max(1, int(payload.get("round_number") or 1))
                        error = str(payload.get("error") or "Autopilot falhou")
                        self._queue_autopilot_retry(project, task_id, round_number, str(payload.get("chat_url") or ""), error)
                        self.notify_finished("error", "Autopilot parou", "Eu não empurrei a falha adiante. Se der, já pedi uma rodada corrigida no mesmo chat.")
                elif kind == "chat_link_detected":
                    project = next((p for p in self.projects if p.id == payload.get("project_id")), None)
                    if project and self._save_chat_link(project, str(payload.get("url") or ""), "browser_history", str(payload.get("session_id") or "")):
                        self.chat_watch_generation += 1
                        if hasattr(self, "chat_capture_label") and self.chat_capture_label.winfo_exists():
                            self.chat_capture_label.configure(text="CHAT VINCULADO ✓ · o link ficou salvo no projeto")
                elif kind == "chat_link_timeout":
                    if hasattr(self, "chat_capture_label") and self.chat_capture_label.winfo_exists():
                        self.chat_capture_label.configure(text="Não consegui detectar o link sozinho. Copie a URL do chat e use Chats → Vincular link copiado.")
                elif kind == "git_overview":
                    if (
                        int(payload.get("generation") or 0) == self.git_overview_generation
                        and self.selected_project_id == str(payload.get("project_id") or "")
                    ):
                        text = str(payload.get("text") or "GIT —")
                        dirty = payload.get("dirty")
                        color = C["green"] if dirty == 0 and payload.get("ok") else (C["peach"] if isinstance(dirty, int) and dirty > 0 else C["muted"])
                        self.active_project_git.configure(text=text, fg=color)
                        self.git_overview_label.configure(text=text, fg=color)
                elif kind == "dictation_polished":
                    target = str(payload.get("target") or "request")
                    value = str(payload.get("text") or "").strip()
                    widget = self._dictation_widget(target)
                    self.finish_dictation_polish_ui(target)
                    if widget is not None and value:
                        widget.delete("1.0", "end")
                        widget.insert("1.0", value)
                        widget.focus_set()
                        if target == "request":
                            self._request_count()
                            self.save_current_draft()
                            self.live_title.configure(text="Ditado lapidado ✓")
                            self.live_subtitle.configure(text="Mantive a ideia e só dei uma arrumada no que a voz pode ter entendido torto.")
                        else:
                            self.agent_hint.configure(text="Ditado lapidado ✓ · confere e manda pro Vórtex")
                    self.notify_finished("ready", "Ditado arrumadinho", "Tirei as tropeçadas da transcrição. Dá uma conferida e manda bala.")
                elif kind == "dictation_polish_error":
                    target = str(payload.get("target") or "request")
                    error = str(payload.get("error") or "Não consegui lapidar o ditado.")
                    self.finish_dictation_polish_ui(target)
                    if target == "agent":
                        self.agent_hint.configure(text="Não consegui lapidar · o texto original ficou intacto")
                    else:
                        self.live_title.configure(text="Não consegui lapidar, mas não perdi nada.")
                        self.live_subtitle.configure(text="Seu texto original continua aí. Pode usar normalmente.")
                    messagebox.showerror(APP_NAME, error, parent=self)
                elif kind == "progress":
                    self.mark_busy_progress()
                    phase = payload.get("phase", "analyzing")
                    text = payload.get("text", "")
                    self.set_phase(phase)
                    self.add_activity(text)
                elif kind == "raw":
                    self.append_raw(payload)
                elif kind == "codex_usage":
                    self.mark_busy_progress()
                    self.current_usage = normalize_usage(payload)
                    self.codex_usage_label.configure(text=usage_text(self.current_usage))
                    self.refresh_usage_dashboard()
                elif kind == "prepare_done":
                    result: PrepareResult = payload
                    self.finish_busy()
                    self.set_phase("done")
                    self.add_activity("CONTEXTO.zip e prompt prontos.")
                    self.current_usage = normalize_usage(result.usage)
                    self.codex_usage_label.configure(text=usage_text(self.current_usage))
                    self.refresh_usage_dashboard()
                    project = next((item for item in self.projects if item.id == str(result.project_id or "")), None) or self.selected_project()
                    if project:
                        result.session_id = result.session_id or str(uuid.uuid4())
                        # Give this exact Task/Rodada a cryptographic-ish local identity before anything leaves Vórtice.
                        stamp_prepare_identity(project, result, self.request_text.get("1.0", "end-1c").strip())
                        try:
                            result.prompt_path = save_prepare_prompt(project, result)
                        except Exception as exc:
                            result.prompt_path = None
                            self.add_activity(f"Não consegui persistir o prompt: {exc}")
                        append_history(
                            project.id, "prepare",
                            sessionId=result.session_id,
                            taskId=result.session_id,
                            taskTitle=result.task_title or self.request_text.get("1.0", "end-1c").strip(),
                            roundId=result.round_id,
                            roundNumber=result.round_number,
                            checklistId=self.composer_checklist_id,
                            request=self.request_text.get("1.0", "end-1c").strip(),
                            summary=result.summary, fileCount=result.file_count, usage=self.current_usage,
                            zipName=result.zip_path.name, zipPath=app_relative_path(result.zip_path),
                            promptPath=app_relative_path(result.prompt_path) if result.prompt_path else "",
                            attachmentCount=result.attachment_count,
                            attachmentPaths=[app_relative_path(path) for path in (result.attachment_paths or [])],
                            attachmentNames=[path.name for path in (result.attachment_paths or [])],
                            resultToken=result.result_token, expectedResultName=result.expected_result_name,
                        )
                        self.selected_session_id = result.session_id
                        self.composer_task_id = result.session_id
                        self.composer_round_id = result.round_id
                        self.composer_round_number = result.round_number
                        self.composer_followup = False
                        self.composer_run_session_id = result.session_id
                        self.composer_run_at = now_iso()
                        self.save_current_draft()
                        self.render_composer_state()
                    self.show_prepare_result(result)
                    self.render_projects()
                    self.render_selected_project()
                    self.refresh_rate_limits_async()
                    task_name = self.current_task_title()
                    if str(self.settings.get("automationMode") or "assist") == "autopilot":
                        self.notify_finished("ready", "Contexto pronto · Autopilot assumiu", f"A task ‘{task_name}’ saiu do forno. Já estou levando o pacote completo pro GPT sozinho.")
                        self.after(250, lambda sid=result.session_id, rid=result.round_id: self._autopilot_handoff_after_prepare(str(sid or ""), str(rid or "")))
                    else:
                        self.notify_finished("ready", "Contexto pronto!", f"A marmita da task ‘{task_name}’ saiu do forno. Já pode mandar pro GPT.")
                elif kind == "prepare_error":
                    self.finish_busy()
                    self.set_phase("error")
                    self.add_activity(str(payload))
                    self.notify_finished("error", "O Vórtice tropeçou", "Parei a cozinha antes de piorar. Vem ver o erro comigo.")
                    messagebox.showerror(APP_NAME, str(payload), parent=self)
                elif kind == "agent_progress":
                    self.mark_busy_progress()
                    value = str(payload or "").strip()
                    self.agent_status_label.configure(text="TRABALHANDO", fg=C["lime"])
                    if value:
                        self.agent_meta.configure(text=value[:180])
                elif kind == "agent_raw":
                    self.append_raw(str(payload))
                elif kind == "agent_usage":
                    self.mark_busy_progress()
                    self.current_usage = normalize_usage(payload)
                    self.codex_usage_label.configure(text=usage_text(self.current_usage))
                    self.refresh_usage_dashboard()
                elif kind == "agent_done":
                    self.finish_busy()
                    project = next((p for p in self.projects if p.id == str(payload.get("project_id") or "")), None)
                    result: AgentRunResult = payload.get("result")
                    if project and isinstance(result, AgentRunResult):
                        task_id = str(payload.get("task_id") or "project")
                        self.current_usage = normalize_usage(result.usage)
                        self._append_agent_message(project.id, "assistant", result.response, task_id=task_id, backup=str(result.backup_path), usage=self.current_usage, kind="action")
                        append_history(project.id, "nexo_action", sessionId=task_id, summary=session_title(str(payload.get("request") or ""), "Ação do Vórtex"), backup=str(result.backup_path), usage=self.current_usage)
                        self.last_agent_backup = result.backup_path
                        self.agent_backup_label.configure(text=f"BACKUP · {result.backup_path.name}")
                        self.agent_rollback_button.configure(state="normal")
                        self.agent_status_label.configure(text="PRONTO", fg=C["green"])
                        if hasattr(self, "agent_avatar"):
                            self.agent_avatar.set_state("done")
                            self.after(2600, lambda: self.agent_avatar.set_state("ready") if hasattr(self, "agent_avatar") and self.agent_avatar.winfo_exists() else None)
                        self.agent_meta.configure(text="Concluído. O backup dessa ação ficou guardado caso você queira voltar.")
                        self.render_agent_chat()
                        self.render_projects()
                        self.refresh_git_overview_async()
                        self.refresh_rate_limits_async()
                        task_title = self.current_task_title() if project.id == self.selected_project_id else session_title(str(payload.get("request") or ""), "task")
                        self.notify_finished("agent", "Vórtex terminou!", f"Fechei a task ‘{task_title}’ sem incendiar o projeto. Vem conferir.")
                elif kind == "agent_error":
                    self.finish_busy()
                    project = next((p for p in self.projects if p.id == str(payload.get("project_id") or "")), None)
                    error = str(payload.get("error") or "Falha no Vórtex.")
                    if project:
                        task_id = str(payload.get("task_id") or "project")
                        self._append_agent_message(project.id, "assistant", f"Não consegui concluir essa ação. {error}", task_id=task_id, kind="error")
                    reverted = "restaurado automaticamente" in error.lower() or "rollback" in error.lower()
                    self.agent_status_label.configure(text="REVERTIDO" if reverted else "ERRO", fg=C["peach"] if reverted else C["red"])
                    if hasattr(self, "agent_avatar"):
                        self.agent_avatar.set_state("ready" if reverted else "error")
                    self.agent_meta.configure(text=error[:220])
                    self.render_agent_chat()
                    self.notify_finished("error", "Vórtex pediu socorro", "Deu ruim numa ação. Se tinha rollback automático, eu já tentei deixar tudo no lugar.")
                    messagebox.showerror(APP_NAME, error, parent=self)
                elif kind == "apply_progress":
                    self.mark_busy_progress()
                    self.apply_result_detail.configure(text=str(payload))
                elif kind == "apply_preview":
                    if int(payload.get("generation") or 0) == self.apply_preview_generation:
                        self.render_apply_preview(payload.get("items") or [])
                elif kind == "apply_preview_error":
                    if int(payload.get("generation") or 0) == self.apply_preview_generation:
                        self.render_apply_preview([])
                        self.apply_files_summary.configure(text=str(payload.get("error") or "Não consegui ler esse ZIP."), fg=C["red"])
                elif kind == "apply_done":
                    result: ApplyResult = payload
                    self.finish_busy()
                    self.last_apply = result
                    failed_checks = [c for c in result.checks if not c.get("ok")]
                    self.apply_result_title.configure(
                        text=f"{len(result.changed)} arquivo(s) aplicado(s)." + (" Há atenção no diff." if failed_checks else " Tudo limpo no diff check.")
                    )
                    detail = f"Backup: {result.backup_path}"
                    if result.skipped:
                        detail += f"\nIgnorados com segurança: {len(result.skipped)}"
                    self.apply_result_detail.configure(text=detail)
                    self.rollback_button.configure(state="normal")
                    self.render_apply_preview(self.apply_preview_items, applied=set(result.changed))
                    project = self.selected_project()
                    if project:
                        task_id = self.current_task_id()
                        append_history(
                            project.id, "apply",
                            sessionId=task_id, taskId=task_id,
                            roundId=self.composer_round_id, roundNumber=self.composer_round_number,
                            summary=f"{len(result.changed)} arquivo(s) aplicado(s)",
                            changedCount=len(result.changed), changed=result.changed,
                            skipped=result.skipped, backup=str(result.backup_path),
                            zipPath=str(self.selected_result_zip or ""), resultZipPath=str(self.selected_result_zip or ""),
                        )
                        append_history(project.id, "homologation", sessionId=task_id, taskId=task_id, roundId=self.composer_round_id, roundNumber=self.composer_round_number, status="review", summary="Código aplicado · aguardando sua homologação")
                    self.render_projects()
                    self.render_selected_project()
                    self.refresh_git_overview_async()
                    self.notify_finished("apply", "Código aplicado!", f"{len(result.changed)} arquivo(s) encaixado(s). Backup guardado e ninguém explodiu.")
                    if failed_checks:
                        messagebox.showwarning(
                            APP_NAME,
                            "Os arquivos foram aplicados e o backup foi criado, mas git diff --check encontrou algo para revisar.\n\nUse o terminal ou devolva o erro para o ChatGPT.",
                            parent=self,
                        )
                elif kind == "apply_error":
                    self.finish_busy()
                    self.apply_result_title.configure(text="Aplicação interrompida.")
                    self.apply_result_detail.configure(text=str(payload))
                    self.notify_finished("error", "Aplicação interrompida", "Eu parei antes de empurrar arquivo errado pro projeto. Vem ver o que aconteceu.")
                    messagebox.showerror(APP_NAME, str(payload), parent=self)
        except queue.Empty:
            pass
        self.after(70, self.poll_events)

    def on_close(self) -> None:
        self.save_current_draft()
        if self.busy:
            if not messagebox.askyesno(APP_NAME, "Há uma operação em andamento. Fechar mesmo assim?", parent=self):
                return
        try:
            if hasattr(self, "companion"):
                self.companion.stop()
        except Exception:
            pass
        self.destroy()


def self_test() -> int:
    print(f"{APP_NAME} {APP_VERSION} self-test")
    with tempfile.TemporaryDirectory(prefix="bridge-test-") as temp:
        base = Path(temp)
        root = base / "project"
        root.mkdir()
        (root / "src").mkdir()
        good = root / "src" / "main.txt"
        good.write_text("hello", encoding="utf-8")
        secret = root / ".env"
        secret.write_text("TOKEN=x", encoding="utf-8")

        assert is_inside(good, root)
        assert not is_blocked_file(good)
        assert is_blocked_file(secret)
        resolved = resolve_selected_file(str(good), [root.resolve()])
        assert resolved is not None
        aliases = root_aliases([root])
        assert aliases and aliases[0].alias.startswith("01-")

        project = Project("x", "Teste", [str(root)])
        attachment = base / "print.png"
        attachment.write_text("fake-image", encoding="utf-8")
        result = {
            "summary": "Teste",
            "files": [str(good), str(secret)],
            "prompt": "Prompt",
            "notes": [],
        }
        prepared = create_context_zip(project, "Teste", result, aliases, [attachment])
        assert prepared.zip_path.exists()
        with zipfile.ZipFile(prepared.zip_path) as zf:
            names = zf.namelist()
            assert any(name.endswith("src/main.txt") for name in names)
            assert not any(name.endswith(".env") for name in names)
            assert "AI-BRIDGE-MANIFEST.json" in names
        assert prepared.attachment_count == 1
        assert prepared.attachment_paths and prepared.attachment_paths[0].is_file()
        prepared.zip_path.unlink(missing_ok=True)

        result_zip = base / "result.zip"
        alias = aliases[0].alias
        with zipfile.ZipFile(result_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{alias}/src/main.txt", "changed")
            zf.writestr(f"{alias}/src/new.txt", "new")
            zf.writestr(f"{alias}/.env", "SECRET=x")
        preview = preview_result_zip(project, result_zip)
        statuses = {item["path"]: item["status"] for item in preview}
        assert statuses[f"{alias}/src/main.txt"] == "modified"
        assert statuses[f"{alias}/src/new.txt"] == "added"
        assert statuses[f"{alias}/.env"] == "blocked"

        assert normalize_chatgpt_chat_url("https://chatgpt.com/c/abc-123") == "https://chatgpt.com/c/abc-123"
        assert normalize_chatgpt_chat_url("https://chatgpt.com/g/g-demo/c/xyz_789?foo=1") == "https://chatgpt.com/g/g-demo/c/xyz_789"
        assert normalize_chatgpt_chat_url("https://example.com/c/nope") == ""

    print("OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    app = BridgeApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
