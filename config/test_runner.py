"""Runner de testes do projeto."""

import logging

from django.test.runner import DiscoverRunner


class QuietTestRunner(DiscoverRunner):
    """Roda a suíte sem o ruído de DEBUG/INFO dos apps.

    Os serviços (Mercado Pago, WhatsApp, notificações) registram o passo a passo
    em INFO — útil no dia a dia, atrapalha na saída dos testes. Avisos e erros
    continuam aparecendo.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        logging.disable(logging.INFO)

    def teardown_test_environment(self, **kwargs):
        logging.disable(logging.NOTSET)
        super().teardown_test_environment(**kwargs)
