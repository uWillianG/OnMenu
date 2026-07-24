"""Recomprime as imagens já gravadas no storage.

O modelo comprime o que entra a partir de agora; este comando cuida do acervo
que já está no disco. Rodar uma vez depois do deploy:

    python manage.py compress_images --dry-run   # mostra o que faria
    python manage.py compress_images
"""

from django.core.management.base import BaseCommand

from menu.imaging import LOGO_SIZE, MENU_PHOTO_SIZE, compress
from menu.models import MenuItem, Restaurant

# (modelo, campo, teto) — os mesmos limites que o upload aplica.
IMAGE_FIELDS = (
    (MenuItem, 'image', MENU_PHOTO_SIZE),
    (Restaurant, 'logo', LOGO_SIZE),
)


def _format_size(num_bytes):
    if abs(num_bytes) >= 1024 * 1024:
        return f'{num_bytes / (1024 * 1024):.1f} MB'
    return f'{num_bytes / 1024:.0f} KB'


class Command(BaseCommand):
    help = 'Recomprime as imagens do cardápio já gravadas, reduzindo o peso do cardápio.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas relata o que seria feito, sem gravar nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        total_before = total_after = 0
        touched = skipped = failed = 0

        for model, field_name, max_size in IMAGE_FIELDS:
            for obj in model.objects.exclude(**{field_name: ''}).iterator():
                field = getattr(obj, field_name)
                try:
                    before = field.size
                except (OSError, ValueError):
                    # Registro aponta para arquivo que não está mais no disco.
                    self.stderr.write(f'  ausente: {field.name}')
                    failed += 1
                    continue

                with field.open('rb') as handle:
                    compressed = compress(handle, size=before, max_size=max_size)

                if compressed is None:
                    skipped += 1
                    continue

                after = compressed.size
                total_before += before
                total_after += after
                touched += 1
                self.stdout.write(
                    f'  {field.name}: {_format_size(before)} → {_format_size(after)}'
                )

                if not dry_run:
                    # Grava por cima do mesmo nome: o registro no banco não muda,
                    # e nenhuma URL já compartilhada quebra.
                    name = field.name
                    field.storage.delete(name)
                    field.storage.save(name, compressed)

        saved = total_before - total_after
        prefix = '[simulação] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}{touched} imagem(ns) reduzida(s), {skipped} já otimizada(s), '
            f'{failed} com problema.'
        ))
        if touched:
            self.stdout.write(self.style.SUCCESS(
                f'{prefix}{_format_size(total_before)} → {_format_size(total_after)} '
                f'(economia de {_format_size(saved)}).'
            ))
