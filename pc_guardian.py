from __future__ import annotations

import csv
import ctypes
import datetime as dt
import functools
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
import winreg


APP_NAME = "CdeDev"
APP_VERSION = "1.2.0"
GITHUB_URL = "https://github.com/Cde571"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CdeDev"
BACKUP_DIR = APP_DIR / "backups"
LOG_DIR = APP_DIR / "logs"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CREATE_NO_WINDOW = 0x08000000


def human_size(value: int | float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_info() -> tuple[int, int, int]:
    state = MemoryStatus()
    state.dwLength = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state))
    return state.ullTotalPhys, state.ullAvailPhys, state.dwMemoryLoad


def run_hidden(command: list[str], timeout: int | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, output.strip()
    except Exception as exc:
        return 1, str(exc)


def iter_files(root: Path, stop: threading.Event | None = None):
    stack = [root]
    while stack:
        if stop and stop.is_set():
            return
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if stop and stop.is_set():
                        return
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        attrs = getattr(stat, "st_file_attributes", 0)
                        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path), stat.st_size, stat.st_mtime
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            continue


@dataclass
class ScanResult:
    folders: list[tuple[str, int, int]]
    large_files: list[tuple[str, int, float]]
    total_bytes: int
    files: int
    elapsed: float
    stopped: bool


@dataclass
class UserProfile:
    sid: str
    path: Path
    loaded: bool
    special: bool
    last_use: str
    size: int | None = None

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)


@functools.lru_cache(maxsize=1)
def current_user_sid() -> str:
    code, output = run_hidden(["whoami", "/user", "/fo", "csv", "/nh"])
    if code == 0:
        match = re.search(r"S-\d(?:-\d+)+", output)
        if match:
            return match.group(0)
    return ""


def list_user_profiles() -> tuple[list[UserProfile], str]:
    script = (
        "Get-CimInstance Win32_UserProfile | ForEach-Object { "
        "[pscustomobject]@{SID=$_.SID;LocalPath=$_.LocalPath;Loaded=[bool]$_.Loaded;"
        "Special=[bool]$_.Special;LastUse=if($_.LastUseTime){$_.LastUseTime.ToString('yyyy-MM-dd HH:mm')}else{''}} "
        "} | ConvertTo-Json -Compress"
    )
    code, output = run_hidden(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=30)
    if code != 0 or not output:
        return [], output or "Windows no devolvió perfiles."
    try:
        raw = json.loads(output)
        if isinstance(raw, dict):
            raw = [raw]
        profiles = []
        for item in raw:
            local_path = item.get("LocalPath")
            sid = item.get("SID")
            if local_path and sid:
                profiles.append(UserProfile(
                    sid=sid,
                    path=Path(local_path),
                    loaded=bool(item.get("Loaded")),
                    special=bool(item.get("Special")),
                    last_use=item.get("LastUse") or "Desconocido",
                ))
        profiles.sort(key=lambda p: (p.special, p.name.lower()))
        return profiles, ""
    except Exception as exc:
        return [], f"No se pudo interpretar la lista de perfiles: {exc}"


def profile_delete_block_reason(profile: UserProfile) -> str | None:
    reserved = {"public", "default", "default user", "all users", "wsiaccount", "devtoolsuser", "defaultuser0", "wdagutilityaccount"}
    try:
        system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\/") + "\\"
        users_root = Path(system_drive) / "Users"
        profile.path.resolve(strict=False).relative_to(users_root.resolve(strict=False))
    except (ValueError, OSError):
        return "La ruta no está dentro de la carpeta Users del sistema."
    if profile.special or profile.name.lower() in reserved:
        return "Es un perfil especial o reservado de Windows."
    if not profile.sid.startswith("S-1-5-21-"):
        return "Pertenece a un servicio o cuenta administrada del sistema."
    if profile.sid == current_user_sid() or profile.path.resolve(strict=False) == Path.home().resolve(strict=False):
        return "Es el perfil del usuario que está ejecutando la aplicación."
    if profile.loaded:
        return "El perfil está cargado; cierra la sesión de ese usuario primero."
    if not re.fullmatch(r"S-\d(?:-\d+)+", profile.sid):
        return "El identificador SID no es válido."
    return None


def delete_user_profile(profile: UserProfile) -> tuple[bool, str]:
    reason = profile_delete_block_reason(profile)
    if reason:
        return False, reason
    script = (
        f"$p=Get-CimInstance Win32_UserProfile -Filter \"SID = '{profile.sid}'\" -ErrorAction Stop;"
        "if(-not $p){throw 'Perfil no encontrado'};"
        "if($p.Loaded -or $p.Special){throw 'Perfil cargado o especial'};"
        "$p | Remove-CimInstance -ErrorAction Stop; 'Perfil eliminado correctamente.'"
    )
    code, output = run_hidden(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=120)
    return code == 0, output or ("Perfil eliminado." if code == 0 else "No se pudo eliminar el perfil.")


