"""Autoriza a HOPE en Google y guarda un token local para Railway."""
import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/drive.readonly',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cliente', type=Path,
                        default=Path(__file__).resolve().with_name('hope-cliente.json'))
    parser.add_argument('--salida', type=Path,
                        default=Path.home() / 'Downloads' / 'hope-token-railway.json')
    args = parser.parse_args()

    if not args.cliente.is_file():
        parser.error(f'No se encontró el JSON del cliente en {args.cliente}. Usa --cliente para indicar la ruta.')
    if args.salida.exists():
        parser.error(f'Ya existe {args.salida}. Usa --salida con otro nombre para conservar el token actual.')
    if not args.salida.parent.is_dir():
        parser.error(f'La carpeta de salida no existe: {args.salida.parent}.')

    flujo = InstalledAppFlow.from_client_secrets_file(str(args.cliente), SCOPES)
    credencial = flujo.run_local_server(host='127.0.0.1', port=0, prompt='consent')
    if not credencial.refresh_token:
        raise SystemExit('Google no devolvió un token de renovación.')
    if credencial.granted_scopes is not None and not set(SCOPES).issubset(credencial.granted_scopes):
        raise SystemExit('Google no concedió todos los permisos de Calendar y Drive.')

    with args.salida.open('x', encoding='utf-8') as archivo:
        archivo.write(credencial.to_json())
    print(f'Archivo generado en: {args.salida}')


if __name__ == '__main__':
    main()
