# AGENTS.md

Django monolith for "Espacio HOPE" (clinical psychology platform). One app (`core`), one project
package (`hope_project`), 57 migrations, deployed to Railway via `Procfile`. No tests, no CI,
no README, no Docker.

## Commands

Use the repo virtualenv interpreter, never a global `python`:

- `.\venv\Scripts\python.exe -m pip install -r requirements.txt` (note: `runtime.txt` pins 3.13 but
  the committed `venv/` is 3.14)
- Dev server: `.\venv\Scripts\python.exe manage.py runserver` — this talks to the **live** Railway
  MySQL (see below), so read carefully before running anything that writes.
- There is no test suite (`core/tests.py` is the untouched Django stub, no pytest/tox config).
  "Verifying" means:
  - `manage.py check`
  - `manage.py makemigrations --check --dry-run` (currently reports no changes)
  - `manage.py probar_reporte_pdf --cita-id <id> [--abrir] [--salida DIR]` — renders the
    patient/psychologist PDFs for a cita without sending mail or touching the DB
- Custom commands worth knowing: `enviar_reportes_citas [--cita-id ID]` (generate + email session
  reports), `generar_bitacoras_historicas_meet`, `poblar_tratamientos` (backfills `TratamientoPaciente`).
- The PowerShell console is cp1252 while the codebase is UTF-8 full of emoji in literals and log
  lines. Prefix long-running commands with `$env:PYTHONIOENCODING="utf-8"` to avoid
  `UnicodeEncodeError` crashes.

## Database and secrets

- `hope_project/settings.py` is committed with the production MySQL credentials (Railway proxy,
  db `hope`), a hardcoded `SECRET_KEY` and `DEBUG = True`. `token.json` and `credentials.json` are
  also tracked. Treat any `migrate`, or any custom command that writes, as a **production change**.
- No `.env` and no python-dotenv — everything external comes from real environment variables:
  - `GOOGLE_TOKEN_JSON` — JSON string (same content as the committed `token.json`) used for Google
    Calendar/Meet. The code reads the env var, not the file, and a refreshed token is *not*
    persisted automatically (you have to update the Railway variable).
  - `GROQ_API_KEY` (every AI feature), `EMAIL_HOST_USER` + `EMAIL_HOST_PASSWORD` (Gmail SMTP, needs
    an app password)
  - `CLIP_API_KEY_PROD` + `CLIP_CLAVE_SECRETA_PROD` (card payments), `PAYPAL_CLIENT_ID`
  - Chromium for PDFs is located through `CHROME_BIN` / `GOOGLE_CHROME_SHIM` / `PUPPETEER_EXECUTABLE_PATH` /
    `EDGE_BIN` … and, on Windows, by probing the Chrome/Edge install paths.

## Architecture that changes how you work

- Nearly all behaviour lives in `core/views.py` (8,847 lines). `core/urls.py` (151 lines) is the only
  app urlconf; `hope_project/urls.py` adds just the admin site — mounted at **`/error-59/`**, not
  `/admin/`.
- **Multi-tenant by hostname, not by Django Sites.** `tectuminhause.*` (TECTUM In-House) and
  `tectumbeneficios.*` (paid corporate tier) hit the *same* views (`inicio`, `inicio_tectum*`,
  `login_usuario`, `panel_generico`, `guardar_cita_ajax`), which branch on `request.get_host()`, a
  `convenio` POST field and `session['convenio_tectum'…]`, then render a different template from
  `templates/tectum/` or `templates/tectum_beneficios/`. A new host must be added to **both**
  `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` in settings.
- Two profile models with different related names: patients are `user.perfil` (`UsuarioPerfil`,
  `es_tectum` flag), therapists are `user.perfil_psicologo` (`PerfilPsicologo`). Access is
  `es_admin(user)` → `is_superuser`, or `hasattr(user, 'perfil_psicologo')`. TECTUM In-House
  additionally requires the email to be in the hardcoded `COLABORADORES_TECTUM` set (`views.py:680`).
