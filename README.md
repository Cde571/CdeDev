# CdeDev

[![GitHub](https://img.shields.io/badge/GitHub-Cde571-181717?logo=github)](https://github.com/Cde571)

Utilidad gráfica para Windows que analiza el uso del disco, localiza archivos
grandes, limpia cachés conocidas y aplica ajustes de rendimiento reversibles.

## Funciones

- Análisis de carpetas y archivos grandes con exportación CSV.
- Limpieza seleccionable de temporales y cachés conocidas.
- Ajustes reversibles de apariencia, energía, hibernación y Modo Juego.
- Inventario de perfiles de Windows con protección del perfil activo, perfiles
  cargados y cuentas especiales.
- Eliminación confirmada de los datos de un perfil mediante la interfaz oficial
  `Win32_UserProfile`; las cuentas se administran por separado en Configuración.
- Recomendaciones locales basadas en espacio recuperable y nivel de riesgo.
- Acceso a Sensor de almacenamiento, aplicaciones instaladas y Recuperación.
- Diagnósticos SFC, DISM y CHKDSK, limpieza de componentes y optimización de la
  unidad mediante las herramientas incluidas en Windows.

## Seguridad

- El análisis es de solo lectura.
- La limpieza siempre muestra una estimación y pide confirmación.
- Los ajustes del registro se respaldan en
  `%LOCALAPPDATA%\CdeDev\backups` antes de aplicarse.
- La aplicación no desactiva Defender, Windows Update, el firewall ni servicios
  esenciales.
- "Dejar solo Windows" abre la página oficial de Recuperación. CdeDev no
  intenta borrar manualmente la raíz del sistema.
- "Liberar RAM" de forma periódica no mejora el rendimiento; Windows administra
  la memoria y la aplicación solo muestra su estado.

## Desarrollo

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\pyinstaller.exe --noconfirm CdeDev.spec
```

El ejecutable portátil se genera en `dist\CdeDev.exe`. El instalador se compila
con Inno Setup 7 usando `installer.iss` y se genera en `installer\`.

## Compatibilidad

- Windows 10 y Windows 11 de 64 bits.
- No requiere Python ni otras dependencias instaladas.
- Solicita permisos de administrador para inspeccionar perfiles y aplicar
  operaciones del sistema.

Autor: [Cde57](https://github.com/Cde571)