def scan_disk(root: Path, stop: threading.Event, notify) -> ScanResult:
    started = time.monotonic()
    groups: dict[str, list[int]] = {}
    large: list[tuple[str, int, float]] = []
    total = 0
    count = 0
    root_text = str(root)
    for path, size, modified in iter_files(root, stop):
        total += size
        count += 1
        try:
            relative = os.path.relpath(path, root_text)
            first = relative.split(os.sep, 1)[0]
            group_path = str(root / first) if os.sep in relative else "[Archivos directos]"
        except ValueError:
            group_path = "[Otros]"
        bucket = groups.setdefault(group_path, [0, 0])
        bucket[0] += size
        bucket[1] += 1
        if size >= 100 * 1024 * 1024:
            large.append((str(path), size, modified))
        if count % 3000 == 0:
            notify(count, total, str(path))
    folders = sorted(
        ((name, data[0], data[1]) for name, data in groups.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    large.sort(key=lambda item: item[1], reverse=True)
    return ScanResult(
        folders=folders,
        large_files=large[:250],
        total_bytes=total,
        files=count,
        elapsed=time.monotonic() - started,
        stopped=stop.is_set(),
    )


@dataclass(frozen=True)
class CleanupTarget:
    key: str
    name: str
    path: Path | None
    description: str
    min_age_hours: int = 0
    special: str | None = None


def cleanup_targets() -> list[CleanupTarget]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return [
        CleanupTarget("user_temp", "Temporales del usuario", Path(os.environ.get("TEMP", local / "Temp")), "Solo elementos con más de 24 horas.", 24),
        CleanupTarget("crash", "Volcados de errores", local / "CrashDumps", "Archivos .dmp creados tras bloqueos."),
        CleanupTarget("d3d", "Caché DirectX", local / "D3DSCache", "Se regenera al usar juegos o aplicaciones 3D."),
        CleanupTarget("nvidia_dx", "NVIDIA DXCache", local / "NVIDIA" / "DXCache", "Caché de sombreadores; se regenera."),
        CleanupTarget("nvidia_gl", "NVIDIA GLCache", local / "NVIDIA" / "GLCache", "Caché OpenGL; se regenera."),
        CleanupTarget("pip", "Caché de pip", local / "pip" / "cache", "Paquetes descargados de Python."),
        CleanupTarget("npm", "Caché de npm", local / "npm-cache", "Paquetes descargados de Node.js."),
        CleanupTarget("codex_tmp", "Temporales de Codex", Path.home() / ".codex" / ".tmp", "Temporales, no sesiones ni proyectos.", 24),
        CleanupTarget("updaters", "Instaladores de actualizaciones", local / "anythingllm-desktop-updater", "Instaladores descargados; pueden volver a descargarse."),
        CleanupTarget("windows_temp", "Temporales de Windows", system_root / "Temp", "Puede requerir administrador y omite archivos en uso.", 24),
        CleanupTarget("recycle", "Papelera de reciclaje", None, "El borrado es permanente.", special="recycle"),
    ]


def folder_size(path: Path, min_age_hours: int = 0) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    threshold = time.time() - min_age_hours * 3600 if min_age_hours else None
    total = 0
    count = 0
    for _, size, modified in iter_files(path):
        if threshold is not None and modified > threshold:
            continue
        total += size
        count += 1
    return total, count


def clean_directory(path: Path, min_age_hours: int = 0) -> tuple[int, int, int]:
    if not path.exists() or not path.is_dir():
        return 0, 0, 0
    threshold = time.time() - min_age_hours * 3600 if min_age_hours else None
    removed_bytes = 0
    removed_files = 0
    failed = 0
    for file_path, size, modified in iter_files(path):
        if threshold is not None and modified > threshold:
            continue
        try:
            file_path.unlink(missing_ok=True)
            removed_bytes += size
            removed_files += 1
        except OSError:
            failed += 1
    directories = []
    for current, names, _ in os.walk(path, topdown=False, followlinks=False):
        for name in names:
            directories.append(Path(current) / name)
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed_bytes, removed_files, failed


HKCU = winreg.HKEY_CURRENT_USER


TWEAKS = {
    "animations": {
        "name": "Reducir animaciones",
        "description": "Desactiva animaciones de ventanas y barra de tareas.",
        "registry": [
            (r"Control Panel\Desktop\WindowMetrics", "MinAnimate", "0", winreg.REG_SZ),
            (r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", "TaskbarAnimations", 0, winreg.REG_DWORD),
        ],
    },
    "transparency": {
        "name": "Desactivar transparencia",
        "description": "Reduce trabajo gráfico del escritorio.",
        "registry": [(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0, winreg.REG_DWORD)],
    },
    "menu_delay": {
        "name": "Menús más rápidos",
        "description": "Reduce la espera de menús de 400 ms a 100 ms.",
        "registry": [(r"Control Panel\Desktop", "MenuShowDelay", "100", winreg.REG_SZ)],
    },
    "extensions": {
        "name": "Mostrar extensiones de archivo",
        "description": "Mejora seguridad y facilita reconocer archivos.",
        "registry": [(r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", "HideFileExt", 0, winreg.REG_DWORD)],
    },
    "game_mode": {
        "name": "Activar Modo Juego",
        "description": "Permite que Windows priorice juegos cuando se ejecutan.",
        "registry": [
            (r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1, winreg.REG_DWORD),
            (r"Software\Microsoft\GameBar", "GameModeEnabled", 1, winreg.REG_DWORD),
        ],
    },
    "high_power": {
        "name": "Plan Alto rendimiento",
        "description": "Mejora respuesta a costa de batería, calor y consumo.",
        "command": "high_power",
    },
    "hibernate_off": {
        "name": "Desactivar hibernación",
        "description": "Libera hiberfil.sys; también desactiva Inicio rápido.",
        "command": "hibernate_off",
    },
}


def read_reg(path: str, name: str):
    try:
        with winreg.OpenKey(HKCU, path, 0, winreg.KEY_READ) as key:
            value, value_type = winreg.QueryValueEx(key, name)
            return True, value, value_type
    except FileNotFoundError:
        return False, None, None


def write_reg(path: str, name: str, value, value_type: int):
    with winreg.CreateKeyEx(HKCU, path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, value_type, value)


def apply_tweaks(keys: list[str]) -> tuple[bool, str, Path | None]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup: dict = {"created": dt.datetime.now().isoformat(), "registry": [], "system": {}}
    messages = []
    try:
        for tweak_key in keys:
            tweak = TWEAKS[tweak_key]
            for path, name, value, value_type in tweak.get("registry", []):
                existed, old_value, old_type = read_reg(path, name)
                backup["registry"].append({
                    "path": path,
                    "name": name,
                    "existed": existed,
                    "value": old_value,
                    "type": old_type,
                })
                write_reg(path, name, value, value_type)
            command = tweak.get("command")
            if command == "high_power":
                _, current = run_hidden(["powercfg", "/getactivescheme"])
                match = re.search(r"([0-9a-fA-F-]{36})", current)
                backup["system"]["power_scheme"] = match.group(1) if match else None
                code, output = run_hidden(["powercfg", "/setactive", "SCHEME_MIN"])
                if code:
                    messages.append(f"Plan de energía: {output or 'no disponible'}")
            elif command == "hibernate_off":
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power") as reg_key:
                        enabled, _ = winreg.QueryValueEx(reg_key, "HibernateEnabled")
                except OSError:
                    enabled = 1
                backup["system"]["hibernate_enabled"] = bool(enabled)
                code, output = run_hidden(["powercfg", "/hibernate", "off"])
                if code:
                    messages.append(f"Hibernación: {output or 'requiere administrador'}")
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = BACKUP_DIR / f"ajustes-{stamp}.json"
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 3000, None)
        except Exception:
            pass
        detail = "Ajustes aplicados. Reinicia sesión para reflejar todos los cambios."
        if messages:
            detail += "\n\nAvisos:\n" + "\n".join(messages)
        return True, detail, backup_path
    except Exception as exc:
        return False, f"No se pudieron completar los ajustes: {exc}", None


def restore_latest_backup() -> tuple[bool, str]:
    backups = sorted(BACKUP_DIR.glob("ajustes-*.json"), reverse=True)
    if not backups:
        return False, "No existe una copia de ajustes para restaurar."
    path = backups[0]
    try:
        backup = json.loads(path.read_text(encoding="utf-8"))
        for item in reversed(backup.get("registry", [])):
            reg_path = item["path"]
            name = item["name"]
            if item["existed"]:
                write_reg(reg_path, name, item["value"], item["type"])
            else:
                try:
                    with winreg.OpenKey(HKCU, reg_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        system = backup.get("system", {})
        if system.get("power_scheme"):
            run_hidden(["powercfg", "/setactive", system["power_scheme"]])
        if system.get("hibernate_enabled") is True:
            run_hidden(["powercfg", "/hibernate", "on"])
        return True, f"Se restauró la copia {path.name}. Reinicia sesión para completar."
    except Exception as exc:
        return False, f"No se pudo restaurar: {exc}"


class PCGuardianApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1120x740")
        self.minsize(940, 640)
        self.configure(bg="#101827")
        self.events: queue.Queue = queue.Queue()
        self.scan_stop = threading.Event()
        self.scan_result: ScanResult | None = None
        self.cleanup_data: list[tuple[CleanupTarget, int, int]] = []
        self._configure_style()
        self._build_header()
        self._build_tabs()
        self.after(150, self._process_events)
        self.after(500, self._refresh_system)

    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#101827")
        style.configure("Card.TFrame", background="#182235")
        style.configure("TLabel", background="#101827", foreground="#e8eef8", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 18), foreground="#ffffff")
        style.configure("Muted.TLabel", foreground="#9fb0c8")
        style.configure("Card.TLabel", background="#182235", foreground="#e8eef8", font=("Segoe UI", 10))
        style.configure("Metric.TLabel", background="#182235", foreground="#69d3ff", font=("Segoe UI Semibold", 17))
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(12, 8))
        style.map("TButton", background=[("active", "#2d8cff")])
        style.configure("Treeview", background="#151f30", fieldbackground="#151f30", foreground="#e8eef8", rowheight=28)
        style.configure("Treeview.Heading", background="#22314a", foreground="#ffffff", font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", "#245b88")])
        style.configure("TNotebook", background="#101827", borderwidth=0)
        style.configure("TNotebook.Tab", background="#182235", foreground="#cbd7e8", padding=(15, 9))
        style.map("TNotebook.Tab", background=[("selected", "#2a75bb")], foreground=[("selected", "#ffffff")])
        style.configure("TCheckbutton", background="#101827", foreground="#e8eef8", font=("Segoe UI", 10))

    def _build_header(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=20, pady=(16, 8))
        ttk.Label(header, text="CdeDev", style="Title.TLabel").pack(side="left")
        github = ttk.Label(header, text="github.com/Cde571", style="Muted.TLabel", cursor="hand2")
        github.pack(side="left", padx=18)
        github.bind("<Button-1>", lambda _event: os.startfile(GITHUB_URL))
        admin_text = "Administrador activo" if is_admin() else "Sin permisos de administrador"
        ttk.Label(header, text=admin_text, style="Muted.TLabel").pack(side="right")

    def _build_tabs(self):
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.dashboard = ttk.Frame(self.tabs)
        self.analyzer = ttk.Frame(self.tabs)
        self.cleaner = ttk.Frame(self.tabs)
        self.optimizer = ttk.Frame(self.tabs)
        self.profiles = ttk.Frame(self.tabs)
        self.recommendations = ttk.Frame(self.tabs)
        self.diagnostics = ttk.Frame(self.tabs)
        self.tabs.add(self.dashboard, text="Panel")
        self.tabs.add(self.analyzer, text="Analizar disco")
        self.tabs.add(self.cleaner, text="Limpieza")
        self.tabs.add(self.optimizer, text="Optimizar")
        self.tabs.add(self.profiles, text="Perfiles")
        self.tabs.add(self.recommendations, text="Recomendaciones")
        self.tabs.add(self.diagnostics, text="Diagnóstico")
        self._build_dashboard()
        self._build_analyzer()
        self._build_cleaner()
        self._build_optimizer()
        self._build_profiles()
        self._build_recommendations()
        self._build_diagnostics()

    def _metric_card(self, parent, title: str):
        card = ttk.Frame(parent, style="Card.TFrame", padding=18)
        card.pack(side="left", fill="both", expand=True, padx=6)
        ttk.Label(card, text=title, style="Card.TLabel").pack(anchor="w")
        value = ttk.Label(card, text="Calculando…", style="Metric.TLabel")
        value.pack(anchor="w", pady=(8, 0))
        return value

    def _build_dashboard(self):
        row = ttk.Frame(self.dashboard)
        row.pack(fill="x", padx=12, pady=18)
        self.disk_metric = self._metric_card(row, "Disco C:")
        self.ram_metric = self._metric_card(row, "Memoria RAM")
        self.state_metric = self._metric_card(row, "Estado")
        info = ttk.Frame(self.dashboard, style="Card.TFrame", padding=20)
        info.pack(fill="x", padx=18, pady=8)
        ttk.Label(info, text="Optimización responsable", style="Metric.TLabel").pack(anchor="w")
        ttk.Label(
            info,
            text=("Windows utiliza la RAM libre como caché; vaciarla constantemente suele empeorar el rendimiento. "
                  "CdeDev se concentra en espacio, procesos de inicio, energía, efectos visuales y diagnósticos reales."),
            style="Card.TLabel",
            wraplength=970,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))
        actions = ttk.Frame(self.dashboard)
        actions.pack(fill="x", padx=18, pady=18)
        ttk.Button(actions, text="Analizar C:\\", command=self._quick_scan).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Revisar limpieza", command=lambda: self.tabs.select(self.cleaner)).pack(side="left", padx=8)
        ttk.Button(actions, text="Abrir aplicaciones de inicio", command=lambda: os.startfile("ms-settings:startupapps")).pack(side="left", padx=8)

    def _build_analyzer(self):
        controls = ttk.Frame(self.analyzer)
        controls.pack(fill="x", padx=12, pady=12)
        self.scan_path = tk.StringVar(value="C:\\")
        ttk.Entry(controls, textvariable=self.scan_path, width=60).pack(side="left", fill="x", expand=True)
        ttk.Button(controls, text="Elegir…", command=self._choose_scan_path).pack(side="left", padx=6)
        self.scan_button = ttk.Button(controls, text="Analizar", command=self._start_scan)
        self.scan_button.pack(side="left", padx=6)
        self.stop_button = ttk.Button(controls, text="Detener", command=self.scan_stop.set, state="disabled")
        self.stop_button.pack(side="left")
        self.scan_status = ttk.Label(self.analyzer, text="Listo.", style="Muted.TLabel")
        self.scan_status.pack(fill="x", padx=14)
        panes = ttk.Panedwindow(self.analyzer, orient="vertical")
        panes.pack(fill="both", expand=True, padx=12, pady=10)
        folder_frame = ttk.Frame(panes)
        file_frame = ttk.Frame(panes)
        panes.add(folder_frame, weight=1)
        panes.add(file_frame, weight=1)
        self.folder_tree = self._tree(folder_frame, ("path", "size", "files"), ("Carpeta", "Tamaño", "Archivos"), (680, 140, 120))
        self.file_tree = self._tree(file_frame, ("path", "size", "modified"), ("Archivos mayores de 100 MiB", "Tamaño", "Modificado"), (680, 140, 150))
        bottom = ttk.Frame(self.analyzer)
        bottom.pack(fill="x", padx=12, pady=(0, 10))
        ttk.Button(bottom, text="Exportar CSV", command=self._export_scan).pack(side="right")

    def _tree(self, parent, columns, headings, widths, open_paths=True):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        tree = ttk.Treeview(container, columns=columns, show="headings")
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w" if column == "path" else "e")
        scroll = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        if open_paths:
            tree.bind("<Double-1>", lambda _e: self._open_tree_path(tree))
        return tree

    def _build_cleaner(self):
        intro = ttk.Label(self.cleaner, text="Calcula primero. Selecciona únicamente lo que quieras eliminar.", style="Muted.TLabel")
        intro.pack(anchor="w", padx=14, pady=(14, 6))
        frame = ttk.Frame(self.cleaner, style="Card.TFrame", padding=10)
        frame.pack(fill="both", expand=True, padx=14, pady=8)
        self.cleanup_list = tk.Listbox(
            frame, selectmode="extended", bg="#151f30", fg="#e8eef8", selectbackground="#245b88",
            relief="flat", font=("Consolas", 10), activestyle="none",
        )
        self.cleanup_list.pack(fill="both", expand=True)
        self.cleanup_status = ttk.Label(self.cleaner, text="Sin calcular.", style="Muted.TLabel")
        self.cleanup_status.pack(fill="x", padx=14)
        buttons = ttk.Frame(self.cleaner)
        buttons.pack(fill="x", padx=14, pady=12)
        ttk.Button(buttons, text="Calcular espacio", command=self._analyze_cleanup).pack(side="left")
        ttk.Button(buttons, text="Limpiar selección", command=self._execute_cleanup).pack(side="left", padx=8)

    def _build_optimizer(self):
        ttk.Label(
            self.optimizer,
            text="Cada cambio del registro se respalda. Los ajustes marcados pueden afectar apariencia, batería o Inicio rápido.",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=14, pady=(14, 8))
        self.tweak_vars: dict[str, tk.BooleanVar] = {}
        body = ttk.Frame(self.optimizer, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=14, pady=6)
        for key, tweak in TWEAKS.items():
            row = ttk.Frame(body, style="Card.TFrame")
            row.pack(fill="x", pady=6)
            var = tk.BooleanVar(value=False)
            self.tweak_vars[key] = var
            check = ttk.Checkbutton(row, text=tweak["name"], variable=var)
            check.pack(anchor="w")
            ttk.Label(row, text=tweak["description"], style="Card.TLabel").pack(anchor="w", padx=(25, 0))
        actions = ttk.Frame(self.optimizer)
        actions.pack(fill="x", padx=14, pady=12)
        ttk.Button(actions, text="Aplicar seleccionados", command=self._apply_tweaks).pack(side="left")
        ttk.Button(actions, text="Deshacer últimos ajustes", command=self._undo_tweaks).pack(side="left", padx=8)
        ttk.Button(actions, text="Opciones visuales avanzadas", command=lambda: os.startfile("SystemPropertiesPerformance.exe")).pack(side="right")

    def _build_profiles(self):
        ttk.Label(
            self.profiles,
            text=("Un perfil contiene Escritorio, Documentos y AppData. Eliminar sus datos no necesariamente elimina "
                  "la cuenta; administra las cuentas desde Configuración."),
            style="Muted.TLabel",
            wraplength=1000,
        ).pack(anchor="w", padx=14, pady=(14, 8))
        frame = ttk.Frame(self.profiles)
        frame.pack(fill="both", expand=True, padx=14, pady=6)
        self.profile_tree = self._tree(
            frame,
            ("name", "path", "size", "last", "state"),
            ("Usuario/perfil", "Ruta", "Tamaño", "Último uso", "Estado"),
            (150, 350, 120, 150, 220), False,
        )
        self.profile_rows: dict[str, UserProfile] = {}
        self.profile_status = ttk.Label(self.profiles, text="Pulsa Actualizar perfiles.", style="Muted.TLabel")
        self.profile_status.pack(fill="x", padx=14)
        actions = ttk.Frame(self.profiles)
        actions.pack(fill="x", padx=14, pady=12)
        ttk.Button(actions, text="Actualizar perfiles", command=self._refresh_profiles).pack(side="left")
        ttk.Button(actions, text="Calcular tamaños", command=self._calculate_profile_sizes).pack(side="left", padx=8)
        ttk.Button(actions, text="Eliminar datos del perfil", command=self._delete_selected_profile).pack(side="left", padx=8)
        ttk.Button(actions, text="Administrar cuentas", command=lambda: os.startfile("ms-settings:otherusers")).pack(side="right")

    def _build_recommendations(self):
        ttk.Label(
            self.recommendations,
            text="Opciones oficiales y recomendaciones ordenadas por riesgo. Nada se ejecuta automáticamente.",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=14, pady=(14, 8))
        frame = ttk.Frame(self.recommendations)
        frame.pack(fill="both", expand=True, padx=14, pady=6)
        self.rec_tree = self._tree(
            frame,
            ("priority", "item", "space", "risk", "recommendation"),
            ("Prioridad", "Elemento", "Espacio", "Riesgo", "Recomendación"),
            (90, 210, 110, 100, 560), False,
        )
        actions = ttk.Frame(self.recommendations)
        actions.pack(fill="x", padx=14, pady=10)
        ttk.Button(actions, text="Generar recomendaciones", command=self._generate_recommendations).pack(side="left")
        ttk.Button(actions, text="Sensor de almacenamiento", command=lambda: os.startfile("ms-settings:storagesense")).pack(side="left", padx=6)
        ttk.Button(actions, text="Aplicaciones instaladas", command=lambda: os.startfile("ms-settings:appsfeatures")).pack(side="left", padx=6)
        ttk.Button(actions, text="Limpieza de componentes", command=self._component_cleanup).pack(side="left", padx=6)
        danger = ttk.Frame(self.recommendations, style="Card.TFrame", padding=14)
        danger.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Label(danger, text="Reinstalación limpia / dejar solo Windows", style="Metric.TLabel").pack(anchor="w")
        ttk.Label(
            danger,
            text=("Abre Recuperación de Windows. Allí puedes elegir «Conservar mis archivos» o «Quitar todo». "
                  "La segunda opción elimina cuentas, archivos, aplicaciones y configuraciones; requiere copia de seguridad y clave BitLocker."),
            style="Card.TLabel", wraplength=850, justify="left",
        ).pack(side="left", anchor="w", pady=(8, 0), fill="x", expand=True)
        ttk.Button(danger, text="Abrir Recuperación", command=self._open_recovery).pack(side="right", padx=(12, 0))

    def _build_diagnostics(self):
        buttons = ttk.Frame(self.diagnostics)
        buttons.pack(fill="x", padx=14, pady=12)
        items = [
            ("Verificar sistema (SFC)", ["sfc", "/verifyonly"]),
            ("Revisar imagen (DISM)", ["dism", "/online", "/cleanup-image", "/scanhealth"]),
            ("Revisar disco (CHKDSK)", ["chkdsk", "C:", "/scan"]),
            ("Vaciar caché DNS", ["ipconfig", "/flushdns"]),
            ("Optimizar unidad", ["defrag", "C:", "/O", "/U", "/V"]),
        ]
        for text, command in items:
            ttk.Button(buttons, text=text, command=lambda c=command: self._run_diagnostic(c)).pack(side="left", padx=4)
        self.diag_text = tk.Text(
            self.diagnostics, bg="#0b1220", fg="#d7e2f2", insertbackground="white", relief="flat",
            font=("Consolas", 10), wrap="word",
        )
        self.diag_text.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.diag_text.insert("end", "Los diagnósticos no reparan automáticamente. SFC y DISM pueden tardar varios minutos.\n")

    def _refresh_system(self):
        try:
            total, used, free = shutil.disk_usage("C:\\")
            ram_total, ram_free, ram_load = memory_info()
            self.disk_metric.configure(text=f"{human_size(used)} / {human_size(total)}")
            self.ram_metric.configure(text=f"{ram_load}% · {human_size(ram_free)} libres")
            self.state_metric.configure(text=f"{human_size(free)} libres")
        except Exception:
            pass
        self.after(3000, self._refresh_system)

    def _quick_scan(self):
        self.scan_path.set("C:\\")
        self.tabs.select(self.analyzer)
        self._start_scan()

    def _choose_scan_path(self):
        selected = filedialog.askdirectory(initialdir=self.scan_path.get())
        if selected:
            self.scan_path.set(selected)

    def _start_scan(self):
        path = Path(self.scan_path.get().strip())
        if not path.exists() or not path.is_dir():
            messagebox.showerror(APP_NAME, "La carpeta indicada no existe.")
            return
        self.folder_tree.delete(*self.folder_tree.get_children())
        self.file_tree.delete(*self.file_tree.get_children())
        self.scan_stop.clear()
        self.scan_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.scan_status.configure(text="Iniciando análisis…")
        def notify(count, size, current):
            self.events.put(("scan_progress", count, size, current))
        def work():
            result = scan_disk(path, self.scan_stop, notify)
            self.events.put(("scan_done", result))
        threading.Thread(target=work, daemon=True).start()

    def _show_scan_result(self, result: ScanResult):
        self.scan_result = result
        for path, size, files in result.folders:
            self.folder_tree.insert("", "end", values=(path, human_size(size), f"{files:,}"))
        for path, size, modified in result.large_files:
            stamp = dt.datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M")
            self.file_tree.insert("", "end", values=(path, human_size(size), stamp))
        state = "Detenido" if result.stopped else "Finalizado"
        self.scan_status.configure(
            text=f"{state}: {result.files:,} archivos, {human_size(result.total_bytes)}, {result.elapsed:.1f} segundos."
        )
        self.scan_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _export_scan(self):
        if not self.scan_result:
            messagebox.showinfo(APP_NAME, "Primero ejecuta un análisis.")
            return
        destination = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="reporte-espacio.csv"
        )
        if not destination:
            return
        with open(destination, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["TIPO", "RUTA", "BYTES", "TAMAÑO", "ARCHIVOS/MODIFICADO"])
            for path, size, files in self.scan_result.folders:
                writer.writerow(["CARPETA", path, size, human_size(size), files])
            for path, size, modified in self.scan_result.large_files:
                writer.writerow(["ARCHIVO", path, size, human_size(size), dt.datetime.fromtimestamp(modified).isoformat()])
        messagebox.showinfo(APP_NAME, f"Reporte guardado en:\n{destination}")

    def _open_tree_path(self, tree):
        selected = tree.selection()
        if not selected:
            return
        path = tree.item(selected[0], "values")[0]
        if path.startswith("["):
            return
        target = Path(path)
        try:
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            elif target.exists():
                os.startfile(target)
        except OSError:
            pass

    def _analyze_cleanup(self):
        self.cleanup_list.delete(0, "end")
        self.cleanup_data = []
        self.cleanup_status.configure(text="Calculando…")
        def work():
            rows = []
            for target in cleanup_targets():
                if target.special == "recycle":
                    size, count = folder_size(Path("C:/$Recycle.Bin"))
                else:
                    size, count = folder_size(target.path, target.min_age_hours) if target.path else (0, 0)
                rows.append((target, size, count))
            self.events.put(("cleanup_done", rows))
        threading.Thread(target=work, daemon=True).start()

    def _show_cleanup(self, rows):
        self.cleanup_data = rows
        total = 0
        for index, (target, size, count) in enumerate(rows):
            self.cleanup_list.insert("end", f"{target.name:<32} {human_size(size):>12}   {count:>8,} archivos   · {target.description}")
            if target.key not in {"recycle", "windows_temp", "updaters"} and size:
                self.cleanup_list.selection_set(index)
                total += size
        self.cleanup_status.configure(text=f"Selección inicial recuperaría hasta {human_size(total)}. Los archivos en uso se omitirán.")

    def _execute_cleanup(self):
        indices = list(self.cleanup_list.curselection())
        if not indices or not self.cleanup_data:
            messagebox.showinfo(APP_NAME, "Selecciona al menos una categoría calculada.")
            return
        selected = [self.cleanup_data[i] for i in indices]
        estimated = sum(row[1] for row in selected)
        names = "\n".join(f"• {row[0].name}: {human_size(row[1])}" for row in selected)
        if not messagebox.askyesno(
            APP_NAME,
            f"Se intentará eliminar permanentemente hasta {human_size(estimated)}:\n\n{names}\n\n¿Continuar?",
        ):
            return
        self.cleanup_status.configure(text="Limpiando…")
        def work():
            removed = files = failed = 0
            messages = []
            for target, _, _ in selected:
                if target.special == "recycle":
                    try:
                        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0001 | 0x0002 | 0x0004)
                        if result != 0:
                            messages.append("No se pudo vaciar completamente la papelera.")
                    except Exception as exc:
                        messages.append(f"Papelera: {exc}")
                elif target.path:
                    size, count, errors = clean_directory(target.path, target.min_age_hours)
                    removed += size
                    files += count
                    failed += errors
            self.events.put(("clean_finished", removed, files, failed, messages))
        threading.Thread(target=work, daemon=True).start()

    def _apply_tweaks(self):
        selected = [key for key, var in self.tweak_vars.items() if var.get()]
        if not selected:
            messagebox.showinfo(APP_NAME, "Selecciona al menos un ajuste.")
            return
        names = "\n".join(f"• {TWEAKS[key]['name']}" for key in selected)
        if not messagebox.askyesno(APP_NAME, f"Se aplicarán estos ajustes:\n\n{names}\n\nSe creará una copia para deshacerlos. ¿Continuar?"):
            return
        ok, detail, backup = apply_tweaks(selected)
        if ok:
            messagebox.showinfo(APP_NAME, f"{detail}\n\nCopia: {backup}")
        else:
            messagebox.showerror(APP_NAME, detail)

    def _undo_tweaks(self):
        if not messagebox.askyesno(APP_NAME, "¿Restaurar la copia de ajustes más reciente?"):
            return
        ok, detail = restore_latest_backup()
        (messagebox.showinfo if ok else messagebox.showerror)(APP_NAME, detail)

    def _refresh_profiles(self):
        self.profile_status.configure(text="Consultando perfiles de Windows…")
        def work():
            profiles, error = list_user_profiles()
            self.events.put(("profiles_done", profiles, error))
        threading.Thread(target=work, daemon=True).start()

    def _show_profiles(self, profiles: list[UserProfile], error: str = ""):
        self.profile_tree.delete(*self.profile_tree.get_children())
        self.profile_rows = {}
        for index, profile in enumerate(profiles):
            reason = profile_delete_block_reason(profile)
            state = f"Protegido: {reason}" if reason else "Disponible para eliminar"
            iid = f"profile-{index}"
            self.profile_rows[iid] = profile
            self.profile_tree.insert(
                "", "end", iid=iid,
                values=(profile.name, str(profile.path), human_size(profile.size) if profile.size is not None else "Sin calcular", profile.last_use, state),
            )
        self.profile_status.configure(text=error or f"{len(profiles)} perfiles encontrados. Los perfiles protegidos no pueden eliminarse desde CdeDev.")

    def _calculate_profile_sizes(self):
        if not self.profile_rows:
            self._refresh_profiles()
            return
        profiles = list(self.profile_rows.values())
        self.profile_status.configure(text="Calculando tamaños de perfiles; puede tardar varios minutos…")
        def work():
            for index, profile in enumerate(profiles, 1):
                profile.size, _ = folder_size(profile.path)
                self.events.put(("profile_progress", index, len(profiles), profile.name))
            self.events.put(("profiles_done", profiles, ""))
        threading.Thread(target=work, daemon=True).start()

    def _delete_selected_profile(self):
        selected = self.profile_tree.selection()
        if len(selected) != 1:
            messagebox.showinfo(APP_NAME, "Selecciona exactamente un perfil.")
            return
        profile = self.profile_rows.get(selected[0])
        if not profile:
            return
        reason = profile_delete_block_reason(profile)
        if reason:
            messagebox.showerror(APP_NAME, f"Este perfil no puede eliminarse:\n\n{reason}")
            return
        typed = simpledialog.askstring(
            APP_NAME,
            f"Se eliminarán permanentemente todos los datos de:\n{profile.path}\n\n"
            f"La cuenta puede seguir existiendo. Escribe {profile.name} para confirmar:",
            parent=self,
        )
        if typed != profile.name:
            messagebox.showinfo(APP_NAME, "Confirmación incorrecta. No se eliminó nada.")
            return
        self.profile_status.configure(text=f"Eliminando el perfil {profile.name}…")
        def work():
            ok, detail = delete_user_profile(profile)
            self.events.put(("profile_deleted", ok, detail))
        threading.Thread(target=work, daemon=True).start()

    def _generate_recommendations(self):
        self.rec_tree.delete(*self.rec_tree.get_children())
        self.rec_tree.insert("", "end", values=("…", "Analizando", "", "", "Calculando recomendaciones…"))
        def work():
            rows: list[tuple[str, str, int, str, str]] = []
            total, used, free = shutil.disk_usage("C:\\")
            free_percent = free / total * 100 if total else 0
            priority = "Alta" if free_percent < 15 else "Media" if free_percent < 25 else "Baja"
            rows.append((priority, "Espacio libre en C:", free, "Seguro", f"Queda {free_percent:.1f}%. Procura mantener al menos 15–20% libre."))
            for target in cleanup_targets():
                if target.special == "recycle":
                    size, _ = folder_size(Path("C:/$Recycle.Bin"))
                elif target.path:
                    size, _ = folder_size(target.path, target.min_age_hours)
                else:
                    size = 0
                if size >= 250 * 1024 * 1024:
                    risk = "Revisar" if target.key in {"recycle", "updaters"} else "Bajo"
                    rows.append(("Alta" if size >= 5 * 1024**3 else "Media", target.name, size, risk, target.description))
            hiberfile = Path("C:/hiberfil.sys")
            try:
                hiber_size = hiberfile.stat().st_size
            except OSError:
                hiber_size = 0
            if hiber_size:
                rows.append(("Media", "Hibernación", hiber_size, "Moderado", "Desactívala solo si no usas hibernación y aceptas perder Inicio rápido."))
            windows_old = Path("C:/Windows.old")
            if windows_old.exists():
                size, _ = folder_size(windows_old)
                rows.append(("Alta", "Windows anterior", size, "Moderado", "Elimínalo desde Archivos temporales de Windows, no manualmente."))
            if self.scan_result:
                for path, size, _ in self.scan_result.large_files[:8]:
                    if size >= 1024**3 and not path.lower().startswith(("c:\\windows", "c:\\program files")):
                        rows.append(("Media", "Archivo grande", size, "Revisar", path))
            for profile in self.profile_rows.values():
                if profile.size and profile.size >= 1024**3 and not profile_delete_block_reason(profile):
                    rows.append(("Media", f"Perfil {profile.name}", profile.size, "Alto", f"Último uso: {profile.last_use}. Confirma copia de seguridad antes de eliminar."))
            rows.append(("Baja", "Aplicaciones de inicio", 0, "Bajo", "Desactiva desde Configuración solo programas que reconozcas y no necesites al iniciar."))
            rows.sort(key=lambda r: ({"Alta": 0, "Media": 1, "Baja": 2}[r[0]], -r[2]))
            self.events.put(("recommendations_done", rows))
        threading.Thread(target=work, daemon=True).start()

    def _show_recommendations(self, rows):
        self.rec_tree.delete(*self.rec_tree.get_children())
        for priority, item, space, risk, recommendation in rows:
            self.rec_tree.insert("", "end", values=(priority, item, human_size(space) if space else "—", risk, recommendation))

    def _component_cleanup(self):
        if not messagebox.askyesno(
            APP_NAME,
            "Windows eliminará componentes reemplazados de actualizaciones mediante DISM. "
            "No se usará /ResetBase. Puede tardar y no debe interrumpirse. ¿Continuar?",
        ):
            return
        self.tabs.select(self.diagnostics)
        self._run_diagnostic(["dism", "/online", "/cleanup-image", "/startcomponentcleanup"])

    def _open_recovery(self):
        if messagebox.askyesno(
            APP_NAME,
            "La opción «Quitar todo» de Recuperación elimina archivos personales, aplicaciones, cuentas y ajustes. "
            "Haz copia de seguridad y conserva tu clave BitLocker.\n\n"
            "CdeDev solo abrirá Configuración; Windows pedirá nuevas confirmaciones. ¿Abrir Recuperación?",
        ):
            os.startfile("ms-settings:recovery")

    def _run_diagnostic(self, command: list[str]):
        self.diag_text.insert("end", f"\n> {' '.join(command)}\n")
        self.diag_text.see("end")
        def work():
            code, output = run_hidden(command)
            self.events.put(("diagnostic", command, code, output))
        threading.Thread(target=work, daemon=True).start()

    def _process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "scan_progress":
                    _, count, size, current = event
                    self.scan_status.configure(text=f"{count:,} archivos · {human_size(size)} · {current[:100]}")
                elif kind == "scan_done":
                    self._show_scan_result(event[1])
                elif kind == "cleanup_done":
                    self._show_cleanup(event[1])
                elif kind == "clean_finished":
                    _, removed, files, failed, messages = event
                    text = f"Se eliminaron {files:,} archivos ({human_size(removed)}). {failed:,} archivos no pudieron eliminarse."
                    if messages:
                        text += "\n" + "\n".join(messages)
                    self.cleanup_status.configure(text=text)
                    messagebox.showinfo(APP_NAME, text)
                elif kind == "diagnostic":
                    _, command, code, output = event
                    self.diag_text.insert("end", f"Código de salida: {code}\n{output or '(sin salida)'}\n")
                    self.diag_text.see("end")
                elif kind == "profiles_done":
                    _, profiles, error = event
                    self._show_profiles(profiles, error)
                elif kind == "profile_progress":
                    _, current, total, name = event
                    self.profile_status.configure(text=f"Calculando perfiles {current}/{total}: {name}")
                elif kind == "profile_deleted":
                    _, ok, detail = event
                    (messagebox.showinfo if ok else messagebox.showerror)(APP_NAME, detail)
                    self._refresh_profiles()
                elif kind == "recommendations_done":
                    self._show_recommendations(event[1])
        except queue.Empty:
            pass
        self.after(150, self._process_events)


if __name__ == "__main__":
    APP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    app = PCGuardianApp()
    app.mainloop()
