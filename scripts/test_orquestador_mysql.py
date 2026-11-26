"""Local disposable MySQL only. Never imports production settings or Railway credentials."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['DJANGO_SETTINGS_MODULE'] = 'hope_project.settings_flujo_test'
from django.conf import settings

if os.environ.get('HOPE_MYSQL_TEST_CONFIRM') != 'BASE_LOCAL_DESECHABLE':
    raise SystemExit('Configure HOPE_MYSQL_TEST_CONFIRM=BASE_LOCAL_DESECHABLE para usar MySQL local aislado.')

settings.DATABASES = {'default': {
    'ENGINE': 'django.db.backends.mysql', 'HOST': '127.0.0.1',
    'PORT': os.environ.get('HOPE_MYSQL_TEST_PORT', '3307'),
    'NAME': 'hope_orquestador_aislado',
    'USER': os.environ['HOPE_MYSQL_TEST_USER'], 'PASSWORD': os.environ['HOPE_MYSQL_TEST_PASSWORD'],
    'TEST': {'NAME': 'test_hope_orquestador_aislado'},
    'OPTIONS': {'charset': 'utf8mb4'},
}}

import django
django.setup()
from django.core.management import call_command
call_command('test', 'core.test_orquestador', interactive=False)
