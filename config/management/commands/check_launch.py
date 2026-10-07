from django.core.management.base import BaseCommand, CommandError
from config.readiness import launch_requirements


class Command(BaseCommand):
    help = 'Lista os dados e credenciais que o responsável precisa preencher antes de abrir ao público.'

    def handle(self, *args, **options):
        checks = launch_requirements()
        for check in checks:
            self.stdout.write(f'{"OK" if check["ok"] else "PENDENTE"}: {check["label"]}')
        if any(not check['ok'] for check in checks):
            raise CommandError('Preencha os itens pendentes e execute novamente.')
        self.stdout.write(self.style.SUCCESS('Dados e credenciais de lançamento preenchidos.'))
