from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import CuestionarioRegistro, HistorialClinico, PerfilPsicologo


class SintesisClinicaTests(TestCase):
    @patch('groq.Groq')
    def test_sintesis_usa_las_bitacoras_con_o_sin_cuestionario(self, groq):
        usuario = User.objects.create_user('terapeuta')
        psicologo = PerfilPsicologo.objects.create(usuario=usuario, cedula_profesional='prueba')
        paciente = User.objects.create_user('paciente')
        HistorialClinico.objects.create(
            paciente=paciente, psicologo=psicologo, notas_sesion='Se revisaron hábitos de sueño.',
        )
        self.client.force_login(usuario)
        crear = groq.return_value.chat.completions.create
        crear.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='<p>Síntesis de prueba.</p>'))],
        )
        url = reverse('generar_sintesis', args=[paciente.pk])

        for respuestas in (None, {}, {'motivo_consulta': 'Mejorar el descanso'}):
            with self.subTest(respuestas=respuestas):
                if respuestas is not None:
                    CuestionarioRegistro.objects.update_or_create(
                        paciente=paciente, flujo_elegido='individual', defaults={'respuestas': respuestas},
                    )
                respuesta = self.client.post(url)
                self.assertEqual(respuesta.json()['status'], 'success', respuesta.content)
                prompt = crear.call_args.kwargs['messages'][0]['content']
                self.assertIn('Se revisaron hábitos de sueño.', prompt)
                if respuestas:
                    self.assertIn('Motivo consulta: Mejorar el descanso', prompt)
                else:
                    self.assertNotIn('CUESTIONARIO INICIAL', prompt)
