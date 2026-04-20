import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Los mismos permisos que tienes en views.py
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def generar_nuevo_token():
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    # Esto abrirá una ventana en tu navegador
    creds = flow.run_local_server(port=0)
    
    # Guardamos el alma de la nueva cuenta
    with open('token.json', 'w') as token:
        token.write(creds.to_json())
    print("¡Éxito! Nuevo token.json generado.")

if __name__ == '__main__':
    generar_nuevo_token()