- Scheduling: `obtener_slots_psicologo()` composes availability as
  `EsquemaHorarioPsicologo` (date-range rows, validated in `clean()`) → per-weekday
  `HorarioPersonalizadoDia` overrides (filtered by session type; a weekday that *has* overrides
  ignores the base jornada) → minus `DiaFestivo`, `DiaLibrePsicologo`, existing `Cita`, and a hardcoded
  `bloqueos_globales` list of past team-meeting slots. Slots are 1 hour and the scan is capped at
  120 days (`max_iteraciones`). Prices/commissions come from `PRECIO_BASE_SESION` /
  `COMISION_PORCENTAJE_SESION` and are always recomputed server-side.
- The 2-hour minimum lead time for booking is **frontend only**: `HORAS_ANTICIPACION_MINIMA` is
  duplicated by hand in `templates/panel_generico.html`, `templates/tectum/panel-tectum.html` and
  `templates/tectum_beneficios/panel-beneficios.html` (compared in CDMX time). The backend accepts
  any slot, so a booking rule change means editing all three panels.
- PDFs: `core/utils_pdf.convertir_html_a_pdf()` tries headless Chromium `--print-to-pdf`, then
  WeasyPrint, then PyMuPDF (which does **not** support modern CSS). Deploy images install chromium
  via `Aptfile` / `railpack.json` and set `CHROME_BIN` in `nixpacks.toml`. The actual report
  templates are `templates/Pruebas/claude_pascinete.html` (patient) and
  `templates/Pruebas/claude_psioclog.html` (therapist) — "Pruebas" here means *production*.
- Uploads (doctor photos, consent PDFs, clinical attachments, generated reports) use plain
  `FileSystemStorage` under `MEDIA_ROOT = media/`. There is no object-storage backend, so on Railway
  they live on the ephemeral container filesystem.

## Traps

- `core/views.py` defines the same function twice and **Python keeps the last definition**:
  `procesar_registro_taller` (dead copy at 3456 vs. active `@require_POST @csrf_protect` at 4464) and
  `_procesar_reporte_de_una_cita` (dead 3-arg version at 5740 vs. active 4-arg version at 5941).
  Grep the name before editing either. `core/urls.py` also repeats
  `name='panel_doctor'`, `name='guardar_historial'` and `name='ver_imagen'`.
- `core/cargar_horarios.py` and `core/cargar_junio.py` are stale one-off loaders that import the
  removed `HorarioPsicologo` model — running them raises ImportError. Leave them alone.
- `_enviar_correo_ipp_async()` (`views.py:4068`) has no callers despite its name; other mail sends
  are synchronous.
- 12 views use `@csrf_exempt`. Newer AJAX endpoints use `@require_POST @csrf_protect` plus the CSRF
  header — match the newer style unless there is a reason not to.
- `requirements.txt` is UTF-16LE with BOM and ends with a duplicate unpinned `requests` line;
  preserve that encoding when editing by hand (pip reads the BOM fine).
- Static: `staticfiles/` is the collected, gitignored output. Under `DEBUG` WhiteNoise serves
  straight from `static/`, but production runs `collectstatic` with
  `CompressedManifestStaticFilesStorage`, so a `{% static %}` reference to a file missing from
  `static/` only breaks in production.

## Conventions

- Spanish is the working language — model/field/view names, comments, templates, log lines and UI
  copy (`LANGUAGE_CODE = 'es-mx'`, `TIME_ZONE = 'America/Mexico_City'`). New code should match.
- Comments are prefixed with emoji markers (`# 🔥 NUEVO:`, `# 🔥 OPTIMIZADO:`) and sections use
  `# ===…` banners.
- Templates are flat in `templates/` (shared `base.html`), page JS is inline at the bottom of each
  template, and `static/js/main.js` holds shared behaviour. Availability/booking markup exists as
  near-duplicate panels that are kept in sync manually.
