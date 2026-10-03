"""Windows language provisioning. Never executes administrator-provided shell text."""
import ctypes
import json
import os
import subprocess

from backend.services.tts_service import list_available_voices
from backend.field.constants import INSTALL_LANGUAGES

MANUAL_GUIDANCE = "Windows 설정 > 시간 및 언어 > 언어 및 지역에서 언어/음성 기능을 설치한 후 음성 재검색을 실행하세요. 필요하면 로그아웃 또는 재부팅 후 재검색하세요."


def is_admin():
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def powershell(script, timeout=30, language=None):
    env = os.environ.copy()
    if language:
        env["PPE_INSTALL_LANGUAGE"] = language
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; " + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def scan_voices():
    try:
        voices = list_available_voices()
        result = {"voices": voices, "voice_scan_ok": True}
    except Exception as exc:
        result = {"voices": [], "voice_scan_ok": False, "error": str(exc), "guidance": MANUAL_GUIDANCE}
    try:
        installed = powershell("Import-Module LanguagePackManagement; @(Get-InstalledLanguage | ForEach-Object { $_.LanguageId }) | ConvertTo-Json -Compress")
        if installed.returncode:
            result["language_scan_error"] = installed.stderr[-1000:]
        else:
            languages = json.loads(installed.stdout or "[]")
            result["installed_languages"] = [languages] if isinstance(languages, str) else languages
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        result["language_scan_error"] = str(exc)
    return result


def install_language(language, allowed=False):
    if language not in INSTALL_LANGUAGES:
        return {"installed": False, "reason": "unsupported_language", "guidance": MANUAL_GUIDANCE}
    if not allowed:
        return {"installed": False, "reason": "installation_not_enabled", "guidance": MANUAL_GUIDANCE}
    if not is_admin():
        return {"installed": False, "reason": "administrator_required", "guidance": MANUAL_GUIDANCE}
    try:
        completed = powershell("Import-Module LanguagePackManagement; Install-Language -Language $env:PPE_INSTALL_LANGUAGE | Out-Null",
                               timeout=1200, language=language)
        result = {"installed": completed.returncode == 0, "reason": "installed" if completed.returncode == 0 else "installation_failed",
                  "requested_language": language, "guidance": MANUAL_GUIDANCE}
        if completed.returncode:
            result["error"] = completed.stderr[-2000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {"installed": False, "reason": "installation_failed", "error": str(exc), "guidance": MANUAL_GUIDANCE}
    # Installation success never implies a SAPI5 voice is available.
    return {**result, **scan_voices()}
